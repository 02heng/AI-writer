from __future__ import annotations

import json
import re
from typing import Any


def _repair_trailing_commas(blob: str) -> str:
    """Remove trailing commas before } or ] (common LLM JSON mistakes)."""
    s = blob
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r",\s*([\]}])", r"\1", s)
    return s


def _escape_raw_newlines_inside_json_strings(blob: str) -> str:
    """
    LLM 常在 "beat" 等长串里写真实换行，违反 JSON；此处在双引号串内将裸换行改写成 \\n。
    使用简单状态机，忽略已转义序列内的字符。
    """
    out: list[str] = []
    i = 0
    in_str = False
    n = len(blob)
    while i < n:
        c = blob[i]
        if not in_str:
            if c == '"':
                in_str = True
            out.append(c)
            i += 1
            continue
        if c == "\\":
            if i + 1 < n:
                out.append(c)
                out.append(blob[i + 1])
                i += 2
            else:
                out.append(c)
                i += 1
            continue
        if c == '"':
            in_str = False
            out.append(c)
            i += 1
            continue
        if c == "\n":
            out.append("\\n")
            i += 1
            continue
        if c == "\r":
            if i + 1 < n and blob[i + 1] == "\n":
                i += 2
            else:
                i += 1
            out.append("\\n")
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _decode_json_object_blob(blob: str) -> dict[str, Any]:
    """Try several repairs common in LLM JSON before failing."""
    decoder = json.JSONDecoder()
    seen: set[str] = set()
    variants: list[str] = []
    for v in (
        blob,
        _repair_trailing_commas(blob),
        _escape_raw_newlines_inside_json_strings(blob),
        _escape_raw_newlines_inside_json_strings(_repair_trailing_commas(blob)),
    ):
        if v not in seen:
            seen.add(v)
            variants.append(v)
    last_err: json.JSONDecodeError | None = None
    for v in variants:
        try:
            obj, _ = decoder.raw_decode(v)
            if not isinstance(obj, dict):
                raise ValueError("JSON 根须为对象")
            return obj
        except json.JSONDecodeError as e:
            last_err = e
            continue
    if last_err is not None:
        raise last_err
    raise ValueError("JSON 无法解析")


def extract_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("响应中未找到 JSON 对象")
    blob = text[start : end + 1]
    try:
        return _decode_json_object_blob(blob)
    except json.JSONDecodeError as e:
        raise json.JSONDecodeError(e.msg, e.doc, e.pos) from None
