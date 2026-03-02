from pathlib import Path

from derad_agent.indexing.tsv_reader import iter_notes_tsv_rows, list_tweet_ids


def test_iter_notes_tsv_rows_normalizes_fields(tmp_path: Path):
    tsv = tmp_path / "notes.tsv"
    tsv.write_text(
        "\t".join(
            [
                "noteId",
                "noteAuthorParticipantId",
                "createdAtMillis",
                "tweetId",
                "classification",
                "misleadingFactualError",
                "notMisleadingFactuallyCorrect",
                "summary",
                "isMediaNote",
                "isCollaborativeNote",
            ]
        )
        + "\n"
        + "\t".join(
            [
                "n1",
                "a1",
                "1713978050878",
                "t1",
                "MISINFORMED_OR_POTENTIALLY_MISLEADING",
                "1",
                "0",
                "This claim is not correct.",
                "0",
                "1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    rows = list(iter_notes_tsv_rows(tsv))
    assert len(rows) == 1
    row = rows[0]
    assert row["tweet_id"] == "t1"
    assert row["note_id"] == "n1"
    assert row["classification"] == "MISINFORMED_OR_POTENTIALLY_MISLEADING"
    assert row["is_collaborative_note"] == 1
    assert row["created_utc"] == 1713978050.878
    assert row["label_flags"]["misleadingFactualError"] == 1


def test_list_tweet_ids_returns_sorted_unique(tmp_path: Path):
    tsv = tmp_path / "notes.tsv"
    tsv.write_text(
        "noteId\tnoteAuthorParticipantId\tcreatedAtMillis\ttweetId\tclassification\tsummary\tisMediaNote\tisCollaborativeNote\n"
        "n1\ta1\t1713978050878\tt2\tMISINFORMED\tone\t0\t0\n"
        "n2\ta2\t1713978050878\tt1\tNOT_MISLEADING\ttwo\t0\t0\n"
        "n3\ta3\t1713978050878\tt2\tMISINFORMED\tthree\t0\t0\n",
        encoding="utf-8",
    )
    assert list_tweet_ids([tsv]) == ["t1", "t2"]

