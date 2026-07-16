# tests/test_generate_profiles_cli.py
from __future__ import annotations
import csv, json, importlib.util, pathlib
from study.interface.build_db import build

_MOD = pathlib.Path(__file__).resolve().parent.parent / "study" / "scripts" / "generate_profiles.py"
_spec = importlib.util.spec_from_file_location("generate_profiles", _MOD)
genmod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(genmod)

TOPICS = ["healthcare", "immigration", "lgbt", "race", "religion", "cost"]
POLS = ["negative", "positive", "center"]


def _build_db(tmp_path):
    sel, notes, db = tmp_path / "posts.csv", tmp_path / "notes.csv", tmp_path / "study.db"
    with open(sel, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["tweetId", "text", "polarity_condition", "topic_condition", "created_at"])
        i = 0
        for t in TOPICS:
            for p in POLS:
                for k in range(6):
                    i += 1
                    w.writerow([f"{i:018d}", f"post {t} {p} {k}", p, t, "2026-01-01T00:00:00.000Z"])
    with open(notes, "w", newline="") as f:
        csv.writer(f).writerow(["tweetId", "summary", "classification", "noteId"])
    build(str(sel), str(notes), str(db))
    return str(db)


def test_cli_writes_balanced_artifacts(tmp_path):
    db = _build_db(tmp_path)
    outdir = tmp_path / "profiles"
    genmod.run(db_path=db, out_dir=str(outdir))
    data = json.loads((outdir / "profiles.json").read_text())
    assert data["seed"] == 20260716
    assert len(data["profiles"]) == 456
    assert len(data["claim_orders"]["D"]) == 228 and len(data["claim_orders"]["R"]) == 228
    # every access code resolves in the DB (i.e. codes are real)
    from study.interface import db as dbmod
    conn = dbmod.connect(db)
    try:
        sample = data["profiles"][0]
        assert dbmod.resolve_code(conn, sample["access_codes"][0]) is not None
    finally:
        conn.close()
    # csv row count == 456 * 36
    with open(outdir / "profiles.csv") as f:
        assert sum(1 for _ in csv.DictReader(f)) == 456 * 36
    assert "OK" in (outdir / "profiles_report.md").read_text()
