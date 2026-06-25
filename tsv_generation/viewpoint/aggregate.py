"""Stage 3: pool a tweet's notes per class, kernel-smooth at the two group centroids,
derive net stance / consensus / polarity (spec §3.3-3.4, §4 Stage 3)."""
import argparse
import os

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


def aggregate_notes(rwf, x_A, x_B, note_factors, bw=BW, somewhat=SOMEWHAT):
    """One row per note with per-group smoothed rate + note factor."""
    df = rwf.copy()
    df["h"] = remap_somewhat(df["helpfulNum"].to_numpy(), somewhat)
    f = df["f_u"].to_numpy()
    df["wA"] = gaussian_kernel(f - x_A, bw)
    df["wB"] = gaussian_kernel(f - x_B, bw)
    df["wAh"] = df["wA"] * df["h"]
    df["wBh"] = df["wB"] * df["h"]
    df["isA"] = (df["group"] == "A").astype(int)
    df["isB"] = (df["group"] == "B").astype(int)
    g = df.groupby(["noteId", "tweetId", "classification"], observed=True).agg(
        wA=("wA", "sum"), wAh=("wAh", "sum"), wB=("wB", "sum"), wBh=("wBh", "sum"),
        nA=("isA", "sum"), nB=("isB", "sum"),
    ).reset_index()
    g["mislead_A"] = g["wAh"] / g["wA"]
    g["mislead_B"] = g["wBh"] / g["wB"]
    g = g.merge(note_factors[["noteId", "f_n"]], on="noteId", how="left")
    g = g.rename(columns={"f_n": "noteFactor_fn"})
    return g[["noteId", "tweetId", "classification", "mislead_A", "mislead_B",
              "nA", "nB", "noteFactor_fn"]]


def attach_status(tweet_df, nsh, notes):
    """Add communityFlagged = tweet has a misleading note that is CRH (public status).

    tweetId dtype can differ between the rwf-derived index (string) and the
    freshly-read notes (int64), so compare as strings to be dtype-robust.
    """
    merged = notes.merge(nsh[["noteId", "currentStatus"]], on="noteId", how="left")
    crh_mis = merged[(merged["classification"] == MISLEADING) &
                     (merged["currentStatus"] == "CURRENTLY_RATED_HELPFUL")]
    flagged = set(crh_mis["tweetId"].astype(str))
    out = tweet_df.copy()
    out["communityFlagged"] = [str(t) in flagged for t in out.index]
    return out


def write_outputs(tweet_df, note_df, out_dir, suffix=""):
    os.makedirs(out_dir, exist_ok=True)
    tname = f"tweet_lean{suffix}.tsv"
    tweet_df.reset_index().to_csv(os.path.join(out_dir, tname), sep="\t", index=False)
    if note_df is not None:
        note_df.to_csv(os.path.join(out_dir, "note_lean.tsv"), sep="\t", index=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rwf", required=True)
    ap.add_argument("--note-factors", required=True)
    ap.add_argument("--rater-factors", required=True)
    ap.add_argument("--status", required=True)
    ap.add_argument("--notes", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--source", default=None)
    ap.add_argument("--defense-tag", action="store_true")
    args = ap.parse_args()

    from .groups import eval_points
    rwf = pd.read_parquet(args.rwf)
    rater_factors = pd.read_parquet(args.rater_factors)
    note_factors = pd.read_parquet(args.note_factors)
    nsh = pd.read_csv(args.status, sep="\t", usecols=["noteId", "currentStatus"])
    notes = pd.read_csv(args.notes, sep="\t", usecols=["noteId", "tweetId", "classification"])

    x_A, x_B = eval_points(rater_factors)
    tweet_df = aggregate_tweets(rwf, x_A, x_B, source=args.source, defense_tag=args.defense_tag)
    tweet_df = attach_status(tweet_df, nsh, notes)
    suffix = ".popsampled" if args.source == "POPULATION_SAMPLED" else ""
    note_df = None if args.source == "POPULATION_SAMPLED" else aggregate_notes(rwf, x_A, x_B, note_factors)
    if note_df is not None:
        note_df = note_df.merge(nsh[["noteId", "currentStatus"]], on="noteId", how="left")
    write_outputs(tweet_df, note_df, args.out, suffix=suffix)
    print(f"x_A={x_A:.4f} x_B={x_B:.4f} | tweets={len(tweet_df)} | wrote to {args.out}")


if __name__ == "__main__":
    main()
