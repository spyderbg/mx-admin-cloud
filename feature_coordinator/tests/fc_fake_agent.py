#!/usr/bin/python3
"""A stand-in for the `claude` and `codex` CLIs, used by the Phase 3 tests.

It answers `--version` and `--help` (`exec --help`) with the flags the launchers require,
and otherwise behaves like a non-interactive agent run: it reads the prompt from stdin,
takes the JSON action list after a `FAKE-ACTIONS:` line, performs the actions relative to
its working directory and reports every attempt in the CLI's own output format
(Claude Code stream-json `result` event, or Codex JSONL events). No model is involved.

Actions: write, append, try_write, delete, symlink, read, try_read, sleep, wait_for, git,
ignore_sigterm, daemon (a grandchild that keeps appending to a file; "pid_path" records its
identity, "ignore_sigterm" makes it need SIGKILL), once (nested actions only on the first
invocation: its marker file is created first), exit. W4.1 (watchdog tests): pidfile (this
process's "pid start_ticks"), trap_sigterm (log each SIGTERM to a file; ignore it unless "exit"),
check_gone (append "<label> gone" or "<label> ALIVE" for the process recorded in a pidfile — proof
that a previous agent had ended before this one started). A `wait_for` with
"required": true is a synchronization point: if its file does not appear within the timeout,
the run fails (exit 3) instead of continuing.
W1 isolation probes (report only outcomes, never a credential value): http (a request to the
broker endpoint with this run's token, none, or a given one), connect (TCP), connect_unix
(a path or an `@abstract` name), resolve (DNS), exists, scan (markers in /proc environ and
cmdline of every visible process, in this environment, and in files), exec (run a program,
e.g. code the agent wrote) and crash (SIGKILL itself).
Standard library only: it runs inside the sandbox with the system interpreter.

W2 (native backend): it also answers the CLIs' status commands — `auth status --json` (Claude
Code) and `login status` (Codex) — and reports its model and `apiKeySource` in the Claude init
event. Because native runs get an allowlisted environment, tests steer this through a control
file next to the executable (`<name>.json`): {"auth": "subscription" | "api-key" | "none" |
"unknown", "report_model": "...", "api_key_source": "...", "follow_prompt": true}. With
`follow_prompt` it also carries out a plain "Create the file PATH containing exactly one line:
TEXT" instruction (the subscription smoke test's prompt) when the prompt has no FAKE-ACTIONS;
`"around": {"PATH": {"before": [actions], "after": [actions]}}` runs actions around writing PATH
(e.g. a rendezvous of two agents whose prompts the tests cannot change). W4.2: it also follows a
multi-line "Create the file PATH with exactly the content below" instruction with a fenced block,
taken from the CURRENT STEP section only (the real-agent smoke test's prompt, unchanged).
"""

# pylint: disable=missing-function-docstring

import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

BROKER = "http://127.0.0.1:47613"

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


def start_ticks(pid):
    """Field 22 of /proc/PID/stat (None if the process is gone or a zombie)."""
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii") as handle:
            rest = handle.read().rsplit(") ", 1)[1].split()
    except (OSError, IndexError):
        return None
    return None if rest[0] in ("Z", "X", "x") else int(rest[19])


def identity_line(pid):
    return f"{pid} {start_ticks(pid)}\n"


def daemon(path, interval, pid_path=None, ignore_term=False):
    """Fork a grandchild (new session) that appends to *path* until it is killed."""
    pid = os.fork()
    if pid == 0:
        os.setsid()
        if ignore_term:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
        if pid_path:
            write(pid_path, identity_line(os.getpid()))
        while True:
            try:
                with open(path, "a", encoding="utf-8") as handle:
                    handle.write(f"{time.time()}\n")
            except OSError:
                pass
            time.sleep(interval)
    return pid


def trap_sigterm(path, leave):
    """Record every SIGTERM in *path*; exit on it only if *leave*."""
    def handler(_signum, _frame):
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"sigterm {os.getpid()} {time.time()}\n")
        if leave:
            os._exit(143)
    signal.signal(signal.SIGTERM, handler)


def broker_request(act):
    token = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("FC_BROKER_TOKEN") or ""
    token = token if act.get("token", "env") == "env" else act.get("token") or ""
    headers = {"Content-Type": "application/json", "anthropic-version": "2023-06-01"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(act.get("body", {"model": "m", "max_tokens": 8, "messages": []})).encode()
    request = urllib.request.Request(BROKER + act.get("path", "/v1/messages"), data=body, headers=headers,
                                     method=act.get("method", "POST"))
    try:
        with urllib.request.urlopen(request, timeout=act.get("timeout", 20)) as response:
            return {"status": response.status, "body": response.read()[:300].decode("utf-8", "replace")}
    except urllib.error.HTTPError as exc:
        return {"status": exc.code, "body": exc.read()[:300].decode("utf-8", "replace")}


def connect(act):
    with socket.create_connection((act["host"], act["port"]), timeout=act.get("timeout", 3)):
        return "connected"


def connect_unix(path):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(3)
        sock.connect("\0" + path[1:] if path.startswith("@") else path)
        return "connected"


def scan(markers, roots, session_token=False):
    """Where any marker occurs: labels only, never the matching text. With *session_token*, this
    run's own broker token is searched for too — in command lines and files, not environments
    (the runner holds it in its environment by design)."""
    hits = []
    token = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("FC_BROKER_TOKEN") or ""
    for pid in sorted(p for p in os.listdir("/proc") if p.isdigit()):
        if session_token and token:
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as handle:
                    if token.encode() in handle.read():
                        hits.append(f"/proc/{pid}/cmdline:token")
            except OSError:
                pass
        for name in ("environ", "cmdline"):
            try:
                with open(f"/proc/{pid}/{name}", "rb") as handle:
                    data = handle.read()
            except OSError:
                continue
            hits += [f"/proc/{pid}/{name}:{i}" for i, m in enumerate(markers) if m.encode() in data]
    hits += [f"env:{i}" for i, m in enumerate(markers) if any(m in v for v in os.environ.values())]
    for root in roots:
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                try:
                    with open(os.path.join(dirpath, name), "rb") as handle:
                        data = handle.read(4_000_000)
                except OSError:
                    continue
                hits += [f"{os.path.join(dirpath, name)}:{i}" for i, m in enumerate(markers) if m.encode() in data]
                if session_token and token and token.encode() in data:
                    hits.append(f"{os.path.join(dirpath, name)}:token")
    return {"hits": hits, "pids": sorted(int(p) for p in os.listdir("/proc") if p.isdigit())}


def run_probe(report, act):  # pylint: disable=too-many-branches
    kind = act["do"]
    label = act.get("label", kind)
    if kind == "http":
        attempt(report, label, lambda: broker_request(act))
        if act.get("required") and (not report[-1]["ok"] or report[-1]["value"]["status"] != 200):
            emit(report, error=True)  # like a real CLI: no model, no work
            sys.exit(3)
    elif kind == "connect":
        attempt(report, label, lambda: connect(act))
    elif kind == "connect_unix":
        attempt(report, label, lambda: connect_unix(act["path"]))
    elif kind == "resolve":
        attempt(report, label, lambda: socket.getaddrinfo(act["host"], 443)[0][4][0])
    elif kind == "exists":
        report.append({"action": label, "ok": True, "value": [p for p in act["paths"] if os.path.lexists(p)]})
    elif kind == "scan":
        report.append({"action": label, "ok": True,
                       "value": scan(act["markers"], act.get("roots", []), act.get("session_token", False))})
    elif kind == "exec":
        proc = subprocess.run(act["argv"], capture_output=True, text=True, check=False, timeout=60)
        report.append({"action": label, "ok": proc.returncode == 0, "value": proc.stdout[-3000:],
                       "error": proc.stderr[-500:]})
    elif kind == "crash":
        os.kill(os.getpid(), signal.SIGKILL)


PROBES = ("http", "connect", "connect_unix", "resolve", "exists", "scan", "exec", "crash")


def run_actions(actions):  # pylint: disable=too-many-branches,too-many-statements
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
            report.append({"action": "daemon", "ok": True, "value": daemon(
                act["path"], act.get("interval", 0.05), act.get("pid_path"), act.get("ignore_sigterm", False))})
        elif kind == "pidfile":
            write(act["path"], identity_line(os.getpid()))
        elif kind == "trap_sigterm":
            trap_sigterm(act["path"], act.get("exit", False))
        elif kind == "check_gone":
            with open(act["pid_file"], encoding="utf-8") as handle:
                pid, ticks = handle.read().split()
            alive = start_ticks(int(pid)) == int(ticks)
            with open(act["path"], "a", encoding="utf-8") as handle:
                handle.write(f"{act.get('label', 'previous')} {'ALIVE' if alive else 'gone'}\n")
        elif kind == "exit":  # "message": the CLI's own error text (e.g. a network or usage-limit error)
            emit(report, error=act.get("code", 1) != 0, message=act.get("message"))
            sys.exit(act.get("code", 1))
        elif kind in PROBES:
            run_probe(report, act)
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
    print(json.dumps(data.get(auth, {"loggedIn": True, "authMethod": "something-new", "apiProvider": "firstParty"}),
                     indent=2))  # several lines, as the real `claude auth status --json`
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


def current_step_file(prompt):
    """(path, content) of a "Create the file PATH with exactly the content below" instruction with a
    fenced block in the prompt's CURRENT STEP section, or None (completed steps are never redone)."""
    section = prompt.split("(do ONLY this step now):", 1)
    found = re.search(r"^Create the file (\S+) with exactly the content below[^\n]*\n```[^\n]*\n(.*?)^```",
                      section[-1], re.MULTILINE | re.DOTALL) if len(section) == 2 else None
    return (found.group(1), found.group(2)) if found else None


def main():
    args = sys.argv[1:]
    if "--version" in args:
        print("fake-agent 9.9.9")
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
    found = (create.group(1), create.group(2) + "\n") if create else current_step_file(prompt)
    if not actions and found and control().get("follow_prompt"):
        around = control().get("around", {}).get(found[0], {})
        actions = [*around.get("before", ()), {"do": "write", "path": found[0], "text": found[1]},
                   *around.get("after", ())]
    emit(run_actions(actions))
    return 0


if __name__ == "__main__":
    sys.exit(main())
