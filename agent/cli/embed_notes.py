"""Build and save per-tweet embeddings from raw Community Notes TSVs.

Reads two files from the public Community Notes dataset:

  notes.tsv             — note content (noteId, tweetId, summary, classification,
                          createdAtMillis, …)
  noteStatusHistory.tsv — rating outcomes (noteId, currentStatus, …)

Joins on noteId, keeps only CURRENTLY_RATED_HELPFUL notes, and builds a
tweet-level embedding from the most recent N helpful note summaries.

Only tweets with at least one helpful note are written to the index.
The notes_cache sidecar stores only helpful notes (sorted by recency),
so no runtime filtering is needed at query time.

Outputs three files under ``--out``:
- ``tweet_ids.npy``    — object array of tweet IDs (row order matches embeddings).
- ``embeddings.npy``   — float32 [num_tweets, dim] matrix of tweet-level embeddings.
- ``notes_cache.json`` — {tweet_id: [note records, most-recent first]}
                         where every record has current_status == CURRENTLY_RATED_HELPFUL.

Intermediate checkpoints are written to ``--out/_checkpoint/`` so a crashed
run can be resumed without re-embedding already-processed tweets. The checkpoint
directory is deleted automatically on successful completion.
"""

import argparse
import csv
import json
import shutil
from collections import defaultdict
from pathlib import Path

import numpy as np

from agent.llm.config import get_embedder
from agent.runtime.notes_index import HELPFUL_STATUS


BATCH_SIZE = 500
_CHECKPOINT_DIR = "_checkpoint"
_PROGRESS_FILE = "progress.json"


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


def _load_checkpoint(checkpoint_dir: Path) -> tuple[int, list[np.ndarray]]:
    """Return (start_tweet_idx, list_of_chunk_arrays) from a previous run, or (0, [])."""
    progress_file = checkpoint_dir / _PROGRESS_FILE
    if not progress_file.exists():
        return 0, []
    progress = json.loads(progress_file.read_text())
    done = progress["done"]
    chunks = [np.load(p) for p in sorted(checkpoint_dir.glob("emb_*.npy"))]
    loaded = sum(len(c) for c in chunks)
    if loaded != done:
        print(f"  WARNING: checkpoint claims {done} done but chunks hold {loaded} — starting fresh")
        return 0, []
    print(f"  Resuming from checkpoint: {done}/{progress['total']} tweets already embedded")
    return done, chunks


def _save_checkpoint(checkpoint_dir: Path, batch_start: int, batch_arr: np.ndarray, done: int, total: int):
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    np.save(checkpoint_dir / f"emb_{batch_start:07d}.npy", batch_arr)
    (checkpoint_dir / _PROGRESS_FILE).write_text(json.dumps({"done": done, "total": total}))


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
        # Apr 2026: dataset max is 14 notes/tweet so this cap never fires; kept for future growth
        help="Max helpful notes per tweet used for embedding text, most recent first (default: 15)",
    )
    args = parser.parse_args()

    # ── Step 1: Load status map ──────────────────────────────────────────
    print(f"[1/4] Loading status map from {args.status_tsv} …")
    status_map = _load_status_map(args.status_tsv)
    print(f"      {len(status_map):,} note statuses loaded")

    # ── Step 2: Filter notes (helpful only), group by tweet ──────────────
    print(f"[2/4] Filtering notes from {args.notes_tsv} …")
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
        f"      {len(tweet_ids):,} tweets with helpful notes "
        f"({total_notes:,} helpful, {skipped:,} non-helpful skipped)"
    )

    # ── Step 3: Sort by recency; save notes_cache and tweet_ids ──────────
    print("[3/4] Sorting notes by recency and saving intermediate artifacts …")
    texts = []
    for tid in tweet_ids:
        notes = sorted(
            note_records[tid],
            key=lambda n: (n.get("created_at_millis") or 0),
            reverse=True,
        )
        note_records[tid] = notes
        texts.append("\n\n".join(n["summary"] for n in notes[: args.notes_for_embedding]))

    args.out.mkdir(parents=True, exist_ok=True)
    np.save(args.out / "tweet_ids.npy", np.array(tweet_ids, dtype=object), allow_pickle=True)
    notes_cache = {tid: note_records[tid] for tid in tweet_ids}
    with (args.out / "notes_cache.json").open("w", encoding="utf-8") as f:
        json.dump(notes_cache, f, ensure_ascii=False)
    print(f"      Saved tweet_ids.npy and notes_cache.json → {args.out}/")

    # ── Step 4: Embed with checkpointing ─────────────────────────────────
    print("[4/4] Embedding …")
    checkpoint_dir = args.out / _CHECKPOINT_DIR
    start_idx, chunks = _load_checkpoint(checkpoint_dir)

    embedder = get_embedder()
    for i in range(start_idx, len(texts), BATCH_SIZE):
        batch = texts[i : i + BATCH_SIZE]
        batch_arr = np.array(embedder.embed_documents(batch), dtype="float32")
        chunks.append(batch_arr)
        done = i + len(batch)
        _save_checkpoint(checkpoint_dir, i, batch_arr, done, len(texts))
        print(f"      {done}/{len(texts)} tweets embedded")

    embeddings = np.concatenate(chunks, axis=0) if chunks else np.empty((0,), dtype="float32")
    np.save(args.out / "embeddings.npy", embeddings)

    shutil.rmtree(checkpoint_dir, ignore_errors=True)
    print(f"\nDone. Index saved to {args.out}/")


if __name__ == "__main__":
    main()
