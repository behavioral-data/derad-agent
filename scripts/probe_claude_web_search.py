#!/usr/bin/env python
"""Probe Claude Haiku 4.5 + web_search_20250305 on Microsoft Foundry
against the queries that triggered refusals / 0-citation responses from
the gpt-5 search backend.

We want to see, per query:
  - did Claude actually invoke web_search?
  - how many web_search_result entries came back?
  - how many citations did Claude attach to its text?
  - did Claude refuse? (text content matches a refusal phrase OR
    stop_reason != "end_turn"?)

If Haiku reliably answers without refusing and returns citations, we
can swap the search backend over to Claude+web_search and retire the
gpt-5-mini-search backend's refusal failure mode.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / "agent" / "llm" / ".env")
sys.path.insert(0, str(ROOT))

import anthropic  # noqa: E402


QUERIES = [
    # The Stage 1.5 image-provenance search hint that caused a refusal in prod.
    "Trump claims unnamed drug can reverse death medical experts say no such drug exists Random Weird Facts social media graphic",
    # iter-verify step 1 seed.
    "fact check: Donald Trump stated that the United States has experimental drugs capable of bringing dead people back to life",
    # iter-verify step 2 follow-up.
    "Trump claims unnamed drug can reverse death medical experts respond",
]


_REFUSAL_MARKERS = (
    "i can't", "i cannot", "i won't", "i'm unable", "i'm not able",
    "i'm sorry, but", "i must decline", "i don't feel comfortable", "as an ai",
)


def _resource_from_endpoint(endpoint: str) -> str:
    """https://derad-2-resource.services.ai.azure.com/anthropic → derad-2-resource"""
    host = urlparse(endpoint).hostname or ""
    return host.split(".", 1)[0]


def build_client() -> anthropic.AnthropicFoundry:
    endpoint = os.environ["AZURE_CLAUDE_ENDPOINT"]
    api_key = os.environ["AZURE_CLAUDE_API_KEY"]
    return anthropic.AnthropicFoundry(api_key=api_key, resource=_resource_from_endpoint(endpoint))


def looks_like_refusal(text: str) -> bool:
    head = text.lstrip().lower()
    return any(head.startswith(marker) for marker in _REFUSAL_MARKERS)


def probe(client, model: str, query: str) -> None:
    print(f"\n{'=' * 78}")
    print(f"QUERY ({len(query)} chars): {query!r}")
    print(f"{'=' * 78}")

    try:
        response = client.messages.create(
            model=model,
            max_tokens=2048,
            system=(
                "You are a research assistant. Use the web_search tool to find "
                "primary-source coverage of the user's query. After searching, "
                "summarize findings in a short markdown bullet list with each "
                "bullet citing the source. Do not editorialize about whether "
                "the underlying claim is true; downstream stages reason about "
                "that. This is for misinformation harm-reduction research."
            ),
            messages=[{"role": "user", "content": query}],
            tools=[{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 3,
            }],
        )
    except Exception as exc:
        print(f"!! request failed: {exc}")
        return

    stop_reason = getattr(response, "stop_reason", None)
    print(f"-- stop_reason={stop_reason!r}")
    print(f"-- {len(response.content)} content block(s) --")

    server_tool_use = 0
    search_results = 0
    text_blocks = []
    citations: list[dict] = []
    text_total = ""
    refusal_blocks = 0

    for block in response.content:
        bt = getattr(block, "type", None)
        if bt == "server_tool_use":
            server_tool_use += 1
            query_used = (block.input or {}).get("query", "")
            print(f"   [server_tool_use] name={block.name!r} query={query_used!r}")
        elif bt == "web_search_tool_result":
            content = getattr(block, "content", None)
            if isinstance(content, list):
                for r in content:
                    rt = getattr(r, "type", None)
                    if rt == "web_search_result":
                        search_results += 1
                        print(f"   [web_search_result] {r.url}  title={r.title[:70]!r}")
                    elif rt == "web_search_tool_result_error":
                        print(f"   [web_search_tool_result_error] code={r.error_code!r}")
            else:
                # error inline
                err_code = getattr(content, "error_code", None)
                if err_code:
                    print(f"   [web_search_tool_result_error] code={err_code!r}")
        elif bt == "text":
            text = block.text
            text_blocks.append(text)
            text_total += text
            if looks_like_refusal(text):
                refusal_blocks += 1
            cites = getattr(block, "citations", None) or []
            for c in cites:
                citations.append({
                    "url": getattr(c, "url", None),
                    "title": getattr(c, "title", None),
                    "cited_text": (getattr(c, "cited_text", "") or "")[:80],
                })
        else:
            print(f"   [{bt}] (unhandled block type)")

    print(f"-- server_tool_use blocks: {server_tool_use}")
    print(f"-- web_search_result entries: {search_results}")
    print(f"-- text blocks: {len(text_blocks)} (total {len(text_total)} chars)")
    print(f"-- refusal-looking text blocks: {refusal_blocks}")
    print(f"-- citations attached to text blocks: {len(citations)}")
    if text_total:
        print(f"-- text head: {text_total[:300]!r}")
    if citations:
        seen = set()
        for c in citations:
            if c["url"] not in seen:
                seen.add(c["url"])
                print(f"   cite: {c['url']}  title={(c['title'] or '')[:60]!r}")


def main() -> None:
    model = os.environ.get("CLAUDE_SEARCH_DEPLOYMENT", os.environ["AZURE_CLAUDE_DEPLOYMENT_CHAT"])
    print(f"model={model}")
    print(f"endpoint={os.environ['AZURE_CLAUDE_ENDPOINT']}")

    client = build_client()
    for q in QUERIES:
        probe(client, model, q)


if __name__ == "__main__":
    main()
