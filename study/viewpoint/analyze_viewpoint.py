"""Exploratory analysis of tweet_lean.tsv / note_lean.tsv."""
import numpy as np
import pandas as pd

D = "cn_data/viewpoint_out"
MIS = "MISINFORMED_OR_POTENTIALLY_MISLEADING"
SUBTAGS = ["misleadingFactualError", "misleadingMissingImportantContext",
           "misleadingManipulatedMedia", "misleadingUnverifiedClaimAsFact",
           "misleadingSatire", "misleadingOther"]

t = pd.read_csv(f"{D}/tweet_lean.tsv", sep="\t")
print(f"=== COVERAGE ===")
print(f"tweets total: {len(t):,}")
print(f"  with >=1 misleading note (mislead_A not null): {t.mislead_A.notna().sum():,}")
print(f"  with a defend signal (defend_A not null):      {t.defend_A.notna().sum():,}")
print(f"nA percentiles: " + ", ".join(f"p{p}={t.nA.quantile(p/100):.0f}" for p in (50,75,90,99)))
print(f"nB percentiles: " + ", ".join(f"p{p}={t.nB.quantile(p/100):.0f}" for p in (50,75,90,99)))

wc = t[(t.nA >= 5) & (t.nB >= 5)].copy()      # well-covered: >=5 ratings from EACH group
print(f"\nwell-covered (nA>=5 AND nB>=5): {len(wc):,} ({100*len(wc)/len(t):.1f}%)")

print(f"\n=== POLARIZATION LANDSCAPE (well-covered tweets) ===")
print(f"polarity: mean={wc.polarity.mean():.3f} std={wc.polarity.std():.3f} "
      f"p5={wc.polarity.quantile(.05):.2f} p50={wc.polarity.quantile(.5):.2f} p95={wc.polarity.quantile(.95):.2f}")

# Group "flags it misleading" if its net stance is clearly positive.
TH = 0.5
A = wc.netStance_A >= TH
B = wc.netStance_B >= TH
both    = ( A &  B).sum()
a_only  = ( A & ~B).sum()
b_only  = (~A &  B).sum()
neither = (~A & ~B).sum()
n = len(wc)
print(f"\nquadrants (group 'flags' if netStance>={TH}):")
print(f"  both groups flag (bipartisan misleading): {both:,} ({100*both/n:.1f}%)")
print(f"  group A only flags:                       {a_only:,} ({100*a_only/n:.1f}%)")
print(f"  group B only flags:                       {b_only:,} ({100*b_only/n:.1f}%)")
print(f"  neither flags:                            {neither:,} ({100*neither/n:.1f}%)")

# Genuine dueling: one group net-misleading, the other net-fine (opposite signs, both decisive).
duel = (wc.netStance_A.abs() >= 0.3) & (wc.netStance_B.abs() >= 0.3) & \
       (np.sign(wc.netStance_A) != np.sign(wc.netStance_B))
print(f"\ngenuine dueling (|netStance|>=0.3 both, opposite signs): {duel.sum():,} ({100*duel.sum()/n:.1f}%)")

print(f"\n=== WHAT DOES EACH SIDE FLAG? (misleading sub-tags) ===")
nl = pd.read_csv(f"{D}/note_lean.tsv", sep="\t",
                 usecols=["tweetId", "classification"] + SUBTAGS)
nlm = nl[nl.classification == MIS]
# per-tweet: did ANY misleading note on it carry each sub-tag?
tags_by_tweet = nlm.groupby("tweetId")[SUBTAGS].max().clip(upper=1)
wcx = wc.merge(tags_by_tweet, on="tweetId", how="left")
# Compare A-leaning-flagged vs B-leaning-flagged well-covered tweets.
a_lean = wcx[(wcx.netStance_A >= TH) & (wcx.netStance_B < 0.2)]   # A flags, B doesn't
b_lean = wcx[(wcx.netStance_B >= TH) & (wcx.netStance_A < 0.2)]   # B flags, A doesn't
print(f"A-leaning-flagged tweets: {len(a_lean):,} | B-leaning-flagged: {len(b_lean):,}")
print(f"{'sub-tag':38s} {'A-side %':>9s} {'B-side %':>9s}")
for s in SUBTAGS:
    print(f"{s:38s} {100*a_lean[s].fillna(0).mean():>8.1f}% {100*b_lean[s].fillna(0).mean():>8.1f}%")

print(f"\n=== EXAMPLES (well-covered) ===")
def show(df, cols, label):
    print(f"\n{label}:")
    print(df[cols].to_string(index=False))
cols = ["tweetId", "mislead_A", "mislead_B", "defend_A", "defend_B",
        "netStance_A", "netStance_B", "polarity", "nA", "nB", "communityFlagged"]
show(wc.sort_values("consensus", ascending=False).head(3), cols, "Strongest bipartisan-misleading (top consensus)")
show(wc[duel].sort_values("polarity", ascending=False).head(3), cols, "A flags / B defends (polarity high +)")
show(wc[duel].sort_values("polarity").head(3), cols, "B flags / A defends (polarity high -)")
