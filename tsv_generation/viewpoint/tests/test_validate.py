import numpy as np
import pytest
import pandas as pd
from viewpoint.validate import run_checks


def test_run_checks_reports_expected_keys():
    df = pd.DataFrame({
        "consensus": [0.9, 0.1, np.nan],
        "polarity": [0.2, -0.3, 0.0],
        "communityFlagged": [True, False, False],
    })
    out = run_checks(df)
    assert set(out) == {"n_tweets", "frac_flagged_high_consensus", "polarity_mean", "polarity_two_sided"}
    assert out["n_tweets"] == 3
    assert out["polarity_two_sided"] is True   # has both >0 and <0 values
    assert out["frac_flagged_high_consensus"] == pytest.approx(1.0)   # row 0 (consensus 0.9) is flagged
    assert out["polarity_mean"] == pytest.approx(-0.1 / 3)            # mean of [0.2, -0.3, 0.0]
