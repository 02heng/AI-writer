"""Test configuration and fixtures for AI Writer backend tests."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any, Generator

import pytest

# Add parent directory to path for imports
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def temp_data_dir() -> Generator[Path, None, None]:
    """Create a temporary directory for test data."""
    temp_dir = Path(tempfile.mkdtemp(prefix="aiwriter_test_"))
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def sample_book_meta() -> dict[str, Any]:
    """Sample book metadata for testing."""
    return {
        "id": "testbook123456",
        "title": "Test Novel",
        "created_at": 1700000000.0,
        "updated_at": 1700000000.0,
        "premise": "A test novel for unit testing.",
    }


@pytest.fixture
def sample_chapter_plan() -> dict[str, Any]:
    """Sample chapter plan for testing."""
    return {
        "idx": 1,
        "beat": "The protagonist discovers a mysterious artifact in their attic.",
        "pov": "第三人称限定主角",
        "conflict": "Curiosity vs. fear of the unknown",
        "scenes": [
            {
                "location": "Attic",
                "event": "Finding the artifact",
                "characters_present": ["protagonist"],
                "conflict": "Deciding whether to investigate",
                "outcome": "Takes the artifact downstairs",
            },
            {
                "location": "Living room",
                "event": "Examining the artifact",
                "characters_present": ["protagonist"],
                "conflict": "Understanding its purpose",
                "outcome": "Artifact begins to glow",
            },
        ],
        "hook_end": "The artifact reveals a hidden message.",
        "kb_tags": ["artifact", "mystery"],
        "characters_present": ["protagonist"],
    }


@pytest.fixture
def sample_character_profile() -> dict[str, Any]:
    """Sample character profile for testing."""
    return {
        "name": "Alice",
        "aliases": ["Ali", "The Curious One"],
        "age": 28,
        "appearance": "Short brown hair, green eyes, average height",
        "personality": ["curious", "brave", "stubborn"],
        "speech_pattern": "Speaks in short, direct sentences when excited",
        "background": "Former archaeologist turned bookstore owner",
        "relationships": {
            "Bob": "childhood friend",
            "Professor Chen": "mentor",
        },
        "arc_stage": "call_to_adventure",
        "first_appear_chapter": 1,
        "last_mentioned_chapter": 1,
    }


@pytest.fixture
def sample_memory_entry() -> dict[str, Any]:
    """Sample memory entry for testing."""
    return {
        "room": "情节",
        "title": "Discovery of the artifact",
        "body": "- Alice found a glowing orb in her attic\n- The orb is warm to touch\n- It shows visions when held",
        "chapter_label": "1",
    }


@pytest.fixture
def sample_kb_content() -> str:
    """Sample knowledge base content for testing."""
    return """# World Settings

## Magic System
- Artifacts can store memories
- Only certain people can activate them
- Activation requires emotional resonance

## Locations
- The Attic: Old family storage, full of antiques
- The Bookstore: Alice's shop, cozy and mysterious
"""


@pytest.fixture
def mock_llm_response_chapter() -> str:
    """Mock LLM response for chapter generation."""
    return """Alice pushed open the attic door, coughing as dust swirled around her.

"Grandmother's things," she muttered, stepping carefully between cardboard boxes.

Something glinted in the corner. She knelt down, brushing away years of neglect to reveal an orb the size of her fist. It pulsed with a faint blue light, warm against her palms when she picked it up.

"What are you?" she whispered.

The orb flickered. An image formed in its depths—a door she'd never seen, in a place she didn't recognize. Then came words, etched in light:

*Find the key before the shadow wakes.*

Alice's hands trembled. This wasn't just an antique. This was something else entirely."""
