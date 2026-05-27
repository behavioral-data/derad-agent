"""CLI driver for the fact-check pipeline.

Usage:
    python -m agent.factcheck "<claim text>"
    python -m agent.factcheck --tone satirical "<claim text>"
    python -m agent.factcheck --image <url> "<claim text>"
    python -m agent.factcheck --invoker "what's the context" "<claim text>"

`--invoker` passes the text the invoker would have written alongside
the bot handle in their mention tweet. The extractor parses it and
chooses the action. With no `--invoker`, the action is inferred from
the claim character.

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
        choices=("neutral", "agreeable", "satirical"),
        default="neutral",
        help="Tone renderer (default: neutral).",
    )
    parser.add_argument(
        "--all-tones",
        action="store_true",
        help="Render all three tones from the same frozen object (invariance demo).",
    )
    parser.add_argument(
        "--image",
        action="append",
        default=[],
        help="URL of an image to include (repeat for multiple). Triggers Stage 1.5.",
    )
    parser.add_argument(
        "--invoker",
        default="",
        help="Invoker instruction (the text the invoker would have written alongside @eddiexbot in their mention). Drives action selection.",
    )
    args = parser.parse_args(argv)

    print(f"Claim: {args.claim}", file=sys.stderr)
    if args.invoker:
        print(f"Invoker: {args.invoker!r}", file=sys.stderr)
    if args.image:
        print(f"Images: {args.image}", file=sys.stderr)
    print("", file=sys.stderr)

    frozen = run_pipeline(
        args.claim,
        image_urls=args.image or None,
        invoker_instruction=args.invoker,
    )
    print(
        f"Action: {frozen.action} (source={frozen.action_source}, pivoted_from={frozen.pivoted_from})\n"
        f"Action outcome: {frozen.action_outcome}\n"
        f"Verdict (legacy): {frozen.verdict_label}\n"
        f"Frozen: data/freezes/{frozen.invocation_id}.json\n",
        file=sys.stderr,
    )

    view = view_for_renderer(frozen, parent_post_text=args.claim)
    tones: list[Tone] = ["agreeable", "neutral", "satirical"] if args.all_tones else [args.tone]
    for tone in tones:
        text = render(view, tone)
        print(f"--- {tone} ---")
        print(text)
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
