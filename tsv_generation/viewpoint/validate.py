"""Spec §9 sanity checks over a tweet_lean table."""
import argparse
import numpy as np
import pandas as pd

from .constants import MISLEADING


def run_checks(tweet_df):
    high = tweet_df[tweet_df["consensus"] >= 0.5]
    low = tweet_df[tweet_df["consensus"] < 0.5]
    frac_high = float(high["communityFlagged"].mean()) if len(high) else float("nan")
    frac_low = float(low["communityFlagged"].mean()) if len(low) else float("nan")
    pol = tweet_df["polarity"].dropna()
    return {
        "n_tweets": int(len(tweet_df)),
        "frac_flagged_high_consensus": frac_high,
        "frac_flagged_low_consensus": frac_low,
        "flag_contrast_ratio": (frac_high / frac_low) if (frac_low and frac_low > 0) else float("nan"),
        "polarity_mean": float(pol.mean()) if len(pol) else float("nan"),
        "polarity_two_sided": bool((pol > 0).any() and (pol < 0).any()),
    }


def fn_sign_agreement(note_df):
    """Among misleading notes, fraction where sign(mislead_A - mislead_B) matches sign(noteFactor_fn).
    The group that finds a misleading note more helpful should sit on the same side as the note's
    factor; high agreement (>0.5, ideally near 1) validates the factor axis. NaN if no eligible notes.
    """
    d = note_df[note_df["classification"] == MISLEADING].dropna(
        subset=["mislead_A", "mislead_B", "noteFactor_fn"])
    d = d[(d["mislead_A"] != d["mislead_B"]) & (d["noteFactor_fn"] != 0)]
    if len(d) == 0:
        return float("nan")
    agree = np.sign(d["mislead_A"] - d["mislead_B"]) == np.sign(d["noteFactor_fn"])
    return float(agree.mean())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tweet-lean", required=True)
    ap.add_argument("--note-lean", default=None)
    args = ap.parse_args()
    df = pd.read_csv(args.tweet_lean, sep="\t")
    for k, v in run_checks(df).items():
        print(f"{k}: {v}")
    if args.note_lean is not None:
        note_df = pd.read_csv(args.note_lean, sep="\t")
        print(f"fn_sign_agreement: {fn_sign_agreement(note_df)}")


if __name__ == "__main__":
    main()
