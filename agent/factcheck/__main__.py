"""CLI driver for the thin-slice fact-check pipeline.

Usage:
    python -m agent.factcheck "<claim text>"
    python -m agent.factcheck --tone agonistic "<claim text>"

With no claim, runs the Rosa Camfield worked example from the design doc.
"""
from __future__ import annotations

import argparse
import sys

from .freeze import view_for_renderer
from .pipeline import run_pipeline
from .render import render
from .schema import Tone


_DEFAULT_EXAMPLE = (
    "Photo shows a 101-year-old woman named Rosa Camfield who gave birth to her 17th child."
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent.factcheck")
    parser.add_argument("claim", nargs="?", default=_DEFAULT_EXAMPLE, help="Claim text to fact-check.")
    parser.add_argument(
        "--tone",
        choices=("neutral", "agreeable", "agonistic"),
        default="neutral",
        help="Tone renderer (default: neutral).",
    )
    parser.add_argument(
        "--all-tones",
        action="store_true",
        help="Render all three tones from the same frozen object (invariance demo).",
    )
    args = parser.parse_args(argv)

    print(f"Claim: {args.claim}\n", file=sys.stderr)

    frozen = run_pipeline(args.claim)
    print(
        f"Verdict: {frozen.verdict_label}\n"
        f"Frozen: data/freezes/{frozen.invocation_id}.json\n",
        file=sys.stderr,
    )

    view = view_for_renderer(frozen)
    tones: list[Tone] = ["agreeable", "neutral", "agonistic"] if args.all_tones else [args.tone]
    for tone in tones:
        text = render(view, tone)
        print(f"--- {tone} ---")
        print(text)
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
