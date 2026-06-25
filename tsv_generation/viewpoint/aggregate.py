"""Stage 3: pool a tweet's notes per class, kernel-smooth at the two group centroids,
derive net stance / consensus / polarity (spec §3.3-3.4, §4 Stage 3)."""
import numpy as np
import pandas as pd

from .kernel import gaussian_kernel, remap_somewhat
from .constants import BW, SOMEWHAT, MISLEADING, NOT_MISLEADING


def _smoothed_by_tweet(df, x_A, x_B, bw):
    """Per-tweet kernel-weighted mean of df['h'] at x_A and x_B, plus raw A/B counts.
    Returns a DataFrame indexed by tweetId with columns rate_A, rate_B, nA, nB."""
    if len(df) == 0:
        return pd.DataFrame(columns=["rate_A", "rate_B", "nA", "nB"])
    f = df["f_u"].to_numpy()
    h = df["h"].to_numpy()
    wA = gaussian_kernel(f - x_A, bw)
    wB = gaussian_kernel(f - x_B, bw)
    tmp = pd.DataFrame({
        "tweetId": df["tweetId"].to_numpy(),
        "wA": wA, "wAh": wA * h,
        "wB": wB, "wBh": wB * h,
        "isA": (df["group"] == "A").to_numpy(dtype=int),
        "isB": (df["group"] == "B").to_numpy(dtype=int),
    })
    g = tmp.groupby("tweetId").sum()
    out = pd.DataFrame({
        "rate_A": g["wAh"] / g["wA"],
        "rate_B": g["wBh"] / g["wB"],
        "nA": g["isA"].astype(int),
        "nB": g["isB"].astype(int),
    })
    return out


def aggregate_tweets(rwf, x_A, x_B, bw=BW, somewhat=SOMEWHAT, source=None, defense_tag=False):
    df = rwf
    if source is not None:
        df = df[df["ratingSourceBucketed"] == source]
    df = df.copy()
    df["h"] = remap_somewhat(df["helpfulNum"].to_numpy(), somewhat)

    mis = _smoothed_by_tweet(df[df["classification"] == MISLEADING], x_A, x_B, bw)
    notmis = df[df["classification"] == NOT_MISLEADING]
    if defense_tag:
        # Treat a NoteNotNeeded tag on a MISLEADING note as a "tweet is fine" vote (h=1).
        extra = df[(df["classification"] == MISLEADING) & (df["notHelpfulNoteNotNeeded"] == 1)].copy()
        extra["h"] = 1.0
        notmis = pd.concat([notmis, extra], ignore_index=True)
    dfd = _smoothed_by_tweet(notmis, x_A, x_B, bw)

    out = pd.DataFrame(index=mis.index.union(dfd.index))
    out["mislead_A"] = mis["rate_A"].astype("float64")
    out["mislead_B"] = mis["rate_B"].astype("float64")
    out["defend_A"] = dfd["rate_A"].astype("float64")
    out["defend_B"] = dfd["rate_B"].astype("float64")
    out["nA"] = mis["nA"].fillna(0).astype(int)
    out["nB"] = mis["nB"].fillna(0).astype(int)
    mis_n = df[df["classification"] == MISLEADING].groupby("tweetId")["noteId"].nunique()
    notmis_n = df[df["classification"] == NOT_MISLEADING].groupby("tweetId")["noteId"].nunique()
    out["nMisleadingNotes"] = mis_n.reindex(out.index)
    out["nNotMisleadingNotes"] = notmis_n.reindex(out.index)
    for col in ["nA", "nB", "nMisleadingNotes", "nNotMisleadingNotes"]:
        out[col] = out[col].fillna(0).astype(int)

    net_A = out["mislead_A"].fillna(0.0) - out["defend_A"].fillna(0.0)
    net_B = out["mislead_B"].fillna(0.0) - out["defend_B"].fillna(0.0)
    out["netStance_A"] = net_A
    out["netStance_B"] = net_B
    out["consensus"] = np.minimum(out["mislead_A"], out["mislead_B"])  # NaN if either NaN
    out["polarity"] = net_A - net_B
    out.index.name = "tweetId"
    return out
