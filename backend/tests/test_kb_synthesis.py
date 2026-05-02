"""kb 总则合并（不调用 LLM）。"""

from __future__ import annotations

from pathlib import Path

from app.kb_synthesis import AUTHOR_BIBLE_SYNTHESIS_NAME, _merge_year_sections, merge_writer_kb_block


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


OLD_Y = """# X

## 人物卡

| a | b |
|---|---|

## 年表

| T | evt | note |
|---|---|---|
| 甲年 | M1 |  |
| 乙年 | M2 |  |

## 规则与世界观

- rule
"""


def test_merge_year_sections_keeps_facts_when_new_shrink() -> None:
    shrunk = OLD_Y.replace("| 乙年 | M2 |  |\n", "").replace(
        "| 甲年 | M1 |  |\n",
        "| 戊年 | M5 |  |\n",
    )
    out, patched = _merge_year_sections(OLD_Y, shrunk)
    assert patched is True
    assert "戊年" in out
    assert "乙年" in out and "M2" in out


def test_merge_year_sections_insert_when_heading_missing() -> None:
    new_md = "## 人物卡\n\nx\n\n## 规则与世界观\n\nrye\n"
    out, patched = _merge_year_sections(OLD_Y, new_md)
    assert patched is True
    assert "## 年表" in out
    assert "甲年" in out
    assert "## 规则与世界观" in out
