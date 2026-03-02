from types import SimpleNamespace

from derad_agent.runtime.retriever import retrieve_with_expansion


class _FakeEmb:
    def embed_query(self, _query):
        return [0.1, 0.2, 0.3]


def _doc(note_id, tweet_id, thread_key, classification="NOT_MISLEADING"):
    return SimpleNamespace(
        page_content=f"note {note_id}",
        metadata={
            "note_id": note_id,
            "tweet_id": tweet_id,
            "thread_key": thread_key,
            "classification": classification,
            "created_utc": 1.0,
            "label_flags": {},
        },
    )


class _FakeVDB:
    def __init__(self):
        self.seed_docs = [
            (_doc("n-seed-1", "tweet-1", "thread-1"), 0.1),  # sim ~= 0.909
            (_doc("n-seed-2", "tweet-2", "thread-2"), 3.0),  # sim = 0.25
        ]
        self.by_thread = {
            "thread-1": [_doc("n-extra-1", "tweet-1", "thread-1")],
            "thread-2": [_doc("n-extra-2", "tweet-2", "thread-2")],
        }

    def similarity_search_with_score_by_vector(self, _query_vector, k=8):
        return self.seed_docs[:k]

    def similarity_search(self, _query, k=20, filter=None):
        key = (filter or {}).get("thread_key")
        return list(self.by_thread.get(key, []))[:k]


def test_retrieve_with_expansion_applies_similarity_threshold_before_expansion():
    vdb = _FakeVDB()
    docs = retrieve_with_expansion(
        query="test",
        vdb=vdb,
        emb=_FakeEmb(),
        similarity_min=0.6,
        k_semantic=8,
        max_per_thread=6,
    )

    note_ids = {d.metadata.get("note_id") for d in docs}
    assert "n-seed-1" in note_ids
    assert "n-extra-1" in note_ids
    assert "n-seed-2" not in note_ids
    assert "n-extra-2" not in note_ids

    seed_doc = next(d for d in docs if d.metadata.get("note_id") == "n-seed-1")
    assert seed_doc.metadata.get("retrieval_source") == "semantic_seed"
    assert seed_doc.metadata.get("retrieval_similarity") >= 0.6
