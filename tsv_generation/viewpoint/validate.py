"""Spec §9 sanity checks over a tweet_lean table."""
import argparse
import pandas as pd


def run_checks(tweet_df):
    high = tweet_df[tweet_df["consensus"] >= 0.5]
    frac = float(high["communityFlagged"].mean()) if len(high) else float("nan")
    pol = tweet_df["polarity"].dropna()
    return {
        "n_tweets": int(len(tweet_df)),
        "frac_flagged_high_consensus": frac,
        "polarity_mean": float(pol.mean()) if len(pol) else float("nan"),
        "polarity_two_sided": bool((pol > 0).any() and (pol < 0).any()),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tweet-lean", required=True)
    args = ap.parse_args()
    df = pd.read_csv(args.tweet_lean, sep="\t")
    for k, v in run_checks(df).items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
