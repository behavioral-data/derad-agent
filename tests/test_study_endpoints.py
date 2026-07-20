"""Integration: /api/session (assign + daily codes, no condition leak),
/api/exposure logging, and the Qualtrics iframe CSP — against a realistic
6-topic x 3-polarity x 6-post study DB."""
from __future__ import annotations

import csv

from study.interface.build_db import build
from study.interface.profiles import generate_profiles
from study.interface.server import create_app
from study.interface import db as dbmod
from study.interface import study_store
from study.interface.study_store import InMemoryStudyStore, reset_store, StoredProfile

TOPICS = ["healthcare", "immigration", "lgbt", "race", "religion", "cost"]
POLS = ["negative", "positive", "center"]


def _build_db(tmp_path):
    sel = tmp_path / "posts.csv"
    notes = tmp_path / "notes.csv"
    db = tmp_path / "study.db"
    with open(sel, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["tweetId", "text", "polarity_condition", "topic_condition", "created_at"])
        i = 0
        for t in TOPICS:
            for p in POLS:
                for k in range(6):
                    i += 1
                    w.writerow([f"{i:018d}", f"post {t} {p} {k}", p, t,
                                "2026-01-01T00:00:00.000Z"])
    with open(notes, "w", newline="") as f:
        csv.writer(f).writerow(["tweetId", "summary", "classification", "noteId"])
    build(str(sel), str(notes), str(db))
    return str(db)


def _load_pool(db):
    conn = dbmod.connect(db)
    try:
        cells = dbmod.cells(conn)
        profiles, orders = generate_profiles(
            cells, lambda p, c: dbmod.code_for(conn, p, c))
    finally:
        conn.close()
    store = InMemoryStudyStore()
    store.load_profiles(
        [StoredProfile(p.profile_id, p.condition, p.target_party, p.blocks) for p in profiles],
        orders)
    reset_store(store)


def test_session_claims_and_returns_daily_codes(tmp_path):
    db = _build_db(tmp_path)
    _load_pool(db)
    c = create_app(db_path=db).test_client()
    r = c.get("/api/session?pid=PROLIFIC1&party=Democrat&day=1")
    assert r.status_code == 200
    js = r.get_json()
    assert js["day"] == 1 and len(js["codes"]) == 12            # 12 posts/day now
    assert "condition" not in js and "post_id" not in js
    day1 = set(js["codes"])
    assert set(c.get("/api/session?pid=PROLIFIC1&party=Democrat&day=1").get_json()["codes"]) == day1
    day3 = set(c.get("/api/session?pid=PROLIFIC1&party=Democrat&day=3").get_json()["codes"])
    assert len(day3) == 12 and day3.isdisjoint(day1)
    assert "frame-ancestors" in r.headers.get("Content-Security-Policy", "")


def test_session_requires_party_and_valid_day(tmp_path):
    db = _build_db(tmp_path)
    _load_pool(db)
    c = create_app(db_path=db).test_client()
    assert c.get("/api/session?pid=P&day=1").status_code == 400          # missing party
    assert c.get("/api/session?pid=P&party=x&day=1").status_code == 400  # bad party
    assert c.get("/api/session?pid=P&party=D&day=9").status_code == 400  # day out of 1..3


def test_out_of_range_day_does_not_burn_a_profile(tmp_path):
    db = _build_db(tmp_path)
    _load_pool(db)
    c = create_app(db_path=db).test_client()
    pid = "FRESHOOR"

    r = c.get(f"/api/session?pid={pid}&party=Democrat&day=9")
    assert r.status_code == 400
    # The bad day must not have claimed a pool slot for this first-time pid.
    assert study_store.get_store().get_assignment(pid) is None

    # Retrying with a valid day succeeds as a fresh, ordinary claim.
    r2 = c.get(f"/api/session?pid={pid}&party=Democrat&day=1")
    assert r2.status_code == 200
    js = r2.get_json()
    assert js["day"] == 1 and len(js["codes"]) == 12
    assert study_store.get_store().get_assignment(pid) is not None


def test_exposure_logging(tmp_path):
    db = _build_db(tmp_path)
    _load_pool(db)
    c = create_app(db_path=db).test_client()
    code = c.get("/api/session?pid=PROLIFIC1&party=Democrat&day=1").get_json()["codes"][0]

    r = c.post("/api/exposure", json={"code": code, "pid": "PROLIFIC1", "day": 1, "dwell_ms": 4200})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    exps = study_store.get_store().exposures()
    assert len(exps) == 1 and exps[0].pid == "PROLIFIC1" and exps[0].dwell_ms == 4200
    assert exps[0].condition in ("neutral", "agreeable", "satirical", "control")

    # re-post (pagehide dwell) upserts the same row, not a new one
    c.post("/api/exposure", json={"code": code, "pid": "PROLIFIC1", "day": 1, "dwell_ms": 9000})
    exps = study_store.get_store().exposures()
    assert len(exps) == 1 and exps[0].dwell_ms == 9000

    assert c.post("/api/exposure", json={"code": "zzzzzzzzzzzz", "pid": "x"}).status_code == 404


def test_session_token_gate(tmp_path, monkeypatch):
    """When DERAD_SESSION_TOKEN is set, /api/session needs a matching ?token=."""
    db = _build_db(tmp_path)
    _load_pool(db)
    monkeypatch.setenv("DERAD_SESSION_TOKEN", "s3cret-token")
    c = create_app(db_path=db).test_client()
    # missing / wrong token -> 401 (and must not have claimed a slot)
    assert c.get("/api/session?pid=TOKPID&party=D&day=1").status_code == 401
    assert c.get("/api/session?pid=TOKPID&party=D&day=1&token=nope").status_code == 401
    assert study_store.get_store().get_assignment("TOKPID") is None
    # correct token -> 200, normal claim
    r = c.get("/api/session?pid=TOKPID&party=D&day=1&token=s3cret-token")
    assert r.status_code == 200 and len(r.get_json()["codes"]) == 12


def test_session_no_token_required_when_env_unset(tmp_path, monkeypatch):
    """Unset DERAD_SESSION_TOKEN -> dev mode, no token check (regression)."""
    db = _build_db(tmp_path)
    _load_pool(db)
    monkeypatch.delenv("DERAD_SESSION_TOKEN", raising=False)
    c = create_app(db_path=db).test_client()
    assert c.get("/api/session?pid=NOTOKPID&party=D&day=1").status_code == 200


def test_session_rejects_malformed_pid(tmp_path, monkeypatch):
    """Bad pid shape -> 400 before any store call (no slot burned)."""
    db = _build_db(tmp_path)
    _load_pool(db)
    monkeypatch.delenv("DERAD_SESSION_TOKEN", raising=False)
    c = create_app(db_path=db).test_client()
    # single quote (OData-injection shape) -> 400
    assert c.get("/api/session?pid=bad'pid&party=D&day=1").status_code == 400
    # 70-char pid exceeds the 64-char cap -> 400, nothing claimed
    long_pid = "P" * 70
    assert c.get(f"/api/session?pid={long_pid}&party=D&day=1").status_code == 400
    assert study_store.get_store().get_assignment(long_pid) is None


def test_browse_gate(tmp_path, monkeypatch):
    """/browse is 404 unless DERAD_ENABLE_BROWSE is truthy."""
    db = _build_db(tmp_path)
    _load_pool(db)
    monkeypatch.delenv("DERAD_ENABLE_BROWSE", raising=False)
    assert create_app(db_path=db).test_client().get("/browse").status_code == 404
    monkeypatch.setenv("DERAD_ENABLE_BROWSE", "1")
    r = create_app(db_path=db).test_client().get("/browse")
    assert r.status_code == 200 and b"Mock-X study" in r.data
