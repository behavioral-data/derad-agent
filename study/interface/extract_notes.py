"""One-time join: reduce the large Community Notes dumps to the single shown
note per study post. Writes a small, committable CSV consumed by build_db.

Heavy step: the notes/status TSVs are ~1.4 GB / ~800 MB. Run once; the output
(study/data/notes.csv) is committed so build_db stays fast.
"""
from __future__ import annotations

import argparse
import csv
import html
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))          # .../study/interface
_STUDY = os.path.dirname(_HERE)                              # .../study
_ROOT = os.path.dirname(_STUDY)                              # repo root
DEFAULT_SELECTED = os.path.join(_STUDY, "data", "posts.csv")
DEFAULT_NOTES = os.path.join(_ROOT, "tsv_generation", "cn_data_20260630", "notes-00000.tsv")
DEFAULT_STATUS = os.path.join(_ROOT, "tsv_generation", "cn_data_20260630", "noteStatusHistory-00000.tsv")
DEFAULT_OUT = os.path.join(_STUDY, "data", "notes.csv")

csv.field_size_limit(10_000_000)  # note summaries can be long


def select_shown_note(candidates):
    """Pick the displayed note: most-recent CURRENTLY_RATED_HELPFUL, else
    most-recent overall, else None."""
    if not candidates:
        return None
    crh = [c for c in candidates if c["status"] == "CURRENTLY_RATED_HELPFUL"]
    pool = crh if crh else candidates
    return max(pool, key=lambda c: c["ts"])


def _wanted_ids(selected_csv):
    with open(selected_csv, newline="") as f:
        return {r["tweetId"] for r in csv.DictReader(f)}


def extract(selected_csv, notes_tsv, status_tsv, out_csv):
    wanted = _wanted_ids(selected_csv)

    # Pass A: collect candidate notes (text/classification) for wanted tweets.
    by_tweet = {}            # tweetId -> list of partial candidate dicts
    note_ids = set()
    with open(notes_tsv, newline="") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            tid = r["tweetId"]
            if tid not in wanted:
                continue
            by_tweet.setdefault(tid, []).append({
                "noteId": r["noteId"],
                "summary": html.unescape(r["summary"]),
                "classification": r["classification"],
            })
            note_ids.add(r["noteId"])

    # Pass B: status + timestamp for just those candidate notes.
    status = {}              # noteId -> (currentStatus, ts)
    with open(status_tsv, newline="") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            nid = r["noteId"]
            if nid not in note_ids:
                continue
            try:
                ts = int(r["timestampMillisOfCurrentStatus"] or 0)
            except ValueError:
                ts = 0
            status[nid] = (r["currentStatus"], ts)

    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    written = 0
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["tweetId", "noteId", "classification", "summary"])
        w.writeheader()
        for tid, cands in by_tweet.items():
            for c in cands:
                st, ts = status.get(c["noteId"], ("", 0))
                c["status"], c["ts"] = st, ts
            chosen = select_shown_note(cands)
            if chosen is None:
                continue
            w.writerow({
                "tweetId": tid,
                "noteId": chosen["noteId"],
                "classification": chosen["classification"],
                "summary": chosen["summary"],
            })
            written += 1
    return written


def main():
    ap = argparse.ArgumentParser(description="Extract the shown community note per study post.")
    ap.add_argument("--selected", default=DEFAULT_SELECTED)
    ap.add_argument("--notes", default=DEFAULT_NOTES)
    ap.add_argument("--status", default=DEFAULT_STATUS)
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args()
    n = extract(args.selected, args.notes, args.status, args.out)
    print(f"Wrote {n} notes to {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
