"""Build and save per-tweet embeddings from raw Community Notes TSVs.

Reads two files from the public Community Notes dataset:

  notes.tsv             — note content (noteId, tweetId, summary, classification,
                          createdAtMillis, …)
  noteStatusHistory.tsv — rating outcomes (noteId, currentStatus, …)

Joins on noteId, keeps only CURRENTLY_RATED_HELPFUL notes, and builds a
tweet-level embedding from the most recent N helpful note summaries.

Only tweets with at least one helpful note are written to the index.
The notes_cache sidecar stores only helpful notes (sorted by recency),
so no runtime filtering is needed.

Outputs three files under ``--out``:
- ``tweet_ids.npy``    — object array of tweet IDs (row order matches embeddings).
- ``embeddings.npy``   — float32 [num_tweets, dim] matrix of tweet-level embeddings.
- ``notes_cache.json`` — {tweet_id: [note records, most-recent first]}
                         where every record has current_status == CURRENTLY_RATED_HELPFUL.
"""

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from derad_agent.llm.config import get_embedder
from derad_agent.runtime.notes_index import HELPFUL_STATUS


def _safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _load_status_map(status_tsv: Path) -> dict:
    """Return {noteId: currentStatus} from noteStatusHistory.tsv."""
    status_map = {}
    with status_tsv.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            note_id = row.get("noteId", "").strip()
            status = row.get("currentStatus", "").strip()
            if note_id:
                status_map[note_id] = status
    return status_map


def main():
    parser = argparse.ArgumentParser(
        description="Build tweet-level notes index from raw Community Notes TSVs."
    )
    parser.add_argument("notes_tsv", type=Path, help="Path to notes.tsv (note content)")
    parser.add_argument(
        "status_tsv", type=Path,
        help="Path to noteStatusHistory.tsv (rating outcomes)",
    )
    parser.add_argument("--out", type=Path, default=Path("indexes/notes_index"))
    parser.add_argument(
        "--notes-for-embedding", type=int, default=15, metavar="N",
        help="Max helpful notes per tweet used for embedding text, most recent first (default: 15)",
    )
    args = parser.parse_args()

    print(f"Loading status map from {args.status_tsv} …")
    status_map = _load_status_map(args.status_tsv)
    print(f"  {len(status_map):,} note statuses loaded")

    # Collect only helpful notes, grouped by tweet
    note_records: dict[str, list] = defaultdict(list)
    skipped = 0
    with args.notes_tsv.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            tweet_id = row.get("tweetId", "").strip()
            summary = row.get("summary", "").strip()
            note_id = row.get("noteId", "").strip()
            if not tweet_id or not summary:
                continue
            if status_map.get(note_id) != HELPFUL_STATUS:
                skipped += 1
                continue
            note_records[tweet_id].append(
                {
                    "note_id": note_id,
                    "summary": summary,
                    "classification": row.get("classification", "").strip(),
                    "created_at_millis": _safe_int(row.get("createdAtMillis")),
                    "current_status": HELPFUL_STATUS,
                }
            )

    tweet_ids = sorted(note_records)
    total_notes = sum(len(v) for v in note_records.values())
    print(
        f"{len(tweet_ids):,} tweets with helpful notes  "
        f"({total_notes:,} helpful notes kept, {skipped:,} non-helpful skipped)"
    )

    # Sort each tweet's notes by recency; use top N for the embedding text
    texts = []
    for tid in tweet_ids:
        notes = sorted(
            note_records[tid],
            key=lambda n: (n.get("created_at_millis") or 0),
            reverse=True,
        )
        note_records[tid] = notes
        embedding_notes = notes[: args.notes_for_embedding]
        texts.append("\n\n".join(n["summary"] for n in embedding_notes))

    embedder = get_embedder()
    all_embeddings = []
    for i in range(0, len(texts), 500):
        batch = texts[i : i + 500]
        all_embeddings.extend(embedder.embed_documents(batch))
        print(f"  embedded {min(i + 500, len(texts))}/{len(texts)}")

    args.out.mkdir(parents=True, exist_ok=True)
    np.save(args.out / "tweet_ids.npy", np.array(tweet_ids, dtype=object), allow_pickle=True)
    np.save(args.out / "embeddings.npy", np.array(all_embeddings, dtype="float32"))

    notes_cache = {tid: note_records[tid] for tid in tweet_ids}
    with (args.out / "notes_cache.json").open("w", encoding="utf-8") as f:
        json.dump(notes_cache, f, ensure_ascii=False)

    print(f"Saved to {args.out}/")


if __name__ == "__main__":
    main()
