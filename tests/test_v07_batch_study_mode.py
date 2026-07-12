import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from study.scripts.batch_generate_replies import load_posts, study_window


def _write_csv(tmp_path: Path) -> Path:
    p = tmp_path / "posts.csv"
    with p.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["tweetId", "text", "created_at"])
        w.writeheader()
        w.writerow({"tweetId": "1", "text": "hello",
                    "created_at": "2026-04-21T13:41:12.000Z"})
    return p


def test_load_posts_carries_created_at(tmp_path):
    posts = load_posts(_write_csv(tmp_path))
    assert posts[0].created_at == "2026-04-21T13:41:12.000Z"


def test_study_window_computes_cutoff():
    as_of, cutoff = study_window("2026-04-21T13:41:12.000Z")
    assert as_of == datetime(2026, 4, 21, 13, 41, 12, tzinfo=timezone.utc)
    assert cutoff - as_of == timedelta(hours=48)


def test_study_window_rejects_missing():
    import pytest
    with pytest.raises(ValueError):
        study_window("")
