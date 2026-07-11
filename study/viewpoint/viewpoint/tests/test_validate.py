import math
import numpy as np
import pytest
import pandas as pd
from viewpoint.validate import run_checks, fn_sign_agreement
from viewpoint.constants import MISLEADING


def test_run_checks_reports_expected_keys():
    df = pd.DataFrame({
        "consensus": [0.9, 0.1, np.nan],
        "polarity": [0.2, -0.3, 0.0],
        "communityFlagged": [True, False, False],
    })
    out = run_checks(df)
    assert set(out) == {
        "n_tweets", "frac_flagged_high_consensus", "frac_flagged_low_consensus",
        "flag_contrast_ratio", "polarity_mean", "polarity_two_sided",
    }
    assert out["n_tweets"] == 3
    assert out["polarity_two_sided"] is True   # has both >0 and <0 values
    assert out["frac_flagged_high_consensus"] == pytest.approx(1.0)   # row 0 (consensus 0.9) is flagged
    assert out["polarity_mean"] == pytest.approx(-0.1 / 3)            # mean of [0.2, -0.3, 0.0]
    # row 1 (consensus=0.1) is unflagged -> low rate is 0.0
    assert out["frac_flagged_low_consensus"] == pytest.approx(0.0)
    # contrast ratio is NaN because low rate is 0
    assert math.isnan(out["flag_contrast_ratio"])


def test_fn_sign_agreement():
    df = pd.DataFrame({
        "classification": [MISLEADING, MISLEADING],
        "mislead_A": [0.9, 0.1],
        "mislead_B": [0.2, 0.8],
        "noteFactor_fn": [0.3, -0.4],
    })
    # Row 0: A > B, f_n > 0 -> agree
    # Row 1: B > A (A < B), f_n < 0 -> agree
    assert fn_sign_agreement(df) == pytest.approx(1.0)


def test_fn_sign_agreement_empty_is_nan():
    # All eligible notes filtered out (mislead_A == mislead_B) -> NaN.
    df = pd.DataFrame({
        "classification": [MISLEADING, MISLEADING],
        "mislead_A": [0.5, 0.7],
        "mislead_B": [0.5, 0.7],
        "noteFactor_fn": [0.3, -0.2],
    })
    assert np.isnan(fn_sign_agreement(df))
