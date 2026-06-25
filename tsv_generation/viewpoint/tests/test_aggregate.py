import numpy as np
import pandas as pd
import pytest
from viewpoint.aggregate import aggregate_tweets
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
