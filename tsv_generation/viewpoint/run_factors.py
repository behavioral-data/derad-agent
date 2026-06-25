"""Stage 1+2 driver: one read pass -> Expansion MF factors + ratings_with_factors.

Reuses the repo scorer's own filtering and MF methods (no reimplementation):
  scorer._filter_input -> _prepare_data_for_scoring -> _run_stable_matrix_factorization.
Skips topic model, PFlip/PCRH, diligence/harassment MFs, refit, status, contributor, PSS.
"""
import argparse
import os
import sys

import pandas as pd

from .constants import SEED, SCORING_SRC, CN_DATA, OUT_DIR
from .groups import assign_group
from .stage2 import build_ratings_with_factors

sys.path.insert(0, SCORING_SRC)
import scoring.constants as c                       # noqa: E402
from scoring.process_data import LocalDataLoader     # noqa: E402
from scoring.mf_expansion_scorer import MFExpansionScorer  # noqa: E402


def run(sample_ratings=0.0):
    os.makedirs(OUT_DIR, exist_ok=True)

    # --- load once (normalized participant IDs; consistent across factors & join) ---
    loader = LocalDataLoader(
        notesPath=f"{CN_DATA}/notes-00000.tsv",
        ratingsPath=f"{CN_DATA}/ratings",
        noteStatusHistoryPath=f"{CN_DATA}/noteStatusHistory-00000.tsv",
        userEnrollmentPath=f"{CN_DATA}/userEnrollment-00000.tsv",
        headers=True,
    )
    notes, ratings, nsh, userEnrollment = loader.get_data()
    if sample_ratings > 0:
        ratings = ratings.sample(frac=sample_ratings, random_state=SEED)

    # --- Stage 1: single Expansion MF (stable-init OFF for speed) ---
    scorer = MFExpansionScorer(seed=SEED, useStableInitialization=False)
    emptyTopics = pd.DataFrame({c.noteIdKey: [], c.noteTopicKey: []})  # unused by Expansion
    ratings_f, _ = scorer._filter_input(emptyTopics, ratings, nsh, userEnrollment)
    prep = scorer._prepare_data_for_scoring(
        ratings_f[[c.noteIdKey, c.raterParticipantIdKey, c.helpfulNumKey,
                   c.createdAtMillisKey, c.helpfulnessLevelKey,
                   c.notHelpfulIncorrectTagKey, c.notHelpfulIrrelevantSourcesTagKey,
                   c.notHelpfulSourcesMissingOrUnreliableTagKey,
                   c.notHelpfulSpamHarassmentOrAbuseTagKey, c.notHelpfulOtherTagKey]]
    )
    noteParams, raterParams, _gi = scorer._run_stable_matrix_factorization(
        prep[[c.noteIdKey, c.raterParticipantIdKey, c.helpfulNumKey]],
        userEnrollment[[c.participantIdKey, c.modelingGroupKey]],
    )

    rater_factors = raterParams[[c.raterParticipantIdKey, c.internalRaterFactor1Key]].rename(
        columns={c.raterParticipantIdKey: "raterParticipantId", c.internalRaterFactor1Key: "f_u"}
    ).dropna(subset=["f_u"])
    rater_factors["group"] = assign_group(rater_factors["f_u"].to_numpy())
    note_factors = noteParams[[c.noteIdKey, c.internalNoteFactor1Key, c.internalNoteInterceptKey]].rename(
        columns={c.noteIdKey: "noteId", c.internalNoteFactor1Key: "f_n",
                 c.internalNoteInterceptKey: "i_n"}
    )
    rater_factors.to_parquet(f"{OUT_DIR}/rater_factors.parquet", index=False)
    note_factors.to_parquet(f"{OUT_DIR}/note_factors.parquet", index=False)

    # --- Stage 2 (fused): join the already-loaded ratings to factors ---
    notes_renamed = notes.rename(columns={c.noteIdKey: "noteId", c.tweetIdKey: "tweetId",
                                          c.classificationKey: "classification"})
    ratings_renamed = ratings.rename(columns={c.noteIdKey: "noteId",
                                              c.raterParticipantIdKey: "raterParticipantId",
                                              c.helpfulNumKey: "helpfulNum",
                                              c.ratingSourceBucketedKey: "ratingSourceBucketed"})
    rwf = build_ratings_with_factors(ratings_renamed, notes_renamed, rater_factors, note_factors)
    rwf.to_parquet(f"{OUT_DIR}/ratings_with_factors.parquet", index=False)

    n_pos = int((rater_factors["group"] == "A").sum())
    n_neg = int((rater_factors["group"] == "B").sum())
    print(f"factored raters: {len(rater_factors)} (A={n_pos}, B={n_neg}); "
          f"ratings_with_factors rows: {len(rwf)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample-ratings", type=float, default=0.0)
    run(ap.parse_args().sample_ratings)


if __name__ == "__main__":
    main()
