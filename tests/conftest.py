"""Shared fixtures for agent tests."""

import pytest


@pytest.fixture
def mock_user_dir(tmp_path):
    """A temporary directory pretending to be an index directory."""
    user_dir = tmp_path / "test_user"
    user_dir.mkdir()
    return user_dir


# ── mockx study interface fixtures ──────────────────────────────────────────
MOCKX_SELECTED_CSV = (
    "tweetId,text,created_at,polarity_condition,topic_condition\n"
    "t1,first post text,2026-06-04T14:22:00Z,negative,lgbt\n"
    "t1,first post text,2026-06-04T14:22:00Z,negative,race\n"   # dup tweetId, 2nd topic
    "t2,second post text,2026-06-04T10:05:00Z,positive,immigration\n"
)
MOCKX_NOTES_CSV = (
    "tweetId,noteId,classification,summary\n"
    "t1,n1,MISINFORMED_OR_POTENTIALLY_MISLEADING,Note for t1\n"
    "t2,n2,NOT_MISLEADING,Note for t2\n"
)


@pytest.fixture
def mockx_db(tmp_path):
    """Build a tiny study.db from inline fixtures; return its path."""
    from mockx.build_db import build
    sel = tmp_path / "selected.csv"
    sel.write_text(MOCKX_SELECTED_CSV)
    notes = tmp_path / "notes_selected.csv"
    notes.write_text(MOCKX_NOTES_CSV)
    out = tmp_path / "study.db"
    build(str(sel), str(notes), str(out))
    return str(out)
