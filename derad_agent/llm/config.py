"""Centralised Azure OpenAI configuration.

Loads credentials from ``derad_agent/llm/.env`` and exposes factory
helpers for embedding and chat models, plus path constants for index
and TSV data locations.
"""
import functools
from pathlib import Path
import os
import warnings
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _PACKAGE_ROOT.parent


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

from langchain_openai import AzureOpenAIEmbeddings as _EmbCls  # type: ignore

# GPT-5 Responses API requires 2025-03-01-preview or later for reasoning controls
_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2025-03-01-preview")

# Which LLM provider handles the final reply generation for each tone.
# "openai" → Azure OpenAI (GPT-5-mini)
# "grok"   → Azure AI Services (Grok), requires AZURE_AI_ENDPOINT
STYLE_LLM_PROVIDERS: dict[str, str] = {
    "agreeable": "openai",
    "neutral":   "openai",
    "satirical": "openai",
}


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


def _parse_bool_env(var: str, default: bool = False) -> bool:
    return os.getenv(var, str(default).lower()).lower() == "true"


def get_embedder():
    """Return an Azure OpenAI embedding model."""
    return _EmbCls(
        azure_deployment=_require_env("AZURE_OPENAI_DEPLOYMENT_EMBED"),
        azure_endpoint=_require_env("AZURE_OPENAI_ENDPOINT"),
        api_key=_require_env("AZURE_OPENAI_API_KEY"),
        api_version=_API_VERSION,
    )


@functools.lru_cache(maxsize=16)
def get_llm(
    temperature: float = None,
    max_tokens: int = 2048,
    reasoning_effort: str = None,
    text_verbosity: str = None,
    provider: str = "openai",
):
    """Get a chat model (cached per unique argument combination).

    provider="grok"  — Azure AI Services (Grok); requires AZURE_AI_ENDPOINT.
                       reasoning_effort and text_verbosity are silently ignored.
    provider="openai" — Azure OpenAI (default).
    """
    if provider == "grok":
        from langchain_openai import ChatOpenAI
        ai_endpoint = _require_env("AZURE_AI_ENDPOINT")
        model_name = os.getenv("AZURE_AI_DEPLOYMENT_CHAT", "grok-4.3")
        config: dict = {
            "base_url": f"{ai_endpoint.rstrip('/')}/models",
            "api_key": _require_env("AZURE_OPENAI_API_KEY"),
            "model": model_name,
            "max_tokens": max_tokens,
        }
        if temperature is not None:
            config["temperature"] = temperature
        return ChatOpenAI(**config)

    from langchain_openai import AzureChatOpenAI

    config = {
        "azure_deployment": _require_env("AZURE_OPENAI_DEPLOYMENT_CHAT"),
        "azure_endpoint": _require_env("AZURE_OPENAI_ENDPOINT"),
        "api_key": _require_env("AZURE_OPENAI_API_KEY"),
        "api_version": _API_VERSION,
        "max_tokens": max_tokens,
    }
    if temperature is not None:
        config["temperature"] = temperature
    if reasoning_effort is not None:
        config["reasoning"] = {"effort": reasoning_effort}
    if text_verbosity is not None:
        config["verbosity"] = text_verbosity

    return AzureChatOpenAI(**config)


@functools.lru_cache(maxsize=4)
def get_x_client(tone="agreeable"):
    """X client, cached per tone."""
    from xdk import Client
    from xdk.oauth1_auth import OAuth1

    oauth1 = OAuth1(
        api_key=_require_env("X_API_KEY"),
        api_secret=_require_env("X_API_SECRET"),
        callback="oob",
        access_token=_require_env(f"X_ACCESS_TOKEN_{tone.upper()}"),
        access_token_secret=_require_env(f"X_ACCESS_TOKEN_SECRET_{tone.upper()}")
    )
    client = Client(auth=oauth1)
    return client

__all__ = [
    "NOTES_TSV_ROOT",
    "INDEX_ROOT",
    "STYLE_LLM_PROVIDERS",
    "get_embedder",
    "get_llm",
]
