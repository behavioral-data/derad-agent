"""Thin Claude wrapper for the factcheck pipeline.

Reuses `agent.llm.config.get_llm` (Claude on Azure AI Services). The helper
here adds JSON-mode prompting so each stage gets a typed result back as a
Pydantic model.
"""
from __future__ import annotations

import json
import re
from typing import Type, TypeVar

from pydantic import BaseModel, ValidationError

from agent.llm.config import get_llm


T = TypeVar("T", bound=BaseModel)


_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def pruned_context(ctx: dict | None) -> dict | None:
    """Drop None/empty values from a tweet_context payload so the prompt
    doesn't carry noise. Returns None when nothing remains."""
    if not ctx:
        return None
    clean = {k: v for k, v in ctx.items() if v not in (None, "", [], {})}
    return clean or None


def _extract_json(text: str) -> str:
    """Pull a JSON object out of an LLM response (handles fenced + raw)."""
    match = _JSON_BLOCK_RE.search(text)
    if match:
        return match.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text.strip()


def call_claude_json(
    prompt: str,
    *,
    schema: Type[T],
    system: str = "",
    reasoning_effort: str | None = "medium",
    max_tokens: int = 4096,
    timeout: float = 120.0,
) -> T:
    """Call Claude expecting a JSON object that parses into `schema`.

    `timeout` is the per-request HTTP wall-clock cap. Stages override to
    appropriate values (extract ~30s, reconcile ~90s) so one slow call
    can't burn an entire pipeline-semaphore slot for the full 120s.

    Raises ValueError on parse/validation failure — callers should treat that
    as an audit failure (Stage 5 forces NEI on any structural problem).
    """
    schema_json = json.dumps(schema.model_json_schema(), indent=2)
    full_system = (
        (system + "\n\n" if system else "")
        + "Respond with a single JSON object that validates against this JSON Schema. "
        + "Do not add prose before or after the JSON.\n\n"
        + f"Schema:\n```json\n{schema_json}\n```"
    )

    llm = get_llm(reasoning_effort=reasoning_effort, max_tokens=max_tokens, timeout=timeout)
    response = llm.invoke(
        [
            {"role": "system", "content": full_system},
            {"role": "user", "content": prompt},
        ]
    )
    content = getattr(response, "content", str(response))
    if isinstance(content, list):
        # langchain_anthropic can return a list of blocks when thinking is on;
        # pick the first text block.
        text_parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        content = "\n".join(text_parts) if text_parts else json.dumps(content)
    raw_json = _extract_json(content)
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as primary_exc:
        # Common LLM failure: unescaped inner quotes, trailing commas, leftover
        # code-fence text. json-repair fixes most of these heuristically without
        # an extra Claude round-trip.
        import logging
        try:
            from json_repair import repair_json
            repaired = repair_json(content)
            data = json.loads(repaired)
            logging.getLogger(__name__).info(
                "call_claude_json: json-repair recovered output (%s)", primary_exc,
            )
        except Exception:
            raise ValueError(
                f"Claude did not return valid JSON (json-repair also failed): {primary_exc}\n---\n{content[:1000]}"
            ) from primary_exc
    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"Claude output failed schema validation: {exc}\n---\n{raw_json[:1000]}") from exc
