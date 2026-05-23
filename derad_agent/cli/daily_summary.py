"""Generate a per-participant summary of yesterday's bot replies for DM composition.

Reads MentionEvents from the current month's partition, filters to yesterday's
replies that have a study_code, and prints a table grouped by participant.
Researchers use this to compose the daily DM survey links.

Usage:
    derad-daily-summary              # yesterday's data
    derad-daily-summary --date 2026-05-15  # specific date
"""

import argparse
import logging
import os
from datetime import date, datetime, timedelta, timezone

logger = logging.getLogger(__name__)


def _get_events_for_date(target_date: date) -> list[dict]:
    """Query MentionEvents for a specific UTC date."""
    backend = os.getenv("DERAD_EVENTS_BACKEND", "memory").lower()
    if backend != "tables":
        logger.warning("DERAD_EVENTS_BACKEND is not 'tables' — no data to summarize")
        return []

    from azure.data.tables import TableServiceClient
    from azure.identity import DefaultAzureCredential

    endpoint = os.environ["DERAD_TABLES_ENDPOINT"]
    svc = TableServiceClient(endpoint=endpoint, credential=DefaultAzureCredential())
    tbl = svc.get_table_client("MentionEvents")

    partition = target_date.strftime("%Y-%m")
    date_str = target_date.isoformat()
    next_date_str = (target_date + timedelta(days=1)).isoformat()

    filter_q = (
        f"PartitionKey eq '{partition}' "
        f"and received_at_utc ge datetime'{date_str}T00:00:00Z' "
        f"and received_at_utc lt datetime'{next_date_str}T00:00:00Z'"
    )

    rows = []
    for ent in tbl.query_entities(filter_q):
        study_code = ent.get("study_code")
        if not study_code:
            continue
        rows.append({
            "mention_id": ent.get("mention_id"),
            "reply_id": ent.get("reply_id"),
            "study_code": study_code,
            "participant_id": ent.get("participant_id") or ent.get("author_id"),
            "author_username": ent.get("author_username"),
            "tone": ent.get("tone"),
            "study_day": ent.get("study_day"),
            "parent_id": ent.get("parent_id"),
            "outcome": ent.get("outcome"),
        })
    return rows


_BOT_HANDLE = os.getenv("BOT_HANDLE", "eddiexbot")


def main() -> None:
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Daily DM survey summary for researchers")
    parser.add_argument(
        "--date",
        default=None,
        help="Target UTC date as YYYY-MM-DD (default: yesterday)",
    )
    args = parser.parse_args()

    if args.date:
        target_date = date.fromisoformat(args.date)
    else:
        target_date = (datetime.now(timezone.utc) - timedelta(days=1)).date()

    print(f"\n=== Daily DM Summary for {target_date} ===\n")

    events = _get_events_for_date(target_date)
    if not events:
        print("No study replies found for this date.")
        return

    # Group by participant
    by_participant: dict[str, list[dict]] = {}
    for ev in events:
        pid = ev["participant_id"] or "unknown"
        by_participant.setdefault(pid, []).append(ev)

    for participant_id, replies in sorted(by_participant.items()):
        username = replies[0].get("author_username") or participant_id
        tone = replies[0].get("tone")
        study_day = replies[0].get("study_day")
        print(f"@{username} (id={participant_id}, bot=@{_BOT_HANDLE}, tone={tone}, day={study_day})")
        print("-" * 60)
        for r in sorted(replies, key=lambda x: x.get("study_code", "")):
            reply_id = r.get("reply_id", "")
            parent_id = r.get("parent_id", "")
            code = r.get("study_code", "????")
            outcome = r.get("outcome", "")
            post_url = (
                f"https://x.com/{_BOT_HANDLE}/status/{reply_id}" if reply_id else "(no reply posted)"
            )
            original_url = (
                f"https://x.com/i/web/status/{parent_id}" if parent_id else ""
            )
            print(f"  [{code}] {post_url}")
            if original_url:
                print(f"          (original: {original_url})")
            if outcome and outcome != "replied":
                print(f"          !! outcome={outcome}")
        print()

    print(f"Total: {len(events)} replies across {len(by_participant)} participant(s)")
