"""List all registered study participants.

Usage:
    derad-list-participants
    derad-list-participants --tone neutral          # filter by tone
    derad-list-participants --format csv           # machine-readable output
"""

import argparse
import csv
import sys
from datetime import datetime, timezone

from agent.app.participants import get_store


def _study_day(enrolled_at, now):
    return (now.date() - enrolled_at.date()).days + 1


def main() -> None:
    parser = argparse.ArgumentParser(description="List registered study participants")
    parser.add_argument("--tone", default=None, choices=["agreeable", "neutral", "agonistic"],
                        help="Filter by assigned tone.")
    parser.add_argument("--format", default="table", choices=["table", "csv"],
                        help="Output format (default: table).")
    args = parser.parse_args()

    participants = get_store().list_all()
    if args.tone:
        participants = [p for p in participants if p.tone == args.tone]

    participants.sort(key=lambda p: (p.tone, p.author_username))
    now = datetime.now(timezone.utc)

    if not participants:
        print("No participants registered." if not args.tone
              else f"No participants with tone={args.tone}.")
        return

    if args.format == "csv":
        writer = csv.writer(sys.stdout)
        writer.writerow(["author_id", "username", "tone", "enrolled", "study_day", "notes"])
        for p in participants:
            writer.writerow([
                p.author_id, p.author_username, p.tone,
                p.enrolled_at_utc.date().isoformat(),
                _study_day(p.enrolled_at_utc, now),
                p.notes,
            ])
        return

    # Table format
    col_w = [16, 22, 12, 12, 9]
    header = ["AUTHOR_ID", "USERNAME", "TONE", "ENROLLED", "STUDY_DAY"]
    sep = "  ".join("-" * w for w in col_w)
    row_fmt = "  ".join(f"{{:<{w}}}" for w in col_w)

    counts = {"agreeable": 0, "neutral": 0, "agonistic": 0}
    for p in participants:
        counts[p.tone] = counts.get(p.tone, 0) + 1

    print(f"\n{len(participants)} participant(s)  "
          f"[agreeable={counts['agreeable']}  neutral={counts['neutral']}  agonistic={counts['agonistic']}]\n")
    print(row_fmt.format(*header))
    print(sep)
    for p in participants:
        print(row_fmt.format(
            p.author_id,
            f"@{p.author_username}",
            p.tone,
            p.enrolled_at_utc.date().isoformat(),
            str(_study_day(p.enrolled_at_utc, now)),
        ))
    print()
