from __future__ import annotations

from pathlib import Path

from app.long_context_tail import append_chapter_tail_snippet, load_chapter_tail_for_prompt


def test_append_and_load_tail(tmp_path: Path) -> None:
    root = tmp_path / "book"
    (root / "memory").mkdir(parents=True)
    append_chapter_tail_snippet(root, chapter_n=1, chapter_title="开篇", snippet="主角登场")
    append_chapter_tail_snippet(root, chapter_n=2, chapter_title="转折", snippet="遇到反派")
    txt = load_chapter_tail_for_prompt(root, max_chars=8000)
    assert "第1章" in txt and "第2章" in txt
    assert "主角登场" in txt
