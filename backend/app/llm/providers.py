"""LLM provider abstraction for multi-model support.

This module provides a unified interface for different LLM providers,
allowing seamless switching between OpenAI, Claude, DeepSeek, and others.

Usage:
    from app.llm.providers import get_llm_provider

    provider = get_llm_provider("gpt-4")
    response = provider.chat(system="...", user="...")
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any, Iterator, Optional

import httpx

# Try importing providers
try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False
    OpenAI = None

try:
    from anthropic import Anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False
    Anthropic = None

from ..core.logging import get_logger

logger = get_logger(__name__)


# =============================================================================
# Base Provider Interface
# =============================================================================

class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    @abstractmethod
    def chat(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.8,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> str:
        """Send a chat completion request.

        Args:
            system: System prompt
            user: User message
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            **kwargs: Additional provider-specific options

        Returns:
            Generated text
        """
        pass

    @abstractmethod
    def stream_chat(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.8,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> Iterator[str]:
        """Stream a chat completion response.

        Args:
            system: System prompt
            user: User message
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            **kwargs: Additional provider-specific options

        Yields:
            Text chunks as they arrive
        """
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name."""
        pass


# =============================================================================
# DeepSeek Provider
# =============================================================================

class DeepSeekProvider(LLMProvider):
    """DeepSeek API provider (OpenAI-compatible)."""

    DEFAULT_BASE_URL = "https://api.deepseek.com"
    DEFAULT_MODEL = "deepseek-v4-flash"

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        if not HAS_OPENAI:
            raise ImportError("openai package required for DeepSeek provider")

        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self.base_url = (base_url or os.environ.get("DEEPSEEK_BASE_URL", self.DEFAULT_BASE_URL)).rstrip("/")
        self.model = model or os.environ.get("DEEPSEEK_MODEL", self.DEFAULT_MODEL)

        if not self.api_key:
            raise ValueError("DEEPSEEK_API_KEY not configured")

        connect = float(os.environ.get("AIWRITER_HTTP_CONNECT_TIMEOUT", "60").strip() or "60")
        read = float(os.environ.get("AIWRITER_HTTP_READ_TIMEOUT", "600").strip() or "600")
        write = float(os.environ.get("AIWRITER_HTTP_WRITE_TIMEOUT", "180").strip() or "180")
        pool = float(os.environ.get("AIWRITER_HTTP_POOL_TIMEOUT", "60").strip() or "60")
        timeout = httpx.Timeout(connect=connect, read=read, write=write, pool=pool)
        max_retries = int(os.environ.get("AIWRITER_OPENAI_MAX_RETRIES", "6").strip() or "6")
        max_retries = max(0, min(max_retries, 12))
        self._client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=timeout,
            max_retries=max_retries,
        )

    @property
    def name(self) -> str:
        return "deepseek"

    def chat(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.8,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> str:
        response = self._client.chat.completions.create(
            model=kwargs.get("model", self.model),
            temperature=temperature,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )

        choice = response.choices[0]
        if not choice.message or not choice.message.content:
            return ""

        return choice.message.content.strip()

    def stream_chat(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.8,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> Iterator[str]:
        stream = self._client.chat.completions.create(
            model=kwargs.get("model", self.model),
            temperature=temperature,
            max_tokens=max_tokens,
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


# =============================================================================
# OpenAI Provider
# =============================================================================

class OpenAIProvider(LLMProvider):
    """OpenAI API provider."""

    DEFAULT_MODEL = "gpt-4o"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        if not HAS_OPENAI:
            raise ImportError("openai package required for OpenAI provider")

        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.model = model or self.DEFAULT_MODEL

        if not self.api_key:
            raise ValueError("OPENAI_API_KEY not configured")

        connect = float(os.environ.get("AIWRITER_HTTP_CONNECT_TIMEOUT", "60").strip() or "60")
        read = float(os.environ.get("AIWRITER_HTTP_READ_TIMEOUT", "600").strip() or "600")
        write = float(os.environ.get("AIWRITER_HTTP_WRITE_TIMEOUT", "180").strip() or "180")
        pool = float(os.environ.get("AIWRITER_HTTP_POOL_TIMEOUT", "60").strip() or "60")
        timeout = httpx.Timeout(connect=connect, read=read, write=write, pool=pool)
        max_retries = int(os.environ.get("AIWRITER_OPENAI_MAX_RETRIES", "6").strip() or "6")
        max_retries = max(0, min(max_retries, 12))
        self._client = OpenAI(api_key=self.api_key, timeout=timeout, max_retries=max_retries)

    @property
    def name(self) -> str:
        return "openai"

    def chat(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.8,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> str:
        response = self._client.chat.completions.create(
            model=kwargs.get("model", self.model),
            temperature=temperature,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )

        choice = response.choices[0]
        if not choice.message or not choice.message.content:
            return ""

        return choice.message.content.strip()

    def stream_chat(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.8,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> Iterator[str]:
        stream = self._client.chat.completions.create(
            model=kwargs.get("model", self.model),
            temperature=temperature,
            max_tokens=max_tokens,
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


# =============================================================================
# Claude Provider
# =============================================================================

class ClaudeProvider(LLMProvider):
    """Anthropic Claude API provider."""

    DEFAULT_MODEL = "claude-sonnet-4-20250514"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        if not HAS_ANTHROPIC:
            raise ImportError("anthropic package required for Claude provider")

        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = model or self.DEFAULT_MODEL

        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY not configured")

        self._client = Anthropic(api_key=self.api_key)

    @property
    def name(self) -> str:
        return "claude"

    def chat(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.8,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> str:
        response = self._client.messages.create(
            model=kwargs.get("model", self.model),
            max_tokens=max_tokens or 4096,
            temperature=temperature,
            system=system,
            messages=[
                {"role": "user", "content": user},
            ],
        )

        if not response.content:
            return ""

        # Extract text from content blocks
        text_parts = []
        for block in response.content:
            if hasattr(block, "text"):
                text_parts.append(block.text)

        return "".join(text_parts).strip()

    def stream_chat(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.8,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> Iterator[str]:
        with self._client.messages.stream(
            model=kwargs.get("model", self.model),
            max_tokens=max_tokens or 4096,
            temperature=temperature,
            system=system,
            messages=[
                {"role": "user", "content": user},
            ],
        ) as stream:
            for text in stream.text_stream:
                yield text


# =============================================================================
# Factory Function
# =============================================================================

def get_llm_provider(
    model_id: str,
    *,
    api_key: Optional[str] = None,
    **kwargs: Any,
) -> LLMProvider:
    """Get an LLM provider based on model ID.

    Args:
        model_id: Model identifier (e.g., "deepseek-v4-flash", "gpt-4o", "claude-sonnet")
        api_key: Optional API key override
        **kwargs: Additional provider-specific options

    Returns:
        LLM provider instance

    Raises:
        ValueError: If the model is not supported or API key is missing
    """
    model_lower = model_id.lower()

    if model_lower.startswith("deepseek"):
        return DeepSeekProvider(
            api_key=api_key or kwargs.get("deepseek_api_key"),
            model=model_id,
            **{k: v for k, v in kwargs.items() if k != "deepseek_api_key"},
        )

    if model_lower.startswith("gpt") or model_lower.startswith("o1") or model_lower.startswith("o3"):
        return OpenAIProvider(
            api_key=api_key or kwargs.get("openai_api_key"),
            model=model_id,
            **{k: v for k, v in kwargs.items() if k != "openai_api_key"},
        )

    if model_lower.startswith("claude"):
        return ClaudeProvider(
            api_key=api_key or kwargs.get("anthropic_api_key"),
            model=model_id,
            **{k: v for k, v in kwargs.items() if k != "anthropic_api_key"},
        )

    # Default to DeepSeek for unknown models
    logger.warning(f"Unknown model '{model_id}', defaulting to DeepSeek provider")
    return DeepSeekProvider(
        api_key=api_key,
        model=model_id,
        **kwargs,
    )


def list_available_providers() -> list[dict[str, Any]]:
    """List all available LLM providers and their status.

    Returns:
        List of provider info dictionaries
    """
    providers = []

    # DeepSeek
    providers.append({
        "name": "deepseek",
        "available": HAS_OPENAI,
        "configured": bool(os.environ.get("DEEPSEEK_API_KEY")),
        "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
    })

    # OpenAI
    providers.append({
        "name": "openai",
        "available": HAS_OPENAI,
        "configured": bool(os.environ.get("OPENAI_API_KEY")),
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "o1", "o3"],
    })

    # Claude
    providers.append({
        "name": "claude",
        "available": HAS_ANTHROPIC,
        "configured": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "models": ["claude-sonnet-4-20250514", "claude-3-5-sonnet", "claude-3-haiku"],
    })

    return providers
