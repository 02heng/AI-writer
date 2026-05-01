"""LLM module for AI Writer.

This module provides both legacy functions and new provider abstraction.
"""

from __future__ import annotations

import os
import time
from typing import Iterable, Iterator, Optional

import httpx

try:
    from openai import APIConnectionError, APITimeoutError, BadRequestError, OpenAI

    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False
    OpenAI = None  # type: ignore[misc, assignment]

    class _OpenAIImportStub(Exception):
        """占位：无 openai 包时不应匹配任何真实异常。"""

    APIConnectionError = _OpenAIImportStub  # type: ignore[misc, assignment]
    APITimeoutError = _OpenAIImportStub  # type: ignore[misc, assignment]
    BadRequestError = _OpenAIImportStub  # type: ignore[misc, assignment]

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

DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
DEFAULT_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")

_client: OpenAI | None = None
_client_config_key: str | None = None


class LLMTransportError(Exception):
    """在多次重试后仍无法连上 DeepSeek / OpenAI 兼容接口时抛出（非配置类错误）。"""


class ContextWindowExhausted(Exception):
    """提示上下文窗口已满，输出空间不足以生成有意义的回复。"""

    def __init__(self, prompt_tokens: int, context_limit: int, remaining: int) -> None:
        self.prompt_tokens = prompt_tokens
        self.context_limit = context_limit
        self.remaining = remaining
        super().__init__(
            f"上下文窗口已满（prompt≈{prompt_tokens} tokens，窗口={context_limit}），"
            f"剩余输出空间仅 {remaining} tokens。请精简蒸馏卡片、记忆或主题内容后重试。"
        )


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        v = int(raw)
    except ValueError:
        return default
    return max(lo, min(hi, v))


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _http_timeout() -> httpx.Timeout:
    connect = _env_float("AIWRITER_HTTP_CONNECT_TIMEOUT", 60.0)
    read = _env_float("AIWRITER_HTTP_READ_TIMEOUT", 600.0)
    write = _env_float("AIWRITER_HTTP_WRITE_TIMEOUT", 180.0)
    pool = _env_float("AIWRITER_HTTP_POOL_TIMEOUT", 60.0)
    return httpx.Timeout(connect=connect, read=read, write=write, pool=pool)


def _client_build_key() -> str:
    return "|".join(
        (
            os.environ.get("DEEPSEEK_API_KEY", "").strip(),
            DEEPSEEK_BASE_URL,
            repr(
                (
                    _env_float("AIWRITER_HTTP_CONNECT_TIMEOUT", 60.0),
                    _env_float("AIWRITER_HTTP_READ_TIMEOUT", 600.0),
                    _env_float("AIWRITER_HTTP_WRITE_TIMEOUT", 180.0),
                    _env_float("AIWRITER_HTTP_POOL_TIMEOUT", 60.0),
                )
            ),
            str(_env_int("AIWRITER_OPENAI_MAX_RETRIES", 6, 0, 12)),
        )
    )


def _format_upstream_failure(exc: BaseException | None) -> str:
    parts: list[str] = []
    if exc is not None:
        parts.append(str(exc).strip() or type(exc).__name__)
    cur: BaseException | None = exc
    depth = 0
    while cur is not None and depth < 4:
        cur = cur.__cause__
        depth += 1
        if cur is not None:
            parts.append(str(cur).strip() or type(cur).__name__)
    detail = "；".join(p for p in parts if p)
    hint = (
        "请检查：本机网络与 DNS、是否需要系统代理、防火墙是否放行 HTTPS；"
        "DEEPSEEK_BASE_URL 是否可达（默认 https://api.deepseek.com）；"
        "若在海外/国内线路不稳定可多试几次。也可通过环境变量调大超时："
        "AIWRITER_HTTP_CONNECT_TIMEOUT、AIWRITER_HTTP_READ_TIMEOUT。"
    )
    if detail:
        return f"无法连接 DeepSeek API（已重试）。{detail}。{hint}"
    return f"无法连接 DeepSeek API（已重试）。{hint}"


def get_client() -> "OpenAI":
    """获取默认 DeepSeek OpenAI 兼容客户端（带较长超时与可配置重试）。"""
    global _client, _client_config_key
    if not HAS_OPENAI or OpenAI is None:
        raise RuntimeError("未安装 openai 包，无法调用 DeepSeek")
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise RuntimeError("未配置 DEEPSEEK_API_KEY")
    cfg = _client_build_key()
    if _client is not None and _client_config_key == cfg:
        return _client
    max_retries = _env_int("AIWRITER_OPENAI_MAX_RETRIES", 6, 0, 12)
    _client = OpenAI(
        api_key=key,
        base_url=DEEPSEEK_BASE_URL,
        timeout=_http_timeout(),
        max_retries=max_retries,
    )
    _client_config_key = cfg
    return _client


def reset_llm_client_cache() -> None:
    """清除 HTTP 客户端缓存（连接异常重试前会调用；单测或热切换 Key 时可用）。"""
    global _client, _client_config_key
    _client = None
    _client_config_key = None


def writer_completion_max_tokens() -> int:
    """章节正文希望申请的 max_tokens 上限（实际发送前会再按上下文收紧）。

    DeepSeek V4 支持 1M 上下文窗口、384K 输出。
    若与超长 prompt 相加超过模型上下文，服务商可能仍返回与 max_tokens 相关的 400，
    故 chat_completion 内会动态 clamp。
    """
    return _env_int("AIWRITER_WRITER_MAX_TOKENS", 16384, 1, 384000)


def _estimate_prompt_tokens(system: str, user: str) -> int:
    """无 tokenizer 时对 prompt 长度的保守估计（略高估输入更安全）。"""
    n = len(system) + len(user)
    per = _env_float("AIWRITER_PROMPT_TOKENS_PER_CHAR", 0.65)
    per = max(0.25, min(per, 1.5))
    return max(1, int(n * per))


def _clamp_max_tokens_to_context(
    requested: int,
    *,
    system: str,
    user: str,
) -> int:
    """保证 prompt + max_tokens 不易超过模型上下文；并遵守单条 max_tokens API 上限。"""
    api_max = _env_int("AIWRITER_COMPLETION_MAX_API", 16384, 1024, 384000)
    ctx = _env_int("AIWRITER_MODEL_CONTEXT_TOKENS", 1000000, 4096, 1000000)
    reserve = _env_int("AIWRITER_COMPLETION_RESERVE", 384, 64, 8192)
    est_in = _estimate_prompt_tokens(system, user)
    room = ctx - est_in - reserve
    # 输出空间严重不足时直接抛错，避免静默返回空内容
    min_output = _env_int("AIWRITER_COMPLETION_MIN_OUTPUT", 512, 64, 4096)
    if room < min_output:
        raise ContextWindowExhausted(est_in, ctx, max(0, room))
    if room < 1:
        room = 1
    cap = min(max(1, requested), api_max, room)
    if cap < requested:
        logger.warning(
            "llm.chat_completion clamp max_tokens %s -> %s (est_prompt_tokens~%s ctx=%s)",
            requested,
            cap,
            est_in,
            ctx,
        )
    return cap


def _is_max_tokens_bad_request(exc: BaseException) -> bool:
    s = str(exc).lower()
    return "max_tokens" in s and ("invalid" in s or "400" in s)


def chat_completion(
    *,
    system: str,
    user: str,
    model: str | None = None,
    temperature: float = 0.8,
    max_tokens: int | None = None,
) -> str:
    """Send a chat completion request using DeepSeek."""
    m = model or DEFAULT_MODEL
    app_retries = _env_int("AIWRITER_LLM_APP_RETRIES", 3, 1, 8)
    last_exc: BaseException | None = None

    mt_initial: int | None = None
    if max_tokens is not None:
        mt_initial = _clamp_max_tokens_to_context(max(1, int(max_tokens)), system=system, user=user)

    def _one_call(use_mt: int | None) -> str:
        client = get_client()
        create_kw: dict = {
            "model": m,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if use_mt is not None:
            create_kw["max_tokens"] = use_mt
        resp = client.chat.completions.create(**create_kw)
        choice = resp.choices[0]
        if not choice.message or not choice.message.content:
            return ""
        return choice.message.content.strip()

    def _call_with_max_tokens_downshift(start_mt: int | None) -> str:
        """先按 start_mt 请求；若仍报 max_tokens 无效则减半直至省略参数。"""
        cur = start_mt
        last_br: BaseException | None = None
        for _ in range(8):
            try:
                return _one_call(cur)
            except BadRequestError as e:
                last_br = e
                if cur is not None and _is_max_tokens_bad_request(e):
                    if cur > 1024:
                        nxt = max(512, cur // 2)
                        if nxt < cur:
                            logger.warning(
                                "llm.chat_completion max_tokens rejected, retry %s -> %s",
                                cur,
                                nxt,
                                extra={"err": str(e)[:280]},
                            )
                            cur = nxt
                            continue
                    logger.warning(
                        "llm.chat_completion max_tokens rejected, retry without max_tokens",
                        extra={"err": str(e)[:280]},
                    )
                    cur = None
                    continue
                raise
        if last_br is not None:
            raise last_br
        raise RuntimeError("chat_completion: exhausted max_tokens downshift without result")

    for attempt in range(app_retries):
        try:
            return _call_with_max_tokens_downshift(mt_initial)
        except BadRequestError:
            raise
        except (APIConnectionError, APITimeoutError) as e:
            last_exc = e
            reset_llm_client_cache()
            if attempt + 1 < app_retries:
                delay = min(12.0, 1.0 * (2**attempt))
                logger.warning(
                    "llm.chat_completion transport error, retrying",
                    extra={"attempt": attempt + 1, "max": app_retries, "delay_s": delay, "err": str(e)},
                )
                time.sleep(delay)
        except RuntimeError:
            raise

    raise LLMTransportError(_format_upstream_failure(last_exc)) from last_exc


def stream_chat_completion(
    *,
    system: str,
    user: str,
    model: str | None = None,
    temperature: float = 0.8,
) -> Iterable[str]:
    """Stream a chat completion response using DeepSeek."""
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
    "LLMTransportError",
    "get_client",
    "reset_llm_client_cache",
    "writer_completion_max_tokens",
    "chat_completion",
    "stream_chat_completion",
    "DEEPSEEK_BASE_URL",
    "DEFAULT_MODEL",
    "LLMProvider",
    "DeepSeekProvider",
    "OpenAIProvider",
    "ClaudeProvider",
    "get_llm_provider",
    "list_available_providers",
]