"""Compute sampling-frame distributions for the dataset README/report."""
import numpy as np
import pandas as pd

D = "cn_data/viewpoint_out"
MIS = "MISINFORMED_OR_POTENTIALLY_MISLEADING"
SUB = ["misleadingFactualError", "misleadingMissingImportantContext",
       "misleadingManipulatedMedia", "misleadingUnverifiedClaimAsFact",
       "misleadingSatire", "misleadingOther"]
SHORT = {s: s.replace("misleading", "") for s in SUB}

t = pd.read_csv(f"{D}/tweet_lean.tsv", sep="\t")
nl = pd.read_csv(f"{D}/note_lean.tsv", sep="\t", usecols=["tweetId", "classification"] + SUB)
tags = nl[nl.classification == MIS].groupby("tweetId")[SUB].max().clip(upper=1)
t = t.merge(tags, on="tweetId", how="left")

frame = t[(t.nA >= 5) & (t.nB >= 5) & (t.mislead_A.notna())].copy()  # recommended sampling frame
print(f"FRAME: well-covered (nA,nB>=5) with a misleading note = {len(frame):,} of {len(t):,} tweets")

# polarity bins
pb = [-2.01, -0.5, -0.15, 0.15, 0.5, 2.01]
pl = ["B-strong (<=-0.5)", "B-lean", "balanced (|p|<0.15)", "A-lean", "A-strong (>=0.5)"]
frame["polBin"] = pd.cut(frame.polarity, pb, labels=pl)
print("\n=== POLARITY STRATA (frame) ===")
g = frame.groupby("polBin", observed=True).agg(n=("tweetId", "size"),
        flagged_pct=("communityFlagged", lambda s: round(100*s.mean(), 1)),
        mean_consensus=("consensus", lambda s: round(s.mean(), 2)))
print(g.to_string())

# consensus bins
cb = [-.01, .2, .4, .6, 1.01]
cl = ["low(<.2)", "med(.2-.4)", "high(.4-.6)", "vhigh(>.6)"]
frame["consBin"] = pd.cut(frame.consensus, cb, labels=cl)
print("\n=== CONSENSUS STRATA (frame) ===")
print(frame.groupby("consBin", observed=True).agg(n=("tweetId","size"),
        flagged_pct=("communityFlagged", lambda s: round(100*s.mean(),1))).to_string())

print("\n=== SUB-TAG PREVALENCE (frame; share of tweets whose misleading note carries the tag) ===")
for s in SUB:
    print(f"  {SHORT[s]:28s} {100*frame[s].fillna(0).mean():5.1f}%   (n={int(frame[s].fillna(0).sum()):,})")

print("\n=== POLARITY x SUB-TAG cell counts (frame) — for stratified sampling ===")
hdr = "polBin".ljust(20) + "".join(f"{SHORT[s][:10]:>11s}" for s in SUB) + f"{'n':>9s}"
print(hdr)
for lab in pl:
    sub = frame[frame.polBin == lab]
    row = lab.ljust(20) + "".join(f"{int(sub[s].fillna(0).sum()):>11,}" for s in SUB) + f"{len(sub):>9,}"
    print(row)

print("\n=== tag co-occurrence: # misleading sub-tags per tweet (frame) ===")
frame["nTags"] = frame[SUB].fillna(0).sum(axis=1)
print(frame.nTags.value_counts().sort_index().to_string())
