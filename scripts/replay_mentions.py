#!/usr/bin/env python3
"""Replay mentions that failed with a pipeline error, re-running them through
the (now-fixed) pipeline so the participant gets the reply they should have.

Built for the 2026-05-28 outage where source_lists.json was missing from the
wheel and ~10 mentions died at import with FileNotFoundError. By default it
auto-selects exactly those: events whose latest outcome is `pipeline_error`
with a "No such file or directory" error_detail AND that never got a
successful reply. You can also pass explicit mention ids.

Safety:
  * DEFAULT IS A DRY RUN — it lists what it would replay and posts nothing.
    Add --post to actually run the pipeline and post replies.
  * Idempotent: a mention that already has a `replied`/`replied_no_link`
    event (a reply_id) is skipped, so re-running never double-posts.
  * Each replay goes through the CURRENT process_mention, so the threading
    guard, source-list classification, and self-reply handling all apply.

Run (needs the prod env: X creds, AZURE_CLAUDE_*, DERAD_TABLES_ENDPOINT,
DERAD_EVENTS_BACKEND=tables, SERVER_NAME=<prod host>, BOT_USER_ID, BOT_HANDLE):
    python -m scripts.replay_mentions                 # dry run (default)
    python -m scripts.replay_mentions --post          # actually post
    python -m scripts.replay_mentions --post 2060... 2060...   # explicit ids
"""
from __future__ import annotations

import argparse
import logging
import sys

logger = logging.getLogger("replay")

_FILE_BUG_MARKER = "No such file or directory"


def _select_failed(store) -> list[dict]:
    """All MentionEvents, newest-first per mention, classifying recoverability."""
    rows = list(store._events.list_entities(select=[
        "RowKey", "mention_id", "parent_id", "author_id", "tone", "outcome",
        "error_detail", "reply_id", "received_at_utc",
    ]))
    by_mention: dict[str, list[dict]] = {}
    for r in rows:
        mid = r.get("mention_id")
        if mid:
            by_mention.setdefault(mid, []).append(r)
    selected = []
    for mid, evs in by_mention.items():
        evs.sort(key=lambda e: e.get("RowKey", ""))
        # Already succeeded at some point → never replay.
        if any(e.get("reply_id") for e in evs):
            continue
        latest = evs[-1]
        if latest.get("outcome") != "pipeline_error":
            continue
        is_file_bug = _FILE_BUG_MARKER in (latest.get("error_detail") or "")
        selected.append({
            "mention_id": mid,
            "parent_id": latest.get("parent_id"),
            "author_id": latest.get("author_id"),
            "tone": latest.get("tone"),
            "received_at_utc": latest.get("received_at_utc"),
            "is_file_bug": is_file_bug,
            "error_detail": (latest.get("error_detail") or "")[:80],
        })
    return selected


def main() -> int:
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mention_ids", nargs="*", help="explicit mention ids (default: auto-select JSON-bug failures)")
    ap.add_argument("--post", action="store_true", help="actually run the pipeline and POST replies (default: dry run)")
    ap.add_argument("--all-errors", action="store_true",
                    help="include pipeline_error failures from other causes (transient network), not just the file bug")
    args = ap.parse_args()

    # Import here so --help works without the prod env wired up.
    import os as _os
    from agent.app import events as events_module
    from agent.app.app import process_mention, _resolve_tone
    from agent.app.utils import fetch_tweet
    from agent.llm.config import get_x_client

    def _already_replied_on_x(mention_ids: set[str]) -> set[str]:
        """Ground-truth idempotency: which of these mentions @eddiexbot already
        has a reply to on X. The events table can't be trusted here (its writes
        are what failed), so we ask X directly. The bot's reply to a mention M
        carries referenced_tweets replied_to == M, so we scan the bot's recent
        posts and intersect their replied_to targets with our set."""
        handle = (_os.getenv("BOT_HANDLE") or "eddiexbot").lstrip("@")
        replied: set[str] = set()
        try:
            pages = get_x_client().posts.search_recent(
                query=f"from:{handle}",
                tweet_fields=["referenced_tweets"],
                max_results=100,
            )
            for i, page in enumerate(pages):
                if i >= 5:
                    break
                for tw in (getattr(page, "data", None) or []):
                    d = tw if isinstance(tw, dict) else getattr(tw, "__dict__", {})
                    for r in (d.get("referenced_tweets") or []):
                        if r.get("type") == "replied_to" and r.get("id") in mention_ids:
                            replied.add(r.get("id"))
        except Exception:
            logger.exception("X idempotency check failed — aborting to avoid double-posting")
            raise
        return replied

    store = events_module.get_store()
    if not hasattr(store, "_events"):
        print("Replay requires the Tables backend (DERAD_EVENTS_BACKEND=tables, DERAD_TABLES_ENDPOINT set).", file=sys.stderr)
        return 1

    candidates = _select_failed(store)
    if args.mention_ids:
        wanted = set(args.mention_ids)
        candidates = [c for c in candidates if c["mention_id"] in wanted]
    elif not args.all_errors:
        candidates = [c for c in candidates if c["is_file_bug"]]

    if not candidates:
        print("Nothing to replay (no matching unrecovered pipeline_error mentions).")
        return 0

    # Ground-truth idempotency: drop any mention @eddiexbot has ALREADY replied
    # to on X (e.g. the few posted before the event-write bug was caught). The
    # events table can't tell us this — its writes are what failed.
    already = _already_replied_on_x({c["mention_id"] for c in candidates})
    if already:
        print(f"Already replied on X (skipping {len(already)}): {sorted(already)}")
    candidates = [c for c in candidates if c["mention_id"] not in already]
    if not candidates:
        print("All candidate mentions already have a reply on X — nothing to post.")
        return 0

    print(f"=== {'POST' if args.post else 'DRY RUN (no posting)'} — {len(candidates)} mention(s) to replay ===")
    for c in candidates:
        print(f"  mention={c['mention_id']} parent={c['parent_id']} author={c['author_id']} "
              f"tone={c['tone']} file_bug={c['is_file_bug']}")

    if not args.post:
        print("\nDry run only. Re-run with --post to run the pipeline and post replies.")
        return 0

    replayed = 0
    for c in candidates:
        mid = c["mention_id"]
        snap = fetch_tweet(mid)
        if snap is None or not snap.text:
            logger.warning("mention %s unreachable on X (deleted?) — skipping", mid)
            continue
        tone = c["tone"] or _resolve_tone(c["author_id"] or "")
        tweet = {
            "id_str": mid,
            "in_reply_to_status_id_str": c["parent_id"],
            "text": snap.text,
            "user": {"id_str": snap.author_id or c["author_id"], "username": snap.author_username},
        }
        received_at = c["received_at_utc"] or events_module.utcnow()
        logger.info("Replaying mention %s (tone=%s) …", mid, tone)
        try:
            process_mention(tone, tweet, received_at)
            replayed += 1
        except Exception:
            logger.exception("Replay failed for mention %s", mid)

    print(f"\nReplayed {replayed}/{len(candidates)} mention(s). Check the events table for the new outcomes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
