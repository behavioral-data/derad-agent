"""Build and persist a sparse user × note rating matrix from Community Notes ratings TSVs.

Each cell (i, j) holds how user i rated note j: 1 = HELPFUL, -1 = NOT_HELPFUL,
0 = SOMEWHAT_HELPFUL (explicit entry), absent = never rated.
"""

from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
from scipy.sparse import csc_matrix, csr_matrix, load_npz, save_npz

# helpfulnessLevel string → int8 value stored in the matrix
_LEVEL_TO_INT: Dict[str, int] = {
    "HELPFUL": 1,
    "SOMEWHAT_HELPFUL": 0,
    "NOT_HELPFUL": -1,
}

_CHUNK_SIZE = 1_000_000          # rows buffered before flushing to a numpy array
_PROGRESS_INTERVAL = 5_000_000  # print a status line every N valid rows

_MATRIX_FILE = "rating_matrix.npz"
_USER_IDS_FILE = "user_ids.npy"
_NOTE_IDS_FILE = "note_ids.npy"
_META_FILE = "meta.json"


def _open_tsv(path: Path):
    """Open a ratings TSV and return the reader plus column positions for the three fields we need."""
    fh = path.open("r", encoding="utf-8", newline="")
    try:
        reader = csv.reader(fh, delimiter="\t")
        header = next(reader)
        col = {name: i for i, name in enumerate(header)}
        return fh, reader, col["noteId"], col["raterParticipantId"], col["helpfulnessLevel"]
    except (KeyError, StopIteration) as exc:
        fh.close()
        raise ValueError(f"Unexpected TSV format in {path.name}: {exc}") from exc


def _collect_ids(
    tsv_paths: List[Path],
    progress: bool,
) -> Tuple[Dict[str, int], Dict[str, int]]:
    """Pass 1 — stream all TSVs and collect unique user and note IDs into sorted index dicts."""
    user_set: set = set()
    note_set: set = set()
    total = 0
    min_cols = 0

    for path in tsv_paths:
        fh, reader, nc, rc, lc = _open_tsv(path)
        min_cols = max(nc, rc, lc) + 1
        try:
            for row in reader:
                if len(row) < min_cols:
                    continue
                level = row[lc]
                if level not in _LEVEL_TO_INT:
                    continue
                rater = row[rc]
                note = row[nc]
                if not rater or not note:
                    continue
                user_set.add(rater)
                note_set.add(note)
                total += 1
                if progress and total % _PROGRESS_INTERVAL == 0:
                    print(f"  [pass 1] {total:,} ratings scanned — {path.name}")
        finally:
            fh.close()

    if progress:
        print(f"  [pass 1] done — {total:,} valid ratings, "
              f"{len(user_set):,} users, {len(note_set):,} notes")

    user_index = {uid: i for i, uid in enumerate(sorted(user_set))}
    note_index = {nid: i for i, nid in enumerate(sorted(note_set))}
    return user_index, note_index


def _fill_matrix(
    tsv_paths: List[Path],
    user_index: Dict[str, int],
    note_index: Dict[str, int],
    progress: bool,
) -> csr_matrix:
    """Pass 2 — translate IDs to indices and accumulate COO data in numpy chunks, then build CSR."""
    n_users = len(user_index)
    n_notes = len(note_index)

    row_chunks: List[np.ndarray] = []
    col_chunks: List[np.ndarray] = []
    dat_chunks: List[np.ndarray] = []

    rows_buf: List[int] = []
    cols_buf: List[int] = []
    data_buf: List[int] = []

    total = 0
    t0 = time.monotonic()
    min_cols = 0

    def _flush():
        row_chunks.append(np.array(rows_buf, dtype=np.int32))
        col_chunks.append(np.array(cols_buf, dtype=np.int32))
        dat_chunks.append(np.array(data_buf, dtype=np.int8))
        rows_buf.clear()
        cols_buf.clear()
        data_buf.clear()

    for path in tsv_paths:
        fh, reader, nc, rc, lc = _open_tsv(path)
        min_cols = max(nc, rc, lc) + 1
        try:
            for row in reader:
                if len(row) < min_cols:
                    continue
                level = row[lc]
                if level not in _LEVEL_TO_INT:
                    continue
                rater = row[rc]
                note = row[nc]
                if not rater or not note:
                    continue
                u = user_index.get(rater)
                n = note_index.get(note)
                if u is None or n is None:
                    continue
                rows_buf.append(u)
                cols_buf.append(n)
                data_buf.append(_LEVEL_TO_INT[level])
                total += 1
                if len(rows_buf) >= _CHUNK_SIZE:
                    _flush()
                if progress and total % _PROGRESS_INTERVAL == 0:
                    elapsed = time.monotonic() - t0
                    rate = total / max(elapsed, 1e-9)
                    print(f"  [pass 2] {total:,} rows  |  {rate/1e6:.2f}M rows/s  |  {path.name}")
        finally:
            fh.close()

    if rows_buf:
        _flush()

    if progress:
        elapsed = time.monotonic() - t0
        print(f"  [pass 2] done — {total:,} entries in {elapsed:.1f}s")

    if not row_chunks:
        return csr_matrix((n_users, n_notes), dtype=np.int8)

    mat = csr_matrix(
        (np.concatenate(dat_chunks), (np.concatenate(row_chunks), np.concatenate(col_chunks))),
        shape=(n_users, n_notes),
        dtype=np.int8,
    )

    # If a user rated the same note twice, sum then clip to [-1, 1].
    # Explicit zeros are kept: SOMEWHAT_HELPFUL and cancelled pairs are both valid neutral signals.
    mat.sum_duplicates()
    np.clip(mat.data, -1, 1, out=mat.data)

    return mat


def build_rating_matrix(
    tsv_paths: Iterable[Path],
    *,
    progress: bool = True,
) -> Tuple[csr_matrix, np.ndarray, np.ndarray]:
    """Build a sparse user × note rating matrix from one or more ratings TSV files.

    Returns (matrix, user_ids, note_ids) where matrix[i, j] is the int8 rating
    user_ids[i] gave note_ids[j], and absent entries mean the user never rated that note.
    """
    paths = list(tsv_paths)
    if not paths:
        raise ValueError("tsv_paths must contain at least one file.")

    if progress:
        print(f"Building rating matrix from {len(paths)} file(s)…")

    user_index, note_index = _collect_ids(paths, progress)
    matrix = _fill_matrix(paths, user_index, note_index, progress)

    user_ids = np.array(sorted(user_index, key=user_index.__getitem__), dtype=object)
    note_ids = np.array(sorted(note_index, key=note_index.__getitem__), dtype=object)

    if progress:
        print(f"Matrix shape: {matrix.shape}  |  nnz: {matrix.nnz:,}")

    return matrix, user_ids, note_ids


def save_rating_matrix(
    matrix: csr_matrix,
    user_ids: np.ndarray,
    note_ids: np.ndarray,
    out_dir: Path,
) -> None:
    """Write the matrix and ID arrays to out_dir so they can be reloaded without rebuilding."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    save_npz(str(out_dir / _MATRIX_FILE), matrix)
    np.save(str(out_dir / _USER_IDS_FILE), user_ids, allow_pickle=True)
    np.save(str(out_dir / _NOTE_IDS_FILE), note_ids, allow_pickle=True)

    meta = {
        "n_users": int(matrix.shape[0]),
        "n_notes": int(matrix.shape[1]),
        "nnz": int(matrix.nnz),
        "dtype": str(matrix.dtype),
    }
    (out_dir / _META_FILE).write_text(json.dumps(meta, indent=2))
    print(f"Saved rating matrix to {out_dir}/  (nnz={matrix.nnz:,})")


def load_rating_matrix(
    out_dir: Path,
) -> Tuple[csr_matrix, np.ndarray, np.ndarray]:
    """Load a previously saved rating matrix from out_dir. Returns (matrix, user_ids, note_ids)."""
    out_dir = Path(out_dir)
    matrix = load_npz(str(out_dir / _MATRIX_FILE))
    user_ids = np.load(str(out_dir / _USER_IDS_FILE), allow_pickle=True)
    note_ids = np.load(str(out_dir / _NOTE_IDS_FILE), allow_pickle=True)
    return matrix, user_ids, note_ids


def build_id_index(ids: np.ndarray) -> Dict[str, int]:
    """Return a dict mapping each ID string in ids to its integer index for O(1) lookup."""
    return {str(id_val): i for i, id_val in enumerate(ids)}


def user_rating_vector(matrix: csr_matrix, user_idx: int) -> np.ndarray:
    """Return the dense int8 rating vector for a single user (length n_notes)."""
    return matrix[user_idx, :].toarray().astype(np.int8).ravel()


def note_rating_vector(matrix: csr_matrix, note_idx: int, csc: Optional[csc_matrix] = None) -> np.ndarray:
    """Return the dense int8 rating vector for a single note (length n_users).

    Accepts a pre-built CSC matrix via csc= when multiple notes are queried in sequence.
    """
    m = csc if csc is not None else matrix.tocsc()
    return m[:, note_idx].toarray().astype(np.int8).ravel()
