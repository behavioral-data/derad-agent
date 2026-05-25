"""Search backends for Stage 4.

Two implementations:

* `StubSearchBackend` — canned hits for the Rosa Camfield worked example.
  Used by tests and offline runs.
* `FoundryBingSearchBackend` — thin Foundry Agent Service wrapper exposing
  only the `bing_grounding` tool. Real web search via Azure-native Bing.

`build_default_backend()` picks the right one from env vars.
"""
from __future__ import annotations

import functools
import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from typing import Optional, Protocol


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str
    snippet: str


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


# ── Foundry Bing-grounding backend ────────────────────────────────────────────


_FOUNDRY_AGENT_INSTRUCTIONS = """You are a focused web-search assistant. Your ONLY job is to use the bing_grounding tool to search the public web for the user's query, then output the top hits as a JSON object.

Output STRICTLY this shape and nothing else:
{
  "hits": [
    {"url": "<https url>", "title": "<page title>", "snippet": "<2-3 sentence summary of relevant content>"}
  ]
}

Rules:
- Return between 3 and 6 hits.
- **URLs must be COPIED EXACTLY from the bing_grounding tool's results — never construct, guess, complete, or modify a URL based on plausible patterns. Hallucinated URLs (real-looking slugs that don't match a real article) are the worst failure mode of this stage.** If a URL appears truncated in the tool output, copy what you have; do NOT extend it.
- Do not interpret, opine, or argue about the claim — just report what sources say.
- Snippets must be drawn from the search results, not invented.
- No markdown, no prose outside the JSON object.
"""


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


class FoundryBingSearchBackend:
    """Search via a single-purpose Foundry agent that only carries the bing_grounding tool.

    The agent is idempotently created (create_version on a fixed name) at first use
    and reused thereafter. Each `search()` call posts a single user query and parses
    the JSON-shaped response.
    """

    name: str

    def __init__(
        self,
        *,
        project_endpoint: str,
        bing_connection_id: str,
        model: str,
        agent_name: str = "derad-bing-search",
        credential=None,
    ) -> None:
        self._project_endpoint = project_endpoint
        self._bing_connection_id = bing_connection_id
        self._model = model
        self._agent_name = agent_name
        self._credential = credential
        self._client = None
        self._openai_client = None
        self._agent_ref = None
        self._lock = threading.Lock()
        self.name = f"foundry-bing:{agent_name}"

    def _ensure_client(self):
        if self._client is not None:
            return
        with self._lock:
            if self._client is not None:
                return
            from azure.ai.projects import AIProjectClient
            from azure.identity import DefaultAzureCredential

            credential = self._credential or DefaultAzureCredential()
            client = AIProjectClient(endpoint=self._project_endpoint, credential=credential)
            self._client = client
            self._openai_client = client.get_openai_client()
            self._agent_ref = self._ensure_agent()

    def _ensure_agent(self):
        from azure.ai.projects.models import (
            PromptAgentDefinition,
            BingGroundingTool,
            BingGroundingSearchToolParameters,
            BingGroundingSearchConfiguration,
        )

        tool = BingGroundingTool(
            bing_grounding=BingGroundingSearchToolParameters(
                search_configurations=[
                    BingGroundingSearchConfiguration(project_connection_id=self._bing_connection_id)
                ]
            )
        )
        agent = self._client.agents.create_version(
            agent_name=self._agent_name,
            definition=PromptAgentDefinition(
                model=self._model,
                instructions=_FOUNDRY_AGENT_INSTRUCTIONS,
                tools=[tool],
            ),
            description="Single-purpose Bing-grounding search agent for derad-agent.",
        )
        logger.info("Foundry search agent ready: %s v%s", agent.name, agent.version)
        return {"name": agent.name, "type": "agent_reference"}

    def search(self, query: str, top_k: int = 5) -> list[SearchHit]:
        self._ensure_client()
        logger.info("Foundry search: query head=%r", query[:120])
        try:
            response = self._openai_client.responses.create(
                tool_choice="required",
                input=query,
                extra_body={"agent_reference": self._agent_ref},
                timeout=90,
            )
        except Exception:
            logger.exception("Foundry Bing search failed for query=%r", query)
            return []

        # Annotations from the bing_grounding tool are the source of truth for
        # which URLs were actually returned by Bing. The JSON text the agent
        # writes can contain hallucinated URLs (plausible-looking slugs + IDs
        # that point at non-existent or wrong articles). Filter the JSON hits
        # to only those whose URL appears in an url_citation annotation.
        annotated_urls = _extract_url_citations(response)

        text = _extract_response_text(response)
        if not text:
            logger.warning("Foundry Bing search returned no text for query=%r", query)
            return []

        try:
            data = _parse_hits_json(text)
        except ValueError as exc:
            logger.warning("Could not parse Foundry hits JSON: %s — text head: %s", exc, text[:200])
            return []

        candidate_hits = [
            SearchHit(url=h.get("url", ""), title=h.get("title", ""), snippet=h.get("snippet", ""))
            for h in (data.get("hits") or [])
            if isinstance(h, dict) and h.get("url")
        ]

        if annotated_urls:
            verified, dropped = [], []
            for h in candidate_hits:
                if h.url in annotated_urls:
                    verified.append(h)
                else:
                    dropped.append(h.url)
            if dropped:
                logger.warning(
                    "Foundry search: dropped %d hallucinated URL(s) not in Bing annotations: %s",
                    len(dropped), dropped[:5],
                )
            # If the agent only emitted hallucinated URLs (none matched
            # annotations), surface the annotation URLs directly. We don't
            # have titles/snippets for them, but they're real.
            if not verified and annotated_urls:
                logger.warning(
                    "Foundry search: agent JSON had 0 verifiable URLs; falling back to %d annotation URLs",
                    len(annotated_urls),
                )
                verified = [
                    SearchHit(url=u, title="", snippet="")
                    for u in list(annotated_urls)[:top_k]
                ]
            candidate_hits = verified
        else:
            # No annotations on this response (Bing tool might not always
            # attach them). HEAD-validate to catch obvious 404s.
            candidate_hits = _head_validate(candidate_hits)

        return candidate_hits[:top_k]


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


def _parse_hits_json(text: str) -> dict:
    """Extract a JSON object from possibly noisy LLM output."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = _JSON_BLOCK_RE.search(text)
    if not match:
        raise ValueError("no JSON object found")
    return json.loads(match.group(0))


def _extract_url_citations(response) -> set[str]:
    """Pull every url_citation annotation URL from a Responses-API response.

    The bing_grounding tool attaches url_citation annotations to message-
    content blocks. These annotations are anchored to real Bing search
    results — unlike the JSON URLs the agent writes, they cannot be
    hallucinated by the model.
    """
    urls: set[str] = set()
    output = getattr(response, "output", None) or []
    for item in output:
        item_type = getattr(item, "type", None)
        if item_type != "message":
            continue
        content = getattr(item, "content", None) or []
        for block in content:
            annotations = getattr(block, "annotations", None) or []
            for ann in annotations:
                ann_type = getattr(ann, "type", None) or (
                    ann.get("type") if isinstance(ann, dict) else None
                )
                if ann_type != "url_citation":
                    continue
                url = getattr(ann, "url", None) or (
                    ann.get("url") if isinstance(ann, dict) else None
                )
                if url:
                    urls.add(url)
    return urls


def _head_validate(hits: list[SearchHit], *, timeout_s: float = 4.0) -> list[SearchHit]:
    """Drop hits whose URL doesn't resolve to 2xx/3xx. Defense-in-depth when
    annotation extraction returns nothing; catches obvious 404s but won't
    detect 'page exists but is a different article'."""
    import requests

    out: list[SearchHit] = []
    for h in hits:
        try:
            resp = requests.head(
                h.url,
                timeout=timeout_s,
                allow_redirects=True,
                headers={"User-Agent": "derad-agent-validator/3.0"},
            )
            if 200 <= resp.status_code < 400:
                out.append(h)
            else:
                logger.warning("HEAD-validate dropped %s (status %d)", h.url, resp.status_code)
        except requests.RequestException as exc:
            logger.warning("HEAD-validate dropped %s (%s)", h.url, type(exc).__name__)
    return out


# ── Default backend selection ─────────────────────────────────────────────────


@functools.lru_cache(maxsize=1)
def build_default_backend() -> SearchBackend:
    """Build the default backend from env vars; falls back to StubSearchBackend."""
    endpoint = os.getenv("FOUNDRY_PROJECT_ENDPOINT")
    conn_id = os.getenv("FOUNDRY_BING_CONNECTION_ID")
    model = os.getenv("FOUNDRY_SEARCH_MODEL")
    if endpoint and conn_id and model:
        agent_name = os.getenv("FOUNDRY_SEARCH_AGENT_NAME", "derad-bing-search")
        logger.info("Using FoundryBingSearchBackend (endpoint=%s, agent=%s)", endpoint, agent_name)
        return FoundryBingSearchBackend(
            project_endpoint=endpoint,
            bing_connection_id=conn_id,
            model=model,
            agent_name=agent_name,
        )
    logger.info("Foundry env not set; using StubSearchBackend.")
    return StubSearchBackend()
