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
