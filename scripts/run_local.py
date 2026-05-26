#!/usr/bin/env python
"""Local pipeline runner with full INFO logging — no X integration.

Usage:
    python scripts/run_local.py "<claim text>" [--tone neutral] [--invoker "..."] [--image URL]

Same args as `python -m agent.factcheck` but with logging.basicConfig
turned on, so every stage transition + every LLM call shows up. Use this
when you want to see WHAT the pipeline is doing, not just its final reply.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%H:%M:%S",
)
# Silence verbose dependencies — we want pipeline logs, not raw HTTP.
for noisy in ("httpx", "azure", "azure.core", "azure.identity", "urllib3"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

from agent.factcheck.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
