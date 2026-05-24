import logging
import re
import threading
from dataclasses import dataclass
from typing import Optional

import requests
from markupsafe import escape

from agent.llm.config import get_x_client
from agent.llm.config import INDEX_ROOT
from agent.runtime import get_notes_index_dir
from agent.runtime.notes_index import load_notes_index

logger = logging.getLogger(__name__)

_INDEX = None
_INDEX_LOCK = threading.Lock()


def get_index():
    """Return the in-memory notes index, loading it on first access."""
    global _INDEX
    if _INDEX is not None:
        return _INDEX
    with _INDEX_LOCK:
        if _INDEX is None:
            logger.info("Loading notes index from %s", get_notes_index_dir(INDEX_ROOT))
            _INDEX = load_notes_index(get_notes_index_dir(INDEX_ROOT))
            logger.info("Notes index loaded: %d tweets", len(_INDEX.tweet_ids))
    return _INDEX


def index_loaded() -> bool:
    """Return True if the index is in memory. Does not force a load."""
    return _INDEX is not None


def preload_index_async() -> None:
    """Kick off an index load in a daemon thread so /healthz can answer fast."""
    def _safe_preload():
        try:
            get_index()
        except Exception:
            logger.exception("Index preload failed; will retry on first use")
    threading.Thread(target=_safe_preload, name="index-preload", daemon=True).start()


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


def generate_reply(statement, tone, exclude_tweet_id=None, max_sources=5):
    from agent.runtime.landscape_api import retrieve_statement_landscape

    kwargs = {
        "statement": statement,
        "style": tone,
    }
    if exclude_tweet_id is not None:
        kwargs["exclude_tweet_id"] = str(exclude_tweet_id)

    res = retrieve_statement_landscape(**kwargs)
    reply = res.get("reply") or {}
    text = (reply.get("response") or "").strip()
    queries = list(res.get("queries") or [])

    # "All cited" — every reason the LLM grounded its reply in, used by the
    # research event log. "In-tweet" — the subset whose evidence URLs survived
    # dedup/cap, used by /info so the carousel matches what the user saw.
    all_cited_tweet_ids: list[str] = []
    all_cited_note_ids: list[str] = []
    reasons_detail: list[dict] = []
    tweets: list = []
    notes: list = []
    sources: list = []
    index = get_index()
    seen = set()
    for reason in (reply.get("reasons") or []):
        tid, nid = reason.get("tweet_id"), reason.get("note_id")
        if tid is not None:
            all_cited_tweet_ids.append(str(tid))
        if nid is not None:
            all_cited_note_ids.append(str(nid))
        links = [l.strip() for l in (reason.get("evidence_links") or []) if isinstance(l, str) and l.strip()]
        note_text = None
        if tid is not None and nid is not None:
            for n in index.notes_by_tweet.get(str(tid), []):
                if str(n.get("note_id")) == str(nid):
                    note_text = (n.get("summary") or "").strip() or None
                    break
        reasons_detail.append({
            "reason": str(reason.get("reason") or ""),
            "note_id": str(nid) if nid is not None else None,
            "tweet_id": str(tid) if tid is not None else None,
            "evidence_links": links,
            "note_text": note_text,
        })
        if len(sources) >= max_sources:
            continue
        if links:
            for link in links:
                if link not in seen and len(sources) < max_sources:
                    sources.append(link)
                    seen.add(link)
            tweets.append(tid)
            notes.append(nid)

    return {
        "text": text,
        "sources": sources or None,
        "tweets": tweets or None,
        "notes": notes or None,
        "queries": queries,
        "all_cited_tweet_ids": all_cited_tweet_ids,
        "all_cited_note_ids": all_cited_note_ids,
        "reasons_detail": reasons_detail,
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


_URL_RE = re.compile(r'https?://[^\s<>"]+')


def _linkify_safe(text: str) -> str:
    """HTML-escape `text`, then turn http(s) URLs into <a> tags.

    Untrusted note summaries pass through here. Escaping first means stray HTML
    in the upstream Community Notes feed cannot become live markup; we then
    re-introduce *only* anchor tags for matched URLs.
    """
    parts: list[str] = []
    last_end = 0
    for m in _URL_RE.finditer(text):
        parts.append(str(escape(text[last_end:m.start()])))
        url_safe = str(escape(m.group(0)))
        parts.append(f'<a href="{url_safe}">{url_safe}</a>')
        last_end = m.end()
    parts.append(str(escape(text[last_end:])))
    return "".join(parts)


def generate_notes_html(tweet_ids, note_ids, bot_handle: str = "i") -> str:
    """Render the sources carousel for /info. `bot_handle` is used in fallback hrefs."""
    if not tweet_ids or not note_ids:
        return ""
    index = get_index()
    safe_handle = str(escape(bot_handle))
    notes_html = ""
    for i, (tweet_id, note_id) in enumerate(zip(tweet_ids, note_ids)):
        tweet_notes = index.notes_by_tweet.get(tweet_id, [])
        note_summaries = {note.get("note_id"): note.get("summary") for note in tweet_notes}

        if note_id in note_summaries:
            note_text = _linkify_safe(note_summaries.get(note_id) or "")
            safe_tweet_id = str(escape(tweet_id))
            notes_html += f"""
                <li>
                    <article class="note-body">
                        <h4>Community Note {i + 1}</h4>
                        <p>{note_text}</p>
                        <br>
                        <p>This community note was added by an X user to correct misinformation on <a href="https://twitter.com/{safe_handle}/status/{safe_tweet_id}">this tweet</a>.</p>
                    </article>
                </li>
            """
    return notes_html
