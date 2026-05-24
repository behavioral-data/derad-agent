"""Send survey invitations by X Direct Message.

Usage:
    python -m agent.app.survey --participant-id 123 --form-url https://forms.gle/...
"""

from __future__ import annotations

import argparse
import logging
import os

from agent.llm.config import _require_env

logger = logging.getLogger(__name__)


def _build_dm_client():
    """Build an X client for sending DMs.

    Prefer an OAuth2 user access token when ``X_DM_USER_ACCESS_TOKEN`` is set,
    because the X DM docs show bearer-token user auth. Fall back to the single
    OAuth1 credential set used elsewhere in the app.
    """
    from xdk import Client

    dm_token = os.getenv("X_DM_USER_ACCESS_TOKEN")
    if dm_token:
        return Client(access_token=dm_token)

    from xdk.oauth1_auth import OAuth1

    oauth1 = OAuth1(
        api_key=_require_env("X_API_KEY"),
        api_secret=_require_env("X_API_SECRET"),
        callback=os.getenv("X_OAUTH_CALLBACK", "oob"),
        access_token=_require_env("X_ACCESS_TOKEN"),
        access_token_secret=_require_env("X_ACCESS_TOKEN_SECRET"),
    )
    return Client(auth=oauth1)


def build_survey_message(form_url: str) -> str:
    # TODO: get at most 10 posts from last day replying to given author_id from Posts table
    # TODO: create message with links to all posts and study_id for each post
    # TODO: link to form URL at bottom of message
    # TODO: schedule chron job to generate and send message via DMs at the beginning of each day
    return f"Thanks for interacting with our bot. Please complete this brief survey: {form_url}"


def send_survey_dm(participant_id: str, form_url: str) -> str | None:
    """Send a survey link by DM and return the X DM event ID, if available."""
    from xdk.direct_messages.models import CreateByParticipantIdRequest

    text = build_survey_message(form_url)
    body = CreateByParticipantIdRequest(text=text)
    response = _build_dm_client().direct_messages.create_by_participant_id(
        participant_id=str(participant_id),
        body=body,
    )
    data = response.get("data") if isinstance(response, dict) else getattr(response, "data", None)
    if isinstance(data, dict):
        return data.get("dm_event_id")
    return getattr(data, "dm_event_id", None)


def main() -> None:
    parser = argparse.ArgumentParser(description="Send a Google Form survey link by X DM.")
    parser.add_argument("--participant-id", required=True, help="X numeric user ID to receive the DM.")
    parser.add_argument("--form-url", default=os.getenv("SURVEY_FORM_URL"), help="Google Form URL.")
    args = parser.parse_args()

    if not args.form_url:
        parser.error("--form-url is required unless SURVEY_FORM_URL is set")

    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(message)s")
    dm_event_id = send_survey_dm(args.participant_id, args.form_url)
    logger.info("Sent survey DM to %s; dm_event_id=%s", args.participant_id, dm_event_id)


if __name__ == "__main__":
    main()
