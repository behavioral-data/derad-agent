"""Tests for the mockx Flask app."""
from __future__ import annotations

import pytest

from mockx.server import create_app


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
    assert b"Mock X" in r.data
