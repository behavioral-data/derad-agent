"""
Runtime module for statement-time landscape operations.

This module contains retrieval utilities and the landscape pipeline used to
produce statement-conditioned Community Notes note spaces.
"""

try:
    from .retriever import retrieve_with_expansion  # noqa: F401
except ImportError as exc:  # pragma: no cover - optional dependency
    def retrieve_with_expansion(*args, **kwargs):  # type: ignore
        raise ImportError(
            "langchain-community is required for retrieval support. "
            "Install it via `pip install langchain-community`."
        ) from exc

from .landscape_agent import run_landscape_agent  # noqa: F401
from .landscape_api import build_landscape_index, retrieve_statement_landscape  # noqa: F401
from .misleadingness import build_misleadingness_landscape, build_bucket_landscape  # noqa: F401
from .steps import (  # noqa: F401
    step_1_generate_queries,
    step_2_retrieve_documents,
    step_3_augment_documents,
    step_4_build_landscape_output,
)

__all__ = [
    'retrieve_with_expansion',
    'build_landscape_index',
    'retrieve_statement_landscape',
    'run_landscape_agent',
    'build_misleadingness_landscape',
    'build_bucket_landscape',
    'step_1_generate_queries',
    'step_2_retrieve_documents',
    'step_3_augment_documents',
    'step_4_build_landscape_output',
]
