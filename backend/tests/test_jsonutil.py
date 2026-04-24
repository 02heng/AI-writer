"""Tests for JSON extraction helpers."""

from __future__ import annotations

import pytest

from app.jsonutil import extract_json_object


def test_extract_json_object_trailing_comma_repaired() -> None:
    raw = '{"a":1,"b":[1,2,],}'
    data = extract_json_object(raw)
    assert data["a"] == 1
    assert data["b"] == [1, 2]


def test_extract_json_object_markdown_fence() -> None:
    raw = "```json\n{\"x\": 42}\n```"
    assert extract_json_object(raw) == {"x": 42}


def test_extract_json_object_raw_decode_ignores_trailing_junk() -> None:
    raw = '{"ok":true} \n\n谢谢'
    assert extract_json_object(raw) == {"ok": True}


def test_extract_json_object_rejects_non_object() -> None:
    with pytest.raises(ValueError, match="未找到"):
        extract_json_object("[1,2,3]")


def test_extract_json_object_repairs_unescaped_newline_in_string() -> None:
    # 模拟模型在 "beat" 等字段里回车，导致非法 JSON
    bad = '{\n"arc_notes":"x",\n"chapters":[\n{"idx":1,"title":"T","beat":"A\nB","space_for_later":"","hook_end":""}\n]}\n'
    data = extract_json_object(bad)
    assert data["chapters"][0]["beat"] == "A\nB"
