#!/usr/bin/env python3
"""PreToolUse hook: block Edit/Write/MultiEdit on sensitive paths.

Reads the Claude Code hook JSON payload from stdin and exits 2 (blocking)
if the target file_path matches a sensitive pattern. Exit 0 otherwise.

Blocks:
  - keys.md (credential notes at repo root)
  - any .env or .env.* file
  - anything inside data/ (participant data and run artifacts)

Allows reads — only blocks write-shaped tools, which the matcher in
settings.json should already constrain.
"""
import json
import re
import sys

SENSITIVE = re.compile(
    r"(^|/)("
    r"keys\.md"
    r"|\.env(\..+)?"
    r"|data/"
    r")"
)

try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)

path = (payload.get("tool_input") or {}).get("file_path") or ""
if not path:
    sys.exit(0)

if SENSITIVE.search(path):
    sys.stderr.write(
        f"blocked: {path} is on the sensitive-paths list "
        "(keys.md / .env / data/). Edit it manually if you really mean to.\n"
    )
    sys.exit(2)

sys.exit(0)
