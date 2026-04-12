"""LLM module for AI Writer.

This module provides both legacy functions and new provider abstraction.
"""

from __future__ import annotations

import os
from typing import Iterable, Iterator, Optional

# Try importing OpenAI
try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False
    OpenAI = None

from ..core.logging import get_logger
from .providers import (
    LLMProvider,
    DeepSeekProvider,
    OpenAIProvider,
    ClaudeProvider,
    get_llm_provider,
    list_available_providers,
)

logger = get_logger(__name__)

# Legacy configuration
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
DEFAULT_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")


# =============================================================================
# Legacy Functions (for backward compatibility)
# =============================================================================

def get_client() -> "OpenAI":
    """Get the default DeepSeek OpenAI client."""
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise RuntimeError("未配置 DEEPSEEK_API_KEY")
    return OpenAI(api_key=key, base_url=DEEPSEEK_BASE_URL)


def chat_completion(
    *,
    system: str,
    user: str,
    model: str | None = None,
    temperature: float = 0.8,
) -> str:
    """Send a chat completion request using DeepSeek.

    Args:
        system: System prompt
        user: User message
        model: Model to use (defaults to DEEPSEEK_MODEL env var or deepseek-chat)
        temperature: Sampling temperature

    Returns:
        Generated text
    """
    client = get_client()
    m = model or DEFAULT_MODEL
    resp = client.chat.completions.create(
        model=m,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    choice = resp.choices[0]
    if not choice.message or not choice.message.content:
        return ""
    return choice.message.content.strip()


def stream_chat_completion(
    *,
    system: str,
    user: str,
    model: str | None = None,
    temperature: float = 0.8,
) -> Iterable[str]:
    """Stream a chat completion response using DeepSeek.

    Args:
        system: System prompt
        user: User message
        model: Model to use (defaults to DEEPSEEK_MODEL env var or deepseek-chat)
        temperature: Sampling temperature

    Yields:
        Text chunks as they arrive
    """
    client = get_client()
    m = model or DEFAULT_MODEL
    stream = client.chat.completions.create(
        model=m,
        temperature=temperature,
        stream=True,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta and delta.content:
            yield delta.content


__all__ = [
    # Legacy functions
    "get_client",
    "chat_completion",
    "stream_chat_completion",
    "DEEPSEEK_BASE_URL",
    "DEFAULT_MODEL",
    # New provider interface
    "LLMProvider",
    "DeepSeekProvider",
    "OpenAIProvider",
    "ClaudeProvider",
    "get_llm_provider",
    "list_available_providers",
]
