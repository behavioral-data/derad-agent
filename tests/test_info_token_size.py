"""Tests for the Azure Tables size guard in `_make_info_token`.

Tables enforces ~64 KiB per string property. Verifies that even when the
input `info_payload` is gigantic (counterpoints/perspectives well past the
cap), the `payload_json` ultimately written to Tables stays under `_LIMIT`.
"""

from __future__ import annotations

import json
import os
import threading

import pytest

os.environ.setdefault("X_API_KEY", "test_consumer_key")
os.environ.setdefault("X_API_SECRET", "test_consumer_secret_abc")
os.environ.setdefault("SERVER_NAME", "test.local")
os.environ.setdefault("BOT_USER_ID", "999")

from agent.app import app as app_module  # noqa: E402


_LIMIT = 60_000


class _CapturingTable:
    """Stand-in Azure Tables client that records the upserted entity."""

    def __init__(self):
        self.entity = None
        self.done = threading.Event()

    def upsert_entity(self, entity):
        self.entity = entity
        self.done.set()


def _huge_payload() -> dict:
    """An info_payload whose counterpoints + perspectives alone exceed _LIMIT
    even after `source_quality_table` is dropped entirely."""
    big_text = "x" * 5000
    return {
        "action": "verify",
        "action_outcome": "verified_refuted",
        "verdict_label": "Refuted",
        "headline_finding": "y" * 4000,
        "counterpoints": [{"text": big_text, "url": "https://example.com/cp"} for _ in range(20)],
        "perspectives": [{"text": big_text, "url": "https://example.com/p"} for _ in range(20)],
        "source_quality_table": [
            {"url": "https://example.com/s", "rationale": "z" * 1000} for _ in range(5)
        ],
    }


def test_oversized_payload_is_truncated_before_upsert(monkeypatch):
    table = _CapturingTable()
    monkeypatch.setattr(app_module, "_get_info_table", lambda: table)

    payload = _huge_payload()
    # Sanity: the raw payload is well over the limit.
    assert len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > _LIMIT

    token = app_module._make_info_token(
        "neutral",
        "Here are the facts.",
        payload,
        parent_id="PARENT_1",
        parent_author_username="claimant",
        bot_handle="eddiexbot",
        mention_id="MENTION_1",
        participant_id="AUTHOR_1",
    )
    assert token

    # Wait for the background persist thread.
    assert table.done.wait(timeout=5), "persist thread never completed"
    assert table.entity is not None

    persisted_json = table.entity["payload_json"]
    assert len(persisted_json.encode("utf-8")) <= _LIMIT
    # Minimal-shape fallback still renders verdict context.
    decoded = json.loads(persisted_json)
    assert decoded.get("action") == "verify"
    assert decoded.get("action_outcome") == "verified_refuted"
