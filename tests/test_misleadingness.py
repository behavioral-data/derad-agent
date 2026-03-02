from types import SimpleNamespace

from derad_agent.runtime.misleadingness import (
    build_misleadingness_landscape,
    build_bucket_landscape,
    score_misleadingness_axis,
)


def _doc(tweet_id, note_id, summary, classification, flags=None):
    return SimpleNamespace(
        page_content=summary,
        metadata={
            "tweet_id": tweet_id,
            "note_id": note_id,
            "classification": classification,
            "label_flags": flags or {},
            "retrieval_similarity": 0.8,
        },
    )


def test_build_bucket_landscape_groups_by_tweet_and_buckets():
    docs = [
        _doc("t1", "n1", "This claim is false and misleading.", "MISINFORMED"),
        _doc("t1", "n2", "Fact check says it is incorrect.", "MISINFORMED"),
        _doc("t2", "n3", "This statement is accurate.", "NOT_MISLEADING"),
    ]

    result = build_bucket_landscape("Test statement", docs)
    assert result["statement"] == "Test statement"
    assert len(result["tweet_clusters"]) == 2
    buckets = {c["tweet_id"]: c["bucket"] for c in result["tweet_clusters"]}
    assert buckets["t1"] in {"Misleading", "StronglyMisleading"}
    assert buckets["t2"] in {"NotMisleading", "StronglyNotMisleading"}


def test_score_misleadingness_axis_sign_and_bounds():
    left = score_misleadingness_axis(
        {
            "classification": "MISINFORMED_OR_POTENTIALLY_MISLEADING",
            "label_flags": {"misleadingFactualError": 1},
        }
    )
    right = score_misleadingness_axis(
        {
            "classification": "NOT_MISLEADING",
            "label_flags": {"notMisleadingFactuallyCorrect": 1},
        }
    )
    assert -1.0 <= left <= 1.0
    assert -1.0 <= right <= 1.0
    assert left < 0
    assert right > 0


def test_build_misleadingness_landscape_respects_similarity_threshold_and_max_points():
    docs = [
        _doc("t1", "n1", "This claim is false and misleading.", "MISINFORMED"),
        _doc("t2", "n2", "This statement is accurate.", "NOT_MISLEADING"),
        _doc("t3", "n3", "Weakly related note text.", "NOT_MISLEADING"),
    ]
    docs[2].metadata["retrieval_similarity"] = 0.1

    result = build_misleadingness_landscape(
        "The policy works",
        docs,
        similarity_min=0.2,
        max_points=2,
    )
    points = result["points"]
    assert len(points) == 2
    assert all(p["similarity"] >= 0.2 for p in points)
    assert "misleadingness_axis_quantiles" in result["ranges"]

