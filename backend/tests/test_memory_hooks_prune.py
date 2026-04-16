"""记忆淘汰与结构化伏笔存取。"""
from __future__ import annotations

from pathlib import Path

from app.memory_hooks import read_foreshadowing_state, write_foreshadowing_state
from app.memory_store import add_entry, init_db, prune_episodic_extraction_entries


def test_prune_keeps_newest_extractions(tmp_path: Path) -> None:
    root = tmp_path / "book"
    init_db(root)
    for i in range(5):
        add_entry(
            root,
            room="情节",
            title=f"第 {i} 章 · 生成同步萃取",
            body=f"b{i}",
            chapter_label=str(i),
        )
    r = prune_episodic_extraction_entries(root, keep_last=2)
    assert r["deleted"] == 3
    from app.memory_store import list_entries

    left = list_entries(root, limit=20)
    assert len(left) == 2


def test_foreshadowing_roundtrip(tmp_path: Path) -> None:
    root = tmp_path / "book"
    st = {"version": 1, "hooks": [{"id": "h1", "summary": "线A", "status": "open", "opened_at": "1", "resolved_at": None, "notes": ""}]}
    write_foreshadowing_state(root, st)
    got = read_foreshadowing_state(root)
    assert got["hooks"][0]["id"] == "h1"
