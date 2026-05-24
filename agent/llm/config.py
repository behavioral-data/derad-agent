"""Centralised LLM configuration.

Loads credentials from ``agent/llm/.env`` and exposes factory helpers for
the Claude chat model and the X API client.
"""
import functools
import os
import warnings
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")


def _validate_env() -> None:
    """Warn early if critical env vars for Claude are missing."""
    required = ["AZURE_CLAUDE_ENDPOINT", "AZURE_CLAUDE_API_KEY"]
    missing = [v for v in required if not os.getenv(v)]
    if missing:
        warnings.warn(
            f"Missing environment variables for Claude on Azure: {missing}. "
            f"Chat operations will fail. Check your .env file.",
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


# Map reasoning_effort → Anthropic extended-thinking budget_tokens.
# Minimum is 1024 (per Anthropic docs); larger budgets give the model more
# room to deliberate before producing the visible response.
_CLAUDE_THINKING_BUDGETS: dict[str, int] = {
    "minimal": 1024,
    "low":     1024,
    "medium":  4096,
    "high":    16384,
}


@functools.lru_cache(maxsize=16)
def get_llm(
    temperature: float = None,
    max_tokens: int = 2048,
    reasoning_effort: str = None,
    deployment: str = None,
):
    """Get a Claude chat model via Azure AI Services (cached per arg combo).

    When ``reasoning_effort`` is set, Anthropic extended thinking is enabled
    with a budget drawn from ``_CLAUDE_THINKING_BUDGETS``. The visible-output
    cap stays at ``max_tokens``; the request's overall ``max_tokens`` is bumped
    to ``max_tokens + budget_tokens`` because thinking tokens count against
    the same limit (Anthropic requires ``budget_tokens < max_tokens``).

    Extended thinking is incompatible with ``temperature != 1``; if a caller
    sets one anyway we drop it rather than failing the request.
    """
    from langchain_anthropic import ChatAnthropic
    claude_endpoint = _require_env("AZURE_CLAUDE_ENDPOINT")
    model_name = deployment or os.getenv("AZURE_CLAUDE_DEPLOYMENT_CHAT", "claude-sonnet-4-6")

    config: dict = {
        "model_name": model_name,
        "anthropic_api_url": claude_endpoint,
        "api_key": _require_env("AZURE_CLAUDE_API_KEY"),
    }

    if reasoning_effort:
        budget = _CLAUDE_THINKING_BUDGETS.get(reasoning_effort)
        if budget is None:
            raise ValueError(
                f"Unknown reasoning_effort={reasoning_effort!r}. "
                f"Choose from: {sorted(_CLAUDE_THINKING_BUDGETS)}"
            )
        config["max_tokens"] = max_tokens + budget
        config["thinking"] = {"type": "enabled", "budget_tokens": budget}
        # Anthropic requires temperature unset (effectively 1.0) when thinking
        # is on; ignore any caller-supplied value.
    else:
        config["max_tokens"] = max_tokens
        if temperature is not None:
            config["temperature"] = temperature

    return ChatAnthropic(**config)


@functools.lru_cache(maxsize=1)
def get_x_client():
    """Cached X client for the single bot identity."""
    from xdk import Client
    from xdk.oauth1_auth import OAuth1

    oauth1 = OAuth1(
        api_key=_require_env("X_API_KEY"),
        api_secret=_require_env("X_API_SECRET"),
        callback="oob",
        access_token=_require_env("X_ACCESS_TOKEN"),
        access_token_secret=_require_env("X_ACCESS_TOKEN_SECRET"),
    )
    client = Client(auth=oauth1)
    return client


__all__ = [
    "get_llm",
    "get_x_client",
]
