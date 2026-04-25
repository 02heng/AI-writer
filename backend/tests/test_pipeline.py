"""Tests for pipeline module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestPipelineHelpers:
    """Tests for pipeline helper functions."""

    def test_safe_filename_prefix_basic(self) -> None:
        """Test filename prefix generation."""
        from app.pipeline import _safe_filename_prefix
        
        result = _safe_filename_prefix("My Novel Title")
        assert result == "My Novel Title"
        
    def test_safe_filename_prefix_special_chars(self) -> None:
        """Test filename prefix removes special characters."""
        from app.pipeline import _safe_filename_prefix
        
        result = _safe_filename_prefix('Novel<>:"/\\|?*Title')
        assert "<" not in result
        assert ">" not in result
        assert ":" not in result
        assert '"' not in result
        assert "/" not in result
        assert "\\" not in result
        assert "|" not in result
        assert "?" not in result
        assert "*" not in result

    def test_safe_filename_prefix_truncation(self) -> None:
        """Test filename prefix truncates long titles."""
        from app.pipeline import _safe_filename_prefix
        
        long_title = "A" * 100
        result = _safe_filename_prefix(long_title)
        assert len(result) <= 48

    def test_safe_filename_prefix_empty(self) -> None:
        """Test filename prefix handles empty input."""
        from app.pipeline import _safe_filename_prefix
        
        result = _safe_filename_prefix("")
        assert result == "novel"

    def test_scale_instruction_short(self) -> None:
        """Test scale instruction for short stories."""
        from app.pipeline import _scale_instruction
        
        result = _scale_instruction("short")
        assert "短篇" in result
        assert "短篇读者节奏" in result
        assert "三百至八百" in result
        assert "紧凑" in result

    def test_scale_instruction_medium(self) -> None:
        """Test scale instruction for medium stories."""
        from app.pipeline import _scale_instruction
        
        result = _scale_instruction("medium")
        assert "中篇" in result

    def test_scale_instruction_long(self) -> None:
        """Test scale instruction for long stories."""
        from app.pipeline import _scale_instruction
        
        result = _scale_instruction("long")
        assert "长篇" in result

    def test_protagonist_instruction_male(self) -> None:
        """Test protagonist instruction for male lead."""
        from app.pipeline import _protagonist_instruction
        
        result = _protagonist_instruction("male")
        assert "男性" in result

    def test_protagonist_instruction_female(self) -> None:
        """Test protagonist instruction for female lead."""
        from app.pipeline import _protagonist_instruction
        
        result = _protagonist_instruction("female")
        assert "女性" in result

    def test_protagonist_instruction_any(self) -> None:
        """Test protagonist instruction for any gender."""
        from app.pipeline import _protagonist_instruction
        
        result = _protagonist_instruction("any")
        assert "自然呈现" in result

    def test_sanitize_chapter_body_strips_double_asterisk_bold(self) -> None:
        from app.pipeline import sanitize_chapter_body

        assert sanitize_chapter_body("她说**不行**。") == "她说不行。"
        assert sanitize_chapter_body("**起**与**止**") == "起与止"

    def test_sanitize_chapter_body_strips_markdown_line_noise(self) -> None:
        from app.pipeline import sanitize_chapter_body

        raw = "> 他点头。\n>> 又道：好。\n- 走吧。\n* 也行。\n1. 开场白\n正文一段"
        out = sanitize_chapter_body(raw)
        assert ">" not in out
        assert not out.startswith("-")
        assert "他点头。" in out
        assert "正文一段" in out

    def test_sanitize_chapter_body_strips_comma_hyphen_glitch(self) -> None:
        from app.pipeline import sanitize_chapter_body

        assert "，" in sanitize_chapter_body("沉默,-良久")
        assert ",-" not in sanitize_chapter_body("沉默,-良久")

    def test_sanitize_chapter_body_strips_hash_marks(self) -> None:
        from app.pipeline import sanitize_chapter_body

        out = sanitize_chapter_body("## 不应出现\n正文#号与###小节")
        assert "#" not in out
        assert "不应出现" in out
        assert "正文号与" in out
        assert "小节" in out

    def test_sanitize_chapter_body_strips_leading_dizhang_line(self) -> None:
        """正文开头「第N章」与入库 ## 章题重复，须洗掉。"""
        from app.pipeline import sanitize_chapter_body

        out = sanitize_chapter_body("第1章\n\n他推开门。")
        assert "第1章" not in out.split("\n")[0]
        assert "他推开门" in out

        out2 = sanitize_chapter_body("## 第 2 章 · 副题\n\n正文一段。")
        assert out2.split("\n", 1)[0].strip().startswith("正文")

        out3 = sanitize_chapter_body("第一章\n\n起笔。")
        assert not out3.lstrip().startswith("第")
        assert "起笔" in out3

        out4 = sanitize_chapter_body("第1章是个长句子不会被整行删。")
        assert "第1章" in out4

    def test_sanitize_chapter_body_collapses_ascii_quote_linebreaks(self) -> None:
        from app.pipeline import sanitize_chapter_body

        assert "\n" not in sanitize_chapter_body('他说："\n快逃。\n"')
        out2 = sanitize_chapter_body('第一段。\n\n"第二段对白。"')
        assert "\n\n" in out2
        assert '"第二段对白。"' in out2
        out3 = sanitize_chapter_body('他道："\n第一句\n第二句。"')
        assert out3.count("\n") == 0 or "第一句第二句" in out3.replace("\n", "")

    def test_sanitize_chapter_body_runon_adds_paragraphs(self) -> None:
        """单段一坨长文在足够长、句号足够多时粗分为多段（按句对）。"""
        from app.pipeline import sanitize_chapter_body

        block = "。".join(f"这是第{k}句试验性长文内容" for k in range(48)) + "。"
        one = block + block
        assert "\n" not in one
        assert len(one) >= 1000
        out = sanitize_chapter_body(one)
        assert "\n\n" in out
        assert out.count("\n\n") >= 1

    def test_strip_common_prefix_with_previous_opening(self) -> None:
        from app.text_sanitize import strip_common_prefix_with_previous_opening

        prev = "OPEN_A\n" + "PARA_REPEAT\n" * 30
        tail = "X" * 120
        new = prev + tail
        out = strip_common_prefix_with_previous_opening(
            prev, new, min_chars=50, min_kept_after_strip=40
        )
        assert out == tail

    def test_clean_stored_chapter_text_strips_bold(self) -> None:
        from app.book_storage import clean_stored_chapter_text

        raw = "<!--meta-->\n\n**他们**走了。"
        assert "**" not in clean_stored_chapter_text(raw)
        assert clean_stored_chapter_text(raw).endswith("走了。")


class TestChapterContract:
    """Tests for chapter contract formatting."""

    def test_format_chapter_contract_basic(self) -> None:
        """Test basic chapter contract formatting."""
        from app.pipeline import _format_chapter_contract
        
        chapter = {
            "idx": 1,
            "beat": "The hero arrives at the castle",
        }
        
        result = _format_chapter_contract(1, chapter)
        
        assert "第 1 章" in result
        assert "节拍" in result
        assert "hero arrives at the castle" in result

    def test_format_chapter_contract_full(self) -> None:
        """Test chapter contract with all fields."""
        from app.pipeline import _format_chapter_contract
        
        chapter = {
            "idx": 2,
            "beat": "The hero explores the dungeon",
            "pov": "第三人称限定主角",
            "conflict": "Fear vs. duty",
            "scenes": ["Entering the dungeon", "Finding the treasure"],
            "characters_present": ["Hero", "Guide"],
            "kb_tags": ["dungeon", "treasure"],
            "hook_end": "A trap is triggered",
        }
        
        result = _format_chapter_contract(2, chapter)
        
        assert "第 2 章" in result
        assert "叙事视角" in result
        assert "核心冲突" in result
        assert "场景清单" in result
        assert "出场人物" in result
        assert "关键词" in result
        assert "章末钩子" in result

    def test_format_chapter_contract_continuation(self) -> None:
        """Test chapter contract for continuation."""
        from app.pipeline import _format_chapter_contract
        
        chapter = {
            "idx": 3,
            "beat": "The escape begins",
        }
        
        result = _format_chapter_contract(3, chapter, continuation=True)
        
        assert "续写" in result

    def test_format_chapter_contract_rewrite_continuation_uses_rewrite_label(self) -> None:
        from app.pipeline import _format_chapter_contract

        chapter = {"idx": 3, "beat": "重写要点"}
        result = _format_chapter_contract(3, chapter, continuation=True, is_rewrite=True)

        assert "重写" in result
        assert "续写" not in result.split("【本章写作合同】")[1].split("\n")[0]

    def test_format_chapter_contract_space_for_later(self) -> None:
        from app.pipeline import _format_chapter_contract

        chapter = {"idx": 4, "beat": "推进主线", "space_for_later": "为终盘留反转余地"}
        result = _format_chapter_contract(4, chapter)
        assert "为后文留白" in result
        assert "终盘" in result

    def test_format_chapter_contract_short_length_uses_short_structure(self) -> None:
        from app.pipeline import _format_chapter_contract

        chapter = {"idx": 1, "beat": "开局冲突"}
        result = _format_chapter_contract(1, chapter, length_scale="short")
        assert "结构提示·短篇" in result

    def test_format_chapter_contract_default_structure_not_short_label(self) -> None:
        from app.pipeline import _format_chapter_contract

        chapter = {"idx": 1, "beat": "开局冲突"}
        result = _format_chapter_contract(1, chapter)
        assert "【结构提示】" in result
        assert "结构提示·短篇" not in result
        assert "【篇幅目标】" in result
        assert "2000～4000" in result

    def test_format_chapter_contract_short_includes_wordcount_target(self) -> None:
        from app.pipeline import _format_chapter_contract

        chapter = {"idx": 1, "beat": "开局冲突"}
        result = _format_chapter_contract(1, chapter, length_scale="short")
        assert "【篇幅目标】" in result
        assert "2000～4000" in result

    def test_format_chapter_contract_short_romance_adds_structure_hint(self) -> None:
        from app.pipeline import _format_chapter_contract

        chapter = {"idx": 1, "beat": "开局"}
        result = _format_chapter_contract(1, chapter, length_scale="short", theme_id="romance")
        assert "结构提示·短篇言情" in result

    def test_scale_instruction_short_romance_appends_web_romance_block(self) -> None:
        from app.pipeline import _scale_instruction

        block = _scale_instruction("short", "romance")
        assert "【短篇·网文体·言情专规】" in block


class TestNormalizeChapterEntry:
    """Tests for chapter entry normalization."""

    def test_normalize_chapter_entry_basic(self) -> None:
        """Test normalizing a basic chapter entry."""
        from app.pipeline import _normalize_chapter_entry
        
        raw = {
            "idx": 1,
            "beat": "Opening scene",
        }
        
        result = _normalize_chapter_entry(raw, 1)
        
        assert result is not None
        assert result["idx"] == 1
        assert result["beat"] == "Opening scene"

    def test_normalize_chapter_entry_missing_beat(self) -> None:
        """Test normalizing entry with missing beat."""
        from app.pipeline import _normalize_chapter_entry
        
        raw = {
            "idx": 1,
        }
        
        result = _normalize_chapter_entry(raw, 1)
        
        assert result is None

    def test_normalize_chapter_entry_space_for_later(self) -> None:
        from app.pipeline import _normalize_chapter_entry

        raw = {"idx": 5, "beat": "x", "space_for_later": "留白一句"}
        r = _normalize_chapter_entry(raw, 5)
        assert r is not None
        assert r.get("space_for_later") == "留白一句"

    def test_normalize_chapter_entry_with_scenes(self) -> None:
        """Test normalizing entry with scenes."""
        from app.pipeline import _normalize_chapter_entry
        
        raw = {
            "idx": 1,
            "beat": "Chapter beat",
            "scenes": ["Scene 1", "Scene 2"],
        }
        
        result = _normalize_chapter_entry(raw, 1)
        
        assert result is not None
        assert len(result["scenes"]) == 2
