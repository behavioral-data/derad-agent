"""Group assignment (rater-factor sign) and global evaluation points (spec §3.1-3.2)."""
import numpy as np
import pandas as pd


def assign_group(f_u, moderate_threshold=0.0):
    """Group A = f_u >= 0, Group B = f_u < 0. If moderate_threshold > 0, raters with
    |f_u| <= threshold are unassigned (None). Scalar in -> scalar out; array in -> object array out."""
    arr = np.asarray(f_u, dtype=np.float64)
    scalar = arr.ndim == 0
    arr = np.atleast_1d(arr)
    out = np.empty(arr.shape, dtype=object)
    for i, v in enumerate(arr):
        if moderate_threshold > 0 and abs(v) <= moderate_threshold:
            out[i] = None
        else:
            out[i] = "A" if v >= 0 else "B"
    return out[0] if scalar else out


def eval_points(rater_factors):
    """Return (x_A, x_B): mean f_u over Group A and Group B raters."""
    x_A = float(rater_factors.loc[rater_factors["group"] == "A", "f_u"].mean())
    x_B = float(rater_factors.loc[rater_factors["group"] == "B", "f_u"].mean())
    return x_A, x_B
