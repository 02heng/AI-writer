"""Tests for memory store module."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.memory_store import (
    add_entry,
    build_memory_context,
    delete_entry,
    init_db,
    list_entries,
    read_rollup,
    write_rollup,
)


class TestMemoryStore:
    """Tests for memory store functionality."""

    def test_init_db_creates_tables(self, temp_data_dir: Path) -> None:
        """Test that init_db creates the database and tables."""
        init_db(temp_data_dir)
        
        db_file = temp_data_dir / "memory" / "palace.sqlite3"
        assert db_file.exists()
        
        rollup_file = temp_data_dir / "memory" / "palace_summary.md"
        assert rollup_file.exists()

    def test_add_and_list_entries(
        self,
        temp_data_dir: Path,
        sample_memory_entry: dict,
    ) -> None:
        """Test adding and listing memory entries."""
        init_db(temp_data_dir)
        
        entry = add_entry(
            temp_data_dir,
            room=sample_memory_entry["room"],
            title=sample_memory_entry["title"],
            body=sample_memory_entry["body"],
            chapter_label=sample_memory_entry["chapter_label"],
        )
        
        assert entry["id"] is not None
        assert entry["room"] == sample_memory_entry["room"]
        assert entry["title"] == sample_memory_entry["title"]
        
        entries = list_entries(temp_data_dir)
        assert len(entries) == 1
        assert entries[0]["id"] == entry["id"]

    def test_delete_entry(self, temp_data_dir: Path) -> None:
        """Test deleting a memory entry."""
        init_db(temp_data_dir)
        
        entry = add_entry(
            temp_data_dir,
            room="测试",
            title="To be deleted",
            body="Content",
        )
        
        entries_before = list_entries(temp_data_dir)
        assert len(entries_before) == 1
        
        result = delete_entry(temp_data_dir, entry["id"])
        assert result is True
        
        entries_after = list_entries(temp_data_dir)
        assert len(entries_after) == 0

    def test_delete_nonexistent_entry(self, temp_data_dir: Path) -> None:
        """Test deleting an entry that doesn't exist."""
        init_db(temp_data_dir)
        
        result = delete_entry(temp_data_dir, 99999)
        assert result is False

    def test_read_write_rollup(self, temp_data_dir: Path) -> None:
        """Test reading and writing rollup summary."""
        init_db(temp_data_dir)
        
        test_content = "# Test Summary\n\nThis is a test rollup."
        write_rollup(temp_data_dir, test_content)
        
        content = read_rollup(temp_data_dir)
        assert content == test_content

    def test_build_memory_context(
        self,
        temp_data_dir: Path,
        sample_memory_entry: dict,
    ) -> None:
        """Test building memory context for LLM injection."""
        init_db(temp_data_dir)
        
        write_rollup(temp_data_dir, "Test rollup content")
        add_entry(
            temp_data_dir,
            room=sample_memory_entry["room"],
            title=sample_memory_entry["title"],
            body=sample_memory_entry["body"],
            chapter_label=sample_memory_entry["chapter_label"],
        )
        
        context = build_memory_context(temp_data_dir, max_chars=1000)
        
        assert "记忆宫殿" in context
        assert "总摘要" in context
        assert "近期条目" in context

    def test_build_memory_context_respects_limit(
        self,
        temp_data_dir: Path,
    ) -> None:
        """Test that memory context respects character limit."""
        init_db(temp_data_dir)
        
        # Add many entries
        for i in range(50):
            add_entry(
                temp_data_dir,
                room="情节",
                title=f"Entry {i}",
                body="X" * 100,  # Long body
            )
        
        context = build_memory_context(temp_data_dir, max_chars=500)
        
        assert len(context) <= 520  # Allow small buffer for truncation message
