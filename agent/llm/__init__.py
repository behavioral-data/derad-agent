"""LLM configuration, embedding helpers, and path constants."""

from .config import get_embedder, get_llm, INDEX_ROOT, NOTES_TSV_ROOT

__all__ = [
    "get_embedder",
    "get_llm",
    "INDEX_ROOT",
    "NOTES_TSV_ROOT",
]
