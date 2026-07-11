"""Stage 2: join ratings to rater & note factors + note metadata (spec §4 Stage 2).
Runs inside run_factors.py on the already-loaded ratings (no raw re-read)."""
import pandas as pd
from .constants import MISLEADING, NOT_MISLEADING

_RATING_TAGS = ["notHelpfulNoteNotNeeded", "notHelpfulIncorrect",
                "notHelpfulSourcesMissingOrUnreliable"]
_NOTE_SUBTAGS = ["misleadingFactualError", "misleadingMissingImportantContext",
                 "misleadingManipulatedMedia", "misleadingUnverifiedClaimAsFact",
                 "misleadingSatire", "misleadingOther"]


def build_ratings_with_factors(ratings, notes, rater_factors, note_factors):
    classified = notes[notes["classification"].isin([MISLEADING, NOT_MISLEADING])]
    note_cols = ["noteId", "tweetId", "classification"] + [c for c in _NOTE_SUBTAGS if c in classified.columns]
    rating_cols = (["noteId", "raterParticipantId", "helpfulNum"]
                   + [c for c in (["ratingSourceBucketed"] + _RATING_TAGS) if c in ratings.columns])
    out = (ratings[rating_cols]
           .merge(rater_factors[["raterParticipantId", "f_u", "group"]],
                  on="raterParticipantId", how="inner")          # drops un-factored raters
           .merge(classified[note_cols], on="noteId", how="inner")  # classified notes only
           .merge(note_factors[["noteId", "f_n"]], on="noteId", how="left"))
    return out
