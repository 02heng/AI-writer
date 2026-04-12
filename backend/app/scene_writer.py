"""Scene-level writing module for improved long-text generation.

This module implements the scene-level writing approach inspired by GOAT-Storytelling-Agent,
breaking chapters into smaller scenes for better LLM output quality.

Workflow:
    Chapter Contract → Split into Scenes → Generate Each Scene → Merge into Chapter

Usage:
    from app.scene_writer import (
        split_chapter_into_scenes,
        write_scene,
        write_chapter_by_scenes,
    )

    scenes = split_chapter_into_scenes(chapter_contract)
    chapter_text = write_chapter_by_scenes(scenes, system_prompt, context)
"""

from __future__ import annotations

import json
from typing import Any, Optional

from .core.logging import get_logger, LogContext
from .jsonutil import extract_json_object
from .llm import chat_completion

logger = get_logger(__name__)


# =============================================================================
# Scene Splitting
# =============================================================================

def split_chapter_into_scenes(
    *,
    chapter_contract: dict[str, Any],
    premise: str = "",
    temperature: float = 0.6,
    min_scenes: int = 2,
    max_scenes: int = 6,
) -> list[dict[str, Any]]:
    """Split a chapter contract into individual scenes.

    Args:
        chapter_contract: Chapter plan with beat, pov, conflict, etc.
        premise: Overall story premise for context
        temperature: LLM temperature for scene generation
        min_scenes: Minimum number of scenes to generate
        max_scenes: Maximum number of scenes to generate

    Returns:
        List of scene dictionaries with location, event, characters, etc.
    """
    with LogContext(
        logger, "split_chapter_into_scenes",
        chapter_idx=chapter_contract.get("idx", 0),
    ):
        # Check if scenes already exist in contract
        existing_scenes = chapter_contract.get("scenes")
        if existing_scenes and isinstance(existing_scenes, list) and len(existing_scenes) >= min_scenes:
            logger.info(
                "Using existing scenes from contract",
                extra={"scene_count": len(existing_scenes)},
            )
            return existing_scenes

        # Generate new scenes
        sys_prompt = (
            "你是小说结构编辑。将章节要点拆分为具体场景。\n"
            "## 输出格式（仅 JSON 数组，无 Markdown 围栏）\n"
            '[\n'
            '  {\n'
            '    "location": "string 场景地点",\n'
            '    "time": "string 可选，场景时间",\n'
            '    "event": "string 场景核心事件（必须）",\n'
            '    "characters_present": ["string 出场人物"],\n'
            '    "conflict": "string 场景内冲突",\n'
            '    "mood": "string 情绪基调",\n'
            '    "outcome": "string 场景结果"\n'
            '  }\n'
            ']\n'
            f"## 要求\n"
            f"- 生成 {min_scenes} 到 {max_scenes} 个场景\n"
            f"- 每个场景必须是独立的叙事单元\n"
            f"- 场景之间要有因果或递进关系\n"
            f"- 最后一个场景要引出下一章或留悬念"
        )

        beat = chapter_contract.get("beat", "")
        pov = chapter_contract.get("pov", "")
        conflict = chapter_contract.get("conflict", "")
        hook = chapter_contract.get("hook_end", "")
        chars = chapter_contract.get("characters_present", [])

        user_prompt = f"【章节节拍】\n{beat}\n\n"
        if premise:
            user_prompt += f"【全书梗概】\n{premise[:500]}\n\n"
        if pov:
            user_prompt += f"【叙事视角】{pov}\n"
        if conflict:
            user_prompt += f"【章节冲突】{conflict}\n"
        if hook:
            user_prompt += f"【章末钩子】{hook}\n"
        if chars:
            user_prompt += f"【出场人物】{', '.join(chars)}\n"

        user_prompt += "\n请生成场景列表。"

        try:
            raw = chat_completion(system=sys_prompt, user=user_prompt, temperature=temperature)
            scenes = extract_json_object(raw)

            if isinstance(scenes, list):
                validated_scenes = []
                for s in scenes:
                    if isinstance(s, dict) and s.get("event"):
                        validated_scenes.append(s)
                if len(validated_scenes) >= min_scenes:
                    logger.info(
                        "Generated scenes successfully",
                        extra={"scene_count": len(validated_scenes)},
                    )
                    return validated_scenes
        except Exception as e:
            logger.error(f"Failed to generate scenes: {e}")

        # Fallback: create scenes from beat
        logger.warning("Using fallback scene generation")
        return _create_fallback_scenes(chapter_contract, min_scenes)


def _create_fallback_scenes(
    chapter_contract: dict[str, Any],
    min_scenes: int,
) -> list[dict[str, Any]]:
    """Create simple scenes when LLM generation fails."""
    beat = chapter_contract.get("beat", "Chapter content")
    chars = chapter_contract.get("characters_present", [])

    # Split beat into roughly equal parts
    sentences = beat.replace("。", "。\n").split("\n")
    sentences = [s.strip() for s in sentences if s.strip()]

    if len(sentences) < min_scenes:
        # Create minimal scenes
        return [
            {
                "location": "未指定",
                "event": beat,
                "characters_present": chars,
                "mood": "neutral",
            }
            for _ in range(min_scenes)
        ]

    scenes = []
    for i, sentence in enumerate(sentences[:min_scenes]):
        scenes.append({
            "location": "未指定",
            "event": sentence,
            "characters_present": chars,
            "mood": "neutral" if i < len(sentences) - 1 else "tense",
        })

    return scenes


# =============================================================================
# Scene Writing
# =============================================================================

def write_scene(
    *,
    scene: dict[str, Any],
    system_prompt: str,
    context: str,
    temperature: float = 0.82,
    prev_scene_text: str = "",
    max_chars: int = 3000,
) -> str:
    """Generate text for a single scene.

    Args:
        scene: Scene dictionary with location, event, etc.
        system_prompt: Writer system prompt
        context: Context to inject (premise, characters, memory)
        temperature: LLM temperature
        prev_scene_text: Text of previous scene for continuity
        max_chars: Maximum characters for the scene

    Returns:
        Generated scene text
    """
    # Build scene instruction
    location = scene.get("location", "")
    time = scene.get("time", "")
    event = scene.get("event", "")
    chars = scene.get("characters_present", [])
    conflict = scene.get("conflict", "")
    mood = scene.get("mood", "")
    outcome = scene.get("outcome", "")

    scene_instruction = f"【当前场景】\n"
    if location:
        scene_instruction += f"地点：{location}\n"
    if time:
        scene_instruction += f"时间：{time}\n"
    scene_instruction += f"事件：{event}\n"
    if chars:
        scene_instruction += f"出场人物：{', '.join(chars)}\n"
    if conflict:
        scene_instruction += f"冲突：{conflict}\n"
    if mood:
        scene_instruction += f"情绪基调：{mood}\n"
    if outcome:
        scene_instruction += f"预期结果：{outcome}\n"

    user_prompt = f"{context}\n\n{scene_instruction}\n\n"
    user_prompt += "请写出本场景的完整正文。只输出小说正文，不要说明或标题。\n"
    user_prompt += f"本场景约 {max_chars} 字左右。"

    if prev_scene_text:
        # Add transition instruction
        user_prompt = f"【上一场景结尾】\n...{prev_scene_text[-300:]}\n\n{user_prompt}"
        user_prompt += "\n注意自然承接上一场景。"

    try:
        text = chat_completion(system=system_prompt, user=user_prompt, temperature=temperature)
        return text.strip()
    except Exception as e:
        logger.error(f"Failed to write scene: {e}")
        return ""


# =============================================================================
# Chapter Writing by Scenes
# =============================================================================

def write_chapter_by_scenes(
    *,
    scenes: list[dict[str, Any]],
    system_prompt: str,
    chapter_context: str,
    temperature: float = 0.82,
    scene_temperature_range: tuple[float, float] = (0.78, 0.85),
) -> tuple[str, list[dict[str, Any]]]:
    """Write a complete chapter by generating each scene.

    Args:
        scenes: List of scene dictionaries
        system_prompt: Writer system prompt
        chapter_context: Full context for the chapter
        temperature: Base temperature
        scene_temperature_range: Range for varying temperature per scene

    Returns:
        Tuple of (chapter_text, scene_logs)
    """
    with LogContext(logger, "write_chapter_by_scenes", scene_count=len(scenes)):
        scene_texts: list[str] = []
        scene_logs: list[dict[str, Any]] = []

        min_temp, max_temp = scene_temperature_range

        for i, scene in enumerate(scenes):
            # Vary temperature slightly per scene
            temp = min_temp + (max_temp - min_temp) * (i / max(len(scenes) - 1, 1))

            prev_text = scene_texts[-1] if scene_texts else ""

            text = write_scene(
                scene=scene,
                system_prompt=system_prompt,
                context=chapter_context,
                temperature=temp,
                prev_scene_text=prev_text,
            )

            if text:
                scene_texts.append(text)
                scene_logs.append({
                    "scene_idx": i,
                    "location": scene.get("location", ""),
                    "event": scene.get("event", "")[:100],
                    "length": len(text),
                    "temperature": temp,
                    "success": True,
                })
            else:
                scene_logs.append({
                    "scene_idx": i,
                    "event": scene.get("event", "")[:100],
                    "success": False,
                    "error": "Empty response",
                })

        # Merge scenes
        chapter_text = merge_scenes_to_chapter(scene_texts)

        logger.info(
            "Chapter written by scenes",
            extra={
                "total_scenes": len(scenes),
                "successful_scenes": len(scene_texts),
                "total_length": len(chapter_text),
            },
        )

        return chapter_text, scene_logs


def merge_scenes_to_chapter(
    scene_texts: list[str],
    add_transitions: bool = True,
) -> str:
    """Merge scene texts into a complete chapter.

    Args:
        scene_texts: List of scene text strings
        add_transitions: Whether to add transition hints between scenes

    Returns:
        Merged chapter text
    """
    if not scene_texts:
        return ""

    if len(scene_texts) == 1:
        return scene_texts[0]

    # Simple merge with paragraph break
    merged = "\n\n".join(scene_texts)

    # Clean up any double paragraph breaks
    while "\n\n\n" in merged:
        merged = merged.replace("\n\n\n", "\n\n")

    return merged.strip()


# =============================================================================
# Chapter Generation Entry Point
# =============================================================================

def generate_chapter_with_scenes(
    *,
    chapter_contract: dict[str, Any],
    system_prompt: str,
    premise: str,
    context: str,
    temperature: float = 0.82,
    use_scene_split: bool = True,
    min_scenes: int = 2,
    max_scenes: int = 5,
) -> tuple[str, dict[str, Any]]:
    """Generate a chapter using scene-level approach.

    This is the main entry point for scene-based chapter generation.

    Args:
        chapter_contract: Chapter plan with beat, scenes, etc.
        system_prompt: Writer system prompt
        premise: Story premise
        context: Full context (memory, kb, previous chapters)
        temperature: Base temperature for generation
        use_scene_split: Whether to split into scenes (vs direct generation)
        min_scenes: Minimum scenes when splitting
        max_scenes: Maximum scenes when splitting

    Returns:
        Tuple of (chapter_text, generation_metadata)
    """
    metadata: dict[str, Any] = {
        "method": "scene_based" if use_scene_split else "direct",
        "chapter_idx": chapter_contract.get("idx", 0),
    }

    if not use_scene_split:
        # Fall back to direct chapter generation
        logger.info("Using direct chapter generation")
        user_prompt = f"{context}\n\n请写出本章完整正文。"
        text = chat_completion(system=system_prompt, user=user_prompt, temperature=temperature)
        metadata["method"] = "direct"
        metadata["length"] = len(text)
        return text.strip(), metadata

    # Scene-based generation
    with LogContext(
        logger, "generate_chapter_with_scenes",
        chapter_idx=chapter_contract.get("idx", 0),
    ):
        # Split into scenes
        scenes = split_chapter_into_scenes(
            chapter_contract=chapter_contract,
            premise=premise,
            min_scenes=min_scenes,
            max_scenes=max_scenes,
        )

        metadata["scene_count"] = len(scenes)

        # Write each scene
        chapter_text, scene_logs = write_chapter_by_scenes(
            scenes=scenes,
            system_prompt=system_prompt,
            chapter_context=context,
            temperature=temperature,
        )

        metadata["scene_logs"] = scene_logs
        metadata["length"] = len(chapter_text)

        return chapter_text, metadata
