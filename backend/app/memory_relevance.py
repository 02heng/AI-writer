"""记忆条目与当前写作任务的相关性排序（无向量库：字符 n-gram 重叠 + 轻量关键词）。"""

from __future__ import annotations

import os
import re
from typing import Any


def _tokenize_for_overlap(text: str) -> set[str]:
    t = re.sub(r"\s+", " ", (text or "").lower())
    out: set[str] = set()
    # 英文/数字词
    for w in re.findall(r"[a-z0-9]{2,}", t, flags=re.I):
        if len(w) <= 32:
            out.add(w)
    # 中文 2–4 字滑窗（粗粒度语义 proxy）
    s = re.sub(r"\s+", "", text or "")
    for ln in (2, 3):
        if len(s) >= ln:
            for i in range(0, min(len(s), 4000) - ln + 1):
                out.add(s[i : i + ln])
    return out


def score_memory_entry_against_query(entry: dict[str, Any], query_tokens: set[str]) -> float:
    if not query_tokens:
        return 0.0
    title = str(entry.get("title") or "")
    body = str(entry.get("body") or "")
    room = str(entry.get("room") or "")
    blob = f"{title}\n{body}\n{room}"
    et = _tokenize_for_overlap(blob)
    inter = len(query_tokens & et)
    if inter == 0:
        return 0.0
    # 标题完全子串加权
    bonus = 0.0
    for qt in query_tokens:
        if len(qt) >= 2 and qt in title:
            bonus += 1.5
    return float(inter) + bonus


def rank_memory_entries(
    entries: list[dict[str, Any]],
    query: str,
) -> list[dict[str, Any]]:
    q_tokens = _tokenize_for_overlap(query)
    if not q_tokens:
        return list(entries)
    scored = [(score_memory_entry_against_query(e, q_tokens), i, e) for i, e in enumerate(entries)]
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [x[2] for x in scored]


def semantic_memory_enabled() -> bool:
    v = os.environ.get("AIWRITER_MEMORY_SEMANTIC", "").strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    return True
