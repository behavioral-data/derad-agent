"""Landscape-first Community Notes retrieval package."""

__version__ = "2.0.0"

from .runtime.landscape_api import build_landscape_index, retrieve_statement_landscape
from .runtime.landscape_agent import run_landscape_agent
from .llm.config import INDEX_ROOT, INDEX_NAME, NOTES_TSV_ROOT, get_embedder

__all__ = [
    "build_landscape_index",
    "retrieve_statement_landscape",
    "run_landscape_agent",
    "INDEX_ROOT",
    "INDEX_NAME",
    "NOTES_TSV_ROOT",
    "get_embedder",
]
