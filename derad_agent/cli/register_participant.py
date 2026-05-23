"""Register a study participant in the Participants table.

Usage (by handle — recommended):
    derad-register-participant --username participanthandle --tone neutral

Usage (by known numeric ID):
    derad-register-participant --author-id 12345678 --username participanthandle --tone neutral

When --author-id is omitted the X API is called to look it up from --username.
--tone random picks the least-used tone across current registrations for balance.
"""

import argparse
import logging
import sys
from datetime import datetime, timezone

from derad_agent.app.participants import (
    VALID_TONES,
    Participant,
    ParticipantLookupError,
    get_store,
    lookup_author_id,
    pick_balanced_tone,
)
# Re-exported so existing test monkeypatches on this module keep working.
from derad_agent.llm.config import get_x_client  # noqa: F401

logger = logging.getLogger(__name__)


def _lookup_author_id(username: str) -> str:
    """Resolve @username → X numeric user ID via the X API."""
    # Look up get_x_client from this module's namespace at call time so tests
    # that monkeypatch it on this module keep intercepting the call.
    client = get_x_client()
    try:
        return lookup_author_id(username, client=client)
    except ParticipantLookupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def _pick_tone(requested: str) -> str:
    """Resolve 'random' to the least-used tone across existing registrations."""
    if requested != "random":
        return requested
    return pick_balanced_tone()


def main() -> None:
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(
        description="Register a study participant (looks up X user ID from handle if --author-id is omitted)"
    )
    parser.add_argument(
        "--username", required=True,
        help="X handle without @ (e.g. janesmith). Used for ID lookup and DM composition.",
    )
    parser.add_argument(
        "--author-id", default=None,
        help="X numeric user ID. Looked up automatically from --username when omitted.",
    )
    parser.add_argument(
        "--tone",
        required=True,
        choices=sorted(VALID_TONES) + ["random"],
        help="Assigned bot tone, or 'random' to pick the least-used tone for balance.",
    )
    parser.add_argument(
        "--enrolled", default=None,
        help="Enrollment date as YYYY-MM-DD (default: today UTC).",
    )
    parser.add_argument("--notes", default="", help="Optional researcher notes.")
    args = parser.parse_args()

    clean_username = args.username.lstrip("@")

    # Resolve author ID
    if args.author_id:
        author_id = args.author_id.strip()
    else:
        print(f"Looking up X user ID for @{clean_username} …")
        author_id = _lookup_author_id(clean_username)
        print(f"Found: @{clean_username} → id={author_id}")

    # Resolve tone
    tone = _pick_tone(args.tone)
    if args.tone == "random":
        print(f"Assigned tone: {tone} (balanced)")

    # Resolve enrollment date
    if args.enrolled:
        try:
            enrolled_at = datetime.strptime(args.enrolled, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            print(f"ERROR: --enrolled must be YYYY-MM-DD, got: {args.enrolled}", file=sys.stderr)
            sys.exit(1)
    else:
        enrolled_at = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    participant = Participant(
        author_id=author_id,
        author_username=clean_username,
        tone=tone,
        enrolled_at_utc=enrolled_at,
        notes=args.notes,
    )

    get_store().register(participant)

    print(
        f"Registered: @{participant.author_username} "
        f"(id={participant.author_id}, tone={participant.tone}, "
        f"enrolled={enrolled_at.date()})"
    )
    print("Restart the app service to pick up the new participant in the allow-list.")
