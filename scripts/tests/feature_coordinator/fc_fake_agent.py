#!/usr/bin/python3
"""A stand-in for the `claude` and `codex` CLIs, used by the Phase 3 tests.

It answers `--version` and `--help` (`exec --help`) with the flags the launchers require,
and otherwise behaves like a non-interactive agent run: it reads the prompt from stdin,
takes the JSON action list after a `FAKE-ACTIONS:` line, performs the actions relative to
its working directory and reports every attempt in the CLI's own output format
(Claude Code stream-json `result` event, or Codex JSONL events). No model is involved.

Actions: write, append, try_write, delete, symlink, read, try_read, sleep, wait_for, git,
ignore_sigterm, daemon (a grandchild that keeps appending to a file), once (nested actions only
on the first invocation: its marker file is created first), exit. A `wait_for` with
"required": true is a synchronization point: if its file does not appear within the timeout,
the run fails (exit 3) instead of continuing.
Standard library only: it runs inside the sandbox with the system interpreter.

W2 (native backend): it also answers the CLIs' status commands — `auth status --json` (Claude
Code) and `login status` (Codex) — and reports its model and `apiKeySource` in the Claude init
event. Because native runs get an allowlisted environment, tests steer this through a control
file next to the executable (`<name>.json`): {"auth": "subscription" | "api-key" | "none" |
"unknown", "report_model": "...", "api_key_source": "...", "follow_prompt": true}. With
`follow_prompt` it also carries out a plain "Create the file PATH containing exactly one line:
TEXT" instruction (the subscription smoke test's prompt) when the prompt has no FAKE-ACTIONS;
`"around": {"PATH": {"before": [actions], "after": [actions]}}` runs actions around writing PATH
(e.g. a rendezvous of two agents whose prompts the tests cannot change).
"""

# pylint: disable=missing-function-docstring

import json
import os
import re
import signal
import subprocess
import sys
import time

HELP = """Usage: fake-agent [options]
  -p, --print  --output-format <format> (text, json, stream-json)  --verbose  --restricted
  --tools <tools...>  --settings <file-or-json>  --strict-mcp-config
  --permission-mode <mode> (acceptEdits, dontAsk, plan)  --permission-prompts <target>
  --no-session-persistence  --disable-slash-commands  --max-budget-usd <amount>  --model <model>
exec options:
  --json  --strict-config  -s, --sandbox <SANDBOX_MODE> [read-only, workspace-write]  --ephemeral  --ignore-user-config
  -C, --cd <DIR>  -o, --output-last-message <FILE>  -c, --config <key=value>  -m, --model <MODEL>
"""


def attempt(report, label, func):
    try:
        value = func()
        report.append({"action": label, "ok": True, "value": value})
    except Exception as exc:  # pylint: disable=broad-except
        report.append({"action": label, "ok": False, "error": f"{type(exc).__name__}: {exc}"})


def write(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return len(text)


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()[:200]


def daemon(path, interval):
    """Fork a grandchild (new session) that appends to *path* until it is killed."""
    pid = os.fork()
    if pid == 0:
        os.setsid()
        while True:
            try:
                with open(path, "a", encoding="utf-8") as handle:
                    handle.write(f"{time.time()}\n")
            except OSError:
                pass
            time.sleep(interval)
    return pid


def run_actions(actions):  # pylint: disable=too-many-branches
    report = []
    for act in actions:
        kind = act["do"]
        if kind == "write":
            write(act["path"], act.get("text", "x\n"))
            report.append({"action": f"write {act['path']}", "ok": True})
        elif kind == "append":  # e.g. an audit line per invocation (an absolute path outside the repo)
            with open(act["path"], "a", encoding="utf-8") as handle:
                handle.write(act.get("text", "x\n"))
            report.append({"action": f"append {act['path']}", "ok": True})
        elif kind == "once":
            if not os.path.exists(act["path"]):
                write(act["path"], f"{os.getpid()}\n")
                report += run_actions(act["actions"])
        elif kind == "try_write":
            attempt(report, f"try_write {act['path']}", lambda a=act: write(a["path"], a.get("text", "x\n")))
        elif kind == "delete":
            attempt(report, f"delete {act['path']}", lambda a=act: os.remove(a["path"]))
        elif kind == "symlink":
            attempt(report, f"symlink {act['link']}", lambda a=act: os.symlink(a["target"], a["link"]))
        elif kind in ("read", "try_read"):
            attempt(report, f"read {act['path']}", lambda a=act: read(a["path"]))
        elif kind == "sleep":
            time.sleep(act["seconds"])
        elif kind == "wait_for":
            deadline = time.time() + act.get("timeout", 30)
            while not os.path.exists(act["path"]) and time.time() < deadline:
                time.sleep(0.02)
            report.append({"action": f"wait_for {act['path']}", "ok": os.path.exists(act["path"])})
            if act.get("required") and not report[-1]["ok"]:  # a synchronization point never reached
                report[-1]["error"] = f"timed out after {act.get('timeout', 30)}s"
                emit(report, error=True)
                sys.exit(3)
        elif kind == "git":
            proc = subprocess.run(["git", *act["args"]], capture_output=True, text=True, check=False)
            report.append({"action": "git " + " ".join(act["args"]), "ok": proc.returncode == 0,
                           "error": proc.stderr.strip()[-300:]})
        elif kind == "ignore_sigterm":
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
        elif kind == "daemon":
            report.append({"action": "daemon", "ok": True, "value": daemon(act["path"], act.get("interval", 0.05))})
        elif kind == "exit":  # "message": the CLI's own error text (e.g. a network or usage-limit error)
            emit(report, error=act.get("code", 1) != 0, message=act.get("message"))
            sys.exit(act.get("code", 1))
    return report


MODE = "codex" if len(sys.argv) > 1 and sys.argv[1] in ("exec", "login") else "claude"


def control():
    path = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), os.path.basename(sys.argv[0]) + ".json")
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def requested_model():
    args = sys.argv[1:]
    for flag in ("--model", "-m"):
        if flag in args and args.index(flag) + 1 < len(args):
            return args[args.index(flag) + 1]
    return ""


def init_event():
    model = requested_model()
    reported = control().get("report_model") or (f"claude-{model}-fake-1" if model in ("opus", "sonnet", "haiku")
                                                 else model or "claude-default-fake-1")
    return {"type": "system", "subtype": "init", "model": reported,
            "apiKeySource": control().get("api_key_source", "none")}


def status():
    """`auth status --json` (Claude Code) / `login status` (Codex), as steered by the control file."""
    auth = control().get("auth", "subscription")
    if MODE == "codex":
        print({"subscription": "Logged in using ChatGPT", "api-key": "Logged in using an API key - sk-***",
               "none": "Not logged in"}.get(auth, "Something else"))
        return 0 if auth in ("subscription", "api-key") else 1
    data = {"subscription": {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty",
                             "subscriptionType": "pro", "email": "not-a-real@example.invalid"},
            "api-key": {"loggedIn": True, "authMethod": "api_key", "apiProvider": "firstParty"},
            "none": {"loggedIn": False, "authMethod": "none", "apiProvider": "firstParty"}}
    print(json.dumps(data.get(auth, {"loggedIn": True, "authMethod": "something-new", "apiProvider": "firstParty"})))
    return 0


def emit(report, error=False, message=None):
    text = message or json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd(), "home": os.environ.get("HOME"),
                                  "env_keys": sorted(os.environ), "report": report})
    if MODE == "claude":
        print(json.dumps(init_event()), flush=True)
        print(json.dumps({"type": "result", "subtype": "error_during_execution" if error else "success",
                          "is_error": error, "result": text, "num_turns": 1, "total_cost_usd": 0.0,
                          "permission_denials": []}), flush=True)
    else:
        print(json.dumps({"type": "thread.started"}), flush=True)
        print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": text}}), flush=True)
        print(json.dumps({"type": "turn.failed", "error": {"message": message or "scripted"}} if error
                         else {"type": "turn.completed", "usage": {}}), flush=True)


def main():
    args = sys.argv[1:]
    if "--version" in args:
        print("fake-agent 1.0")
        return 0
    if "--help" in args:
        print(HELP)
        return 0
    if args[:2] in (["auth", "status"], ["login", "status"]):
        return status()
    prompt = sys.stdin.read()
    actions = []
    for line in prompt.splitlines():
        if line.startswith("FAKE-ACTIONS:"):
            actions = json.loads(line[len("FAKE-ACTIONS:"):])
    create = re.search(r"^Create the file (\S+) containing exactly one line: (.+)$", prompt, re.MULTILINE)
    if not actions and create and control().get("follow_prompt"):
        around = control().get("around", {}).get(create.group(1), {})
        actions = [*around.get("before", ()), {"do": "write", "path": create.group(1), "text": create.group(2) + "\n"},
                   *around.get("after", ())]
    emit(run_actions(actions))
    return 0


if __name__ == "__main__":
    sys.exit(main())
