"""Tests for FastAPI endpoints."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


class TestHealthEndpoint:
    """Tests for health check endpoint."""

    def test_health_endpoint(self, temp_data_dir: Path) -> None:
        """Test health endpoint returns expected structure."""
        with patch("app.main.ROOT", temp_data_dir):
            from app.main import app
            
            client = TestClient(app)
            response = client.get("/api/health")
            
            assert response.status_code == 200
            data = response.json()
            assert "ok" in data
            assert "user_data" in data
            assert "deepseek_configured" in data
            assert data.get("api_revision") >= 2
            assert data.get("pipeline_stream") is True


class TestThemesEndpoint:
    """Tests for themes endpoint."""

    def test_themes_endpoint(self, temp_data_dir: Path) -> None:
        """Test themes endpoint returns list."""
        with patch("app.main.ROOT", temp_data_dir):
            from app.main import app
            
            client = TestClient(app)
            response = client.get("/api/themes")
            
            assert response.status_code == 200
            data = response.json()
            assert "themes" in data
            assert isinstance(data["themes"], list)
            assert len(data["themes"]) > 0


class TestKbEndpoint:
    """Tests for knowledge base endpoint."""

    def test_kb_endpoint_empty(self, temp_data_dir: Path) -> None:
        """Test KB endpoint when no files exist."""
        with patch("app.main.ROOT", temp_data_dir):
            from app.main import app
            
            client = TestClient(app)
            response = client.get("/api/kb")
            
            assert response.status_code == 200
            data = response.json()
            assert "files" in data
            assert data["files"] == []

    def test_kb_endpoint_with_files(self, temp_data_dir: Path) -> None:
        """Test KB endpoint with existing files."""
        kb_dir = temp_data_dir / "kb"
        kb_dir.mkdir(parents=True, exist_ok=True)
        (kb_dir / "test-world.md").write_text("# Test World\n\nTest content", encoding="utf-8")
        
        with patch("app.main.ROOT", temp_data_dir):
            from app.main import app
            
            client = TestClient(app)
            response = client.get("/api/kb")
            
            assert response.status_code == 200
            data = response.json()
            assert "test-world.md" in data["files"]


class TestBooksEndpoints:
    """Tests for books endpoints."""

    def test_list_books_empty(self, temp_data_dir: Path) -> None:
        """Test listing books when none exist."""
        with patch("app.main.ROOT", temp_data_dir):
            from app.main import app
            
            client = TestClient(app)
            response = client.get("/api/books")
            
            assert response.status_code == 200
            data = response.json()
            assert data["books"] == []
            assert data.get("total") == 0

    def test_book_not_found(self, temp_data_dir: Path) -> None:
        """Test accessing non-existent book."""
        with patch("app.main.ROOT", temp_data_dir):
            from app.main import app
            
            client = TestClient(app)
            # Use a valid hex ID that doesn't exist
            response = client.get("/api/books/aabbccdd11223344")
            
            assert response.status_code == 404


class TestMemoryEndpoints:
    """Tests for memory endpoints."""

    def test_list_memory_entries_empty(self, temp_data_dir: Path) -> None:
        """Test listing memory entries when none exist."""
        with patch("app.main.ROOT", temp_data_dir):
            from app.main import app
            
            client = TestClient(app)
            response = client.get("/api/memory/entries")
            
            assert response.status_code == 200
            data = response.json()
            assert data["entries"] == []

    def test_add_and_get_memory_entry(self, temp_data_dir: Path) -> None:
        """Test adding and retrieving a memory entry."""
        with patch("app.main.ROOT", temp_data_dir):
            from app.main import app
            
            client = TestClient(app)
            
            # Add entry
            add_response = client.post(
                "/api/memory/entries",
                json={
                    "room": "情节",
                    "title": "Test Entry",
                    "body": "Test content",
                    "chapter_label": "1",
                },
            )
            
            assert add_response.status_code == 200
            entry = add_response.json()["entry"]
            assert entry["title"] == "Test Entry"
            
            # List entries
            list_response = client.get("/api/memory/entries")
            assert list_response.status_code == 200
            entries = list_response.json()["entries"]
            assert len(entries) == 1
            assert entries[0]["title"] == "Test Entry"


class TestExportPlainTextDedupesChapterTitle:
    def test_strip_redundant_chapter_title_line(self) -> None:
        from app.book_storage import _strip_redundant_chapter_title_line

        body = "开场标题\n\n正文第一段。"
        out = _strip_redundant_chapter_title_line(body, 1, "开场标题")
        assert "开场标题" not in out.split("\n")[0]
        assert out.startswith("正文第一段")

        keep = _strip_redundant_chapter_title_line("别的起句\n\n后文", 1, "开场标题")
        assert keep.startswith("别的起句")
