import pandas as pd
from viewpoint.stage2 import build_ratings_with_factors
from viewpoint.constants import MISLEADING, NOT_MISLEADING


def test_join_keeps_only_factored_raters_and_classified_notes():
    ratings = pd.DataFrame({
        "noteId": [10, 10, 11, 12],
        "raterParticipantId": [1, 2, 1, 1],       # rater 2 has no factor; note 12 not classified-joined
        "helpfulNum": [1.0, 0.0, 0.5, 1.0],
        "ratingSourceBucketed": ["DEFAULT"] * 4,
        "notHelpfulNoteNotNeeded": [0, 0, 0, 0],
        "notHelpfulIncorrect": [0, 0, 0, 0],
    })
    notes = pd.DataFrame({"noteId": [10, 11], "tweetId": [100, 100],
                          "classification": [MISLEADING, NOT_MISLEADING]})
    rater_factors = pd.DataFrame({"raterParticipantId": [1], "f_u": [0.3], "group": ["A"]})
    note_factors = pd.DataFrame({"noteId": [10, 11], "f_n": [0.1, -0.2]})

    out = build_ratings_with_factors(ratings, notes, rater_factors, note_factors)
    # rater 2 dropped (no factor); note 12 dropped (not in notes); 2 rows remain (rater 1 on notes 10,11)
    assert len(out) == 2
    assert set(out["noteId"]) == {10, 11}
    assert set(out["tweetId"]) == {100}
    assert out.set_index("noteId").loc[10, "f_n"] == 0.1
    assert (out["f_u"] == 0.3).all()
