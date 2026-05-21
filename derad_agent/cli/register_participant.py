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
import random
import sys
from datetime import datetime, timezone

from derad_agent.app.participants import Participant, get_store
from derad_agent.llm.config import get_x_client

VALID_TONES = {"agreeable", "neutral", "satirical"}

logger = logging.getLogger(__name__)


def _lookup_author_id(username: str, tone_for_client: str = "neutral") -> str:
    """Resolve @username → X numeric user ID via the X API."""
    clean = username.lstrip("@")
    try:
        response = get_x_client(tone=tone_for_client).users.get_by_username(username=clean)
    except Exception as exc:
        logger.error("X API call failed looking up @%s: %s", clean, exc)
        raise SystemExit(1) from exc

    data = getattr(response, "data", None) or {}
    user_id = data.get("id") if isinstance(data, dict) else getattr(data, "id", None)
    if not user_id:
        print(f"ERROR: @{clean} not found on X (or API returned no data).", file=sys.stderr)
        raise SystemExit(1)
    return str(user_id)


def _pick_tone(requested: str) -> str:
    """Resolve 'random' to the least-used tone across existing registrations."""
    if requested != "random":
        return requested

    store = get_store()
    counts = {t: 0 for t in VALID_TONES}
    for p in store.list_all():
        if p.tone in counts:
            counts[p.tone] += 1

    min_count = min(counts.values())
    candidates = [t for t, n in counts.items() if n == min_count]
    return random.choice(candidates)


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
