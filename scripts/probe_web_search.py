#!/usr/bin/env python
"""Probe the Azure OpenAI Responses API + web_search tool to figure out
why the factcheck pipeline is logging "0 url_citation annotations" on
real queries.

Replays the two queries from the failed log (the Trump-revive-drug
mention) directly against the configured backend and dumps:

  - every item in response.output (type + role + tool name)
  - every content block (type + text snippet + annotation count)
  - every annotation (type + url + start/end)
  - the assistant text and any markdown link matches in it
  - what _extract_search_hits would return on this response

If the model isn't calling the tool, the dump shows that. If the model
calls the tool but returns no annotations, the dump shows the tool
output. If neither annotations nor markdown links land, we'll see why
in one go.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / "agent" / "llm" / ".env")
sys.path.insert(0, str(ROOT))

from agent.factcheck.search import (  # noqa: E402
    _MARKDOWN_LINK_RE,
    _extract_response_text,
    _extract_search_hits,
)


QUERIES = [
    "Trump claims unnamed drug can reverse death medical experts say no such drug exists Random Weird Facts social media graphic",
    "fact check: Donald Trump stated that the United States has experimental drugs capable of bringing dead people back to life",
    "Trump claims unnamed drug can reverse death medical experts respond",
]


_WEB_SEARCH_INSTRUCTIONS = """You are a web-search assistant for a fact-checking research pipeline. Your job is to use the web_search tool to find primary-source coverage of the user's query and cite each result.

Always invoke web_search at least once. After the tool returns, write a short markdown bullet list summarizing the most relevant findings, with each bullet linking to the source. Stay grounded in what the search results actually say — do not editorialize about whether the underlying claim is true or false; downstream stages reason about that. The bot is for misinformation harm-reduction; surfacing accurate sources is the task.

Each bullet should read like:
- One factual sentence from the source. (link with the page title)

That's it — no preamble, no analysis paragraphs."""


def build_client():
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential

    endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
    project = AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential())
    return project.get_openai_client()


def describe_output_item(item, idx: int) -> dict:
    """Walk one item in response.output and capture its full shape."""
    rec: dict = {
        "idx": idx,
        "type": getattr(item, "type", None),
    }
    for attr in ("role", "status", "name", "id", "call_id", "action"):
        val = getattr(item, attr, None)
        if val is not None:
            rec[attr] = val if isinstance(val, str) else repr(val)[:200]

    content = getattr(item, "content", None) or []
    blocks = []
    for bi, block in enumerate(content):
        block_rec = {"idx": bi, "type": getattr(block, "type", None)}
        text = getattr(block, "text", None)
        if text:
            block_rec["text_chars"] = len(text)
            block_rec["text_head"] = text[:200]
            block_rec["text_tail"] = text[-200:] if len(text) > 200 else ""
        annotations = getattr(block, "annotations", None) or []
        block_rec["annotation_count"] = len(annotations)
        ann_descs = []
        for ai, ann in enumerate(annotations):
            adesc = {"idx": ai, "type": getattr(ann, "type", None)}
            for attr in ("url", "title", "start_index", "end_index"):
                val = getattr(ann, attr, None)
                if val is None and isinstance(ann, dict):
                    val = ann.get(attr)
                if val is not None:
                    adesc[attr] = val
            ann_descs.append(adesc)
        block_rec["annotations"] = ann_descs
        blocks.append(block_rec)
    rec["content"] = blocks
    return rec


def probe(client, model: str, query: str) -> None:
    print(f"\n{'=' * 78}")
    print(f"QUERY: {query!r}")
    print(f"{'=' * 78}")
    try:
        response = client.responses.create(
            model=model,
            instructions=_WEB_SEARCH_INSTRUCTIONS,
            input=query,
            tools=[{"type": "web_search"}],
            timeout=90,
        )
    except Exception as exc:
        print(f"!! request failed: {exc}")
        return

    output = getattr(response, "output", None) or []
    print(f"\n-- response.output has {len(output)} item(s) --")
    for idx, item in enumerate(output):
        desc = describe_output_item(item, idx)
        print(json.dumps(desc, indent=2, default=str))

    text = _extract_response_text(response)
    print(f"\n-- assistant text ({len(text)} chars) --")
    print(text[:1500])
    if len(text) > 1500:
        print(f"... ({len(text) - 1500} more chars)")

    md_links = list(_MARKDOWN_LINK_RE.finditer(text))
    print(f"\n-- markdown links in text: {len(md_links)} --")
    for m in md_links[:5]:
        print(f"  [{m.group(1)[:60]}]({m.group(2)})")

    hits = _extract_search_hits(response)
    print(f"\n-- _extract_search_hits returned {len(hits)} hit(s) --")
    for h in hits[:5]:
        print(f"  {h.url}  ann={h.is_annotation}  title={h.title[:60]!r}")


def main() -> None:
    model = os.environ.get("FOUNDRY_SEARCH_MODEL")
    endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
    if not (model and endpoint):
        sys.exit("FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_SEARCH_MODEL must be set")
    print(f"endpoint={endpoint}")
    print(f"model={model}")

    client = build_client()
    for q in QUERIES:
        probe(client, model, q)


if __name__ == "__main__":
    main()
