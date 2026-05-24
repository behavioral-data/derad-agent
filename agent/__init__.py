"""derad-agent: retrieve Community Notes and respond to any claim with evidence-backed reasoning."""

__version__ = "2.0.0"

from .runtime.landscape_api import retrieve_statement_landscape
from .runtime.landscape_agent import run_landscape_agent
from .llm.config import INDEX_ROOT, NOTES_TSV_ROOT

__all__ = [
    "retrieve_statement_landscape",
    "run_landscape_agent",
    "INDEX_ROOT",
    "NOTES_TSV_ROOT",
]
