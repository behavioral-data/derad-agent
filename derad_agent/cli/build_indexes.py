#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import pathlib
import sys

try:
    from derad_agent.indexing.index_builder import (
        build_global_index,
        build_tweet_index,
    )
    from derad_agent.llm.config import INDEX_ROOT, NOTES_TSV_ROOT
except ImportError:
    print("\nERROR: Could not import 'derad_agent'.")
    print("Please run this script as a module from the project root:")
    print("python -m derad_agent.cli.build_indexes ...")
    sys.exit(1)


def _load_tweet_list(path: pathlib.Path) -> list[str]:
    tweet_ids: set[str] = set()
    with path.open("r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            tweet_id = None
            try:
                parsed = json.loads(line)
                if isinstance(parsed, str):
                    tweet_id = parsed.strip()
                elif isinstance(parsed, dict):
                    for key in ("tweet_id", "tweetId", "id"):
                        value = parsed.get(key)
                        if isinstance(value, str) and value.strip():
                            tweet_id = value.strip()
                            break
            except json.JSONDecodeError:
                tweet_id = line
            if tweet_id:
                tweet_ids.add(tweet_id)
    return sorted(tweet_ids)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build FAISS indexes from Community Notes TSV data.")
    parser.add_argument(
        "--tsv-root",
        type=pathlib.Path,
        default=NOTES_TSV_ROOT,
        help="Directory containing notes TSV files, or a single TSV file.",
    )
    parser.add_argument(
        "--tsv-file",
        type=pathlib.Path,
        action="append",
        default=[],
        help="Explicit TSV file path (repeatable).",
    )
    parser.add_argument(
        "--index-root",
        type=pathlib.Path,
        default=INDEX_ROOT,
        help="Directory where FAISS indexes are stored.",
    )
    parser.add_argument(
        "--global-index",
        action="store_true",
        default=False,
        help="Build one global index over all notes (recommended).",
    )
    parser.add_argument(
        "--tweet-id",
        default=None,
        help="Build an index for one specific tweet ID.",
    )
    parser.add_argument(
        "--tweet-list",
        type=pathlib.Path,
        default=None,
        help="Path to JSONL/newline-separated tweet IDs for per-tweet indexes.",
    )
    parser.add_argument("--max-retries", type=int, default=5, help="Retry attempts for embedding API calls.")
    parser.add_argument("--initial-retry-delay", type=int, default=5, help="Initial retry backoff in seconds.")
    parser.add_argument(
        "--embedding-batch-size",
        type=int,
        default=2000,
        help="Texts per embedding batch.",
    )
    parser.add_argument(
        "--per-batch-sleep-seconds",
        type=float,
        default=0,
        help="Optional pause after each embedding batch.",
    )
    args = parser.parse_args()

    if args.tweet_id and args.tweet_list:
        print("Error: specify either --tweet-id or --tweet-list, not both.")
        return 1

    tsv_root: pathlib.Path = args.tsv_root
    tsv_files = [p.resolve() for p in args.tsv_file] if args.tsv_file else None
    index_root: pathlib.Path = args.index_root

    if not tsv_root.exists() and not tsv_files:
        print(f"Error: TSV input root '{tsv_root}' not found.")
        return 1

    index_root.mkdir(parents=True, exist_ok=True)

    common_kwargs = {
        "tsv_root": tsv_root,
        "tsv_files": tsv_files,
        "index_root": index_root,
        "max_retries": args.max_retries,
        "initial_retry_delay": args.initial_retry_delay,
        "embedding_batch_size": args.embedding_batch_size,
        "per_batch_sleep_seconds": args.per_batch_sleep_seconds,
    }

    if args.global_index or (not args.tweet_id and not args.tweet_list):
        print(f"Building global Community Notes index into {index_root}")
        build_global_index(**common_kwargs)
        return 0

    if args.tweet_id:
        print(f"Building tweet index for {args.tweet_id}")
        build_tweet_index(args.tweet_id, **common_kwargs)
        return 0

    if args.tweet_list and not args.tweet_list.exists():
        print(f"Error: Tweet list file '{args.tweet_list}' not found.")
        return 1

    tweet_ids = _load_tweet_list(args.tweet_list)
    if not tweet_ids:
        print(f"No tweet IDs discovered in '{args.tweet_list}'.")
        return 0

    print(f"Building {len(tweet_ids)} tweet-specific indexes")
    for tweet_id in tweet_ids:
        build_tweet_index(tweet_id, **common_kwargs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
