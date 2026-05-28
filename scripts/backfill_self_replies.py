#!/usr/bin/env python3
"""One-off backfill: remove bot self-replies from already-collected data.

Two pollution vectors were introduced with the "split link reply" feature
(the bot posts the dossier link as a self-reply to its own fact-check reply):

  1. BotReplyReplies — the bot's link self-reply was logged as a bystander
     reply (no author filter). This script DELETES those rows: any reply
     authored by the bot (author_id == BOT_USER_ID) or whose tweet id is a
     known link_reply_id.
  2. EngagementSnapshots — reply_count from X includes the bot's link reply.
     For HISTORICAL replies (those with no recorded link_reply_id — the older
     image posted the link reply but never persisted its id), we can't look the
     id up, so we apply a heuristic: every fact-check reply posted exactly one
     link self-reply, so `adjusted_reply_count = max(0, reply_count - 1)`.
     Forward replies (link_reply_id present) are left alone — poll_engagement
     already sets their adjusted count precisely from link_reply_id.

     Caveat (accepted): the heuristic over-subtracts in the rare case a reply
     genuinely had no link self-reply (e.g. the post failed). With the observed
     distribution (no reply has >1 reply) the only effect is turning a few
     count-of-1 snapshots to 0. Computed from raw reply_count each run, so
     re-running is idempotent.

Forward collection is already fixed in collect_replies.py / poll_engagement.py;
this only cleans historical rows.

Usage:
    python -m scripts.backfill_self_replies            # dry run (default) — reports, no writes
    python -m scripts.backfill_self_replies --apply    # actually delete/update

Requires the Tables backend (DERAD_TABLES_ENDPOINT) and DefaultAzureCredential.
"""
from __future__ import annotations

import argparse
import os
import sys

from agent.app.events import get_store


def _reply_id_sets(store):
    """Partition known bot fact-check replies:
      heuristic_ids — replies with NO recorded link_reply_id (historical; the
                      heuristic -1 applies to their snapshots).
      link_ids      — the set of recorded link_reply_ids (used to drop the
                      bot's own link reply from BotReplyReplies)."""
    heuristic_ids: set[str] = set()
    link_ids: set[str] = set()
    for reply_id, _tone, _posted, _mention, _parent, link_reply_id in store.iter_reply_ids():
        if not reply_id:
            continue
        if link_reply_id:
            link_ids.add(link_reply_id)
        else:
            heuristic_ids.add(reply_id)
    return heuristic_ids, link_ids


def _clean_reply_replies(store, bot_user_id, link_ids, apply):
    tbl = store._reply_replies
    to_delete = []
    for e in tbl.list_entities(select=["PartitionKey", "RowKey", "author_id", "reply_tweet_id"]):
        author_id = str(e.get("author_id") or "")
        tweet_id = str(e.get("reply_tweet_id") or "")
        if (bot_user_id and author_id == bot_user_id) or (tweet_id in link_ids):
            to_delete.append((e["PartitionKey"], e["RowKey"], tweet_id))
    print(f"BotReplyReplies: {len(to_delete)} bot self-reply row(s) to delete")
    for pk, rk, tweet_id in to_delete[:10]:
        print(f"  - {tweet_id}  ({pk}/{rk})")
    if len(to_delete) > 10:
        print(f"  … and {len(to_delete) - 10} more")
    if apply:
        for pk, rk, _ in to_delete:
            tbl.delete_entity(partition_key=pk, row_key=rk)
        print(f"  deleted {len(to_delete)} row(s).")
    return len(to_delete)


def _recompute_snapshots(store, heuristic_ids, apply):
    """Heuristic backfill: for snapshots of HISTORICAL bot replies (no recorded
    link_reply_id), set adjusted = max(0, reply_count - 1) — the one link
    self-reply X counted. Forward replies (link_reply_id present, not in
    heuristic_ids) are left to poll_engagement's precise logic. Idempotent:
    always computed from raw reply_count."""
    from azure.data.tables import UpdateMode

    tbl = store._engagements
    updates = []
    skipped_forward = 0
    for e in tbl.list_entities(
        select=["PartitionKey", "RowKey", "reply_id", "reply_count", "adjusted_reply_count"]
    ):
        reply_id = str(e.get("reply_id") or "")
        reply_count = int(e.get("reply_count") or 0)
        if reply_id not in heuristic_ids:
            skipped_forward += 1
            continue  # forward reply (has link_reply_id) — poll_engagement owns it
        desired = max(0, reply_count - 1)
        if e.get("adjusted_reply_count") != desired:
            updates.append((e["PartitionKey"], e["RowKey"], desired))
    print(f"EngagementSnapshots: {len(updates)} historical snapshot(s) to set "
          f"adjusted_reply_count (heuristic -1); {skipped_forward} left to poll_engagement")
    if apply:
        for pk, rk, desired in updates:
            tbl.update_entity(
                {"PartitionKey": pk, "RowKey": rk, "adjusted_reply_count": desired},
                mode=UpdateMode.MERGE,
            )
        print(f"  updated {len(updates)} snapshot(s).")
    return len(updates)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="actually delete/update rows (default is a dry run)")
    args = ap.parse_args()

    store = get_store()
    if not hasattr(store, "_reply_replies"):
        print("Backfill requires the Tables backend (set DERAD_TABLES_ENDPOINT).", file=sys.stderr)
        return 1

    bot_user_id = os.getenv("BOT_USER_ID") or None
    if not bot_user_id:
        print("WARNING: BOT_USER_ID unset — relying on link_reply_id match only "
              "(bot replies that aren't the dossier link won't be caught).", file=sys.stderr)

    mode = "APPLY" if args.apply else "DRY RUN (no writes)"
    print(f"=== backfill self-replies — {mode} ===")
    heuristic_ids, link_ids = _reply_id_sets(store)
    print(f"historical replies (heuristic -1): {len(heuristic_ids)}; "
          f"recorded link self-replies: {len(link_ids)}")

    deleted = _clean_reply_replies(store, bot_user_id, link_ids, args.apply)
    updated = _recompute_snapshots(store, heuristic_ids, args.apply)

    if not args.apply:
        print(f"\nDry run complete: would delete {deleted} reply row(s), "
              f"update {updated} snapshot(s). Re-run with --apply to commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
