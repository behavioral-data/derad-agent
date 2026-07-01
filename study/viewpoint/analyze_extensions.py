"""Three extensions: (1) anchor A/B via note-text lexical signature,
(2) seed-term topic breakdown, (3) visuals. Outputs tables to stdout + PNGs."""
import os
import re
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sklearn.feature_extraction.text import CountVectorizer

D = "cn_data/viewpoint_out"
FIG = f"{D}/figs"
os.makedirs(FIG, exist_ok=True)
MIS = "MISINFORMED_OR_POTENTIALLY_MISLEADING"

# Seed terms (from communitynotes/scoring/src/scoring/topic_model.py)
SEED = {
    "Ukraine": [r"ukrain", r"russia", r"kiev", r"kyiv", r"moscow", r"zelensky", r"putin"],
    "Gaza":    [r"israel", r"palestin", r"gaza", r"jerusalem", r"\bhamas\b"],
    "MessiRonaldo": [r"messi\b", r"ronaldo"],
    "Scams":   [r"scam", r"undisclosed\sad", r"terms\sof\sservice", r"help\.x\.com",
                r"x\.com/tos", r"engagement\sfarm", r"spam", r"gambling", r"apostas",
                r"apuestas", r"dropship", r"drop\sship", r"promotion"],
    "IndiaDim2": [r"\bugc\b", r"\bgc\b", r"\bobc\b", r"\bsc\b", r"\bsc[,\s]+st\b", r"\bst[,\s]+sc\b", "आरक्षण"],
}
SEED_RE = {k: re.compile("(?i)" + "|".join(v)) for k, v in SEED.items()}

print("loading...")
t = pd.read_csv(f"{D}/tweet_lean.tsv", sep="\t", dtype={"tweetId": str})
nl = pd.read_csv(f"{D}/note_lean.tsv", sep="\t", dtype={"tweetId": str},
                 usecols=["noteId", "tweetId", "classification", "noteFactor_fn", "nA", "nB"])
notes = pd.read_csv("cn_data/notes-00000.tsv", sep="\t",
                    usecols=["noteId", "summary"], dtype={"noteId": np.int64, "summary": str})
notes["summary"] = notes["summary"].fillna("")

frame = t[(t.nA >= 5) & (t.nB >= 5) & (t.mislead_A.notna())].copy()
A = frame.netStance_A >= 0.5
B = frame.netStance_B >= 0.5
frame["quad"] = np.select([A & B, A & ~B, ~A & B], ["both", "A_only", "B_only"], "neither")
frame["duel"] = ((frame.netStance_A.abs() >= 0.3) & (frame.netStance_B.abs() >= 0.3) &
                 (np.sign(frame.netStance_A) != np.sign(frame.netStance_B)))

# ---------------------------------------------------------------- (2) TOPIC
print("\n" + "=" * 70 + "\n(2) TOPIC BREAKDOWN (seed-term match on note summaries)\n" + "=" * 70)
nm = nl[nl.classification == MIS].merge(notes, on="noteId", how="left")
nm["summary"] = nm["summary"].fillna("")
for topic, rx in SEED_RE.items():
    nm[topic] = nm["summary"].str.contains(rx, na=False)
topic_cols = list(SEED_RE)
nmatch = nm[topic_cols].sum(axis=1)
# single clear topic per note (conflicts -> unassigned), then per-tweet majority
nm["noteTopic"] = np.where(nmatch == 1, np.array(topic_cols)[nm[topic_cols].values.argmax(1)], "Unassigned")
def tweet_topic(s):
    real = s[s != "Unassigned"]
    return real.mode().iloc[0] if len(real) else "Unassigned"
tt = nm.groupby("tweetId")["noteTopic"].apply(tweet_topic).rename("topic")
frame = frame.merge(tt, on="tweetId", how="left")
frame["topic"] = frame["topic"].fillna("Unassigned")

rows = []
for tp, g in frame.groupby("topic"):
    n = len(g)
    rows.append((tp, n, round(100 * (g.quad == "both").mean(), 1),
                 round(100 * (g.quad == "A_only").mean(), 1),
                 round(100 * (g.quad == "B_only").mean(), 1),
                 round(100 * g.duel.mean(), 1),
                 round(g.polarity.mean(), 3),
                 round(100 * g.communityFlagged.mean(), 1)))
tb = pd.DataFrame(rows, columns=["topic", "n", "both%", "A_only%", "B_only%", "duel%", "meanPol", "flagged%"]).sort_values("n", ascending=False)
print(tb.to_string(index=False))

# ---------------------------------------------------------------- (1) ANCHOR
print("\n" + "=" * 70 + "\n(1) ANCHORING: distinctive note-summary terms per viewpoint pole\n" + "=" * 70)
anc = nl[(nl.classification == MIS) & ((nl.nA + nl.nB) >= 10) & (nl.noteFactor_fn.abs() >= 0.3)]
anc = anc.merge(notes, on="noteId", how="left")
anc["summary"] = anc["summary"].fillna("")
anc = anc[anc.summary.str.len() >= 10]
poleA = (anc.noteFactor_fn > 0).values     # Group A (positive factor) prefers these notes
print(f"anchoring set: {len(anc):,} misleading notes (|f_n|>=0.3, >=10 ratings) | A-pole {poleA.sum():,} / B-pole {(~poleA).sum():,}")
vec = CountVectorizer(stop_words="english", min_df=80, token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z]+\b", ngram_range=(1, 2))
X = vec.fit_transform(anc.summary.values)
vocab = np.array(vec.get_feature_names_out())
ca = np.asarray(X[poleA].sum(0)).ravel().astype(float)
cb = np.asarray(X[~poleA].sum(0)).ravel().astype(float)
# Monroe et al. weighted log-odds with informative Dirichlet prior
alpha = ca + cb
a0, b0, A0 = ca.sum(), cb.sum(), alpha.sum()
la = np.log((ca + alpha) / (a0 + A0 - ca - alpha))
lb = np.log((cb + alpha) / (b0 + A0 - cb - alpha))
z = (la - lb) / np.sqrt(1.0 / (ca + alpha) + 1.0 / (cb + alpha))
order = np.argsort(z)
topB = [(vocab[i], round(float(z[i]), 1)) for i in order[:25]]
topA = [(vocab[i], round(float(z[i]), 1)) for i in order[::-1][:25]]
print("\nGroup A pole — distinctive terms in the misleading notes A finds helpful (z):")
print("  " + ", ".join(f"{w}({s})" for w, s in topA))
print("\nGroup B pole — distinctive terms in the misleading notes B finds helpful (z):")
print("  " + ", ".join(f"{w}({s})" for w, s in topB))

# ---------------------------------------------------------------- (3) VISUALS
print("\n" + "=" * 70 + "\n(3) VISUALS\n" + "=" * 70)
plt.rcParams.update({"figure.dpi": 130, "font.size": 10})

# Fig 1: polarity histogram
fig, ax = plt.subplots(figsize=(7, 4))
ax.hist(frame.polarity, bins=80, color="#4C72B0")
for x, lab in [(-0.5, "B-strong"), (0.5, "A-strong")]:
    ax.axvline(x, color="k", ls="--", lw=0.8, alpha=0.5)
ax.set_xlabel("polarity = netStance_A − netStance_B  (<0: Group B flags more,  >0: Group A flags more)")
ax.set_ylabel("tweets"); ax.set_title(f"Polarity distribution (n={len(frame):,} well-covered tweets)")
fig.tight_layout(); fig.savefig(f"{FIG}/polarity_hist.png"); plt.close(fig)

# Fig 2: netStance density (the dueling off-diagonal)
fig, ax = plt.subplots(figsize=(5.6, 5))
hb = ax.hexbin(frame.netStance_A, frame.netStance_B, gridsize=45, bins="log", cmap="viridis")
ax.axhline(0, color="w", lw=0.5); ax.axvline(0, color="w", lw=0.5)
ax.plot([-1, 1], [-1, 1], "w--", lw=0.8, alpha=0.6)
ax.set_xlabel("netStance_A"); ax.set_ylabel("netStance_B")
ax.set_title("Group A vs B net stance\n(top-left & bottom-right = dueling)")
fig.colorbar(hb, label="log10(tweets)"); fig.tight_layout(); fig.savefig(f"{FIG}/netstance_density.png"); plt.close(fig)

# Fig 3: quadrant composition by topic
tops = tb[tb.topic != "Unassigned"].topic.tolist()
comp = []
for tp in tops:
    g = frame[frame.topic == tp]
    comp.append([ (g.quad == q).mean() for q in ["both", "A_only", "B_only", "neither"]])
comp = np.array(comp) * 100
fig, ax = plt.subplots(figsize=(7.5, 4))
bottom = np.zeros(len(tops))
colors = {"both": "#55A868", "A_only": "#4C72B0", "B_only": "#C44E52", "neither": "#999999"}
for j, q in enumerate(["both", "A_only", "B_only", "neither"]):
    ax.bar(tops, comp[:, j], bottom=bottom, label=q, color=colors[q]); bottom += comp[:, j]
ax.set_ylabel("% of topic's well-covered tweets"); ax.set_title("Quadrant composition by topic")
ax.legend(ncol=4, fontsize=8, loc="lower center", bbox_to_anchor=(0.5, -0.28))
fig.tight_layout(); fig.savefig(f"{FIG}/topic_quadrants.png"); plt.close(fig)

# Fig 4: anchoring distinctive terms (diverging)
termsA = topA[:15][::-1]; termsB = topB[:15]
labels = [w for w, _ in termsB] + [w for w, _ in termsA]
vals = [s for _, s in termsB] + [s for _, s in termsA]
fig, ax = plt.subplots(figsize=(7, 7))
cols = ["#C44E52" if v < 0 else "#4C72B0" for v in vals]
ax.barh(range(len(vals)), vals, color=cols)
ax.set_yticks(range(len(labels))); ax.set_yticklabels(labels, fontsize=8)
ax.axvline(0, color="k", lw=0.6)
ax.set_xlabel("log-odds z   (← Group B pole      Group A pole →)")
ax.set_title("Distinctive terms in each pole's preferred misleading notes")
fig.tight_layout(); fig.savefig(f"{FIG}/anchor_terms.png"); plt.close(fig)

print(f"wrote 4 figures to {FIG}/")
print("DONE")
