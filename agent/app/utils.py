import logging
import re
from dataclasses import dataclass
from typing import Optional

import requests

from agent.llm.config import get_x_client

logger = logging.getLogger(__name__)


@dataclass
class TweetSnapshot:
    """Subset of a fetched tweet we care about for replies + event capture."""
    text: str
    author_id: Optional[str] = None
    author_username: Optional[str] = None
    like_count: Optional[int] = None
    retweet_count: Optional[int] = None
    reply_count: Optional[int] = None
    quote_count: Optional[int] = None


def fetch_tweet(tweet_id) -> Optional[TweetSnapshot]:
    """Fetch a tweet by id with author expansion. Returns None on failure.

    xdk's ``Tweet`` and ``Expansions`` are bare ``Any`` type aliases, so
    ``GetByIdResponse.data`` and ``.includes`` are *dicts* at runtime, not
    typed Pydantic models. Subscript access only — ``getattr`` on a dict
    always returns the default and would make every parent-fetch silently
    return None.
    """
    try:
        response = get_x_client().posts.get_by_id(
            id=str(tweet_id),
            tweet_fields=["text", "author_id", "public_metrics"],
            expansions=["author_id"],
            user_fields=["username"],
        )
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        logger.warning("fetch_tweet(%s) failed: HTTP %s", tweet_id, status)
        return None

    data = getattr(response, "data", None) or {}
    text = data.get("text") if isinstance(data, dict) else None
    if not text:
        logger.warning("fetch_tweet(%s) returned no data", tweet_id)
        return None

    author_id = data.get("author_id") if isinstance(data, dict) else None
    public_metrics = data.get("public_metrics") if isinstance(data, dict) else {}
    if not isinstance(public_metrics, dict):
        public_metrics = {}
    author_username: Optional[str] = None
    includes = getattr(response, "includes", None) or {}
    users = includes.get("users") if isinstance(includes, dict) else None
    if users and author_id:
        for user in users:
            if isinstance(user, dict) and str(user.get("id")) == str(author_id):
                author_username = user.get("username")
                break

    return TweetSnapshot(
        text=text,
        author_id=str(author_id) if author_id is not None else None,
        author_username=author_username,
        like_count=public_metrics.get("like_count"),
        retweet_count=public_metrics.get("retweet_count"),
        reply_count=public_metrics.get("reply_count"),
        quote_count=public_metrics.get("quote_count"),
    )


_APP_TO_FACTCHECK_TONE = {
    "agreeable": "agreeable",
    "neutral": "neutral",
    "satirical": "agonistic",  # design uses 'agonistic'; app's legacy label is 'satirical'
}


def generate_reply(statement, tone, exclude_tweet_id=None, max_sources=5):
    """Run the fact-check pipeline and render a reply in the requested tone.

    Returns the dict shape the X app expects. Community-notes-specific fields
    (`tweets`, `notes`, `all_cited_*`, `reasons_detail`) are empty by design —
    they have no analog in the web-evidence pipeline. The /info page falls
    back to showing just the rendered reply + cited URLs.
    """
    from agent.factcheck.freeze import view_for_renderer
    from agent.factcheck.pipeline import run_pipeline
    from agent.factcheck.render import render

    factcheck_tone = _APP_TO_FACTCHECK_TONE.get(tone)
    if factcheck_tone is None:
        logger.warning("generate_reply: unknown tone %r — defaulting to neutral", tone)
        factcheck_tone = "neutral"

    target_tweet_id = str(exclude_tweet_id) if exclude_tweet_id is not None else ""

    try:
        frozen = run_pipeline(statement, target_tweet_id=target_tweet_id)
    except Exception:
        logger.exception("Fact-check pipeline failed for statement head=%r", statement[:80])
        return {
            "text": "", "sources": None, "tweets": None, "notes": None,
            "queries": [], "all_cited_tweet_ids": [], "all_cited_note_ids": [],
            "reasons_detail": [],
        }

    try:
        text = render(view_for_renderer(frozen), factcheck_tone)
    except Exception:
        logger.exception("Render failed for invocation=%s", frozen.invocation_id)
        return {
            "text": "", "sources": None, "tweets": None, "notes": None,
            "queries": [], "all_cited_tweet_ids": [], "all_cited_note_ids": [],
            "reasons_detail": [],
        }

    sources = [
        s.url for s in frozen.presentation_payload.primary_sources_to_cite
    ][:max_sources] or None

    logger.info(
        "Fact-check produced verdict=%s for invocation=%s tone=%s",
        frozen.verdict_label, frozen.invocation_id, factcheck_tone,
    )

    return {
        "text": text,
        "sources": sources,
        "tweets": None,
        "notes": None,
        "queries": [statement],
        "all_cited_tweet_ids": [],
        "all_cited_note_ids": [],
        "reasons_detail": [],
    }


_TCO_URL_RE = re.compile(r'https?://\S+')
_X_TCO_LEN = 23
_X_TWEET_LIMIT = 280


def x_weighted_length(text: str) -> int:
    """Count characters the way X does: every URL is collapsed to 23 chars."""
    return len(_TCO_URL_RE.sub("x" * _X_TCO_LEN, text))


def post_reply(parent_id, reply_text) -> Optional[str]:
    """Post a reply. Returns the new tweet id on success, None on failure.

    Uses the xdk ``CreateRequest`` body shape — passing loose kwargs to
    ``posts.create`` raises because the real signature takes a single ``body``.
    """
    weighted = x_weighted_length(reply_text)
    if weighted > _X_TWEET_LIMIT:
        logger.warning(
            "post_reply refused: text %d weighted chars > %d (parent=%s)",
            weighted, _X_TWEET_LIMIT, parent_id,
        )
        return None

    from xdk.posts.models import CreateRequest, CreateRequestReply

    body = CreateRequest(
        text=reply_text,
        reply=CreateRequestReply(in_reply_to_tweet_id=str(parent_id)),
    )
    try:
        response = get_x_client().posts.create(body=body)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        body_text = (exc.response.text or "")[:500] if exc.response is not None else ""
        logger.warning(
            "post_reply failed (parent=%s): HTTP %s %s",
            parent_id, status, body_text,
        )
        return None

    data = getattr(response, "data", None)
    reply_id = getattr(data, "id", None) if data is not None else None
    if not reply_id:
        logger.warning("post_reply returned no id (parent=%s)", parent_id)
        return None
    reply_id = str(reply_id)
    logger.info("Created reply %s (parent=%s)", reply_id, parent_id)
    return reply_id
