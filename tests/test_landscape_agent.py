from types import SimpleNamespace

from derad_agent.runtime.landscape_agent import run_landscape_agent
from derad_agent.runtime.landscape_api import retrieve_statement_landscape


def _doc(note_id: str, tweet_id: str, summary: str, similarity: float):
    return SimpleNamespace(
        page_content=summary,
        metadata={
            "note_id": note_id,
            "tweet_id": tweet_id,
            "thread_key": tweet_id,
            "classification": "NOT_MISLEADING",
            "label_flags": {"notMisleadingFactuallyCorrect": 1},
            "created_utc": 1.0,
            "retrieval_similarity": similarity,
        },
    )


def test_run_landscape_agent_single_pass_schema_is_stable(monkeypatch, tmp_path):
    user_dir = tmp_path / "idx"
    user_dir.mkdir()

    class _FakeFAISS:
        @staticmethod
        def load_local(*args, **kwargs):
            return object()

    monkeypatch.setattr("derad_agent.runtime.landscape_agent.FAISS", _FakeFAISS)
    monkeypatch.setattr("derad_agent.runtime.landscape_agent.get_embedder", lambda: object())

    def _fake_plan(**kwargs):
        return ["query-1"], None, "{}"

    def _fake_retrieve(**kwargs):
        query = kwargs["queries"][0]
        return [_doc(f"note-{query}", f"tweet-{query}", "This statement is accurate.", 0.92)]

    monkeypatch.setattr("derad_agent.runtime.landscape_agent.step_1_generate_queries", _fake_plan)
    monkeypatch.setattr("derad_agent.runtime.landscape_agent.step_2_retrieve_documents", _fake_retrieve)
    monkeypatch.setattr("derad_agent.runtime.landscape_agent.step_3_augment_documents", lambda **kwargs: kwargs["docs"])
    monkeypatch.setattr(
        "derad_agent.runtime.landscape_agent.step_4_build_landscape_output",
        lambda **kwargs: {
            "statement": kwargs["statement"],
            "landscape_summary": "Synthetic summary for tests.",
            "key_reasons": [],
        },
    )

    first = run_landscape_agent(statement="The policy is effective.", user_dir=user_dir, similarity_min=0.5)
    second = run_landscape_agent(statement="The policy is effective.", user_dir=user_dir, similarity_min=0.5)

    assert len(first["iterations"]) == 1
    assert first["queries"] == ["query-1"]
    assert "misleadingness_landscape" in first
    assert "statement_landscape" in first
    assert "landscape_summary" in first["statement_landscape"]
    assert "key_reasons" in first["statement_landscape"]
    assert "points" in first["misleadingness_landscape"]
    assert first["misleadingness_landscape"] == second["misleadingness_landscape"]


def test_retrieve_statement_landscape_uses_global_index_dir(monkeypatch, tmp_path):
    index_root = tmp_path / "root"
    index_root.mkdir()
    global_dir = index_root / "community_notes_global"
    global_dir.mkdir()

    monkeypatch.setattr("derad_agent.indexing.index_builder.get_global_index_dir", lambda _index_root: global_dir)
    monkeypatch.setattr(
        "derad_agent.runtime.landscape_api.run_landscape_agent",
        lambda **kwargs: {
            "statement": kwargs["statement"],
            "user_dir": kwargs["user_dir"],
            "misleadingness_landscape": {"points": []},
            "queries": [],
            "iterations": [],
            "documents": [],
            "bucket_landscape": {},
            "statement_landscape": {"landscape_summary": "", "key_reasons": []},
        },
    )

    result = retrieve_statement_landscape("hello world", index_root=index_root)
    assert result["user_dir"] == global_dir

