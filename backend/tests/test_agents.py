"""Tests for orchestration agents."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add parent directory to path for imports
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestAgents:
    """Tests for agent functions."""

    @patch("app.orchestration.agents.chat_completion")
    def test_agent_writer_draft(
        self,
        mock_chat: MagicMock,
    ) -> None:
        """Test that writer agent calls LLM correctly."""
        from app.orchestration.agents import agent_writer_draft
        
        mock_chat.return_value = "Generated chapter text..."
        
        result = agent_writer_draft(
            system="You are a writer",
            user_payload="Write chapter 1",
            temperature=0.8,
        )
        
        assert result == "Generated chapter text..."
        mock_chat.assert_called_once()

    @patch("app.orchestration.agents.chat_completion")
    def test_agent_character_polish(
        self,
        mock_chat: MagicMock,
    ) -> None:
        """Test character polish agent."""
        from app.orchestration.agents import agent_character_polish
        
        mock_chat.return_value = "Polished chapter text..."
        
        result = agent_character_polish(
            chapter_text="Original text",
            premise="A story about heroes",
            temperature=0.55,
        )
        
        assert result == "Polished chapter text..."
        mock_chat.assert_called_once()

    @patch("app.orchestration.agents.chat_completion")
    def test_agent_continuity_check_no_violations(
        self,
        mock_chat: MagicMock,
    ) -> None:
        """Test continuity check with no violations."""
        from app.orchestration.agents import agent_continuity_check
        
        mock_chat.return_value = '{"violations": [], "summary": "No issues found"}'
        
        result = agent_continuity_check(
            chapter_text="Chapter content",
            kb_excerpt="World settings",
            premise="Story premise",
            temperature=0.35,
        )
        
        assert result["violations"] == []
        assert result["summary"] == "No issues found"

    @patch("app.orchestration.agents.chat_completion")
    def test_agent_continuity_check_with_violations(
        self,
        mock_chat: MagicMock,
    ) -> None:
        """Test continuity check with violations detected."""
        from app.orchestration.agents import agent_continuity_check
        
        mock_chat.return_value = '''{
            "violations": [
                {
                    "category": "naming",
                    "point": "Character name inconsistent",
                    "evidence": "Line 50",
                    "severity": "med",
                    "suggested_fix": "Use consistent name"
                }
            ],
            "summary": "1 issue found"
        }'''
        
        result = agent_continuity_check(
            chapter_text="Chapter content",
            kb_excerpt="World settings",
            premise="Story premise",
            temperature=0.35,
        )
        
        assert len(result["violations"]) == 1
        assert result["violations"][0]["category"] == "naming"

    @patch("app.orchestration.agents.chat_completion")
    def test_agent_safety_pass_ok(
        self,
        mock_chat: MagicMock,
    ) -> None:
        """Test safety pass with OK status."""
        from app.orchestration.agents import agent_safety_pass
        
        mock_chat.return_value = '{"level": "ok", "notes": "Content is safe", "sanitized_text": ""}'
        
        result = agent_safety_pass(
            chapter_text="Safe content",
            temperature=0.25,
        )
        
        assert result["level"] == "ok"
        assert result["sanitized_text"] == ""

    @patch("app.orchestration.agents.chat_completion")
    def test_agent_safety_pass_block(
        self,
        mock_chat: MagicMock,
    ) -> None:
        """Test safety pass with block status."""
        from app.orchestration.agents import agent_safety_pass
        
        mock_chat.return_value = '{"level": "block", "notes": "Content flagged", "sanitized_text": "Cleaned version"}'
        
        result = agent_safety_pass(
            chapter_text="Flagged content",
            temperature=0.25,
        )
        
        assert result["level"] == "block"
        assert result["sanitized_text"] == "Cleaned version"
