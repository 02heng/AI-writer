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
    decoder = json.JSONDecoder()
    try:
        obj, _ = decoder.raw_decode(blob)
        if not isinstance(obj, dict):
            raise ValueError("JSON 根须为对象")
        return obj
    except json.JSONDecodeError:
        repaired = _repair_trailing_commas(blob)
        try:
            obj, _ = decoder.raw_decode(repaired)
            if not isinstance(obj, dict):
                raise ValueError("JSON 根须为对象")
            return obj
        except json.JSONDecodeError as e:
            raise json.JSONDecodeError(e.msg, e.doc, e.pos) from None
