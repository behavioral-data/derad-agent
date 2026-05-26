"""Search backends for Stage 4.

Three implementations:

* `StubSearchBackend` — canned hits for the Rosa Camfield worked example.
  Used by tests and offline runs.
* `ClaudeWebSearchBackend` — Anthropic Messages API + `web_search_20250305`
  server tool on Microsoft Foundry. URLs come from `web_search_tool_result`
  blocks (server-stamped) plus per-text-block citations carrying
  `cited_text`. Doesn't refuse on edgy queries the way the gpt-5 backend
  did (validated on the Trump-revive-drug refusal case).
* `WebSearchResponsesBackend` — Azure OpenAI Responses API call with the
  native `web_search` tool. Kept as a fallback for cost-sensitive runs
  but has been observed to refuse summarization on certain queries,
  yielding zero hits even when the underlying search ran.

`build_default_backend()` picks the right one from env vars.
"""
from __future__ import annotations

import functools
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional, Protocol
from urllib.parse import urlparse


logger = logging.getLogger(__name__)

# Quiet trafilatura's own logger. When a fetched page isn't real article
# HTML — Cloudflare "Just a moment…" challenge pages, PDFs, near-empty
# bodies — trafilatura logs ERROR/WARNING noise ("empty HTML tree",
# "parsed tree length: 1, wrong data type or not valid HTML", "discarding
# data: None"). We already treat failed extraction as an empty body
# (_fetch_clean_page returns body_markdown=""), so these are benign; keep
# them out of our logs.
logging.getLogger("trafilatura").setLevel(logging.CRITICAL)


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str
    snippet: str
    # True when the URL came from a server-stamped url_citation annotation
    # (authoritative — fabrication is impossible). False when the model
    # transcribed it as inline markdown (still possibly correct, but
    # subject to model-typed-URL risk and needs content validation).
    is_annotation: bool = False
    # Clean article body, extracted from the URL by trafilatura and capped
    # at a few KB. Lets reconcile reason over the underlying reporting
    # rather than just the search-result snippet. Empty when fetch/extract
    # failed (paywall, JS-only page, 404, timeout, etc.).
    body_markdown: str = ""


# Override to 1 to force HEAD+title validation on every URL even when it's
# annotation-stamped. Default off — annotated URLs are server-vouched.
_VALIDATE_ANNOTATIONS = os.getenv("DERAD_VALIDATE_ANNOTATIONS", "0").lower() in ("1", "true", "yes")


class SearchBackend(Protocol):
    name: str

    def search(self, query: str, top_k: int = 5) -> list[SearchHit]: ...


class StubSearchBackend:
    """Hardcoded hits for one example. Drives the thin slice without external calls."""

    name = "stub:canned-rosa-camfield"

    _CANNED: dict[str, list[SearchHit]] = {
        "rosa camfield": [
            SearchHit(
                url="https://www.snopes.com/fact-check/rosa-camfield-101/",
                title="Did a 101-Year-Old Woman Give Birth to Her 17th Child?",
                snippet=(
                    "A photograph showing an elderly woman with a newborn is real, but the "
                    "caption is false. The image shows 101-year-old Rosa Camfield holding her "
                    "two-week-old great-granddaughter Kaylee in March 2015. The claim that the "
                    "woman gave birth to her 17th child at age 101 originated from World News "
                    "Daily Report, a self-described satirical and fictional news site."
                ),
            ),
            SearchHit(
                url="https://www.thequint.com/news/webqoof/woman-with-baby-not-mother-of-17-fake-news",
                title="Photo of 'Mother of 17 at 101' Is Miscaptioned",
                snippet=(
                    "The Quint traced the image to a 2015 Facebook post by the Camfield family. "
                    "Rosa Camfield, then 101, is shown meeting her great-granddaughter Kaylee. "
                    "The 'mother of 17' story is fabricated."
                ),
            ),
            SearchHit(
                url="https://africacheck.org/fact-checks/meta-programme-fact-checks/no-photo-doesnt-show-101-year-old-woman",
                title="No, photo doesn't show 101-year-old woman who gave birth to her 17th child",
                snippet=(
                    "Africa Check confirmed the photo is authentic but miscaptioned. The woman "
                    "is Rosa Camfield, and the baby is her great-granddaughter."
                ),
            ),
            SearchHit(
                url="https://worldnewsdailyreport.com/woman-101-gives-birth-to-her-17th-child/",
                title="Woman, 101, gives birth to her 17th child",
                snippet=(
                    "[SATIRE] The original source of the false claim. World News Daily Report "
                    "describes itself as satirical and entirely fictional."
                ),
            ),
        ],
    }

    def search(self, query: str, top_k: int = 5) -> list[SearchHit]:
        key = next((k for k in self._CANNED if k in query.lower()), None)
        if key is None:
            return []
        return self._CANNED[key][:top_k]


# ── Responses-API web-search backend ──────────────────────────────────────────


_WEB_SEARCH_INSTRUCTIONS = """You are a web-search assistant for a fact-checking research pipeline. Your job is to use the web_search tool to find primary-source coverage of the user's query and cite each result.

Always invoke web_search at least once. After the tool returns, write a short markdown bullet list summarizing the most relevant findings, with each bullet linking to the source. Stay grounded in what the search results actually say — do not editorialize about whether the underlying claim is true or false; downstream stages reason about that. The bot is for misinformation harm-reduction; surfacing accurate sources is the task.

Each bullet should read like:
- One factual sentence from the source. (link with the page title)

That's it — no preamble, no analysis paragraphs."""


class WebSearchResponsesBackend:
    """Search via Azure OpenAI's Responses API with the native `web_search` tool.

    Replaces the previous Foundry-Agent+bing_grounding approach. Annotations
    on the response carry real url_citation entries anchored to actual search
    results — these are our SearchHits. URLs cannot be hallucinated because
    the model never transcribes them; the tool runtime attaches them
    server-side as citations to the model's response text.

    No agent_reference, no create_version churn, no Bing Grounding
    resource — just openai_client.responses.create.
    """

    name: str

    def __init__(
        self,
        *,
        project_endpoint: str,
        model: str,
        credential=None,
    ) -> None:
        self._project_endpoint = project_endpoint
        self._model = model
        self._credential = credential
        self._client = None
        self._openai_client = None
        self._lock = threading.Lock()
        self.name = f"web-search:{model}"

    def _ensure_client(self):
        if self._openai_client is not None:
            return
        with self._lock:
            if self._openai_client is not None:
                return
            from azure.ai.projects import AIProjectClient
            from azure.identity import DefaultAzureCredential

            credential = self._credential or DefaultAzureCredential()
            self._client = AIProjectClient(endpoint=self._project_endpoint, credential=credential)
            self._openai_client = self._client.get_openai_client()
            logger.info("Web-search backend ready: model=%s", self._model)

    def search(self, query: str, top_k: int = 5) -> list[SearchHit]:
        self._ensure_client()
        logger.info("Web-search: query head=%r", query[:120])
        try:
            response = self._openai_client.responses.create(
                model=self._model,
                instructions=_WEB_SEARCH_INSTRUCTIONS,
                input=query,
                tools=[{"type": "web_search"}],
                timeout=90,
            )
        except Exception:
            logger.exception("Web-search call failed for query=%r", query)
            return []

        hits = _extract_search_hits(response)
        if not hits:
            logger.warning("Web-search: 0 url_citation annotations returned for query=%r", query[:120])
            return []

        # Defense-in-depth: HEAD-validate to drop dead links. Annotations are
        # anchored to real search results so URL fabrication is impossible,
        # but the underlying page can be 404/410.
        verified, rejected = _validate_hits(hits)
        if rejected:
            logger.info(
                "Web-search: dropped %d unreachable URL(s): %s",
                len(rejected),
                [f"{u} ({reason})" for u, reason in rejected[:5]],
            )
        return verified[:top_k]


def _ann_get(ann, key: str):
    """Annotation objects can be SDK models or plain dicts depending on
    SDK version; read fields uniformly."""
    val = getattr(ann, key, None)
    if val is None and isinstance(ann, dict):
        val = ann.get(key)
    return val


_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")


def _extract_search_hits(response) -> list[SearchHit]:
    """Pull SearchHits from a Responses-API response produced with
    `tools=[{"type": "web_search"}]`.

    Two paths, in priority order:

    1. `url_citation` annotations attached to message-content blocks.
       Authoritative — URLs are stamped server-side by the tool runtime
       and cannot be hallucinated.
    2. Inline markdown links in the response text. The web_search tool
       sometimes emits no annotations and instead has the model transcribe
       citations as `[title](https://...)`. These URLs are model-typed so
       they CAN be wrong, but they're defended downstream by HEAD+title
       content validation in `_validate_hits`.

    Hits are deduped by URL (first occurrence wins).
    """
    full_text = _extract_response_text(response)
    seen: set[str] = set()
    hits: list[SearchHit] = []

    output = getattr(response, "output", None) or []
    for item in output:
        if getattr(item, "type", None) != "message":
            continue
        content = getattr(item, "content", None) or []
        for block in content:
            annotations = getattr(block, "annotations", None) or []
            for ann in annotations:
                if _ann_get(ann, "type") != "url_citation":
                    continue
                url = _ann_get(ann, "url")
                if not url or url in seen:
                    continue
                seen.add(url)
                title = _ann_get(ann, "title") or ""
                start = _ann_get(ann, "start_index")
                end = _ann_get(ann, "end_index")
                snippet = ""
                if isinstance(start, int) and isinstance(end, int) and full_text:
                    s = max(0, start - 240)
                    e = min(len(full_text), end + 60)
                    snippet = full_text[s:e].strip()
                hits.append(SearchHit(url=url, title=title, snippet=snippet, is_annotation=True))

    if hits or not full_text:
        return hits

    # Fallback: parse inline markdown links. Pair each link's surrounding
    # bullet/sentence as the snippet. These are model-typed URLs, so
    # is_annotation stays False and they get full HEAD+title validation.
    for match in _MARKDOWN_LINK_RE.finditer(full_text):
        title, url = match.group(1).strip(), match.group(2).strip().rstrip(".,;:)")
        if url in seen:
            continue
        seen.add(url)
        s = full_text.rfind("\n", 0, match.start()) + 1
        e = full_text.find("\n", match.end())
        if e == -1:
            e = len(full_text)
        snippet = full_text[s:e].strip().lstrip("- *")
        hits.append(SearchHit(url=url, title=title, snippet=snippet[:400], is_annotation=False))
    return hits


def _extract_response_text(response) -> str:
    """Pull the final assistant text from a Responses-API response.

    The response object exposes `.output` as a list of items; the message with
    role 'assistant' carries content[0].text on its final item.
    """
    output = getattr(response, "output", None) or []
    for item in reversed(output):
        item_type = getattr(item, "type", None)
        if item_type != "message":
            continue
        content = getattr(item, "content", None) or []
        for block in content:
            text = getattr(block, "text", None)
            if text:
                return text
    return ""


# A realistic, current desktop-Chrome UA. The old bot-identifying UA
# ("derad-agent-validator/...") tripped WAFs (Cloudflare/Akamai) on news,
# reference, and gov sites, which returned 403 and caused the hit to be
# dropped entirely. A normal browser UA + the browser-like headers below
# clears the "lazy" bot blocks; hard JS-challenge / paywalled sites still
# won't yield (those need a headless browser or are paywalled regardless).
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Browser-like request headers sent with every page fetch. Many WAFs key
# off the absence of Accept-Language / Sec-Fetch-* as a bot signal.
_BROWSER_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

# Read up to 512 KB of HTML per page — enough for the body of nearly any
# article without runaway memory on the occasional huge page.
_PAGE_MAX_BYTES = 512 * 1024

# Cap the extracted markdown we keep per source. Reconcile passes evidence
# to the LLM, so this directly drives prompt size; 3000 chars ≈ 750 tokens.
_CONTENT_CAP = 3000


def _fetch_clean_page(
    url: str, *, timeout_s: float = 8.0
) -> tuple[Optional[int], Optional[str], Optional[str], str]:
    """Fetch a URL and extract a clean markdown body via trafilatura.

    Returns ``(status_code, final_url, page_title, body_markdown)``.
    Any of status/final_url/page_title is ``None`` on transport failure;
    ``body_markdown`` is ``""`` when extraction was unsuccessful (paywall,
    JS-rendered, non-article page, etc.) but the page itself loaded.

    Single network round-trip per URL — used by ``_classify_hit`` to both
    validate the hit (via title-match for model-typed URLs) and enrich it
    with the actual article body (for reconcile reasoning).
    """
    import requests
    import trafilatura

    try:
        resp = requests.get(
            url,
            timeout=timeout_s,
            allow_redirects=True,
            headers=_BROWSER_HEADERS,
            stream=True,
        )
    except Exception:
        return None, None, None, ""

    try:
        chunks: list[bytes] = []
        total = 0
        for chunk in resp.iter_content(chunk_size=8192):
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total >= _PAGE_MAX_BYTES:
                break
    except Exception:
        try:
            resp.close()
        except Exception:
            pass
        return resp.status_code, str(resp.url), None, ""
    finally:
        try:
            resp.close()
        except Exception:
            pass

    body = b"".join(chunks)
    try:
        html_text = body.decode(resp.encoding or "utf-8", errors="replace")
    except (LookupError, TypeError):
        html_text = body.decode("utf-8", errors="replace")

    # Title — prefer trafilatura's metadata extraction; fall back to regex.
    title: Optional[str] = None
    try:
        meta = trafilatura.extract_metadata(html_text)
        if meta is not None and meta.title:
            title = meta.title.strip()
    except Exception:
        pass
    if not title:
        match = _TITLE_RE.search(html_text)
        if match:
            import html as _html
            title = _html.unescape(match.group(1)).strip() or None

    # Article body in markdown. Trafilatura strips nav/footer/ads/comments
    # and returns just the main content. None on extraction failure.
    body_markdown = ""
    try:
        extracted = trafilatura.extract(
            html_text,
            output_format="markdown",
            include_links=False,
            include_comments=False,
            include_tables=True,
            favor_recall=False,
        )
        if extracted:
            body_markdown = extracted[:_CONTENT_CAP]
    except Exception:
        pass

    return resp.status_code, str(resp.url), title, body_markdown


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> set[str]:
    """Word-tokenize, lowercased, stripped of punctuation, length-filtered."""
    return {tok for tok in _TOKEN_RE.findall(text.lower()) if len(tok) >= 3}


_STOPWORDS = frozenset({
    "the", "and", "for", "with", "that", "this", "from", "into",
    "are", "was", "were", "been", "have", "has", "had", "would", "will",
    "what", "when", "where", "who", "why", "how", "your", "you", "their",
    "about", "after", "all", "any", "but", "can", "could", "did", "does",
    "more", "most", "not", "now", "off", "one", "only", "out", "over",
    "than", "then", "there", "these", "they", "than", "too", "use", "very",
    "via", "viral", "news", "story", "article", "report", "watch", "video",
    "photo", "image", "post", "twitter", "tweet",
})


def _title_match_score(claimed: str, actual: str) -> float:
    """Jaccard overlap of meaningful tokens between claimed and actual titles.
    Returns 0.0..1.0; ≥0.25 is a passable match for fact-checking purposes."""
    if not claimed or not actual:
        return 0.0
    a = _tokenize(claimed) - _STOPWORDS
    b = _tokenize(actual) - _STOPWORDS
    if not a or not b:
        return 0.0
    overlap = a & b
    return len(overlap) / max(len(a | b), 1)


_TITLE_MATCH_THRESHOLD = 0.20
_VALIDATE_MAX_WORKERS = 8


def _classify_hit(h: SearchHit) -> tuple[Optional[SearchHit], Optional[tuple[str, str]]]:
    """Fetch the page, validate the hit, and enrich it with body markdown.

    Single network round-trip via ``_fetch_clean_page``. For all hits we
    populate ``body_markdown`` from trafilatura's extraction so reconcile
    can reason over the actual article. For model-typed URLs (is_annotation
    is False) we additionally check that the fetched page title matches the
    claimed title — guards against the model writing a plausible-looking but
    wrong URL. Annotation-stamped hits skip the title check (server-vouched)
    but still get their body extracted.

    Returns (verified_hit, None) on accept; (None, (url, reason)) on reject.
    Pure-function so it's safe to call from a ThreadPoolExecutor.
    """
    if not h.url.startswith(("http://", "https://")):
        return None, (h.url, "non-http")

    status, final_url, page_title, body_markdown = _fetch_clean_page(h.url)
    if status is None:
        return None, (h.url, "fetch_failed")
    if status >= 400:
        return None, (h.url, f"http_{status}")

    enriched = SearchHit(
        url=h.url,
        title=h.title or (page_title or ""),
        snippet=h.snippet,
        is_annotation=h.is_annotation,
        body_markdown=body_markdown,
    )

    # Annotation-stamped URLs are server-vouched — skip title verification.
    if h.is_annotation and not _VALIDATE_ANNOTATIONS:
        return enriched, None

    if not page_title:
        logger.info("URL %s returned no <title>; passing through unverified.", h.url)
        return enriched, None
    if not h.title:
        # No claimed title to compare against — accept and adopt the page title.
        return enriched, None

    score = _title_match_score(h.title, page_title)
    if score >= _TITLE_MATCH_THRESHOLD:
        return enriched, None
    return None, (
        h.url,
        f"title_mismatch score={score:.2f} claimed={h.title[:40]!r} actual={page_title[:40]!r}",
    )


def _validate_hits(
    hits: list[SearchHit],
) -> tuple[list[SearchHit], list[tuple[str, str]]]:
    """Verify each hit in parallel (HEAD/title fetch is IO-bound).

    Annotation-stamped hits short-circuit with no network call. Returned
    `verified` preserves input order so reconcile sees the highest-ranked
    hits first.
    """
    if not hits:
        return [], []

    workers = min(_VALIDATE_MAX_WORKERS, len(hits))
    results: list[tuple[Optional[SearchHit], Optional[tuple[str, str]]]]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(_classify_hit, hits))

    verified: list[SearchHit] = []
    rejected: list[tuple[str, str]] = []
    for ok, bad in results:
        if ok is not None:
            verified.append(ok)
        elif bad is not None:
            rejected.append(bad)
    return verified, rejected


# ── Claude + web_search backend ───────────────────────────────────────────────


_CLAUDE_SEARCH_SYSTEM = """You are a research assistant for a misinformation harm-reduction pipeline. Use the web_search tool to find primary-source coverage of the user's query. After searching, summarize findings in a short markdown bullet list, citing each source.

Stay grounded in what published sources actually say — do not editorialize about whether the underlying claim is true or false; downstream stages reason about that. Surfacing accurate coverage is the task. If the query touches a sensitive or contested topic, simply surface what credible publishers have reported."""


class ClaudeWebSearchBackend:
    """Search via Anthropic's `web_search_20250305` tool on Microsoft Foundry.

    Returns the URLs Claude cited in its summary, ranked by citation order.
    `web_search_tool_result` blocks carry the server-side result list; per-
    text-block citations carry the load-bearing `cited_text`. URLs are
    server-stamped — Claude cannot fabricate them.

    If Claude cited nothing (rare), we fall back to every URL in the result
    blocks. If the tool returned an error, we log it and return [].
    """

    name: str

    def __init__(self, *, endpoint: str, model: str, api_key: Optional[str] = None) -> None:
        self._endpoint = endpoint
        self._model = model
        self._api_key = api_key or os.environ.get("AZURE_CLAUDE_API_KEY")
        self._client = None
        self._lock = threading.Lock()
        self.name = f"claude-web-search:{model}"

    def _ensure_client(self):
        if self._client is not None:
            return
        with self._lock:
            if self._client is not None:
                return
            from anthropic import AnthropicFoundry

            host = urlparse(self._endpoint).hostname or ""
            resource = host.split(".", 1)[0]
            if not resource:
                raise ValueError(f"Could not derive Foundry resource from endpoint {self._endpoint!r}")
            if not self._api_key:
                raise ValueError("AZURE_CLAUDE_API_KEY is required for ClaudeWebSearchBackend")
            self._client = AnthropicFoundry(api_key=self._api_key, resource=resource)
            logger.info("Claude web-search backend ready: model=%s", self._model)

    def search(self, query: str, top_k: int = 5) -> list[SearchHit]:
        self._ensure_client()
        logger.info("Claude-web-search: query head=%r", query[:120])
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=2048,
                system=_CLAUDE_SEARCH_SYSTEM,
                messages=[{"role": "user", "content": query}],
                tools=[{
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": 3,
                }],
                timeout=90,
            )
        except Exception:
            logger.exception("Claude web-search call failed for query=%r", query)
            return []

        hits = _extract_claude_search_hits(response)
        if not hits:
            logger.warning("Claude-web-search: 0 cited URLs for query=%r", query[:120])
            return []

        # _validate_hits fetches each URL once to (a) catch 404s/paywalls and
        # (b) populate body_markdown via trafilatura so reconcile can reason
        # over the actual article. Annotation-stamped URLs skip the title-
        # match check (server-vouched) but still get their body extracted.
        verified, rejected = _validate_hits(hits)
        if rejected:
            logger.info(
                "Claude-web-search: dropped %d unreachable URL(s): %s",
                len(rejected),
                [f"{u} ({reason})" for u, reason in rejected[:5]],
            )
        return verified[:top_k]


def _extract_claude_search_hits(response) -> list[SearchHit]:
    """Parse SearchHits from an Anthropic web_search response.

    Walks `response.content`:
      - `web_search_tool_result` blocks contribute (url, title) pairs.
      - `web_search_tool_result_error` blocks are logged.
      - `text` blocks contribute `cited_text` for any URLs they cite.

    Ranking: URLs Claude actually cited come first (in citation order),
    followed by uncited search results. If nothing was cited, every result
    URL is returned in result order.
    """
    result_meta: dict[str, str] = {}
    result_order: list[str] = []
    cited_snippets: dict[str, str] = {}
    cited_order: list[str] = []

    for block in response.content:
        bt = getattr(block, "type", None)
        if bt == "web_search_tool_result":
            content = getattr(block, "content", None)
            if isinstance(content, list):
                for r in content:
                    rt = getattr(r, "type", None)
                    if rt == "web_search_result":
                        url = getattr(r, "url", None)
                        if url and url not in result_meta:
                            result_meta[url] = getattr(r, "title", None) or ""
                            result_order.append(url)
                    elif rt == "web_search_tool_result_error":
                        logger.warning(
                            "Claude-web-search: tool error code=%r",
                            getattr(r, "error_code", None),
                        )
            else:
                err_code = getattr(content, "error_code", None) if content else None
                if err_code:
                    logger.warning("Claude-web-search: tool error code=%r", err_code)
        elif bt == "text":
            for c in getattr(block, "citations", None) or []:
                url = getattr(c, "url", None)
                if not url:
                    continue
                if url not in cited_snippets:
                    cited_snippets[url] = (getattr(c, "cited_text", "") or "")[:400]
                    cited_order.append(url)
                if url not in result_meta:
                    result_meta[url] = getattr(c, "title", None) or ""

    if cited_order:
        # Cited first, then any uncited search results.
        ordered = cited_order + [u for u in result_order if u not in cited_snippets]
    else:
        ordered = result_order

    return [
        SearchHit(
            url=url,
            title=result_meta.get(url, ""),
            snippet=cited_snippets.get(url, ""),
            is_annotation=True,
        )
        for url in ordered
    ]


# ── Default backend selection ─────────────────────────────────────────────────


@functools.lru_cache(maxsize=1)
def build_default_backend() -> SearchBackend:
    """Pick a search backend from env vars.

    Preference order:
      1. `ClaudeWebSearchBackend` — when `AZURE_CLAUDE_ENDPOINT` is set and
         `CLAUDE_SEARCH_DEPLOYMENT` names a deployed Claude model. This is
         the default search path because it doesn't refuse on edgy queries.
      2. `WebSearchResponsesBackend` — fallback using Azure OpenAI's gpt-5
         search model. Refuses on some queries (silent zero-hit failures);
         kept as a fallback for cost-sensitive runs.
      3. `StubSearchBackend` — offline / tests.
    """
    claude_endpoint = os.getenv("AZURE_CLAUDE_ENDPOINT")
    claude_model = os.getenv("CLAUDE_SEARCH_DEPLOYMENT")
    if claude_endpoint and claude_model:
        logger.info(
            "Using ClaudeWebSearchBackend (endpoint=%s, model=%s)",
            claude_endpoint, claude_model,
        )
        return ClaudeWebSearchBackend(endpoint=claude_endpoint, model=claude_model)

    endpoint = os.getenv("FOUNDRY_PROJECT_ENDPOINT")
    model = os.getenv("FOUNDRY_SEARCH_MODEL")
    if endpoint and model:
        logger.info("Using WebSearchResponsesBackend (endpoint=%s, model=%s)", endpoint, model)
        return WebSearchResponsesBackend(project_endpoint=endpoint, model=model)
    logger.info("No search env vars set; using StubSearchBackend.")
    return StubSearchBackend()
