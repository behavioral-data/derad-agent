#!/usr/bin/env python3
"""PostToolUse hook: after editing agent/**/*.py, run related pytest selection.

The test files in this repo use loose `test_<keyword>.py` naming, not a strict
1:1 mapping. So we use pytest's -k filter on the file stem to pull in any test
whose name mentions the edited module.

Non-blocking: reports failures via stderr (exit 1 if pytest fails). Does
nothing if the edited file isn't under agent/.
"""
import json
import os
import subprocess
import sys

try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)

path = (payload.get("tool_input") or {}).get("file_path") or ""
if not path:
    sys.exit(0)

# Only Python files under agent/
norm = path.replace("\\", "/")
if "/agent/" not in norm or not norm.endswith(".py"):
    sys.exit(0)
if norm.endswith("__init__.py"):
    sys.exit(0)

stem = os.path.splitext(os.path.basename(norm))[0]
if not stem:
    sys.exit(0)

# Run pytest with -k filter. Quiet, fail fast, short summary on failures.
cmd = ["pytest", "-q", "-k", stem, "--no-header", "-x", "--maxfail=3"]
try:
    res = subprocess.run(
        cmd,
        cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        capture_output=True,
        text=True,
        timeout=60,
    )
except FileNotFoundError:
    # pytest not installed in this env — silent skip
    sys.exit(0)
except subprocess.TimeoutExpired:
    sys.stderr.write(f"pytest -k {stem} timed out after 60s; skipping\n")
    sys.exit(0)

# pytest exit codes: 0=pass, 5=no tests collected (also fine), others=fail
if res.returncode in (0, 5):
    sys.exit(0)

sys.stderr.write(f"\n=== pytest -k {stem} FAILED ===\n")
sys.stderr.write(res.stdout[-2000:] if res.stdout else "")
sys.stderr.write(res.stderr[-1000:] if res.stderr else "")
sys.exit(1)
