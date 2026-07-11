import numpy as np
import pytest
from viewpoint.kernel import gaussian_kernel, smoothed_rate, remap_somewhat


def test_kernel_peak_at_zero():
    assert gaussian_kernel(0.0, bw=0.1) == pytest.approx(1.0 / (0.1 * np.sqrt(2 * np.pi)))


def test_kernel_symmetric_and_decaying():
    assert gaussian_kernel(0.2, 0.1) == pytest.approx(gaussian_kernel(-0.2, 0.1))
    assert gaussian_kernel(0.05, 0.1) > gaussian_kernel(0.30, 0.1)


def test_smoothed_rate_all_helpful_is_one():
    f = np.array([-0.5, 0.0, 0.5])
    h = np.array([1.0, 1.0, 1.0])
    assert smoothed_rate(f, h, x=0.0, bw=0.1) == pytest.approx(1.0)


def test_smoothed_rate_localizes_to_eval_point():
    # rater at x votes helpful; a far rater votes not-helpful -> rate near 1 at x.
    f = np.array([0.5, -0.5])
    h = np.array([1.0, 0.0])
    assert smoothed_rate(f, h, x=0.5, bw=0.1) > 0.99


def test_smoothed_rate_empty_is_nan():
    assert np.isnan(smoothed_rate(np.array([]), np.array([]), x=0.0, bw=0.1))


def test_smoothed_rate_zero_weight_is_nan():
    # A rater very far from x: all kernel weight underflows to 0 -> nan.
    assert np.isnan(smoothed_rate(np.array([100.0]), np.array([1.0]), x=0.0, bw=0.1))


def test_remap_somewhat():
    out = remap_somewhat(np.array([0.0, 0.5, 1.0]), somewhat=0.7)
    assert out == pytest.approx([0.0, 0.7, 1.0])
