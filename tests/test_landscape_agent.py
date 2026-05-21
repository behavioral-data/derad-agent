import json

import numpy as np

from derad_agent.runtime.landscape_agent import run_landscape_agent
from derad_agent.runtime.landscape_api import (
    get_notes_index_dir,
    retrieve_statement_landscape,
)
from derad_agent.runtime import notes_index as notes_index_module


# ── Helpers ───────────────────────────────────────────────────────────


def _write_notes_index(index_dir, tweet_specs):
    """``tweet_specs``: list of ``(tweet_id, embedding_vec, [note_dicts])``."""
    index_dir.mkdir(parents=True, exist_ok=True)
    tweet_ids = [str(t[0]) for t in tweet_specs]
    embeddings = np.array([t[1] for t in tweet_specs], dtype="float32")
    np.save(index_dir / "tweet_ids.npy", np.array(tweet_ids, dtype=object), allow_pickle=True)
    np.save(index_dir / "embeddings.npy", embeddings)
    notes_cache = {str(t[0]): t[2] for t in tweet_specs}
    (index_dir / "notes_cache.json").write_text(json.dumps(notes_cache), encoding="utf-8")


class _FakeEmbedder:
    def __init__(self, query_vec):
        self.query_vec = list(query_vec)

    def embed_query(self, _query):
        return list(self.query_vec)

    def embed_documents(self, texts):
        return [list(self.query_vec) for _ in texts]


def _note(note_id, current_status, summary, ts, classification="MISINFORMED_OR_POTENTIALLY_MISLEADING"):
    return {
        "note_id": note_id,
        "summary": summary,
        "classification": classification,
        "current_status": current_status,
        "created_at_millis": ts,
    }


# ── Tests ─────────────────────────────────────────────────────────────


def test_select_caps_by_per_tweet_and_preserves_order(tmp_path):
    # Index contains only helpful notes, pre-sorted most-recent first (as embed_notes produces).
    # select_recent_helpful_notes should slice to per_tweet and attach similarity.
    index_dir = tmp_path / "notes_index"
    notes = [
        _note("b", "CURRENTLY_RATED_HELPFUL", "newest helpful", 100),
        _note("c", "CURRENTLY_RATED_HELPFUL", "mid helpful", 50),
        _note("a", "CURRENTLY_RATED_HELPFUL", "old helpful", 1),
    ]
    _write_notes_index(index_dir, [("t1", [1.0, 0.0], notes)])
    index = notes_index_module.load_notes_index(index_dir)

    selected = notes_index_module.select_recent_helpful_notes(
        retrieved=[("t1", 0.9)],
        index=index,
        per_tweet=2,
    )
    assert [n["note_id"] for n in selected] == ["b", "c"]
    assert all(n["similarity"] == 0.9 for n in selected)


def test_retrieve_tweets_orders_by_similarity(tmp_path):
    index_dir = tmp_path / "notes_index"
    _write_notes_index(
        index_dir,
        [
            ("t_far", [-1.0, 0.0], []),
            ("t_close", [1.0, 0.0], []),
            ("t_mid", [0.5, 0.5], []),
        ],
    )
    index = notes_index_module.load_notes_index(index_dir)
    hits = notes_index_module.retrieve_tweets(
        query="anything",
        embedder=_FakeEmbedder([1.0, 0.0]),
        index=index,
        k=3,
        similarity_min=-1.0,
    )
    assert [tid for tid, _ in hits] == ["t_close", "t_mid", "t_far"]


def test_run_landscape_agent_end_to_end(monkeypatch, tmp_path):
    index_dir = tmp_path / "notes_index"
    _write_notes_index(
        index_dir,
        [
            (
                "t1",
                [1.0, 0.0],
                # Index contains only helpful notes, sorted most-recent first.
                [
                    _note("n2", "CURRENTLY_RATED_HELPFUL", "fact B", 200),
                    _note("n1", "CURRENTLY_RATED_HELPFUL", "fact A", 100),
                ],
            ),
            (
                "t2",
                [0.9, 0.1],
                [_note("n4", "CURRENTLY_RATED_HELPFUL", "fact C", 50)],
            ),
        ],
    )

    monkeypatch.setattr(
        "derad_agent.runtime.landscape_agent.get_embedder",
        lambda: _FakeEmbedder([1.0, 0.0]),
    )
    monkeypatch.setattr(
        "derad_agent.runtime.landscape_agent.step_1_generate_queries",
        lambda **kwargs: ["q1"],
    )

    captured = {}

    def _fake_compose(statement, notes, **kwargs):
        captured["statement"] = statement
        captured["notes"] = list(notes)
        captured["style"] = kwargs.get("style")
        return {
            "statement": statement,
            "response": "synthetic reply",
            "reasons": [{"reason": "x", "note_id": "n2", "tweet_id": "t1", "evidence_links": []}],
        }

    monkeypatch.setattr(
        "derad_agent.runtime.landscape_agent.step_compose_reply",
        _fake_compose,
    )

    result = run_landscape_agent(
        statement="The policy is effective.",
        notes_index_dir=index_dir,
        k_per_query=5,
        notes_per_tweet=10,
        style="agreeable",
        filter_notes=False,
    )

    assert result["statement"] == "The policy is effective."
    assert result["queries"] == ["q1"]
    assert {t["tweet_id"] for t in result["retrieved_tweets"]} == {"t1", "t2"}
    assert {n["note_id"] for n in result["selected_notes"]} == {"n1", "n2", "n4"}
    assert result["reply"]["response"] == "synthetic reply"

    assert captured["statement"] == "The policy is effective."
    assert captured["style"] == "agreeable"
    assert {n["note_id"] for n in captured["notes"]} == {"n1", "n2", "n4"}


def test_retrieve_statement_landscape_resolves_notes_dir(monkeypatch, tmp_path):
    index_root = tmp_path / "root"
    notes_dir = get_notes_index_dir(index_root)

    captured = {}

    def _fake_agent(**kwargs):
        captured.update(kwargs)
        return {
            "statement": kwargs["statement"],
            "queries": [],
            "retrieved_tweets": [],
            "selected_notes": [],
            "reply": {"statement": kwargs["statement"], "response": "", "reasons": []},
        }

    monkeypatch.setattr("derad_agent.runtime.landscape_api.run_landscape_agent", _fake_agent)

    retrieve_statement_landscape(
        "hello world",
        index_root=index_root,
        notes_per_tweet=7,
        style="satirical",
    )
    assert captured["notes_index_dir"] == notes_dir
    assert captured["notes_per_tweet"] == 7
    assert captured["style"] == "satirical"
