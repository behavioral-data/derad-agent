"""Register a study participant in the Participants table.

Usage:
    derad-register-participant \\
        --author-id 12345678 \\
        --username participanthandle \\
        --tone agreeable \\
        [--enrolled YYYY-MM-DD] \\
        [--notes "Optional researcher notes"]

The participant's X numeric user ID is used as the lookup key for the
allow-list guard in app.py. Registering someone with an existing author-id
updates their record (upsert semantics).
"""

import argparse
import logging
import sys
from datetime import datetime, timezone

from derad_agent.app.participants import Participant, get_store

VALID_TONES = {"agreeable", "neutral", "satirical"}

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Register a study participant")
    parser.add_argument("--author-id", required=True, help="X numeric user ID (e.g. 12345678)")
    parser.add_argument("--username", required=True, help="X handle without @ (e.g. janesmith)")
    parser.add_argument(
        "--tone",
        required=True,
        choices=sorted(VALID_TONES),
        help="Assigned bot tone",
    )
    parser.add_argument(
        "--enrolled",
        default=None,
        help="Enrollment date as YYYY-MM-DD (default: today UTC)",
    )
    parser.add_argument("--notes", default="", help="Optional researcher notes")
    args = parser.parse_args()

    if args.enrolled:
        try:
            enrolled_at = datetime.strptime(args.enrolled, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            print(f"ERROR: --enrolled must be YYYY-MM-DD, got: {args.enrolled}", file=sys.stderr)
            sys.exit(1)
    else:
        enrolled_at = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    participant = Participant(
        author_id=args.author_id.strip(),
        author_username=args.username.lstrip("@"),
        tone=args.tone,
        enrolled_at_utc=enrolled_at,
        notes=args.notes,
    )

    store = get_store()
    store.register(participant)

    print(
        f"Registered: @{participant.author_username} "
        f"(id={participant.author_id}, tone={participant.tone}, "
        f"enrolled={enrolled_at.date()})"
    )
    print("Restart the app service to pick up the new participant in the allow-list.")
