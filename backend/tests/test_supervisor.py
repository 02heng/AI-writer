"""监督智能体：完整性报告与日志压缩。"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.book_storage import append_agent_orchestration_log, create_book, write_chapter
from app.orchestration.supervisor import (
    append_supervisor_final_to_orchestration_state,
    compact_agent_log,
    supervisor_integrity_report,
)
from app.paths import ensure_layout


def test_supervisor_integrity_missing_and_gap(tmp_path: Path) -> None:
    root: Path = tmp_path
    ensure_layout(root)
    plan = {
        "book_title": "测试书",
        "premise": "梗概",
        "chapters": [
            {"idx": 1, "title": "第一章"},
            {"idx": 2, "title": "第二章"},
            {"idx": 3, "title": "第三章"},
        ],
    }
    r = create_book(root, title="测试书", premise="梗概", plan=plan)
    bid = r["book_id"]
    write_chapter(root, bid, 1, "## 第一章\n\n正文")
    write_chapter(root, bid, 3, "## 第三章\n\n正文")
    rep = supervisor_integrity_report(root, bid)
    assert 2 in rep["missing_files"]
    assert 2 in rep["gaps_in_sequence"]
    assert rep["integrity_ok"] is False
    assert rep["needs_attention"] is True


def test_compact_agent_log_strips_steps() -> None:
    alog = {
        "profile": "full",
        "steps": [
            {"agent": "Writer", "ok": True},
            {"agent": "Lore/Continuity", "ok": True, "violations_count": 2},
            {"agent": "Safety", "ok": False, "error": "x" * 500},
        ],
    }
    c = compact_agent_log(alog)
    assert c["profile"] == "full"
    assert len(c["steps"]) == 3
    assert len(c["steps"][2]["error"]) <= 400


def test_save_supervisor_review_to_analytics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIWRITER_ANALYTICS_ROOT", str(tmp_path / "AnalyticsRoot"))
    from app.analytics_store import ensure_analytics_layout, save_supervisor_review_snapshot

    ensure_analytics_layout()
    out = save_supervisor_review_snapshot(
        "abc123def456",
        {"integrity_ok": True},
        {"health_score": 88, "summary": "ok"},
    )
    assert out.get("ok") is True
    assert out.get("filename", "").startswith("supervisor-")
    p = tmp_path / "AnalyticsRoot" / "reviews" / out["filename"]
    assert p.is_file()


def test_append_supervisor_final_caps_open_issues() -> None:
    base = {"step": "idle", "chapter": 0, "open_issues": [{"kind": "x", "ts": float(i)} for i in range(50)]}
    meta = {
        "health_score": 70,
        "summary": "尚可",
        "prompt_iteration_hints": ["加强节奏"],
        "agent_chain_hints": [],
        "continuation_hints": [],
        "next_actions": ["人工抽查第3章"],
        "risks": [],
    }
    integrity = {"integrity_ok": True, "needs_attention": False}
    out = append_supervisor_final_to_orchestration_state(base, integrity=integrity, meta_review=meta)
    issues = out["open_issues"]
    assert len(issues) == 48
    assert issues[-1]["kind"] == "supervisor_final"
    assert issues[-1]["health_score"] == 70
    assert "加强节奏" in issues[-1]["prompt_iteration_hints"]


def test_append_agent_run_readable(tmp_path: Path) -> None:
    root: Path = tmp_path
    ensure_layout(root)
    plan = {"book_title": "A", "premise": "p", "chapters": [{"idx": 1, "title": "T"}]}
    r = create_book(root, title="A", premise="p", plan=plan)
    bid = r["book_id"]
    append_agent_orchestration_log(root, bid, {"chapter": 1, "ts": 1.0, "log": {"profile": "fast"}})
    rep = supervisor_integrity_report(root, bid)
    assert rep["recent_run_count"] >= 1
