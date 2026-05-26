#!/usr/bin/env python
"""Exercise the new ClaudeWebSearchBackend against the three failing
queries from the production log. Verifies end-to-end: env var → backend
selection → search() → SearchHit list."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / "agent" / "llm" / ".env")
sys.path.insert(0, str(ROOT))

# Force Claude backend.
os.environ["CLAUDE_SEARCH_DEPLOYMENT"] = os.environ.get(
    "CLAUDE_SEARCH_DEPLOYMENT", os.environ["AZURE_CLAUDE_DEPLOYMENT_CHAT"]
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

from agent.factcheck.search import build_default_backend  # noqa: E402

QUERIES = [
    "Trump claims unnamed drug can reverse death medical experts say no such drug exists Random Weird Facts social media graphic",
    "fact check: Donald Trump stated that the United States has experimental drugs capable of bringing dead people back to life",
    "Trump claims unnamed drug can reverse death medical experts respond",
]


def main() -> None:
    backend = build_default_backend()
    print(f"backend.name = {backend.name}")
    for q in QUERIES:
        print(f"\n--- query ({len(q)} chars) ---")
        print(f"  {q!r}")
        hits = backend.search(q, top_k=5)
        print(f"  → {len(hits)} hit(s)")
        for i, h in enumerate(hits, 1):
            print(f"  {i}. {h.url}")
            print(f"     title:   {h.title[:80]!r}")
            print(f"     snippet: {h.snippet[:120]!r}")


if __name__ == "__main__":
    main()
