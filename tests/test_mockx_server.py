"""Tests for the mockx Flask app."""
from __future__ import annotations

import pytest

from study.interface.server import create_app


@pytest.fixture
def client(mockx_db):
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
