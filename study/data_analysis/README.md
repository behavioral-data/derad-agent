# Study 2 analysis dataset

419 participants, 14172 post-level observations. Built by
`study.analysis.build_dataset` from the three Qualtrics exports, the interface
telemetry, and Prolific submission records; packaged by
`study.analysis.export_share`.

## Files

| file | grain |
| --- | --- |
| `participants.csv` | one row per participant |
| `post_ratings.csv` | one row per (participant, day, position) — the trial-level table |
| `post_attributes.csv` | one row per post: topic, polarity, lean |
| `note_reasons.csv` | one row per fielded Community Note: the note author's own stated reason |
| `item_wording.csv` | every survey item as shown to participants |
| `CODEBOOK.md` | what every column means |

Join `post_ratings.csv` to `participants.csv` on `pid`, and to the post files on
`post_id`.

## Read this before modelling

**Participant IDs are pseudonyms.** `P0001`-style. The mapping to Prolific IDs is not
in this bundle.

**Two exclusion flags, not applied.** `blank_frame` marks 305 trials where the
embedded frame never loaded, so the participant rated an empty panel — drop those
ratings. `dwell_overwritten` marks 692 trials whose *timings* were overwritten by a
form redisplay; the ratings on those rows are fine, the timings are not.

**Design structure.** Posts are crossed with participants: each of 108 posts appears
in all four conditions, and every completer saw exactly 6 posts per topic and 12 per
polarity stratum. Models that treat trials as independent will understate uncertainty
for anything that varies at the post level — topic and polarity especially, since
there are only 18 posts per topic cell.

**Scales are five-point**, coded 1–5. If you have seen a draft describing 11-point
sliders, that describes an earlier instrument, not this one.

**No scale scores are included.** Subscale construction is an analysis decision. How
it was done here is in `study/analysis/run_analyses.py`.
