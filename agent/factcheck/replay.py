# agent/factcheck/replay.py
"""Deterministic record/replay for the v0.7 loop client.

RecordingClient wraps the real Anthropic client and serializes every
response's content blocks; ReplayClient plays a cassette back as duck-typed
blocks. CI runs the loop against committed cassettes — no network, no keys."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace


def _serialize_block(b) -> dict:
    t = getattr(b, "type", None)
    if t == "text":
        return {"type": "text", "text": getattr(b, "text", "")}
    if t == "tool_use":
        return {"type": "tool_use", "id": getattr(b, "id", "t"),
                "name": getattr(b, "name", ""), "input": getattr(b, "input", {}) or {}}
    return {"type": str(t)}


def _deserialize_block(d: dict) -> SimpleNamespace:
    return SimpleNamespace(**d)


class RecordingClient:
    def __init__(self, inner):
        self._inner = inner
        self.records: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        response = self._inner.messages.create(**kw)
        self.records.append({
            "response_blocks": [_serialize_block(b)
                                for b in (getattr(response, "content", []) or [])],
        })
        return response

    def dump(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps({"records": self.records}, indent=1))


class ReplayClient:
    def __init__(self, path_or_records):
        if isinstance(path_or_records, (str, Path)):
            data = json.loads(Path(path_or_records).read_text())
            self._records = list(data["records"])
        else:
            self._records = list(path_or_records)
        self._i = 0
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):  # noqa: ARG002 — replay ignores the request
        if self._i >= len(self._records):
            raise IndexError(f"cassette exhausted after {self._i} calls")
        rec = self._records[self._i]
        self._i += 1
        blocks = [_deserialize_block(d) for d in rec["response_blocks"]]
        has_tool = any(getattr(b, "type", "") == "tool_use" for b in blocks)
        return SimpleNamespace(content=blocks,
                               stop_reason="tool_use" if has_tool else "end_turn")
