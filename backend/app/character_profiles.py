"""Character profile management system.

This module provides functionality for creating, storing, and retrieving
character profiles for novels, ensuring consistency in character traits,
speech patterns, and relationships throughout the story.

Data structure:
    books/{book_id}/characters/
    ├── index.json              # Character index
    ├── {character_name}.json   # Individual profile
    └── relationships.json      # Relationship graph

Usage:
    from app.character_profiles import (
        create_character_profile,
        load_character_profile,
        list_characters,
        build_character_context,
    )
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException

from .core.logging import get_logger
from .schemas import CHARACTER_SCHEMA, validate_with_schema

logger = get_logger(__name__)


# =============================================================================
# Path Helpers
# =============================================================================

def characters_dir(book_root: Path) -> Path:
    """Get the characters directory for a book."""
    p = book_root / "characters"
    p.mkdir(parents=True, exist_ok=True)
    return p


def character_index_path(book_root: Path) -> Path:
    """Get the path to the character index file."""
    return characters_dir(book_root) / "index.json"


def character_profile_path(book_root: Path, name: str) -> Path:
    """Get the path to a character profile file."""
    safe_name = re.sub(r'[<>:"/\\|?*]', "", name)[:50]
    return characters_dir(book_root) / f"{safe_name}.json"


def relationships_path(book_root: Path) -> Path:
    """Get the path to the relationships file."""
    return characters_dir(book_root) / "relationships.json"


# =============================================================================
# Index Management
# =============================================================================

def _load_index(book_root: Path) -> dict[str, Any]:
    """Load the character index."""
    p = character_index_path(book_root)
    if not p.is_file():
        return {"characters": [], "last_updated": 0}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"characters": [], "last_updated": 0}


def _save_index(book_root: Path, data: dict[str, Any]) -> None:
    """Save the character index."""
    data["last_updated"] = time.time()
    character_index_path(book_root).write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _update_index_entry(book_root: Path, profile: dict[str, Any]) -> None:
    """Add or update a character entry in the index."""
    index = _load_index(book_root)
    name = profile.get("name", "")
    
    # Find existing entry
    existing_idx = None
    for i, char in enumerate(index.get("characters", [])):
        if char.get("name") == name:
            existing_idx = i
            break
    
    entry = {
        "name": name,
        "first_appear_chapter": profile.get("first_appear_chapter"),
        "last_mentioned_chapter": profile.get("last_mentioned_chapter"),
        "arc_stage": profile.get("arc_stage"),
        "updated_at": time.time(),
    }
    
    if existing_idx is not None:
        index["characters"][existing_idx] = entry
    else:
        index.setdefault("characters", []).append(entry)
    
    _save_index(book_root, index)


# =============================================================================
# Profile CRUD Operations
# =============================================================================

def create_character_profile(
    book_root: Path,
    *,
    name: str,
    age: Optional[int] = None,
    gender: Optional[str] = None,
    appearance: str = "",
    personality: Optional[list[str]] = None,
    speech_pattern: str = "",
    background: str = "",
    motivation: str = "",
    fear: str = "",
    relationships: Optional[dict[str, str]] = None,
    arc_stage: str = "setup",
    first_appear_chapter: int = 1,
    notes: str = "",
    validate: bool = True,
) -> dict[str, Any]:
    """Create a new character profile.

    Args:
        book_root: Path to the book's root directory
        name: Character name (required)
        age: Character age
        gender: Character gender
        appearance: Physical description
        personality: List of personality traits
        speech_pattern: How the character speaks
        background: Character backstory
        motivation: What drives the character
        fear: What the character fears
        relationships: Dict of {other_char: relationship_type}
        arc_stage: Current stage in character arc
        first_appear_chapter: Chapter where character first appears
        notes: Additional author notes
        validate: Whether to validate against schema

    Returns:
        The created profile dictionary
    """
    profile: dict[str, Any] = {
        "name": name.strip(),
        "age": age,
        "gender": gender,
        "appearance": appearance.strip(),
        "personality": personality or [],
        "speech_pattern": speech_pattern.strip(),
        "background": background.strip(),
        "motivation": motivation.strip(),
        "fear": fear.strip(),
        "relationships": relationships or {},
        "arc_stage": arc_stage,
        "first_appear_chapter": first_appear_chapter,
        "last_mentioned_chapter": first_appear_chapter,
        "notes": notes.strip(),
        "created_at": time.time(),
        "updated_at": time.time(),
    }

    # Remove None values
    profile = {k: v for k, v in profile.items() if v is not None}

    # Validate
    if validate:
        is_valid, error = validate_with_schema(profile, CHARACTER_SCHEMA)
        if not is_valid:
            logger.warning(f"Character profile validation failed: {error}")

    # Save profile
    p = character_profile_path(book_root, name)
    p.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Update index
    _update_index_entry(book_root, profile)

    # Update relationships if provided
    if relationships:
        _update_relationships(book_root, name, relationships)

    logger.info(f"Created character profile: {name}")
    return profile


def load_character_profile(book_root: Path, name: str) -> Optional[dict[str, Any]]:
    """Load a character profile by name.

    Args:
        book_root: Path to the book's root directory
        name: Character name

    Returns:
        Profile dictionary or None if not found
    """
    p = character_profile_path(book_root, name)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def update_character_profile(
    book_root: Path,
    name: str,
    updates: dict[str, Any],
) -> dict[str, Any]:
    """Update an existing character profile.

    Args:
        book_root: Path to the book's root directory
        name: Character name
        updates: Dictionary of fields to update

    Returns:
        Updated profile

    Raises:
        HTTPException: If character not found
    """
    profile = load_character_profile(book_root, name)
    if not profile:
        raise HTTPException(status_code=404, detail=f"角色不存在: {name}")

    # Apply updates
    profile.update(updates)
    profile["updated_at"] = time.time()

    # Save
    p = character_profile_path(book_root, name)
    p.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Update index
    _update_index_entry(book_root, profile)

    logger.info(f"Updated character profile: {name}")
    return profile


def delete_character_profile(book_root: Path, name: str) -> bool:
    """Delete a character profile.

    Args:
        book_root: Path to the book's root directory
        name: Character name

    Returns:
        True if deleted, False if not found
    """
    p = character_profile_path(book_root, name)
    if not p.is_file():
        return False

    p.unlink()

    # Remove from index
    index = _load_index(book_root)
    index["characters"] = [
        c for c in index.get("characters", []) if c.get("name") != name
    ]
    _save_index(book_root, index)

    logger.info(f"Deleted character profile: {name}")
    return True


def list_characters(book_root: Path) -> list[dict[str, Any]]:
    """List all characters for a book.

    Args:
        book_root: Path to the book's root directory

    Returns:
        List of character summary dictionaries
    """
    index = _load_index(book_root)
    return index.get("characters", [])


# =============================================================================
# Relationships
# =============================================================================

def _update_relationships(
    book_root: Path,
    char_name: str,
    relationships: dict[str, str],
) -> None:
    """Update the relationship graph for a character."""
    p = relationships_path(book_root)
    
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
    else:
        data = {}

    # Update relationships for this character
    data[char_name] = relationships

    # Also update reverse relationships
    for other_name, rel_type in relationships.items():
        if other_name not in data:
            data[other_name] = {}
        # Could add reverse relationship type here

    p.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_relationships(book_root: Path, char_name: str) -> dict[str, str]:
    """Get all relationships for a character.

    Args:
        book_root: Path to the book's root directory
        char_name: Character name

    Returns:
        Dict of {other_char: relationship_type}
    """
    p = relationships_path(book_root)
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get(char_name, {})
    except (OSError, json.JSONDecodeError):
        return {}


# =============================================================================
# Context Building
# =============================================================================

def build_character_context(
    book_root: Path,
    scene_characters: list[str],
    *,
    max_chars: int = 2000,
) -> str:
    """Build context string for characters in a scene.

    Args:
        book_root: Path to the book's root directory
        scene_characters: List of character names in the scene
        max_chars: Maximum characters for the context

    Returns:
        Formatted context string for LLM injection
    """
    parts: list[str] = ["【场景角色档案】"]
    used = 0

    for name in scene_characters:
        profile = load_character_profile(book_root, name)
        if not profile:
            continue

        char_block = f"\n【{name}】\n"
        
        if profile.get("appearance"):
            char_block += f"外貌：{profile['appearance']}\n"
        
        personality = profile.get("personality", [])
        if personality:
            char_block += f"性格：{', '.join(personality)}\n"
        
        if profile.get("speech_pattern"):
            char_block += f"说话风格：{profile['speech_pattern']}\n"
        
        if profile.get("motivation"):
            char_block += f"动机：{profile['motivation']}\n"
        
        # Get relationships with other scene characters
        rels = get_relationships(book_root, name)
        scene_rels = {k: v for k, v in rels.items() if k in scene_characters}
        if scene_rels:
            rel_str = ", ".join(f"{k}({v})" for k, v in scene_rels.items())
            char_block += f"场景内关系：{rel_str}\n"

        if used + len(char_block) > max_chars:
            parts.append("\n…（角色档案过长已截断）")
            break

        parts.append(char_block)
        used += len(char_block)

    return "\n".join(parts)


def update_character_mentions(
    book_root: Path,
    chapter_idx: int,
    mentioned_names: list[str],
) -> None:
    """Update last_mentioned_chapter for multiple characters.

    Args:
        book_root: Path to the book's root directory
        chapter_idx: Current chapter number
        mentioned_names: List of character names mentioned
    """
    for name in mentioned_names:
        profile = load_character_profile(book_root, name)
        if profile:
            update_character_profile(
                book_root,
                name,
                {"last_mentioned_chapter": chapter_idx},
            )


# =============================================================================
# Character Arc Tracking
# =============================================================================

ARC_STAGES = [
    "setup",
    "call_to_adventure",
    "refusal",
    "meeting_mentor",
    "crossing_threshold",
    "tests_allies_enemies",
    "approach",
    "ordeal",
    "reward",
    "road_back",
    "resurrection",
    "return",
]


def advance_character_arc(
    book_root: Path,
    name: str,
    new_stage: Optional[str] = None,
) -> dict[str, Any]:
    """Advance a character's arc to the next or specified stage.

    Args:
        book_root: Path to the book's root directory
        name: Character name
        new_stage: Specific stage to advance to (optional)

    Returns:
        Updated profile
    """
    profile = load_character_profile(book_root, name)
    if not profile:
        raise HTTPException(status_code=404, detail=f"角色不存在: {name}")

    current_stage = profile.get("arc_stage", "setup")
    
    if new_stage:
        if new_stage not in ARC_STAGES:
            raise HTTPException(
                status_code=400,
                detail=f"无效的角色弧阶段: {new_stage}",
            )
    else:
        # Advance to next stage
        current_idx = ARC_STAGES.index(current_stage) if current_stage in ARC_STAGES else 0
        next_idx = min(current_idx + 1, len(ARC_STAGES) - 1)
        new_stage = ARC_STAGES[next_idx]

    return update_character_profile(book_root, name, {"arc_stage": new_stage})


def get_characters_by_arc_stage(book_root: Path, stage: str) -> list[str]:
    """Get all characters at a specific arc stage.

    Args:
        book_root: Path to the book's root directory
        stage: Arc stage to filter by

    Returns:
        List of character names
    """
    index = _load_index(book_root)
    return [
        c.get("name", "")
        for c in index.get("characters", [])
        if c.get("arc_stage") == stage
    ]
