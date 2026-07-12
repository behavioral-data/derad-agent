# tests/test_v07_snapshot.py
from datetime import datetime, timezone
from unittest import mock

import requests

from agent.factcheck import snapshot
from agent.factcheck.search import FetchedPage


class _CdxResp:
    status_code = 200
    def json(self):
        return [["timestamp", "original"], ["20260421080000", "https://news.test/gas"]]
    def raise_for_status(self):
        pass


def test_snapshot_lookup_builds_id_url():
    with mock.patch("requests.get", return_value=_CdxResp()):
        url = snapshot.snapshot_lookup(
            "https://news.test/gas", datetime(2026, 4, 23, tzinfo=timezone.utc))
    assert url == "https://web.archive.org/web/20260421080000id_/https://news.test/gas"


def test_fetch_snapshot_none_when_no_capture():
    class _Empty(_CdxResp):
        def json(self):
            return [["timestamp", "original"]]
    with mock.patch("requests.get", return_value=_Empty()):
        page = snapshot.fetch_snapshot(
            "https://news.test/gas", datetime(2026, 4, 23, tzinfo=timezone.utc))
    assert page is None


def test_fetch_snapshot_reports_original_url():
    fetched = FetchedPage(200, "https://web.archive.org/web/20260421080000id_/https://news.test/gas",
                          "Gas prices fall", "body", "2026-04-21")
    with mock.patch("requests.get", return_value=_CdxResp()), \
         mock.patch("agent.factcheck.snapshot._fetch_clean_page", return_value=fetched):
        page = snapshot.fetch_snapshot(
            "https://news.test/gas", datetime(2026, 4, 23, tzinfo=timezone.utc))
    assert page.final_url == "https://news.test/gas"
    assert page.body_markdown == "body"


def test_snapshot_lookup_sends_spec_params():
    with mock.patch("requests.get", return_value=_CdxResp()) as m:
        snapshot.snapshot_lookup(
            "https://news.test/gas", datetime(2026, 4, 23, tzinfo=timezone.utc))
    args, kwargs = m.call_args
    assert args[0] == "https://web.archive.org/cdx/search/cdx"
    assert kwargs["params"] == {
        "url": "https://news.test/gas",
        "to": "20260423000000",
        "limit": "-1",
        "output": "json",
        "filter": "statuscode:200",
        "fl": "timestamp,original",
    }
    assert kwargs["timeout"] == 6.0   # fail-fast lookup default


def test_snapshot_lookup_none_on_network_error():
    with mock.patch("requests.get", side_effect=requests.RequestException("boom")):
        url = snapshot.snapshot_lookup(
            "https://news.test/gas", datetime(2026, 4, 23, tzinfo=timezone.utc))
    assert url is None


def test_cdx_circuit_breaker_trips_and_skips(monkeypatch):
    import requests as _requests
    from agent.factcheck import snapshot as snap
    # reset breaker state
    monkeypatch.setattr(snap, "_cdx_consecutive_failures", 0)
    monkeypatch.setattr(snap, "_cdx_disabled_until", 0.0)
    calls = {"n": 0}

    def _boom(*a, **kw):
        calls["n"] += 1
        raise _requests.ReadTimeout("slow")

    with mock.patch("requests.get", side_effect=_boom):
        for _ in range(3):
            assert snap.snapshot_lookup(
                "https://news.test/x", datetime(2026, 4, 23, tzinfo=timezone.utc)) is None
        # breaker tripped: 4th call returns None WITHOUT a network attempt
        assert snap.snapshot_lookup(
            "https://news.test/x", datetime(2026, 4, 23, tzinfo=timezone.utc)) is None
    assert calls["n"] == 3
    assert snap._cdx_disabled_until > 0


def test_cdx_success_resets_breaker(monkeypatch):
    from agent.factcheck import snapshot as snap
    monkeypatch.setattr(snap, "_cdx_consecutive_failures", 2)
    monkeypatch.setattr(snap, "_cdx_disabled_until", 0.0)
    with mock.patch("requests.get", return_value=_CdxResp()):
        url = snap.snapshot_lookup(
            "https://news.test/gas", datetime(2026, 4, 23, tzinfo=timezone.utc))
    assert url is not None
    assert snap._cdx_consecutive_failures == 0
