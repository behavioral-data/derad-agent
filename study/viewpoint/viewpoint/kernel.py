"""Gaussian kernel smoothing — the core of the misleadingness metric (spec §3.3)."""
import numpy as np


def gaussian_kernel(d, bw=0.1):
    """Unnormalized-position Gaussian kernel weight(s) for distance(s) d."""
    d = np.asarray(d, dtype=np.float64)
    return (1.0 / (bw * np.sqrt(2 * np.pi))) * np.exp(-0.5 * (d / bw) ** 2)


def smoothed_rate(factors, helpful, x, bw=0.1):
    """Kernel-weighted mean helpful value at evaluation point x.

    factors, helpful: 1-D arrays of equal length. Returns nan if empty or all
    weights underflow to 0.
    """
    factors = np.asarray(factors, dtype=np.float64)
    helpful = np.asarray(helpful, dtype=np.float64)
    if factors.size == 0:
        return float("nan")
    w = gaussian_kernel(factors - x, bw)
    denom = w.sum()
    if denom <= 0:
        return float("nan")
    return float((w * helpful).sum() / denom)


def remap_somewhat(helpful_num, somewhat=0.7):
    """Map raw helpfulNum {0,0.5,1.0} -> {0, somewhat, 1.0} (spec §3.3)."""
    out = np.asarray(helpful_num, dtype=np.float64).copy()
    out[out == 0.5] = somewhat
    return out
