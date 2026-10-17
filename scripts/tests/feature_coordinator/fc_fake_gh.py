#!/usr/bin/env python3
"""A fake `gh` executable for the adapter tests: replays scripted `gh api --include` outputs.

FAKE_GH_SCRIPT names a JSON list of steps; each invocation consumes the first one:
    {"stdout": "...", "stderr": "...", "exit": 0, "sleep": 0}
FAKE_GH_LOG (optional) receives one JSON line per invocation: {"argv": [...], "stdin": "..."}.
It never contacts GitHub.
"""

import json
import os
import sys
import time

with open(os.environ["FAKE_GH_SCRIPT"], encoding="utf-8") as handle:
    steps = json.load(handle)
step = steps.pop(0) if steps else {"exit": 99, "stderr": "fake gh: no scripted response"}
with open(os.environ["FAKE_GH_SCRIPT"], "w", encoding="utf-8") as handle:
    json.dump(steps, handle)
stdin = sys.stdin.read() if "--input" in sys.argv else ""
if os.environ.get("FAKE_GH_LOG"):
    with open(os.environ["FAKE_GH_LOG"], "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"argv": sys.argv[1:], "stdin": stdin}) + "\n")
time.sleep(float(step.get("sleep", 0)))
sys.stdout.write(step.get("stdout", ""))
sys.stderr.write(step.get("stderr", ""))
sys.exit(int(step.get("exit", 0)))
