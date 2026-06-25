import numpy as np
import pandas as pd
import pytest
from viewpoint.aggregate import aggregate_tweets, aggregate_notes, attach_status
from viewpoint.constants import MISLEADING, NOT_MISLEADING

X_A, X_B = 0.5, -0.5

def _row(tweet, note, cls, f_u, group, h, src="DEFAULT", nn=0):
    return {"tweetId": tweet, "noteId": note, "classification": cls,
            "f_u": f_u, "group": group, "helpfulNum": h,
            "ratingSourceBucketed": src, "notHelpfulNoteNotNeeded": nn}


def test_both_groups_find_misleading_note_helpful():
    rows = [
        _row(1, 10, MISLEADING, 0.5, "A", 1.0),
        _row(1, 10, MISLEADING, 0.5, "A", 1.0),
        _row(1, 10, MISLEADING, -0.5, "B", 1.0),
        _row(1, 10, MISLEADING, -0.5, "B", 1.0),
    ]
    out = aggregate_tweets(pd.DataFrame(rows), X_A, X_B)
    assert out.loc[1, "mislead_A"] == pytest.approx(1.0)
    assert out.loc[1, "mislead_B"] == pytest.approx(1.0)
    assert out.loc[1, "consensus"] == pytest.approx(1.0)
    assert out.loc[1, "polarity"] == pytest.approx(0.0)
    assert out.loc[1, "nA"] == 2 and out.loc[1, "nB"] == 2
    assert np.isnan(out.loc[1, "defend_A"])
    assert out.loc[1, "netStance_A"] == pytest.approx(1.0)


def test_polarized_group_a_flags_group_b_does_not():
    rows = [
        _row(2, 20, MISLEADING, 0.5, "A", 1.0),
        _row(2, 20, MISLEADING, 0.5, "A", 1.0),
        _row(2, 20, MISLEADING, -0.5, "B", 0.0),
        _row(2, 20, MISLEADING, -0.5, "B", 0.0),
    ]
    out = aggregate_tweets(pd.DataFrame(rows), X_A, X_B)
    assert out.loc[2, "mislead_A"] == pytest.approx(1.0)
    assert out.loc[2, "mislead_B"] == pytest.approx(0.0)
    assert out.loc[2, "polarity"] == pytest.approx(1.0)   # netStance_A - netStance_B = 1 - 0


def test_net_stance_uses_defend_when_present():
    rows = [
        _row(3, 30, MISLEADING, 0.5, "A", 1.0),
        _row(3, 31, NOT_MISLEADING, 0.5, "A", 1.0),   # group A also endorses a "fine" note
    ]
    out = aggregate_tweets(pd.DataFrame(rows), X_A, X_B)
    assert out.loc[3, "mislead_A"] == pytest.approx(1.0)
    assert out.loc[3, "defend_A"] == pytest.approx(1.0)
    assert out.loc[3, "netStance_A"] == pytest.approx(0.0)   # 1 - 1
    assert out.loc[3, "nMisleadingNotes"] == 1
    assert out.loc[3, "nNotMisleadingNotes"] == 1


def test_population_sampled_filter():
    rows = [
        _row(4, 40, MISLEADING, 0.5, "A", 1.0, src="DEFAULT"),
        _row(4, 40, MISLEADING, 0.5, "A", 0.0, src="POPULATION_SAMPLED"),
    ]
    out = aggregate_tweets(pd.DataFrame(rows), X_A, X_B, source="POPULATION_SAMPLED")
    assert out.loc[4, "mislead_A"] == pytest.approx(0.0)   # only the sampled (h=0) row counts


def test_defense_tag_folds_note_not_needed_without_inflating_counts():
    rows = [
        _row(5, 50, MISLEADING, 0.5, "A", 0.0, nn=1),   # A: not-helpful + "note not needed"
        _row(5, 50, MISLEADING, -0.5, "B", 1.0),        # B finds the misleading note helpful
    ]
    out = aggregate_tweets(pd.DataFrame(rows), X_A, X_B, defense_tag=True)
    assert out.loc[5, "defend_A"] == pytest.approx(1.0)     # the NoteNotNeeded vote folded in
    assert out.loc[5, "nNotMisleadingNotes"] == 0           # no genuine not-misleading notes
    assert out.loc[5, "nMisleadingNotes"] == 1


def test_aggregate_notes_one_row_per_note_with_fn():
    rows = [
        _row(1, 10, MISLEADING, 0.5, "A", 1.0),
        _row(1, 11, NOT_MISLEADING, -0.5, "B", 1.0),
    ]
    nf = pd.DataFrame({"noteId": [10, 11], "f_n": [0.12, -0.03]})
    out = aggregate_notes(pd.DataFrame(rows), X_A, X_B, nf)
    assert set(out["noteId"]) == {10, 11}
    assert out.set_index("noteId").loc[10, "noteFactor_fn"] == pytest.approx(0.12)
    assert out.set_index("noteId").loc[10, "classification"] == MISLEADING


def test_attach_status_sets_community_flagged():
    tweet_df = pd.DataFrame(index=pd.Index([1, 2], name="tweetId"))
    notes = pd.DataFrame({"noteId": [10, 20], "tweetId": [1, 2],
                          "classification": [MISLEADING, MISLEADING]})
    nsh = pd.DataFrame({"noteId": [10, 20],
                        "currentStatus": ["CURRENTLY_RATED_HELPFUL", "NEEDS_MORE_RATINGS"]})
    out = attach_status(tweet_df, nsh, notes)
    assert bool(out.loc[1, "communityFlagged"]) is True
    assert bool(out.loc[2, "communityFlagged"]) is False


def test_attach_status_robust_to_tweetid_dtype_mismatch():
    # Reproduces the production bug: rwf-derived index is STRING tweetIds,
    # while freshly-read notes carry INT tweetIds.
    tweet_df = pd.DataFrame(index=pd.Index(["100", "200"], name="tweetId"))
    notes = pd.DataFrame({"noteId": [10, 20], "tweetId": [100, 200],
                          "classification": [MISLEADING, MISLEADING]})
    nsh = pd.DataFrame({"noteId": [10, 20],
                        "currentStatus": ["CURRENTLY_RATED_HELPFUL", "NEEDS_MORE_RATINGS"]})
    out = attach_status(tweet_df, nsh, notes)
    assert bool(out.loc["100", "communityFlagged"]) is True
    assert bool(out.loc["200", "communityFlagged"]) is False
