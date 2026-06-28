"""Tests for mockx.extract_notes — community-note selection + extraction."""
from __future__ import annotations

import csv

from mockx.extract_notes import extract, select_shown_note


def test_prefers_currently_rated_helpful():
    cands = [
        {"noteId": "a", "summary": "A", "classification": "X",
         "status": "NEEDS_MORE_RATINGS", "ts": 100},
        {"noteId": "b", "summary": "B", "classification": "X",
         "status": "CURRENTLY_RATED_HELPFUL", "ts": 50},
    ]
    assert select_shown_note(cands)["noteId"] == "b"


def test_breaks_ties_by_most_recent():
    cands = [
        {"noteId": "old", "summary": "", "classification": "",
         "status": "CURRENTLY_RATED_HELPFUL", "ts": 10},
        {"noteId": "new", "summary": "", "classification": "",
         "status": "CURRENTLY_RATED_HELPFUL", "ts": 99},
    ]
    assert select_shown_note(cands)["noteId"] == "new"


def test_returns_none_when_empty():
    assert select_shown_note([]) is None


def test_extract_joins_and_writes_one_row_per_post(tmp_path):
    sel = tmp_path / "selected.csv"
    sel.write_text("tweetId,text\nt1,hello\nt2,world\n")

    notes = tmp_path / "notes.tsv"
    notes.write_text(
        "noteId\ttweetId\tclassification\tsummary\n"
        "n1\tt1\tMISINFORMED_OR_POTENTIALLY_MISLEADING\told note\n"
        "n2\tt1\tMISINFORMED_OR_POTENTIALLY_MISLEADING\tshown note\n"
        "n3\tt2\tNOT_MISLEADING\tt2 note\n"
        "n9\tt999\tX\tirrelevant\n"
    )

    status = tmp_path / "status.tsv"
    status.write_text(
        "noteId\tx\ty\tz\tw\ttimestampMillisOfCurrentStatus\tcurrentStatus\n"
        "n1\t.\t.\t.\t.\t10\tCURRENTLY_RATED_HELPFUL\n"
        "n2\t.\t.\t.\t.\t20\tCURRENTLY_RATED_HELPFUL\n"
        "n3\t.\t.\t.\t.\t5\tCURRENTLY_RATED_HELPFUL\n"
    )

    out = tmp_path / "notes_selected.csv"
    n = extract(str(sel), str(notes), str(status), str(out))
    assert n == 2

    rows = {r["tweetId"]: r for r in csv.DictReader(out.open())}
    assert rows["t1"]["noteId"] == "n2"          # most-recent CRH
    assert rows["t1"]["summary"] == "shown note"
    assert rows["t2"]["noteId"] == "n3"
    assert set(rows["t1"].keys()) == {"tweetId", "noteId", "classification", "summary"}
