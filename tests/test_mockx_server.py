"""Tests for the mockx Flask app."""
from __future__ import annotations

import pytest

from study.interface.server import create_app


@pytest.fixture
def client(mockx_db, monkeypatch):
    # These tests exercise the legacy ?post_id=&condition= dev/QA form of
    # /api/thread, which is gated behind DERAD_ENABLE_BROWSE (same flag as
    # /browse) so it can't serve any condition for a known post_id in prod.
    monkeypatch.setenv("DERAD_ENABLE_BROWSE", "1")
    app = create_app(db_path=mockx_db)
    app.config.update(TESTING=True)
    return app.test_client()


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200


def test_api_thread_ok(client):
    r = client.get("/api/thread?post_id=t1&condition=neutral")
    assert r.status_code == 200
    data = r.get_json()
    assert data["post"]["post_id"] == "t1"
    assert data["intervention"]["bot_handle"] == "eddiexbot"


def test_api_thread_control(client):
    r = client.get("/api/thread?post_id=t1&condition=control")
    assert r.status_code == 200
    assert r.get_json()["intervention"]["kind"] == "community_note"


def test_api_thread_bad_condition_400(client):
    r = client.get("/api/thread?post_id=t1&condition=bogus")
    assert r.status_code == 400


def test_api_thread_missing_post_404(client):
    r = client.get("/api/thread?post_id=nope&condition=neutral")
    assert r.status_code == 404


def test_api_thread_legacy_form_404_when_browse_disabled(mockx_db, monkeypatch):
    """The legacy ?post_id=&condition= form is gated: 404 when DERAD_ENABLE_BROWSE is unset."""
    monkeypatch.delenv("DERAD_ENABLE_BROWSE", raising=False)
    c = create_app(db_path=mockx_db).test_client()
    assert c.get("/api/thread?post_id=t1&condition=neutral").status_code == 404


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert b"<title>" in r.data


def test_api_thread_strips_design_fields(client):
    """Design-metadata fields must be absent; functional fields must be present."""
    r = client.get("/api/thread?post_id=t1&condition=neutral")
    assert r.status_code == 200
    data = r.get_json()
    # Fields that must NOT appear in the response
    for field in ("condition", "is_stub", "note_classification", "source_note_id"):
        assert field not in data["intervention"], f"intervention.{field} leaked"
    for field in ("polarity_condition", "topic_condition"):
        assert field not in data["post"], f"post.{field} leaked"
    # Functional fields that must still be present
    assert data["intervention"]["kind"] in ("bot_reply", "community_note")
    assert data["intervention"]["bot_handle"] == "eddiexbot"


def test_api_thread_control_strips_design_fields(client):
    """Control condition (community note) also strips design fields."""
    r = client.get("/api/thread?post_id=t1&condition=control")
    assert r.status_code == 200
    data = r.get_json()
    for field in ("condition", "is_stub", "note_classification", "source_note_id"):
        assert field not in data["intervention"], f"intervention.{field} leaked"
