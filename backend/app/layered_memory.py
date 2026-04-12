"""Layered memory architecture for long-context novel writing.

This module implements a three-tier memory system inspired by modern AI agent
architectures, designed to maintain consistency in long novel generation.

Memory Tiers:
    1. Long-term Memory (LTM): Persistent world rules, character profiles
    2. Episodic Memory (EM): Events, plot points, foreshadowing
    3. Working Memory (WM): Recent chapters, immediate context

Usage:
    from app.layered_memory import (
        LayeredMemory,
        build_context_for_chapter,
    )

    memory = LayeredMemory(book_root)
    context = memory.build_context(current_chapter=5, scene_chars=["Alice"])
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from .core.logging import get_logger, LogContext
from .memory_store import (
    add_entry as add_memory_entry,
    build_memory_context as build_legacy_memory_context,
    init_db,
    list_entries,
    read_rollup,
    write_rollup,
)

logger = get_logger(__name__)


class MemoryTier(Enum):
    """Memory tier levels."""
    LONG_TERM = "long_term"      # World rules, character profiles
    EPISODIC = "episodic"        # Events, plot points
    WORKING = "working"          # Recent context


# =============================================================================
# Long-term Memory (World Building)
# =============================================================================

def load_world_rules(book_root: Path) -> str:
    """Load world-building rules from kb/ directory.

    Args:
        book_root: Path to the book's root directory

    Returns:
        Concatenated world rules text
    """
    kb_dir = book_root / "kb"
    if not kb_dir.is_dir():
        return ""

    parts: list[str] = []
    for md_file in sorted(kb_dir.glob("*.md")):
        try:
            content = md_file.read_text(encoding="utf-8").strip()
            if content:
                parts.append(f"【{md_file.stem}】\n{content}")
        except OSError:
            continue

    return "\n\n".join(parts)


def load_character_profiles_summary(book_root: Path) -> str:
    """Load summary of all character profiles.

    Args:
        book_root: Path to the book's root directory

    Returns:
        Formatted character summary
    """
    chars_dir = book_root / "characters"
    if not chars_dir.is_dir():
        return ""

    index_path = chars_dir / "index.json"
    if not index_path.is_file():
        return ""

    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""

    parts: list[str] = ["【角色清单】"]
    for char in index.get("characters", []):
        name = char.get("name", "")
        if not name:
            continue
        
        profile_path = chars_dir / f"{name}.json"
        if profile_path.is_file():
            try:
                profile = json.loads(profile_path.read_text(encoding="utf-8"))
                summary = f"- {name}"
                if profile.get("age"):
                    summary += f"（{profile['age']}岁）"
                if profile.get("personality"):
                    summary += f"：{', '.join(profile['personality'][:3])}"
                parts.append(summary)
            except (OSError, json.JSONDecodeError):
                parts.append(f"- {name}")

    return "\n".join(parts)


# =============================================================================
# Episodic Memory (Events)
# =============================================================================

def get_episodic_events(
    book_root: Path,
    *,
    limit: int = 30,
    chapter_range: Optional[tuple[int, int]] = None,
) -> list[dict[str, Any]]:
    """Get episodic events from memory store.

    Args:
        book_root: Path to the book's root directory
        limit: Maximum number of events
        chapter_range: Optional (min_ch, max_ch) filter

    Returns:
        List of event dictionaries
    """
    init_db(book_root)
    entries = list_entries(book_root, limit=limit * 2)

    events = []
    for entry in entries:
        if chapter_range:
            ch_label = entry.get("chapter_label", "")
            try:
                ch_num = int(ch_label) if ch_label else 0
                if not (chapter_range[0] <= ch_num <= chapter_range[1]):
                    continue
            except ValueError:
                pass

        events.append(entry)
        if len(events) >= limit:
            break

    return events


def format_episodic_memory(events: list[dict[str, Any]], max_chars: int = 3000) -> str:
    """Format episodic events for context injection.

    Args:
        events: List of event dictionaries
        max_chars: Maximum characters

    Returns:
        Formatted text
    """
    if not events:
        return ""

    parts: list[str] = ["【关键事件回顾】"]
    used = 0

    for event in events:
        block = f"\n[{event.get('room', '情节')}] {event.get('title', '')}"
        if event.get("chapter_label"):
            block += f"（第{event['chapter_label']}章）"
        block += f"\n{event.get('body', '')}"

        if used + len(block) > max_chars:
            parts.append("\n…（事件记录过长已截断）")
            break

        parts.append(block)
        used += len(block)

    return "\n".join(parts)


# =============================================================================
# Working Memory (Recent Chapters)
# =============================================================================

def get_recent_chapter_summaries(
    book_root: Path,
    current_chapter: int,
    *,
    lookback: int = 3,
) -> str:
    """Get summaries of recent chapters.

    Args:
        book_root: Path to the book's root directory
        current_chapter: Current chapter being written
        lookback: Number of chapters to look back

    Returns:
        Formatted recent chapter summaries
    """
    if current_chapter <= 1:
        return "（这是第一章，无前文摘要。）"

    parts: list[str] = ["【最近章节摘要】"]

    # Check for chapter summaries in rollup
    rollup = read_rollup(book_root)
    if rollup:
        # Parse chapter summaries from rollup
        lines = rollup.split("\n")
        for line in lines:
            if "第" in line and "章摘要" in line:
                parts.append(line)
                if len(parts) > lookback + 1:
                    break

    if len(parts) == 1:
        # Fallback to reading last chapters
        chapters_dir = book_root / "chapters"
        if chapters_dir.is_dir():
            for ch_num in range(current_chapter - 1, max(0, current_chapter - lookback - 1), -1):
                ch_path = chapters_dir / f"{ch_num:02d}.md"
                if ch_path.is_file():
                    try:
                        content = ch_path.read_text(encoding="utf-8")
                        # Extract first 200 chars as summary
                        summary = content.strip()[:200].replace("\n", " ")
                        parts.append(f"第{ch_num}章：{summary}…")
                    except OSError:
                        continue

    return "\n".join(parts) if len(parts) > 1 else "（无前文摘要）"


def get_previous_chapter_text(
    book_root: Path,
    current_chapter: int,
    *,
    max_chars: int = 4000,
) -> str:
    """Get the text of the previous chapter for context.

    Args:
        book_root: Path to the book's root directory
        current_chapter: Current chapter being written
        max_chars: Maximum characters to include

    Returns:
        Previous chapter text (truncated)
    """
    if current_chapter <= 1:
        return ""

    prev_ch_path = book_root / "chapters" / f"{current_chapter - 1:02d}.md"
    if not prev_ch_path.is_file():
        return ""

    try:
        content = prev_ch_path.read_text(encoding="utf-8")
        # Remove HTML comment header if present
        if content.strip().startswith("<!--"):
            end = content.find("-->")
            if end != -1:
                content = content[end + 3 :].strip()

        # Truncate to max_chars, preferring the end
        if len(content) > max_chars:
            content = "…[前文省略]…\n" + content[-max_chars:]

        return content
    except OSError:
        return ""


# =============================================================================
# Layered Memory Class
# =============================================================================

class LayeredMemory:
    """Manages the three-tier memory system for a book.

    Usage:
        memory = LayeredMemory(book_root)
        context = memory.build_context(current_chapter=5)
    """

    def __init__(
        self,
        book_root: Path,
        *,
        ltm_max_chars: int = 2000,
        em_max_chars: int = 3000,
        wm_max_chars: int = 5000,
    ) -> None:
        """Initialize layered memory.

        Args:
            book_root: Path to the book's root directory
            ltm_max_chars: Max chars for long-term memory
            em_max_chars: Max chars for episodic memory
            wm_max_chars: Max chars for working memory
        """
        self.book_root = book_root
        self.ltm_max_chars = ltm_max_chars
        self.em_max_chars = em_max_chars
        self.wm_max_chars = wm_max_chars

        # Ensure memory is initialized
        init_db(book_root)

    def get_long_term_memory(self) -> str:
        """Get long-term memory content."""
        parts: list[str] = []

        # World rules
        world_rules = load_world_rules(self.book_root)
        if world_rules:
            parts.append(world_rules[:self.ltm_max_chars // 2])

        # Character profiles
        char_summary = load_character_profiles_summary(self.book_root)
        if char_summary:
            parts.append(char_summary)

        return "\n\n".join(parts)[:self.ltm_max_chars]

    def get_episodic_memory(
        self,
        *,
        chapter_range: Optional[tuple[int, int]] = None,
    ) -> str:
        """Get episodic memory content."""
        events = get_episodic_events(
            self.book_root,
            limit=20,
            chapter_range=chapter_range,
        )
        return format_episodic_memory(events, self.em_max_chars)

    def get_working_memory(
        self,
        current_chapter: int,
        *,
        include_prev_chapter: bool = True,
    ) -> str:
        """Get working memory content."""
        parts: list[str] = []

        # Recent chapter summaries
        summaries = get_recent_chapter_summaries(
            self.book_root,
            current_chapter,
            lookback=3,
        )
        parts.append(summaries)

        # Previous chapter text
        if include_prev_chapter and current_chapter > 1:
            prev_text = get_previous_chapter_text(
                self.book_root,
                current_chapter,
                max_chars=self.wm_max_chars - 500,
            )
            if prev_text:
                parts.append(f"\n【上一章正文（节选）】\n{prev_text}")

        return "\n".join(parts)[:self.wm_max_chars]

    def build_context(
        self,
        current_chapter: int,
        *,
        scene_characters: Optional[list[str]] = None,
        include_ltm: bool = True,
        include_em: bool = True,
        include_wm: bool = True,
        max_total_chars: int = 8000,
    ) -> str:
        """Build complete context for chapter generation.

        Args:
            current_chapter: Current chapter being written
            scene_characters: Characters in current scene (for filtering)
            include_ltm: Include long-term memory
            include_em: Include episodic memory
            include_wm: Include working memory
            max_total_chars: Maximum total characters

        Returns:
            Formatted context string for LLM injection
        """
        with LogContext(
            logger, "build_memory_context",
            chapter=current_chapter,
        ):
            parts: list[str] = []
            used = 0

            # Long-term memory
            if include_ltm:
                ltm = self.get_long_term_memory()
                if ltm:
                    parts.append(f"【世界观与设定】\n{ltm}")
                    used += len(ltm)

            # Episodic memory
            if include_em and used < max_total_chars:
                em = self.get_episodic_memory()
                if em:
                    remaining = max_total_chars - used
                    parts.append(em[:remaining])
                    used += len(em)

            # Working memory
            if include_wm and used < max_total_chars:
                remaining = max_total_chars - used
                wm = self.get_working_memory(
                    current_chapter,
                    include_prev_chapter=(current_chapter > 1),
                )
                if wm:
                    parts.append(wm[:remaining])

            return "\n\n".join(parts)

    def add_event(
        self,
        *,
        room: str,
        title: str,
        body: str,
        chapter_label: Optional[str] = None,
    ) -> dict[str, Any]:
        """Add a new event to episodic memory.

        Args:
            room: Event category
            title: Event title
            body: Event description
            chapter_label: Associated chapter

        Returns:
            Created event entry
        """
        return add_memory_entry(
            self.book_root,
            room=room,
            title=title,
            body=body,
            chapter_label=chapter_label,
        )

    def update_summary(self, summary: str) -> None:
        """Update the overall memory summary."""
        write_rollup(self.book_root, summary)


# =============================================================================
# Convenience Functions
# =============================================================================

def build_context_for_chapter(
    book_root: Path,
    current_chapter: int,
    *,
    max_chars: int = 8000,
) -> str:
    """Build context for chapter generation.

    This is a convenience function that creates a LayeredMemory instance
    and builds context in one call.

    Args:
        book_root: Path to the book's root directory
        current_chapter: Current chapter being written
        max_chars: Maximum characters

    Returns:
        Formatted context string
    """
    memory = LayeredMemory(book_root)
    return memory.build_context(current_chapter, max_total_chars=max_chars)
