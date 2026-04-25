"""kb 总则合并（不调用 LLM）。"""

from __future__ import annotations

from pathlib import Path

from app.kb_synthesis import AUTHOR_BIBLE_SYNTHESIS_NAME, merge_writer_kb_block


def test_merge_writer_kb_only_user(temp_data_dir: Path) -> None:
    u = "【设定摘录：x.md】\nhello"
    assert merge_writer_kb_block(temp_data_dir, u) == u


def test_merge_writer_kb_synthesis_first(temp_data_dir: Path) -> None:
    (temp_data_dir / "kb").mkdir(parents=True, exist_ok=True)
    p = temp_data_dir / "kb" / AUTHOR_BIBLE_SYNTHESIS_NAME
    p.write_text("# 人物卡\n|a|b|\n|1|2|\n", encoding="utf-8")
    u = "【设定摘录：extra.md】\nZZZ_USER_KB_TAIL"
    out = merge_writer_kb_block(temp_data_dir, u)
    assert "人物卡" in out
    assert "作者圣经" in out
    assert out.index("人物卡") < out.index("ZZZ_USER_KB_TAIL")
