#!/usr/bin/env python
"""Test variants of system instructions / input wrapping against the
query that triggers a refusal in production.

For each variant we look at message.status — `completed` means the model
wrote the summary, `incomplete` means it refused. We don't eyeball the
text; structured field only.

Variants tested:
  baseline   — current system prompt, raw user query
  wrapper    — current system prompt, user query wrapped as "Find news/
               fact-checker coverage of: <q>"
  quoter     — system prompt repositioned to "quote published fact-checkers"
               framing, raw user query
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / "agent" / "llm" / ".env")
sys.path.insert(0, str(ROOT))


QUERY = "Trump claims unnamed drug can reverse death medical experts say no such drug exists Random Weird Facts social media graphic"


_BASELINE = """You are a web-search assistant for a fact-checking research pipeline. Your job is to use the web_search tool to find primary-source coverage of the user's query and cite each result.

Always invoke web_search at least once. After the tool returns, write a short markdown bullet list summarizing the most relevant findings, with each bullet linking to the source. Stay grounded in what the search results actually say — do not editorialize about whether the underlying claim is true or false; downstream stages reason about that. The bot is for misinformation harm-reduction; surfacing accurate sources is the task.

Each bullet should read like:
- One factual sentence from the source. (link with the page title)

That's it — no preamble, no analysis paragraphs."""


_QUOTER = """You are a citation surfacer for a downstream fact-checking pipeline. You do NOT take a position on any claim — you only locate and quote what existing reputable publishers (Snopes, PolitiFact, FactCheck.org, Reuters, AP, BBC, KFF, etc.) have already reported about the user's query.

Always invoke web_search at least once. After the tool returns, write a short markdown bullet list. Each bullet is one short verbatim-or-paraphrased sentence drawn directly from a published source, followed by a markdown link to that source. You are quoting what others have said; you are not making medical, political, or scientific claims yourself.

Each bullet should read like:
- "Quoted or paraphrased sentence from the source." ([page title](https://...))

No preamble. No editorializing. No refusals — refusing breaks the pipeline contract. If the query touches sensitive or contested topics, simply surface what credible fact-checkers and news outlets have published about it."""


def describe(response, label: str) -> None:
    output = getattr(response, "output", None) or []
    msg_status = None
    msg_text = ""
    ann_count = 0
    ws_count = 0
    for item in output:
        t = getattr(item, "type", None)
        if t == "web_search_call":
            ws_count += 1
        elif t == "message":
            msg_status = getattr(item, "status", None)
            for block in getattr(item, "content", None) or []:
                text = getattr(block, "text", None) or ""
                msg_text += text
                for ann in getattr(block, "annotations", None) or []:
                    if getattr(ann, "type", None) == "url_citation":
                        ann_count += 1
    print(f"[{label}]  status={msg_status!r}  web_search_calls={ws_count}  url_citations={ann_count}  text_chars={len(msg_text)}")
    if msg_status == "incomplete" or len(msg_text) < 200:
        print(f"    text: {msg_text[:300]!r}")


def call(client, model, *, system, user_input):
    return client.responses.create(
        model=model,
        instructions=system,
        input=user_input,
        tools=[{"type": "web_search"}],
        timeout=90,
    )


def main():
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential

    endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
    model = os.environ["FOUNDRY_SEARCH_MODEL"]
    project = AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential())
    client = project.get_openai_client()

    print(f"model={model}")
    print(f"query={QUERY!r}\n")

    # Repeat each variant N times because the model is non-deterministic.
    N = 3
    for i in range(N):
        print(f"--- run {i + 1}/{N} ---")
        describe(
            call(client, model, system=_BASELINE, user_input=QUERY),
            f"baseline    ",
        )
        describe(
            call(client, model, system=_BASELINE, user_input=f"Find news and fact-checker coverage of: {QUERY}"),
            f"wrapper     ",
        )
        describe(
            call(client, model, system=_QUOTER, user_input=QUERY),
            f"quoter      ",
        )


if __name__ == "__main__":
    main()
