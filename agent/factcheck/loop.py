# agent/factcheck/loop.py
"""v0.7 bounded agentic loop — the evidence core that was actually validated.

One strong model + three tools (server web_search, client fetch_page, client
finalize) executes the versioned playbook. Hard bounds: max_turns, wall clock.
The raw message history is returned so the verifier's single revision round
can continue the same conversation."""
from __future__ import annotations

import functools
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

from pydantic import ValidationError

from .context import PipelineContext
from .draft import DraftVerdict
from .llm import pruned_context
from .loop_tools import ToolRuntime
from .prompt_store import load_prompt

logger = logging.getLogger(__name__)

_FORCED_FINALIZE = ("Budget exhausted — call the finalize tool NOW with your "
                    "best draft from the evidence you already have.")


@dataclass
class LoopStats:
    turns: int = 0
    tool_calls: int = 0
    finalized: bool = False
    hit_turn_cap: bool = False
    hit_wall_clock: bool = False


@functools.lru_cache(maxsize=1)
def build_loop_client():
    """Real AnthropicFoundry client + chat deployment name from env."""
    from anthropic import AnthropicFoundry
    endpoint = os.environ["AZURE_CLAUDE_ENDPOINT"]
    api_key = os.environ["AZURE_CLAUDE_API_KEY"]
    model = os.environ.get("AZURE_CLAUDE_DEPLOYMENT_CHAT", "claude-sonnet")
    resource = (urlparse(endpoint).hostname or "").split(".", 1)[0]
    return AnthropicFoundry(api_key=api_key, resource=resource), model


def _tools():
    return [
        {"type": "web_search_20250305", "name": "web_search", "max_uses": 12},
        {
            "name": "fetch_page",
            "description": ("Fetch a URL and return its extracted article body "
                            "(untrusted data) plus published_date. Use for any page "
                            "you need to actually read, including the post's own links."),
            "input_schema": {"type": "object",
                             "properties": {"url": {"type": "string"}},
                             "required": ["url"]},
        },
        {
            "name": "finalize",
            "description": "Submit the structured verdict. Call exactly once, when done.",
            "input_schema": DraftVerdict.model_json_schema(),
        },
    ]


def _initial_user_message(post_text: str, ctx: PipelineContext,
                          as_of: Optional[datetime], cutoff: Optional[datetime]) -> str:
    parts = [f"POST (the content to fact-check):\n{post_text}\n"]
    parts.append(f"POST_DATE: {as_of.isoformat() if as_of else 'unknown'}")
    tc = pruned_context(ctx.tweet_context)
    if tc:
        parts.append("AUTHOR/TWEET CONTEXT:\n" + json.dumps(tc, indent=1, default=str))
        urls = [u.get("expanded_url") for u in (tc.get("expanded_urls") or [])
                if isinstance(u, dict) and u.get("expanded_url")]
        if urls:
            parts.append("The post links to: " + ", ".join(urls) +
                         "\nFetch the post's linked article FIRST (it is often the claim's source).")
    if ctx.image_summaries:
        parts.append("ATTACHED IMAGES:\n" + json.dumps(ctx.image_summaries, indent=1))
    if cutoff is not None:
        parts.append(f"STUDY MODE: evidence cutoff = {cutoff.isoformat()}. Only cite "
                     "sources published on/before the cutoff; your reply must read as "
                     "written within hours of the post.")
    return "\n\n".join(parts)


def _record_server_search(runtime: ToolRuntime, block) -> None:
    content = getattr(block, "content", None)
    if not isinstance(content, list):
        return
    results = []
    for r in content:
        if getattr(r, "type", None) == "web_search_result":
            results.append({"url": getattr(r, "url", ""),
                            "title": getattr(r, "title", "") or "",
                            "snippet": ""})
    if results:
        runtime.record_search_results("web_search", results)


def _drive(messages: list, *, client, runtime: ToolRuntime, model: str,
           system: str, max_turns: int, wall_clock_s: float) -> tuple[Optional[DraftVerdict], LoopStats]:
    stats = LoopStats()
    start = time.monotonic()
    forced = False
    while True:
        if stats.turns >= max_turns or (time.monotonic() - start) > wall_clock_s:
            if stats.turns >= max_turns:
                stats.hit_turn_cap = True
            else:
                stats.hit_wall_clock = True
            if forced:
                return None, stats
            forced = True
            messages.append({"role": "user", "content": _FORCED_FINALIZE})
        response = client.messages.create(
            model=model, max_tokens=8192, system=system,
            messages=messages, tools=_tools(),
        )
        stats.turns += 1
        blocks = list(getattr(response, "content", []) or [])
        assistant_content = []
        tool_results = []
        draft: Optional[DraftVerdict] = None
        for block in blocks:
            btype = getattr(block, "type", None)
            if btype == "web_search_tool_result":
                _record_server_search(runtime, block)
            if btype != "tool_use":
                continue
            stats.tool_calls += 1
            name = getattr(block, "name", "")
            tool_id = getattr(block, "id", "t")
            if name == "fetch_page":
                url = (getattr(block, "input", {}) or {}).get("url", "")
                out = runtime.fetch_page(url)
                tool_results.append({"type": "tool_result", "tool_use_id": tool_id,
                                     "content": out})
            elif name == "finalize":
                try:
                    draft = DraftVerdict.model_validate(getattr(block, "input", {}) or {})
                except ValidationError as exc:
                    tool_results.append({"type": "tool_result", "tool_use_id": tool_id,
                                         "is_error": True,
                                         "content": f"finalize rejected: {exc}"[:2000]})
                    draft = None
        # Serialize the assistant turn back into history (text + tool_use raw)
        messages.append({"role": "assistant", "content": [
            _block_to_dict(b) for b in blocks if getattr(b, "type", None) in ("text", "tool_use")
        ] or [{"type": "text", "text": ""}]})
        if draft is not None:
            stats.finalized = True
            return draft, stats
        if tool_results:
            messages.append({"role": "user", "content": tool_results})
        elif getattr(response, "stop_reason", "") == "end_turn" and not forced:
            messages.append({"role": "user", "content":
                             "Continue the playbook. When done, call finalize."})


def _block_to_dict(b) -> dict:
    if getattr(b, "type", None) == "text":
        return {"type": "text", "text": getattr(b, "text", "")}
    return {"type": "tool_use", "id": getattr(b, "id", "t"),
            "name": getattr(b, "name", ""), "input": getattr(b, "input", {}) or {}}


def run_loop(
    post_text: str, *, client, ctx: PipelineContext,
    as_of: Optional[datetime] = None, cutoff: Optional[datetime] = None,
    runtime: Optional[ToolRuntime] = None,
    max_turns: int = 24, wall_clock_s: float = 480.0,
    model: Optional[str] = None,
) -> tuple[Optional[DraftVerdict], ToolRuntime, LoopStats, list]:
    runtime = runtime or ToolRuntime(cutoff=cutoff)
    if model is None:
        client_built, model = build_loop_client() if client is None else (client, "claude-sonnet")
        client = client or client_built
    system = load_prompt("loop_playbook")
    messages = [{"role": "user",
                 "content": _initial_user_message(post_text, ctx, as_of, cutoff)}]
    draft, stats = _drive(messages, client=client, runtime=runtime, model=model,
                          system=system, max_turns=max_turns, wall_clock_s=wall_clock_s)
    return draft, runtime, stats, messages


def revise_in_loop(
    messages: list, revision_instructions: str, *, client, runtime: ToolRuntime,
    model: Optional[str] = None, max_turns: int = 6, wall_clock_s: float = 180.0,
) -> tuple[Optional[DraftVerdict], LoopStats]:
    if model is None:
        _, model = build_loop_client()
    messages.append({"role": "user", "content":
                     ("REVISION REQUIRED by the independent verifier. Address every "
                      "point, then call finalize once more:\n" + revision_instructions)})
    system = load_prompt("loop_playbook")
    return _drive(messages, client=client, runtime=runtime, model=model,
                  system=system, max_turns=max_turns, wall_clock_s=wall_clock_s)
