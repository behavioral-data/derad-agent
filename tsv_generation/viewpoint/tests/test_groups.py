import numpy as np
import pandas as pd
import pytest
from viewpoint.groups import assign_group, eval_points


def test_assign_group_sign_default():
    g = assign_group(np.array([0.3, -0.2, 0.0]))
    assert list(g) == ["A", "B", "A"]   # f_u == 0 -> A


def test_assign_group_moderate_threshold_drops_center():
    g = assign_group(np.array([0.3, -0.05, 0.05]), moderate_threshold=0.1)
    assert g[0] == "A" and g[1] is None and g[2] is None


def test_eval_points_are_group_means():
    rf = pd.DataFrame(
        {"raterParticipantId": [1, 2, 3, 4],
         "f_u": [0.2, 0.4, -0.1, -0.3],
         "group": ["A", "A", "B", "B"]}
    )
    x_A, x_B = eval_points(rf)
    assert x_A == pytest.approx(0.3)
    assert x_B == pytest.approx(-0.2)


def test_eval_points_missing_group_raises():
    rf = pd.DataFrame({"raterParticipantId": [1, 2], "f_u": [0.2, 0.4], "group": ["A", "A"]})
    with pytest.raises(ValueError):
        eval_points(rf)
