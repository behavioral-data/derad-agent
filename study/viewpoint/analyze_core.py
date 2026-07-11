"""Core/US axis: anchoring (distinctive terms per pole) + validation stats."""
import numpy as np, pandas as pd
from sklearn.feature_extraction.text import CountVectorizer
import sys
sys.path.insert(0, "viewpoint")
from viewpoint.validate import run_checks, fn_sign_agreement

D = "cn_data/viewpoint_out_core"
MIS = "MISINFORMED_OR_POTENTIALLY_MISLEADING"

t = pd.read_csv(f"{D}/tweet_lean.tsv", sep="\t", dtype={"tweetId": str})
nl = pd.read_csv(f"{D}/note_lean.tsv", sep="\t",
                 usecols=["noteId", "classification", "noteFactor_fn", "nA", "nB", "mislead_A", "mislead_B"])
notes = pd.read_csv("cn_data/notes-00000.tsv", sep="\t",
                    usecols=["noteId", "summary"], dtype={"noteId": np.int64, "summary": str})
notes["summary"] = notes["summary"].fillna("")

frame = t[(t.nA >= 5) & (t.nB >= 5) & (t.mislead_A.notna())].copy()
A = frame.netStance_A >= 0.5; B = frame.netStance_B >= 0.5
print("=== CORE (US) axis — validation ===")
print(f"frame (nA,nB>=5, has misleading note): {len(frame):,} of {len(t):,}  (raters A=214,224 / B=322,758; x_A=+0.322 x_B=-0.317)")
print(f"quadrants: both={100*(A&B).mean():.1f}% A_only={100*(A&~B).mean():.1f}% "
      f"B_only={100*(~A&B).mean():.1f}% neither={100*(~A&~B).mean():.1f}%")
duel = ((frame.netStance_A.abs()>=0.3)&(frame.netStance_B.abs()>=0.3)&
        (np.sign(frame.netStance_A)!=np.sign(frame.netStance_B)))
print(f"dueling: {100*duel.mean():.1f}%")
for k, v in run_checks(frame).items():
    print(f"  {k}: {v}")
print(f"  fn_sign_agreement: {fn_sign_agreement(nl):.3f}")

print("\n=== CORE (US) axis — anchoring (distinctive note-summary terms) ===")
anc = nl[(nl.classification == MIS) & ((nl.nA + nl.nB) >= 10) & (nl.noteFactor_fn.abs() >= 0.3)].merge(notes, on="noteId", how="left")
anc["summary"] = anc["summary"].fillna("")
anc = anc[anc.summary.str.len() >= 10]
poleA = (anc.noteFactor_fn > 0).values
print(f"anchoring set: {len(anc):,} notes | A-pole {poleA.sum():,} / B-pole {(~poleA).sum():,}")
vec = CountVectorizer(stop_words="english", min_df=80, token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z]+\b", ngram_range=(1, 2))
X = vec.fit_transform(anc.summary.values); vocab = np.array(vec.get_feature_names_out())
ca = np.asarray(X[poleA].sum(0)).ravel().astype(float); cb = np.asarray(X[~poleA].sum(0)).ravel().astype(float)
alpha = ca + cb; a0, b0, A0 = ca.sum(), cb.sum(), alpha.sum()
z = (np.log((ca+alpha)/(a0+A0-ca-alpha)) - np.log((cb+alpha)/(b0+A0-cb-alpha))) / np.sqrt(1/(ca+alpha)+1/(cb+alpha))
order = np.argsort(z)
print("\nGroup A pole terms:", ", ".join(f"{vocab[i]}({z[i]:.0f})" for i in order[::-1][:28]))
print("\nGroup B pole terms:", ", ".join(f"{vocab[i]}({z[i]:.0f})" for i in order[:28]))
print("\nDONE")
