"""Integration: /api/session (assign + daily codes, no condition leak),
/api/exposure logging, and the Qualtrics iframe CSP — against a realistic
6-topic x 3-polarity x 6-post study DB."""
from __future__ import annotations

import csv

from study.interface.build_db import build
from study.interface.server import create_app
from study.interface import study_store
from study.interface.study_store import InMemoryStudyStore, reset_store

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


def test_session_returns_daily_codes_without_leaking_condition(tmp_path):
    db = _build_db(tmp_path)
    reset_store(InMemoryStudyStore())
    c = create_app(db_path=db).test_client()

    r = c.get("/api/session?pid=PROLIFIC1&day=1")
    assert r.status_code == 200
    js = r.get_json()
    assert js["day"] == 1 and len(js["codes"]) == 9
    assert "condition" not in js and "post_id" not in js      # nothing readable leaks
    day1 = set(js["codes"])

    # idempotent for the same participant/day
    assert set(c.get("/api/session?pid=PROLIFIC1&day=1").get_json()["codes"]) == day1
    # a different day is a different, disjoint block of 9
    day2 = set(c.get("/api/session?pid=PROLIFIC1&day=2").get_json()["codes"])
    assert len(day2) == 9 and day2.isdisjoint(day1)

    # embeddable in Qualtrics
    csp = r.headers.get("Content-Security-Policy", "")
    assert "frame-ancestors" in csp and "qualtrics.com" in csp


def test_session_validates_input(tmp_path):
    db = _build_db(tmp_path)
    reset_store(InMemoryStudyStore())
    c = create_app(db_path=db).test_client()
    assert c.get("/api/session?pid=&day=1").status_code == 400
    assert c.get("/api/session?pid=P&day=x").status_code == 400
    assert c.get("/api/session?pid=P&day=99").status_code == 400   # out of 1..6


def test_exposure_logging(tmp_path):
    db = _build_db(tmp_path)
    reset_store(InMemoryStudyStore())
    c = create_app(db_path=db).test_client()
    code = c.get("/api/session?pid=PROLIFIC1&day=1").get_json()["codes"][0]

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
