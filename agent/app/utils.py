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
    "agonistic": "agonistic",
    "satirical": "agonistic",  # legacy alias — any pre-rename participant
                                # records still resolve correctly.
}


def _info_payload_from_frozen(frozen) -> dict:
    """Project the FrozenVerdict to the JSON-serializable payload the
    `/info` page renders. All sources + reasoning + per-action structured
    content live here — none of it appears in the tweet body itself."""
    pp = frozen.presentation_payload
    return {
        "action": frozen.action,
        "action_outcome": frozen.action_outcome,
        "action_source": frozen.action_source,
        "pivoted_from": frozen.pivoted_from,
        "invoker_instruction_text": frozen.invoker_instruction_text,
        "headline_finding": pp.headline_finding,
        "tone_neutral_justification": frozen.tone_neutral_justification,
        "counter_fact": pp.counter_fact,
        "context_note": pp.context_note,
        "load_bearing_evidence_snippet": pp.load_bearing_evidence_snippet,
        "pivot_disclosure": pp.pivot_disclosure,
        "counterpoints": [
            {
                "summary": cp.summary,
                "weight": cp.weight,
                "citing_sources": [{"url": s.url, "tier": s.tier} for s in cp.citing_sources],
            }
            for cp in pp.counterpoints
        ],
        "perspectives": [
            {
                "label": p.label,
                "summary": p.summary,
                "citing_sources": [{"url": s.url, "tier": s.tier} for s in p.citing_sources],
            }
            for p in pp.perspectives
        ],
        "primary_sources": [
            {"url": s.url, "display_name": s.display_name}
            for s in pp.primary_sources_to_cite
        ],
        "source_quality_table": [
            {"url": s.url, "tier": s.tier, "tier_source": s.tier_source, "rationale": s.rationale}
            for s in frozen.source_quality_table
        ],
        "verdict_label": frozen.verdict_label,
        "invocation_id": frozen.invocation_id,
    }


def generate_reply(statement, tone, exclude_tweet_id=None, max_sources=5,
                   image_urls=None, tweet_context=None, invoker_instruction=""):
    """Run the fact-check pipeline and render a reply in the requested tone.

    The rendered text contains NO URLs — sources + reasoning live on the
    /info page reached via the short link appended downstream. Returns
    `{text, sources, info_payload, verdict_label, action, action_outcome, queries}`.

    `info_payload` carries everything the /info page renders: action +
    outcome + counterpoints / perspectives / context_note + every cited
    source + the source-quality table.
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
    # `pipeline_error` — that's the outcome we want for telemetry.
    frozen = run_pipeline(
        statement,
        target_tweet_id=target_tweet_id,
        image_urls=list(image_urls) if image_urls else None,
        tweet_context=tweet_context or None,
        invoker_instruction=invoker_instruction or "",
    )
    text = render(view_for_renderer(frozen), factcheck_tone)

    sources = [
        s.url for s in frozen.presentation_payload.primary_sources_to_cite
    ][:max_sources] or None

    logger.info(
        "Fact-check produced action=%s outcome=%s for invocation=%s tone=%s",
        frozen.action, frozen.action_outcome, frozen.invocation_id, factcheck_tone,
    )

    return {
        "text": text,
        "sources": sources,
        "info_payload": _info_payload_from_frozen(frozen),
        "verdict_label": frozen.verdict_label,
        "action": frozen.action,
        "action_outcome": frozen.action_outcome,
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
