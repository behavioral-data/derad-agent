import logging
from dataclasses import dataclass
from typing import Optional

import requests

from agent.llm.config import get_x_client
from agent.shared.text import X_TWEET_LIMIT, x_weighted_length

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
    # Image URLs attached to the tweet (photos only; videos/GIFs skipped).
    # X API populates url for photos and preview_image_url for video/animated_gif.
    image_urls: list = None  # type: ignore[assignment]
    # Fact-checking context fields (added in v3.x). All optional; missing
    # fields fall back to None when the X API doesn't return them.
    created_at: Optional[str] = None
    lang: Optional[str] = None
    possibly_sensitive: Optional[bool] = None
    expanded_urls: list = None  # type: ignore[assignment]  # list[{display_url, expanded_url, title}]
    referenced_tweets: list = None  # type: ignore[assignment]  # list[{type, id}]
    author_verified: Optional[bool] = None
    author_verified_type: Optional[str] = None
    author_description: Optional[str] = None
    author_created_at: Optional[str] = None
    author_followers_count: Optional[int] = None


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
            tweet_fields=[
                "text", "author_id", "public_metrics", "attachments",
                "created_at", "lang", "possibly_sensitive", "entities",
                "referenced_tweets",
            ],
            expansions=["author_id", "attachments.media_keys"],
            user_fields=[
                "username", "verified", "verified_type", "description",
                "created_at", "public_metrics",
            ],
            media_fields=["url", "preview_image_url", "type"],
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
    author_verified: Optional[bool] = None
    author_verified_type: Optional[str] = None
    author_description: Optional[str] = None
    author_created_at: Optional[str] = None
    author_followers_count: Optional[int] = None
    includes = getattr(response, "includes", None) or {}
    users = includes.get("users") if isinstance(includes, dict) else None
    if users and author_id:
        for user in users:
            if isinstance(user, dict) and str(user.get("id")) == str(author_id):
                author_username = user.get("username")
                author_verified = user.get("verified")
                author_verified_type = user.get("verified_type")
                author_description = user.get("description")
                author_created_at = user.get("created_at")
                user_metrics = user.get("public_metrics") or {}
                if isinstance(user_metrics, dict):
                    author_followers_count = user_metrics.get("followers_count")
                break

    image_urls: list[str] = []
    media = includes.get("media") if isinstance(includes, dict) else None
    if media:
        for m in media:
            if not isinstance(m, dict):
                continue
            # Use `url` for static photos; fall back to `preview_image_url` for
            # video / animated_gif. The VLM only reads stills either way.
            mu = m.get("url") or m.get("preview_image_url")
            if mu:
                image_urls.append(mu)

    # Expanded URLs from entities.urls — `t.co/xyz` resolves to the real link
    # plus its page title. Lets the fact-checker see where short links go.
    expanded_urls: list[dict] = []
    entities = data.get("entities") if isinstance(data, dict) else None
    if isinstance(entities, dict):
        for u in (entities.get("urls") or []):
            if not isinstance(u, dict):
                continue
            entry = {
                "display_url": u.get("display_url"),
                "expanded_url": u.get("expanded_url") or u.get("unwound_url"),
                "title": u.get("title"),
            }
            if entry["expanded_url"]:
                expanded_urls.append(entry)

    referenced_tweets: list[dict] = []
    for ref in (data.get("referenced_tweets") or []):
        if isinstance(ref, dict) and ref.get("type") and ref.get("id"):
            referenced_tweets.append({"type": ref["type"], "id": str(ref["id"])})

    return TweetSnapshot(
        text=text,
        author_id=str(author_id) if author_id is not None else None,
        author_username=author_username,
        like_count=public_metrics.get("like_count"),
        retweet_count=public_metrics.get("retweet_count"),
        reply_count=public_metrics.get("reply_count"),
        quote_count=public_metrics.get("quote_count"),
        image_urls=image_urls,
        created_at=data.get("created_at") if isinstance(data, dict) else None,
        lang=data.get("lang") if isinstance(data, dict) else None,
        possibly_sensitive=data.get("possibly_sensitive") if isinstance(data, dict) else None,
        expanded_urls=expanded_urls,
        referenced_tweets=referenced_tweets,
        author_verified=author_verified,
        author_verified_type=author_verified_type,
        author_description=author_description,
        author_created_at=author_created_at,
        author_followers_count=author_followers_count,
    )


_APP_TO_FACTCHECK_TONE = {
    "agreeable": "agreeable",
    "neutral": "neutral",
    "satirical": "agonistic",  # design uses 'agonistic'; app's legacy label is 'satirical'
}


def generate_reply(statement, tone, exclude_tweet_id=None, max_sources=5,
                   image_urls=None, tweet_context=None):
    """Run the fact-check pipeline and render a reply in the requested tone.

    `image_urls` triggers Stage 1.5 multimodal extraction.
    `tweet_context` is metadata from the parent tweet (posted_at, author
    handle/bio/verified/age, expanded t.co URLs, referenced-tweet relations,
    language, sensitive flag, public metrics). Reconcile uses it to spot
    parody/aggregator accounts and to date-stamp the claim.

    Returns `{text, sources, verdict_label, queries}`.
    """
    from agent.factcheck.freeze import view_for_renderer
    from agent.factcheck.pipeline import run_pipeline
    from agent.factcheck.render import render

    factcheck_tone = _APP_TO_FACTCHECK_TONE.get(tone)
    if factcheck_tone is None:
        logger.warning("generate_reply: unknown tone %r — defaulting to neutral", tone)
        factcheck_tone = "neutral"

    target_tweet_id = str(exclude_tweet_id) if exclude_tweet_id is not None else ""

    # Let pipeline + render exceptions propagate. The streamer's
    # process_mention wraps the whole flow in try/except and emits
    # `pipeline_error` — that's the outcome we want for telemetry, NOT a
    # silent `empty_reply` (which previously hid render refusals).
    frozen = run_pipeline(
        statement,
        target_tweet_id=target_tweet_id,
        image_urls=list(image_urls) if image_urls else None,
        tweet_context=tweet_context or None,
    )
    text = render(view_for_renderer(frozen), factcheck_tone)

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
        "verdict_label": frozen.verdict_label,
        "queries": [statement],
    }


def post_reply(parent_id, reply_text) -> Optional[str]:
    """Post a reply. Returns the new tweet id on success, None on failure.

    Uses the xdk ``CreateRequest`` body shape — passing loose kwargs to
    ``posts.create`` raises because the real signature takes a single ``body``.
    """
    weighted = x_weighted_length(reply_text)
    if weighted > X_TWEET_LIMIT:
        logger.warning(
            "post_reply refused: text %d weighted chars > %d (parent=%s)",
            weighted, X_TWEET_LIMIT, parent_id,
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
