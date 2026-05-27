"""Bulk-register study participants from a CSV file.

CSV format (header row required):
    username,tone,enrolled,notes
    janesmith,neutral,2026-05-20,cohort A
    bobdoe,random,,                        ← tone=random auto-assigns; enrolled defaults to today

Columns:
  username   Required. X handle with or without @.
  tone       Required. agreeable | neutral | satirical | random.
  enrolled   Optional. YYYY-MM-DD (default: today UTC).
  notes      Optional. Free text.

The script looks up each participant's X numeric user ID via the API before
registering. Already-registered IDs are upserted (updated in place).

Usage:
    derad-bulk-register participants.csv
    derad-bulk-register participants.csv --dry-run   # preview without writing
"""

import argparse
import csv
import logging
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

from agent.app.participants import Participant, get_store
from agent.llm.config import get_x_client

logger = logging.getLogger(__name__)

VALID_TONES = {"agreeable", "neutral", "satirical"}


def _pick_balanced_tone(counts: dict[str, int]) -> str:
    min_n = min(counts.values())
    candidates = [t for t, n in counts.items() if n == min_n]
    return random.choice(candidates)


def main() -> None:
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Bulk-register participants from a CSV file")
    parser.add_argument("csv_file", help="Path to the CSV file.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be registered without writing anything.")
    parser.add_argument("--tone", default=None, choices=sorted(VALID_TONES) + ["random"],
                        help="Override tone for all rows (ignores CSV tone column).")
    args = parser.parse_args()

    csv_path = Path(args.csv_file)
    if not csv_path.exists():
        print(f"ERROR: file not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    store = get_store()
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    tone_counts = {"agreeable": 0, "neutral": 0, "satirical": 0}
    for p in store.list_all():
        if p.tone in tone_counts:
            tone_counts[p.tone] += 1

    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)

    if not rows:
        print("CSV is empty — nothing to do.")
        return

    missing_cols = [c for c in ("username",) if c not in (reader.fieldnames or [])]
    if missing_cols:
        print(f"ERROR: CSV is missing required column(s): {missing_cols}", file=sys.stderr)
        sys.exit(1)

    print(f"{'DRY RUN — ' if args.dry_run else ''}Processing {len(rows)} row(s) from {csv_path.name}\n")

    ok_count = error_count = 0

    for i, row in enumerate(rows, 1):
        username = row.get("username", "").strip().lstrip("@")
        if not username:
            logger.warning("Row %d: empty username — skipped", i)
            error_count += 1
            continue

        raw_tone = args.tone or row.get("tone", "").strip().lower() or "random"
        if raw_tone == "random":
            tone = _pick_balanced_tone(tone_counts)
        elif raw_tone in VALID_TONES:
            tone = raw_tone
        else:
            logger.warning("Row %d: invalid tone %r — skipped", i, raw_tone)
            error_count += 1
            continue

        enrolled_str = row.get("enrolled", "").strip()
        if enrolled_str:
            try:
                enrolled_at = datetime.strptime(enrolled_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                logger.warning("Row %d: invalid enrolled date %r — skipped", i, enrolled_str)
                error_count += 1
                continue
        else:
            enrolled_at = today

        notes = row.get("notes", "").strip()

        # Look up X numeric user ID
        print(f"  [{i}/{len(rows)}] @{username} …", end=" ", flush=True)
        try:
            response = get_x_client().users.get_by_username(username=username)
            data = getattr(response, "data", None) or {}
            author_id = (data.get("id") if isinstance(data, dict)
                         else getattr(data, "id", None))
            if not author_id:
                print("NOT FOUND on X — skipped")
                error_count += 1
                continue
        except Exception as exc:
            print(f"API error ({exc}) — skipped")
            error_count += 1
            continue

        participant = Participant(
            author_id=str(author_id),
            author_username=username,
            tone=tone,
            enrolled_at_utc=enrolled_at,
            notes=notes,
        )

        if args.dry_run:
            print(f"would register id={author_id} tone={tone} enrolled={enrolled_at.date()}")
        else:
            store.register(participant)
            tone_counts[tone] = tone_counts.get(tone, 0) + 1
            print(f"registered id={author_id} tone={tone} enrolled={enrolled_at.date()}")

        ok_count += 1

    print(f"\n{'(dry run) ' if args.dry_run else ''}Done: {ok_count} registered, {error_count} skipped.")
    if not args.dry_run and ok_count:
        print("Restart the app service to pick up new participants in the allow-list.")
        counts = {t: 0 for t in VALID_TONES}
        for p in store.list_all():
            if p.tone in counts:
                counts[p.tone] += 1
        print(f"Tone balance: agreeable={counts['agreeable']}  neutral={counts['neutral']}  satirical={counts['satirical']}")
