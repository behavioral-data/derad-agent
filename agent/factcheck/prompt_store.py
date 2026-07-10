"""Versioned prompt assets for the v0.7 loop pipeline.

Prompts live as .md files in agent/factcheck/prompts/. `prompt_version()` is a
12-hex digest over every prompt file (sorted by name) — recorded in each
freeze's backend_version.prompt_version so a verdict is tied to the exact
prompt text that produced it.
"""
from __future__ import annotations

import functools
import hashlib
from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def load_prompt(name: str) -> str:
    path = _PROMPTS_DIR / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"No prompt asset named {name!r} in {_PROMPTS_DIR}")
    return path.read_text(encoding="utf-8")


@functools.lru_cache(maxsize=1)
def prompt_version() -> str:
    h = hashlib.sha256()
    for path in sorted(_PROMPTS_DIR.glob("*.md")):
        h.update(path.name.encode())
        h.update(path.read_bytes())
    return h.hexdigest()[:12]
