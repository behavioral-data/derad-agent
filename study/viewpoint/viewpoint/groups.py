"""Group assignment (rater-factor sign) and global evaluation points (spec §3.1-3.2)."""
import numpy as np
import pandas as pd


def assign_group(f_u, moderate_threshold=0.0):
    """Group A = f_u >= 0, Group B = f_u < 0. If moderate_threshold > 0, raters with
    |f_u| <= threshold are unassigned (None). Scalar in -> scalar out; array in -> object array out."""
    arr = np.asarray(f_u, dtype=np.float64)
    scalar = arr.ndim == 0
    arr = np.atleast_1d(arr)
    out = np.where(arr >= 0, "A", "B").astype(object)
    if moderate_threshold > 0:
        out[np.abs(arr) <= moderate_threshold] = None
    return out[0] if scalar else out


def eval_points(rater_factors):
    """Return (x_A, x_B): mean f_u over Group A and Group B raters. Raises if a group is empty."""
    a = rater_factors.loc[rater_factors["group"] == "A", "f_u"]
    b = rater_factors.loc[rater_factors["group"] == "B", "f_u"]
    if len(a) == 0 or len(b) == 0:
        raise ValueError(f"eval_points needs both groups; got |A|={len(a)}, |B|={len(b)}")
    return float(a.mean()), float(b.mean())
