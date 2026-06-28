#!/usr/bin/env python
"""Batch-generate bot replies for tweets listed in a CSV.

For each post, runs the fact-check pipeline once per tone condition and
writes a new CSV with columns: id, neutral, satirical, agreeable.

Usage:
    .venv/bin/python scripts/batch_generate_replies.py /path/to/posts.csv
    .venv/bin/python scripts/batch_generate_replies.py posts.csv -o replies.csv -n 5

One-time setup (from repo root):
    python3.13 -m venv .venv && .venv/bin/pip install -e . -r requirements.txt

Requires Azure Claude credentials in agent/llm/.env (same as the live bot).
Does not post to X — generation only.
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))


def _check_runtime_deps() -> None:
    """Fail fast with setup instructions when deps are missing or mismatched."""
    try:
        import pydantic  # noqa: F401
        from pydantic import model_validator  # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            "pydantic v2 is required (need model_validator). "
            "From repo root run:\n"
            "  python3.13 -m venv .venv && .venv/bin/pip install -e . -r requirements.txt\n"
            "Then use: .venv/bin/python scripts/batch_generate_replies.py ..."
        ) from exc
    try:
        import langchain_anthropic  # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            "langchain-anthropic is not installed for this Python interpreter "
            f"({sys.executable}). From repo root run:\n"
            "  .venv/bin/pip install -e . -r requirements.txt\n"
            "Then use: .venv/bin/python scripts/batch_generate_replies.py ..."
        ) from exc


_check_runtime_deps()

from dotenv import load_dotenv

load_dotenv(_REPO_ROOT / "agent" / "llm" / ".env")

from agent.app.participants import VALID_TONES
from agent.app.utils import generate_reply

# Output column order (fixed per user request).
OUTPUT_TONES = ("neutral", "satirical", "agreeable")
OUTPUT_FIELDNAMES = ("id", *OUTPUT_TONES)


@dataclass(frozen=True)
class Post:
    """One tweet to process."""

    id: str
    text: str


def default_output_path(input_path: Path) -> Path:
    """Derive ``{stem}_replies.csv`` beside the input file."""
    return input_path.with_name(f"{input_path.stem}_replies.csv")


def load_posts(input_path: Path, *, limit: int | None = None) -> list[Post]:
    """Read posts from a CSV with ``id`` (or ``tweetId``) and ``text`` columns."""
    posts: list[Post] = []
    with input_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            post_id = (row.get("id") or row.get("tweetId") or "").strip()
            text = (row.get("text") or "").strip()
            if not post_id:
                logging.warning("Skipping row with no id/tweetId: %r", row)
                continue
            if not text:
                logging.warning("Skipping id=%s — empty text", post_id)
                continue
            posts.append(Post(id=post_id, text=text))
            if limit is not None and len(posts) >= limit:
                break
    return posts


def generate_reply_for_tone(
    statement: str,
    *,
    tweet_id: str,
    tone: str,
) -> str:
    """Run the pipeline for one tone and return reply text (empty on failure)."""
    if tone not in VALID_TONES:
        raise ValueError(f"Unknown tone {tone!r}; expected one of {VALID_TONES}")

    result = generate_reply(
        statement=statement,
        tone=tone,
        exclude_tweet_id=tweet_id,
    )
    return (result.get("text") or "").strip()


def generate_all_tones(statement: str, tweet_id: str) -> dict[str, str]:
    """Generate neutral, satirical, and agreeable replies for one post."""
    replies: dict[str, str] = {}
    for tone in OUTPUT_TONES:
        logging.info("id=%s tone=%s — running pipeline", tweet_id, tone)
        try:
            replies[tone] = generate_reply_for_tone(
                statement, tweet_id=tweet_id, tone=tone,
            )
        except Exception:
            logging.exception("id=%s tone=%s — pipeline failed", tweet_id, tone)
            replies[tone] = ""
    return replies


def write_output_header(writer: csv.DictWriter, fh) -> None:
    writer.writeheader()
    fh.flush()


def write_output_row(
    writer: csv.DictWriter, fh, post_id: str, replies: dict[str, str],
) -> None:
    writer.writerow({"id": post_id, **{t: replies.get(t, "") for t in OUTPUT_TONES}})
    fh.flush()


def process_batch(
    input_path: Path,
    output_path: Path,
    *,
    limit: int | None = None,
) -> int:
    """Load posts, generate replies, write output CSV. Returns rows written."""
    posts = load_posts(input_path, limit=limit)
    if not posts:
        logging.warning("No posts to process in %s", input_path)
        return 0

    logging.info(
        "Processing %d post(s) from %s → %s",
        len(posts), input_path, output_path,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=OUTPUT_FIELDNAMES,
            quoting=csv.QUOTE_MINIMAL,
        )
        write_output_header(writer, fh)

        for idx, post in enumerate(posts, start=1):
            logging.info("[%d/%d] id=%s", idx, len(posts), post.id)
            replies = generate_all_tones(post.text, post.id)
            write_output_row(writer, fh, post.id, replies)
            written += 1

    logging.info("Wrote %d row(s) to %s", written, output_path)
    return written


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate neutral/satirical/agreeable bot replies for CSV posts.",
    )
    parser.add_argument(
        "input_csv",
        type=Path,
        help="Input CSV with id (or tweetId) and text columns",
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=None,
        help="Output CSV path (default: <input_stem>_replies.csv beside input)",
    )
    parser.add_argument(
        "-n", "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Process only the first N posts (default: all)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable INFO logging (pipeline stage progress)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    log_level = logging.INFO if args.verbose else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("httpx", "azure", "azure.core", "azure.identity", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    input_path: Path = args.input_csv.expanduser().resolve()
    if not input_path.is_file():
        print(f"ERROR: input file not found: {input_path}", file=sys.stderr)
        return 1

    output_path = (
        args.output.expanduser().resolve()
        if args.output is not None
        else default_output_path(input_path)
    )

    if args.limit is not None and args.limit < 1:
        print("ERROR: --limit must be a positive integer", file=sys.stderr)
        return 1

    try:
        written = process_batch(input_path, output_path, limit=args.limit)
    except Exception:
        logging.exception("Batch failed")
        return 1

    if written == 0:
        return 1
    print(f"Done — {written} row(s) written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
