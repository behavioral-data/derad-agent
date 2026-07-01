#!/usr/bin/env python
"""Batch-generate bot replies for tweets listed in a CSV.

For each post, runs the fact-check pipeline once, renders all three tone
conditions, writes a CSV (id, neutral, satirical, agreeable), and updates
the matching bot_reply rows in mockx/study.db (body + is_stub=0).

Each reply cell contains the reply text plus a "Sources & reasoning:" block
with the cited reference URLs (same sources across tones, matching the live bot).

Usage:
    .venv/bin/python scripts/batch_generate_replies.py /path/to/posts.csv
    .venv/bin/python scripts/batch_generate_replies.py posts.csv -o replies.csv -n 5
    .venv/bin/python scripts/batch_generate_replies.py posts.csv --no-db   # CSV only

One-time setup (from repo root):
    python3.13 -m venv .venv && .venv/bin/pip install -e . -r requirements.txt
    python -m mockx.build_db   # creates mockx/study.db with stub interventions

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
DEFAULT_DB = _REPO_ROOT / "mockx" / "study.db"


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

from agent.app.utils import _APP_TO_FACTCHECK_TONE
from agent.factcheck.freeze import view_for_renderer
from agent.factcheck.pipeline import run_pipeline
from agent.factcheck.render import render
from mockx.db import connect_writable, update_bot_replies

# Output column order (fixed per user request).
OUTPUT_TONES = ("neutral", "satirical", "agreeable")
OUTPUT_FIELDNAMES = ("id", *OUTPUT_TONES)
MAX_SOURCES = 5
SOURCES_HEADER = "Sources & reasoning:"


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


def format_reply_with_sources(text: str, sources: list[str] | None) -> str:
    """Append cited reference URLs below the reply body."""
    body = (text or "").strip()
    urls = [u.strip() for u in (sources or []) if u and u.strip()]
    if not urls:
        return body
    links = "\n".join(urls)
    if body:
        return f"{body}\n\n{SOURCES_HEADER}\n{links}"
    return f"{SOURCES_HEADER}\n{links}"


def _primary_source_urls(frozen) -> list[str]:
    return [
        s.url for s in frozen.presentation_payload.primary_sources_to_cite
    ][:MAX_SOURCES]


def generate_all_tones(
    statement: str,
    tweet_id: str,
    *,
    include_sources: bool = True,
) -> dict[str, str]:
    """Run the pipeline once, render each tone, optionally append source links."""
    logging.info("id=%s — running pipeline", tweet_id)
    try:
        frozen = run_pipeline(
            statement,
            target_tweet_id=tweet_id,
            image_urls=None,
            tweet_context=None,
            invoker_instruction="",
        )
    except Exception:
        logging.exception("id=%s — pipeline failed", tweet_id)
        return {tone: "" for tone in OUTPUT_TONES}

    view = view_for_renderer(frozen, parent_post_text=statement)
    sources = _primary_source_urls(frozen) if include_sources else None
    replies: dict[str, str] = {}

    for tone in OUTPUT_TONES:
        factcheck_tone = _APP_TO_FACTCHECK_TONE.get(tone, tone)
        logging.info("id=%s tone=%s — rendering", tweet_id, tone)
        try:
            text = render(view, factcheck_tone)
            replies[tone] = (
                format_reply_with_sources(text, sources)
                if include_sources
                else (text or "").strip()
            )
        except Exception:
            logging.exception("id=%s tone=%s — render failed", tweet_id, tone)
            replies[tone] = (
                format_reply_with_sources("", sources)
                if include_sources
                else ""
            )

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
    include_sources: bool = True,
    db_path: Path | None = None,
) -> int:
    """Load posts, generate replies, write CSV and optionally study.db. Returns rows written."""
    posts = load_posts(input_path, limit=limit)
    if not posts:
        logging.warning("No posts to process in %s", input_path)
        return 0

    logging.info(
        "Processing %d post(s) from %s → %s%s",
        len(posts), input_path, output_path,
        f" + {db_path}" if db_path else "",
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    conn = None
    if db_path is not None:
        if not db_path.is_file():
            raise FileNotFoundError(
                f"study.db not found at {db_path} — run: python -m mockx.build_db"
            )
        conn = connect_writable(str(db_path))

    written = 0
    db_updates = 0
    try:
        with output_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=OUTPUT_FIELDNAMES,
                quoting=csv.QUOTE_MINIMAL,
            )
            write_output_header(writer, fh)

            for idx, post in enumerate(posts, start=1):
                logging.info("[%d/%d] id=%s", idx, len(posts), post.id)
                replies = generate_all_tones(
                    post.text, post.id, include_sources=include_sources,
                )
                write_output_row(writer, fh, post.id, replies)
                written += 1

                if conn is not None:
                    n = update_bot_replies(conn, post.id, replies)
                    db_updates += n
                    conn.commit()
                    logging.info("id=%s — updated %d intervention row(s) in study.db", post.id, n)
    finally:
        if conn is not None:
            conn.close()

    logging.info("Wrote %d row(s) to %s", written, output_path)
    if db_path is not None:
        logging.info("Updated %d intervention row(s) in %s", db_updates, db_path)
    return written


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate bot replies for CSV posts; write CSV and update study.db.",
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
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=f"study.db path to update (default: {DEFAULT_DB.relative_to(_REPO_ROOT)})",
    )
    parser.add_argument(
        "--no-db",
        action="store_true",
        help="Skip study.db updates (CSV only)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable INFO logging (pipeline stage progress)",
    )
    parser.add_argument(
        "--no-sources",
        action="store_true",
        help="Omit the Sources & reasoning block from reply cells",
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
    db_path = None if args.no_db else args.db.expanduser().resolve()

    if args.limit is not None and args.limit < 1:
        print("ERROR: --limit must be a positive integer", file=sys.stderr)
        return 1

    try:
        written = process_batch(
            input_path,
            output_path,
            limit=args.limit,
            include_sources=not args.no_sources,
            db_path=db_path,
        )
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except LookupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception:
        logging.exception("Batch failed")
        return 1

    if written == 0:
        return 1
    msg = f"Done — {written} row(s) written to {output_path}"
    if db_path is not None:
        msg += f"; study.db updated at {db_path}"
    print(msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
