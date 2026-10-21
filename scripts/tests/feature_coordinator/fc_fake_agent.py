#!/usr/bin/python3
"""A stand-in for the `claude` and `codex` CLIs, used by the Phase 3 tests.

It answers `--version` and `--help` (`exec --help`) with the flags the launchers require,
and otherwise behaves like a non-interactive agent run: it reads the prompt from stdin,
takes the JSON action list after a `FAKE-ACTIONS:` line, performs the actions relative to
its working directory and reports every attempt in the CLI's own output format
(Claude Code stream-json `result` event, or Codex JSONL events). No model is involved.

Actions: write, try_write, delete, symlink, read, try_read, sleep, wait_for, git,
ignore_sigterm, daemon (a grandchild that keeps appending to a file), exit.
W1 isolation probes (report only outcomes, never a credential value): http (a request to the
broker endpoint with this run's token, none, or a given one), connect (TCP), connect_unix
(a path or an `@abstract` name), resolve (DNS), exists, scan (markers in /proc environ and
cmdline of every visible process, in this environment, and in files), exec (run a program,
e.g. code the agent wrote) and crash (SIGKILL itself).
Standard library only: it runs inside the sandbox with the system interpreter.
"""

# pylint: disable=missing-function-docstring

import json
import os
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


def run_actions(actions):  # pylint: disable=too-many-branches
    report = []
    for act in actions:
        kind = act["do"]
        if kind == "write":
            write(act["path"], act.get("text", "x\n"))
            report.append({"action": f"write {act['path']}", "ok": True})
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
        elif kind == "git":
            proc = subprocess.run(["git", *act["args"]], capture_output=True, text=True, check=False)
            report.append({"action": "git " + " ".join(act["args"]), "ok": proc.returncode == 0,
                           "error": proc.stderr.strip()[-300:]})
        elif kind == "ignore_sigterm":
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
        elif kind == "daemon":
            report.append({"action": "daemon", "ok": True, "value": daemon(act["path"], act.get("interval", 0.05))})
        elif kind == "exit":
            emit(report, error=act.get("code", 1) != 0)
            sys.exit(act.get("code", 1))
        elif kind in PROBES:
            run_probe(report, act)
    return report


MODE = "codex" if len(sys.argv) > 1 and sys.argv[1] == "exec" else "claude"


def emit(report, error=False):
    text = json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd(), "home": os.environ.get("HOME"),
                       "env_keys": sorted(os.environ), "report": report})
    if MODE == "claude":
        print(json.dumps({"type": "system", "subtype": "init"}), flush=True)
        print(json.dumps({"type": "result", "subtype": "error_during_execution" if error else "success",
                          "is_error": error, "result": text, "num_turns": 1, "total_cost_usd": 0.0,
                          "permission_denials": []}), flush=True)
    else:
        print(json.dumps({"type": "thread.started"}), flush=True)
        print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": text}}), flush=True)
        print(json.dumps({"type": "turn.failed", "error": {"message": "scripted"}} if error
                         else {"type": "turn.completed", "usage": {}}), flush=True)


def main():
    args = sys.argv[1:]
    if "--version" in args:
        print("fake-agent 9.9.9")
        return 0
    if "--help" in args:
        print(HELP)
        return 0
    prompt = sys.stdin.read()
    actions = []
    for line in prompt.splitlines():
        if line.startswith("FAKE-ACTIONS:"):
            actions = json.loads(line[len("FAKE-ACTIONS:"):])
    emit(run_actions(actions))
    return 0


if __name__ == "__main__":
    sys.exit(main())
