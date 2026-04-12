"""JSON Schema definitions for structured AI output validation.

This module provides JSON Schema definitions that constrain AI output format,
ensuring consistent and reliable structured data for novel planning and generation.

Usage:
    from app.schemas import validate_with_schema, CHAPTER_PLAN_SCHEMA

    is_valid, error = validate_with_schema(data, CHAPTER_PLAN_SCHEMA)
"""

from __future__ import annotations

from typing import Any

try:
    import jsonschema
    from jsonschema import ValidationError
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False
    ValidationError = Exception


# =============================================================================
# Book Plan Schema
# =============================================================================

BOOK_PLAN_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["book_title", "premise", "chapters"],
    "properties": {
        "book_title": {
            "type": "string",
            "minLength": 1,
            "maxLength": 100,
            "description": "The title of the book",
        },
        "premise": {
            "type": "string",
            "minLength": 50,
            "maxLength": 4000,
            "description": "Overall plot summary",
        },
        "meta": {
            "type": "object",
            "properties": {
                "length_scale": {
                    "type": "string",
                    "enum": ["short", "medium", "long"],
                },
                "protagonist_gender": {
                    "type": "string",
                    "enum": ["male", "female", "any"],
                },
                "chapter_count": {
                    "type": "integer",
                    "minimum": 3,
                    "maximum": 1500,
                    "description": "本轮写入 plan 的分章数（一键生成上限）",
                },
                "chapters_this_run": {
                    "type": "integer",
                    "minimum": 3,
                    "maximum": 1500,
                },
                "planned_total_chapters": {
                    "type": "integer",
                    "minimum": 3,
                    "maximum": 5000,
                    "description": "用户声明的全书预定总尺度，可大于 chapter_count",
                },
                "macro_outline": {
                    "type": "object",
                    "description": "两阶策划宏观阶段表（phases、ending_direction 等）",
                },
            },
        },
        "chapters": {
            "type": "array",
            "minItems": 3,
            "maxItems": 1500,
            "items": {"$ref": "#/$defs/chapter"},
        },
    },
    "$defs": {
        "chapter": {
            "type": "object",
            "required": ["idx", "beat"],
            "properties": {
                "idx": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1500,
                    "description": "Chapter number (1-indexed)",
                },
                "beat": {
                    "type": "string",
                    "minLength": 50,
                    "maxLength": 400,
                    "description": "Chapter plot beat in 50-400 characters",
                },
                "pov": {
                    "type": "string",
                    "enum": ["第一人称", "第三人称限定", "第三人称全知", "第二人称"],
                    "description": "Narrative point of view",
                },
                "conflict": {
                    "type": "string",
                    "maxLength": 200,
                    "description": "Core conflict of this chapter",
                },
                "scenes": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 8,
                    "items": {"$ref": "#/$defs/scene"},
                    "description": "List of scenes in this chapter",
                },
                "hook_end": {
                    "type": "string",
                    "maxLength": 150,
                    "description": "Hook at the end of chapter",
                },
                "kb_tags": {
                    "type": "array",
                    "maxItems": 15,
                    "items": {"type": "string", "maxLength": 50},
                    "description": "Keywords to reference in knowledge base",
                },
                "characters_present": {
                    "type": "array",
                    "maxItems": 20,
                    "items": {"type": "string", "maxLength": 50},
                    "description": "Characters appearing in this chapter",
                },
            },
        },
        "scene": {
            "type": "object",
            "required": ["location", "event"],
            "properties": {
                "location": {
                    "type": "string",
                    "maxLength": 100,
                    "description": "Where the scene takes place",
                },
                "time": {
                    "type": "string",
                    "maxLength": 50,
                    "description": "When the scene takes place",
                },
                "event": {
                    "type": "string",
                    "maxLength": 200,
                    "description": "What happens in this scene",
                },
                "characters_present": {
                    "type": "array",
                    "maxItems": 10,
                    "items": {"type": "string"},
                    "description": "Characters in this scene",
                },
                "conflict": {
                    "type": "string",
                    "maxLength": 150,
                    "description": "Conflict in this scene",
                },
                "mood": {
                    "type": "string",
                    "maxLength": 50,
                    "description": "Emotional tone of the scene",
                },
                "outcome": {
                    "type": "string",
                    "maxLength": 150,
                    "description": "Result of this scene",
                },
            },
        },
    },
}


# =============================================================================
# Scene Schema (for scene-level generation)
# =============================================================================

SCENE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["location", "event"],
    "properties": {
        "location": {
            "type": "string",
            "minLength": 1,
            "maxLength": 100,
        },
        "time": {
            "type": "string",
            "maxLength": 50,
        },
        "event": {
            "type": "string",
            "minLength": 10,
            "maxLength": 300,
        },
        "characters_present": {
            "type": "array",
            "items": {"type": "string"},
        },
        "conflict": {
            "type": "string",
            "maxLength": 200,
        },
        "mood": {
            "type": "string",
            "maxLength": 50,
        },
        "outcome": {
            "type": "string",
            "maxLength": 200,
        },
        "dialogue_focus": {
            "type": "boolean",
            "description": "Whether this scene focuses on dialogue",
        },
        "sensory_details": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Key sensory elements to include",
        },
    },
}


# =============================================================================
# Character Profile Schema
# =============================================================================

CHARACTER_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["name"],
    "properties": {
        "name": {
            "type": "string",
            "minLength": 1,
            "maxLength": 50,
            "description": "Character name",
        },
        "aliases": {
            "type": "array",
            "maxItems": 10,
            "items": {"type": "string", "maxLength": 50},
            "description": "Alternative names or nicknames",
        },
        "age": {
            "type": "integer",
            "minimum": 0,
            "maximum": 1000,
            "description": "Character age",
        },
        "gender": {
            "type": "string",
            "enum": ["male", "female", "other", "unknown"],
        },
        "appearance": {
            "type": "string",
            "maxLength": 500,
            "description": "Physical description",
        },
        "personality": {
            "type": "array",
            "maxItems": 10,
            "items": {"type": "string", "maxLength": 30},
            "description": "Personality traits",
        },
        "speech_pattern": {
            "type": "string",
            "maxLength": 300,
            "description": "How the character speaks",
        },
        "background": {
            "type": "string",
            "maxLength": 1000,
            "description": "Character backstory",
        },
        "motivation": {
            "type": "string",
            "maxLength": 300,
            "description": "What drives the character",
        },
        "fear": {
            "type": "string",
            "maxLength": 200,
            "description": "What the character fears",
        },
        "relationships": {
            "type": "object",
            "additionalProperties": {"type": "string"},
            "description": "Relationships with other characters",
        },
        "arc_stage": {
            "type": "string",
            "enum": [
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
            ],
            "description": "Current stage in character arc",
        },
        "first_appear_chapter": {
            "type": "integer",
            "minimum": 1,
            "description": "Chapter where character first appears",
        },
        "last_mentioned_chapter": {
            "type": "integer",
            "minimum": 1,
            "description": "Last chapter where character was mentioned",
        },
        "notes": {
            "type": "string",
            "maxLength": 500,
            "description": "Additional notes for the author",
        },
    },
}


# =============================================================================
# World Building Schema
# =============================================================================

WORLD_BUILDING_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "setting": {
            "type": "object",
            "properties": {
                "time_period": {"type": "string"},
                "world_type": {
                    "type": "string",
                    "enum": ["realistic", "fantasy", "scifi", "historical", "other"],
                },
                "geography": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "description": {"type": "string"},
                        },
                    },
                },
            },
        },
        "magic_system": {
            "type": "object",
            "properties": {
                "exists": {"type": "boolean"},
                "rules": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "limitations": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        },
        "technology": {
            "type": "object",
            "properties": {
                "level": {"type": "string"},
                "key_inventions": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        },
        "social_structure": {
            "type": "object",
            "properties": {
                "government": {"type": "string"},
                "social_classes": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "customs": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        },
        "factions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "goal": {"type": "string"},
                    "members": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "conflicts_with": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
        },
    },
}


# =============================================================================
# Memory Entry Schema
# =============================================================================

MEMORY_ENTRY_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["room", "title", "body"],
    "properties": {
        "room": {
            "type": "string",
            "enum": ["情节", "人物", "世界观", "伏笔", "时间线", "设定", "其他"],
            "description": "Memory category",
        },
        "title": {
            "type": "string",
            "minLength": 1,
            "maxLength": 100,
        },
        "body": {
            "type": "string",
            "minLength": 1,
            "maxLength": 5000,
        },
        "chapter_label": {
            "type": "string",
            "maxLength": 20,
        },
        "importance": {
            "type": "string",
            "enum": ["low", "medium", "high", "critical"],
        },
        "related_characters": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
}


# =============================================================================
# Continuity Check Result Schema
# =============================================================================

CONTINUITY_CHECK_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["violations", "summary"],
    "properties": {
        "violations": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["category", "point"],
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["naming", "timeline", "rules", "identity", "other"],
                    },
                    "point": {"type": "string"},
                    "evidence": {"type": "string"},
                    "severity": {
                        "type": "string",
                        "enum": ["low", "med", "high"],
                    },
                    "suggested_fix": {"type": "string"},
                },
            },
        },
        "summary": {"type": "string"},
    },
}


# =============================================================================
# Validation Functions
# =============================================================================

def validate_with_schema(
    data: dict[str, Any],
    schema: dict[str, Any],
) -> tuple[bool, str]:
    """Validate data against a JSON Schema.

    Args:
        data: The data to validate
        schema: The JSON Schema to validate against

    Returns:
        Tuple of (is_valid, error_message)
        If valid, error_message is empty string.
    """
    if not HAS_JSONSCHEMA:
        # If jsonschema is not installed, skip validation
        return True, ""

    try:
        jsonschema.validate(data, schema)
        return True, ""
    except ValidationError as e:
        return False, str(e.message)
    except Exception as e:
        return False, str(e)


def validate_book_plan(data: dict[str, Any]) -> tuple[bool, str]:
    """Validate a book plan against the schema."""
    return validate_with_schema(data, BOOK_PLAN_SCHEMA)


def validate_chapter(data: dict[str, Any]) -> tuple[bool, str]:
    """Validate a chapter against the chapter schema."""
    chapter_schema = BOOK_PLAN_SCHEMA["$defs"]["chapter"]
    return validate_with_schema(data, chapter_schema)


def validate_scene(data: dict[str, Any]) -> tuple[bool, str]:
    """Validate a scene against the scene schema."""
    return validate_with_schema(data, SCENE_SCHEMA)


def validate_character(data: dict[str, Any]) -> tuple[bool, str]:
    """Validate a character profile against the schema."""
    return validate_with_schema(data, CHARACTER_SCHEMA)


def validate_memory_entry(data: dict[str, Any]) -> tuple[bool, str]:
    """Validate a memory entry against the schema."""
    return validate_with_schema(data, MEMORY_ENTRY_SCHEMA)


def get_schema_for_type(schema_type: str) -> dict[str, Any] | None:
    """Get a schema by type name.

    Args:
        schema_type: One of 'book_plan', 'chapter', 'scene', 'character',
                     'world_building', 'memory_entry', 'continuity_check'

    Returns:
        The schema dict or None if not found
    """
    schemas = {
        "book_plan": BOOK_PLAN_SCHEMA,
        "chapter": BOOK_PLAN_SCHEMA["$defs"]["chapter"],
        "scene": SCENE_SCHEMA,
        "character": CHARACTER_SCHEMA,
        "world_building": WORLD_BUILDING_SCHEMA,
        "memory_entry": MEMORY_ENTRY_SCHEMA,
        "continuity_check": CONTINUITY_CHECK_SCHEMA,
    }
    return schemas.get(schema_type)
