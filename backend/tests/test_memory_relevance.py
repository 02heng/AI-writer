from __future__ import annotations

from app.memory_relevance import rank_memory_entries, score_memory_entry_against_query, _tokenize_for_overlap


def test_tokenize_contains_bigrams() -> None:
    t = _tokenize_for_overlap("李明前往东京")
    assert "李明" in t or "前往" in t


def test_score_prefers_overlap() -> None:
    q = _tokenize_for_overlap("李明 伏笔 东京")
    e1 = {"title": "无关", "body": "日常吃饭", "room": "其他"}
    e2 = {"title": "李明", "body": "李明在东京埋下伏笔", "room": "伏笔"}
    assert score_memory_entry_against_query(e2, q) > score_memory_entry_against_query(e1, q)


def test_rank_orders_by_score() -> None:
    q = "张无忌 光明顶"
    entries = [
        {"id": 1, "room": "x", "title": "做饭", "body": "无", "chapter_label": None},
        {"id": 2, "room": "x", "title": "光明顶", "body": "张无忌大战", "chapter_label": None},
    ]
    ranked = rank_memory_entries(entries, q)
    assert ranked[0]["title"] == "光明顶"
