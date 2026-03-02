"""
Centralised config — Azure-aware.
Loads credentials from .env and exposes helpers:
    get_embedder()  -> AzureOpenAIEmbeddings
"""
from pathlib import Path
import os
import warnings
from dotenv import load_dotenv

# ────────────────────────────────────────────────────────────────────────────
# 0.  .env
# ────────────────────────────────────────────────────────────────────────────
load_dotenv(Path(__file__).resolve().parent / ".env")

# Package and repository roots for portable defaults
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _PACKAGE_ROOT.parent

# ────────────────────────────────────────────────────────────────────────────
# 1.  Generic tunables
# ────────────────────────────────────────────────────────────────────────────
def _path_from_env(env_name: str, default: Path) -> Path:
    value = os.getenv(env_name)
    if value:
        return Path(value).expanduser().resolve()
    return default


NOTES_TSV_ROOT = _path_from_env(
    "DERAD_AGENT_NOTES_TSV_ROOT",
    _REPO_ROOT / "data" / "full",
)
INDEX_ROOT = _path_from_env(
    "DERAD_AGENT_INDEX_ROOT",
    _REPO_ROOT / "indexes",
)

INDEX_NAME = "faiss_idx"

# Constants are defined in shared/constants.py; re-exported here.
from derad_agent.shared.constants import (  # noqa: F401
    POST_SNIP_TOKENS,
    CTX_PARENT_TOKENS,
    CTX_TOP_TOKENS,
    USER_SPLIT_TOKENS,
    CHUNK_MAX_TOKENS,
    K_SEMANTIC,
    MAX_PER_THREAD,
)

from langchain_openai import AzureOpenAIEmbeddings as _EmbCls  # type: ignore

# GPT-5 Responses API requires 2025-03-01-preview or later for reasoning controls
_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2025-03-01-preview")


def _validate_env() -> None:
    """Warn early if critical env vars for embedding are missing."""
    required = ["AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT_EMBED"]
    missing = [v for v in required if not os.getenv(v)]
    if missing:
        warnings.warn(
            f"Missing environment variables for Azure OpenAI: {missing}. "
            f"Embedding/LLM operations will fail. Check your .env file.",
            stacklevel=2,
        )


_validate_env()


def _require_env(var: str) -> str:
    value = os.getenv(var)
    if not value:
        raise ValueError(f"Missing required environment variable: {var}")
    return value


def get_embedder():
    deployment = _require_env("AZURE_OPENAI_DEPLOYMENT_EMBED")
    endpoint = _require_env("AZURE_OPENAI_ENDPOINT")
    key = _require_env("AZURE_OPENAI_API_KEY")
    real_embedder = _EmbCls(
        azure_deployment=deployment,
        azure_endpoint=endpoint,
        api_key=key,
        api_version=_API_VERSION,
    )
    from derad_agent.indexing.tracked_embedder import TrackedEmbedder

    return TrackedEmbedder(real_embedder)


def get_llm(
    temperature: float = None,
    max_tokens: int = 2048,
    reasoning_effort: str = None,
    text_verbosity: str = None
):
    """
    Get Azure OpenAI LLM instance for chat/completion.
    
    Args:
        temperature: Sampling temperature (0.0-2.0)
        max_tokens: Maximum tokens to generate
        reasoning_effort: GPT-5 reasoning effort level: "minimal", "low", "medium", "high"
        text_verbosity: GPT-5 text verbosity level: "low", "medium", "high"
        
    Returns:
        AzureChatOpenAI instance
    """
    from langchain_openai import AzureChatOpenAI
    
    deployment = _require_env("AZURE_OPENAI_DEPLOYMENT_CHAT")
    endpoint = _require_env("AZURE_OPENAI_ENDPOINT")
    key = _require_env("AZURE_OPENAI_API_KEY")
    
    # Base configuration
    config = {
        "azure_deployment": deployment,
        "azure_endpoint": endpoint,
        "api_key": key,
        "api_version": _API_VERSION,
        "max_tokens": max_tokens,
    }
    if temperature is not None:
        config["temperature"] = temperature
    
    # Add GPT-5 specific parameters when supported by installed langchain-openai.
    if reasoning_effort is not None:
        config["reasoning"] = {"effort": reasoning_effort}
    if text_verbosity is not None:
        config["verbosity"] = text_verbosity

    try:
        return AzureChatOpenAI(**config)
    except TypeError as exc:
        unsupported = ("reasoning" in config) or ("verbosity" in config)
        if not unsupported:
            raise
        warnings.warn(
            "Installed langchain-openai does not support reasoning/verbosity; "
            "falling back to standard AzureChatOpenAI arguments.",
            stacklevel=2,
        )
        fallback_config = dict(config)
        fallback_config.pop("reasoning", None)
        fallback_config.pop("verbosity", None)
        try:
            return AzureChatOpenAI(**fallback_config)
        except TypeError:
            raise exc


__all__ = [
    "NOTES_TSV_ROOT",
    "INDEX_ROOT",
    "INDEX_NAME",
    "POST_SNIP_TOKENS",
    "CTX_PARENT_TOKENS",
    "CTX_TOP_TOKENS",
    "USER_SPLIT_TOKENS",
    "CHUNK_MAX_TOKENS",
    "K_SEMANTIC",
    "MAX_PER_THREAD",
    "get_embedder",
    "get_llm",
]
 
