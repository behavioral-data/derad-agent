"""
Community Notes TSV -> chunks -> FAISS index.
"""

from __future__ import annotations

import importlib
import pathlib
import re
import time
import math
from typing import Any, Iterable, List, Optional, Sequence

from openai import RateLimitError

from derad_agent.llm.config import INDEX_NAME, INDEX_ROOT, NOTES_TSV_ROOT, get_embedder

from .chunker import chunk_record
from .tsv_reader import iter_notes_from_paths, list_tweet_ids

DEFAULT_MAX_RETRIES = 5
DEFAULT_INITIAL_RETRY_DELAY = 5
DEFAULT_EMBEDDING_BATCH_SIZE = 2000
DEFAULT_PER_BATCH_SLEEP_SECONDS = 0.0
GLOBAL_INDEX_DIR_NAME = "community_notes_global"


def _sanitize_dir_name(value: str) -> str:
    sanitized = re.sub(r"[^0-9A-Za-z._-]+", "_", value)
    sanitized = sanitized.strip("_")
    return sanitized or "unknown"


def get_global_index_dir(index_root: Optional[pathlib.Path] = None) -> pathlib.Path:
    index_root = index_root or INDEX_ROOT
    return index_root / GLOBAL_INDEX_DIR_NAME


def get_index_dir_for_tweet(tweet_id: str, index_root: Optional[pathlib.Path] = None) -> pathlib.Path:
    index_root = index_root or INDEX_ROOT
    return index_root / f"tweet_{_sanitize_dir_name(tweet_id)}"


def _resolve_tsv_paths(
    tsv_root: Optional[pathlib.Path] = None,
    tsv_files: Optional[Sequence[pathlib.Path]] = None,
) -> List[pathlib.Path]:
    if tsv_files:
        resolved = [p.resolve() for p in tsv_files if p and p.exists()]
        if resolved:
            return sorted(resolved)
    root = (tsv_root or NOTES_TSV_ROOT).resolve()
    if root.is_file() and root.suffix.lower() == ".tsv":
        return [root]
    if not root.exists():
        raise FileNotFoundError(f"TSV root not found: {root}")
    return sorted(p.resolve() for p in root.glob("*.tsv"))


def _iter_records_for_tweet(tsv_paths: Sequence[pathlib.Path], tweet_id: str) -> Iterable[dict]:
    for record in iter_notes_from_paths(tsv_paths):
        if record.get("tweet_id") == tweet_id:
            yield record


def _iter_records_all(tsv_paths: Sequence[pathlib.Path]) -> Iterable[dict]:
    yield from iter_notes_from_paths(tsv_paths)


def _estimate_record_count(tsv_paths: Sequence[pathlib.Path]) -> int:
    """
    Estimate record count as (line_count - header) across TSV files.
    This is an exact count for standard TSV files and enables ETA reporting.
    """
    total = 0
    for path in tsv_paths:
        line_count = 0
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                line_count += chunk.count(b"\n")
        # Subtract header if file has at least one line.
        if line_count > 0:
            line_count -= 1
        total += max(0, line_count)
    return total


def _build_from_records(
    records: Iterable[dict],
    *,
    index_dir_path: pathlib.Path,
    total_expected_chunks: Optional[int] = None,
    embedding_batch_size: Optional[int] = None,
    max_retries: Optional[int] = None,
    initial_retry_delay: Optional[int] = None,
    per_batch_sleep_seconds: Optional[float] = None,
) -> tuple[int, int]:
    emb = get_embedder()
    batch_size = embedding_batch_size or DEFAULT_EMBEDDING_BATCH_SIZE
    retries = max_retries if max_retries is not None else DEFAULT_MAX_RETRIES
    retry_delay = initial_retry_delay if initial_retry_delay is not None else DEFAULT_INITIAL_RETRY_DELAY
    per_batch_sleep = (
        per_batch_sleep_seconds
        if per_batch_sleep_seconds is not None
        else DEFAULT_PER_BATCH_SLEEP_SECONDS
    )

    if batch_size <= 0:
        raise ValueError("embedding_batch_size must be > 0")

    faiss_module = importlib.import_module("langchain_community.vectorstores")
    FAISS = getattr(faiss_module, "FAISS")

    index_dir_path.mkdir(parents=True, exist_ok=True)
    db = None
    batch_texts: List[str] = []
    batch_metas: List[dict[str, Any]] = []
    record_count = 0
    chunk_count = 0
    processed_chunks = 0
    batch_count = 0
    batch_durations: List[float] = []
    start_time = time.time()

    def process_batch(db_handle):
        nonlocal processed_chunks, batch_count, batch_durations
        if not batch_texts:
            return db_handle
        texts = list(batch_texts)
        metas = list(batch_metas)
        active_retry_delay = retry_delay
        batch_started = time.time()
        for attempt in range(retries):
            try:
                embeddings = emb.embed_documents(texts)
                text_embeddings = list(zip(texts, embeddings))
                if db_handle is None:
                    db_handle = FAISS.from_embeddings(
                        text_embeddings=text_embeddings,
                        embedding=emb,
                        metadatas=metas,
                    )
                else:
                    db_handle.add_embeddings(text_embeddings=text_embeddings, metadatas=metas)

                batch_elapsed = time.time() - batch_started
                batch_durations.append(batch_elapsed)
                batch_count += 1
                processed_chunks += len(texts)

                elapsed = time.time() - start_time
                avg_batch = sum(batch_durations) / len(batch_durations)
                avg_chunk_sec = elapsed / processed_chunks if processed_chunks else 0.0
                chunks_per_sec = (processed_chunks / elapsed) if elapsed > 0 else 0.0

                if total_expected_chunks and total_expected_chunks > 0:
                    pct = (processed_chunks / total_expected_chunks) * 100.0
                    remaining = max(0, total_expected_chunks - processed_chunks)
                    eta_sec = remaining * avg_chunk_sec
                    print(
                        "[index] batch "
                        f"{batch_count}/{math.ceil(total_expected_chunks / batch_size)} "
                        f"processed={processed_chunks}/{total_expected_chunks} "
                        f"({pct:.2f}%) batch_sec={batch_elapsed:.2f} "
                        f"avg_batch_sec={avg_batch:.2f} chunks_per_sec={chunks_per_sec:.2f} "
                        f"eta_min={eta_sec/60.0:.1f}",
                        flush=True,
                    )
                else:
                    print(
                        "[index] batch "
                        f"{batch_count} processed={processed_chunks} "
                        f"batch_sec={batch_elapsed:.2f} avg_batch_sec={avg_batch:.2f} "
                        f"chunks_per_sec={chunks_per_sec:.2f}",
                        flush=True,
                    )
                return db_handle
            except RateLimitError:
                if attempt >= retries - 1:
                    raise
                time.sleep(active_retry_delay)
                active_retry_delay *= 2
        return db_handle

    for record in records:
        record_count += 1
        source_hint = f"tsv:{record.get('tweet_id')}:{record.get('note_id')}"
        for text, meta in chunk_record(record, source_hint=source_hint):
            batch_texts.append(text)
            batch_metas.append(meta)
            chunk_count += 1
            if len(batch_texts) >= batch_size:
                db = process_batch(db)
                batch_texts.clear()
                batch_metas.clear()
                if per_batch_sleep and per_batch_sleep > 0:
                    time.sleep(per_batch_sleep)

    if batch_texts:
        db = process_batch(db)

    if db is not None:
        db.save_local(index_dir_path)

    return record_count, chunk_count


def build_tweet_index(
    tweet_id: str,
    *,
    tsv_root: Optional[pathlib.Path] = None,
    tsv_files: Optional[Sequence[pathlib.Path]] = None,
    index_root: Optional[pathlib.Path] = None,
    max_retries: Optional[int] = None,
    initial_retry_delay: Optional[int] = None,
    embedding_batch_size: Optional[int] = None,
    per_batch_sleep_seconds: Optional[float] = None,
    **_: Any,
) -> None:
    paths = _resolve_tsv_paths(tsv_root=tsv_root, tsv_files=tsv_files)
    total_expected_chunks = None
    out_dir = get_index_dir_for_tweet(tweet_id, index_root=index_root) / INDEX_NAME
    record_count, chunk_count = _build_from_records(
        _iter_records_for_tweet(paths, tweet_id),
        index_dir_path=out_dir,
        total_expected_chunks=total_expected_chunks,
        embedding_batch_size=embedding_batch_size,
        max_retries=max_retries,
        initial_retry_delay=initial_retry_delay,
        per_batch_sleep_seconds=per_batch_sleep_seconds,
    )
    if record_count == 0 or chunk_count == 0:
        print(f"[WARN] No usable notes found for tweet {tweet_id}; nothing indexed.")


def build_global_index(
    *,
    tsv_root: Optional[pathlib.Path] = None,
    tsv_files: Optional[Sequence[pathlib.Path]] = None,
    index_root: Optional[pathlib.Path] = None,
    max_retries: Optional[int] = None,
    initial_retry_delay: Optional[int] = None,
    embedding_batch_size: Optional[int] = None,
    per_batch_sleep_seconds: Optional[float] = None,
) -> None:
    paths = _resolve_tsv_paths(tsv_root=tsv_root, tsv_files=tsv_files)
    total_expected_chunks = _estimate_record_count(paths)
    print(
        f"[index] estimated_records={total_expected_chunks} "
        f"batch_size={embedding_batch_size or DEFAULT_EMBEDDING_BATCH_SIZE} "
        f"estimated_batches={math.ceil(total_expected_chunks / (embedding_batch_size or DEFAULT_EMBEDDING_BATCH_SIZE)) if total_expected_chunks else 0}",
        flush=True,
    )
    out_dir = get_global_index_dir(index_root=index_root) / INDEX_NAME
    record_count, chunk_count = _build_from_records(
        _iter_records_all(paths),
        index_dir_path=out_dir,
        total_expected_chunks=total_expected_chunks,
        embedding_batch_size=embedding_batch_size,
        max_retries=max_retries,
        initial_retry_delay=initial_retry_delay,
        per_batch_sleep_seconds=per_batch_sleep_seconds,
    )
    if record_count == 0 or chunk_count == 0:
        print("[WARN] No usable notes found across TSV files; nothing indexed.")


def list_available_tweets(
    tsv_root: Optional[pathlib.Path] = None,
    tsv_files: Optional[Sequence[pathlib.Path]] = None,
) -> List[str]:
    paths = _resolve_tsv_paths(tsv_root=tsv_root, tsv_files=tsv_files)
    return list_tweet_ids(paths)


