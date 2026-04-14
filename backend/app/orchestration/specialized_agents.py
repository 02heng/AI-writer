"""Specialized agents for enhanced novel writing quality.

This module provides additional specialized agents beyond the core Writer/Editor/Safety
pipeline, inspired by LibriScribe and GOAT-Storytelling-Agent architectures.

Additional Agents:
    - WorldbuildingAgent: Check world rules consistency
    - CharacterArcAgent: Track character development
    - PlotHoleAgent: Detect plot holes and inconsistencies
    - StyleConsistencyAgent: Check writing style consistency

Usage:
    from app.orchestration.specialized_agents import (
        agent_worldbuilding_check,
        agent_character_arc_check,
        agent_style_consistency_check,
    )
"""

from __future__ import annotations

import json
from typing import Any, Optional

from ..core.logging import get_logger
from ..jsonutil import extract_json_object
from ..llm import chat_completion

logger = get_logger(__name__)


# =============================================================================
# Worldbuilding Agent
# =============================================================================

def agent_worldbuilding_check(
    *,
    chapter_text: str,
    world_rules: str,
    temperature: float = 0.3,
) -> dict[str, Any]:
    """Check chapter against world-building rules for consistency.

    This agent verifies that the chapter content respects established
    world rules (magic systems, technology, social structures, etc.).

    Args:
        chapter_text: The chapter content to check
        world_rules: Established world-building rules from KB
        temperature: LLM temperature (low for consistency)

    Returns:
        Dict with 'violations' list and 'summary' string
    """
    if not world_rules.strip():
        return {"violations": [], "summary": "无世界观设定可对照"}

    sys_prompt = """你是世界观审核员。检查本章是否违反已设定的世界规则。
## 检查维度
- rules: 能力、科技、魔法、社会规则与设定冲突
- geography: 地理、地点描述与设定矛盾
- timeline: 历史事件、时间线与设定不符
- culture: 文化、习俗、语言与设定不一致
## 输出格式（仅 JSON，无 Markdown）
{
  "violations": [
    {
      "category": "rules|geography|timeline|culture|other",
      "rule": "被违反的具体设定",
      "evidence": "本章中的依据片段",
      "severity": "low|med|high",
      "suggestion": "修改建议"
    }
  ],
  "summary": "一两句总评"
}
若无问题，violations 为空数组。"""

    user_prompt = (
        f"【世界观设定】\n{world_rules[:6000]}\n\n"
        f"【本章正文】\n{chapter_text[:18000]}"
    )

    try:
        raw = chat_completion(
            system=sys_prompt,
            user=user_prompt,
            temperature=temperature,
        )
        result = extract_json_object(raw)
        logger.debug(
            "Worldbuilding check completed",
            extra={"violations_count": len(result.get("violations", []))},
        )
        return result
    except Exception as e:
        logger.error(f"Worldbuilding check failed: {e}")
        return {"violations": [], "summary": "", "_parse_error": True}


# =============================================================================
# Character Arc Agent
# =============================================================================

def agent_character_arc_check(
    *,
    chapter_text: str,
    character_profiles: list[dict[str, Any]],
    current_chapter: int,
    temperature: float = 0.35,
) -> dict[str, Any]:
    """Check character consistency and arc progression.

    This agent verifies that character behavior, speech patterns, and
    development arcs are consistent with established profiles.

    Args:
        chapter_text: The chapter content to check
        character_profiles: List of character profile dictionaries
        current_chapter: Current chapter number
        temperature: LLM temperature

    Returns:
        Dict with 'character_issues' and 'arc_progressions'
    """
    if not character_profiles:
        return {"character_issues": [], "arc_progressions": [], "summary": "无角色档案"}

    # Build character summary for prompt
    char_summaries = []
    for profile in character_profiles[:10]:  # Limit to 10 characters
        name = profile.get("name", "未知")
        personality = ", ".join(profile.get("personality", []))
        speech = profile.get("speech_pattern", "")
        arc = profile.get("arc_stage", "setup")
        char_summaries.append(f"- {name}: 性格({personality}), 说话风格({speech}), 弧线阶段({arc})")

    chars_text = "\n".join(char_summaries)

    sys_prompt = """你是角色一致性审核员。检查本章中角色的行为、对白和发展是否符合已建立的角色档案。
## 检查维度
- personality: 行为与性格设定不符
- speech: 对白风格与设定不一致
- arc: 角色发展是否合理推进
- relationship: 角色间互动与关系设定矛盾
## 输出格式（仅 JSON）
{
  "character_issues": [
    {
      "character": "角色名",
      "type": "personality|speech|arc|relationship",
      "description": "问题描述",
      "evidence": "本章依据",
      "suggestion": "修改建议"
    }
  ],
  "arc_progressions": [
    {
      "character": "角色名",
      "current_stage": "当前阶段",
      "suggested_next": "建议下一阶段"
    }
  ],
  "summary": "总评"
}"""

    user_prompt = (
        f"【角色档案】\n{chars_text}\n\n"
        f"【当前章节】第 {current_chapter} 章\n\n"
        f"【本章正文】\n{chapter_text[:18000]}"
    )

    try:
        raw = chat_completion(
            system=sys_prompt,
            user=user_prompt,
            temperature=temperature,
        )
        result = extract_json_object(raw)
        logger.debug(
            "Character arc check completed",
            extra={"issues_count": len(result.get("character_issues", []))},
        )
        return result
    except Exception as e:
        logger.error(f"Character arc check failed: {e}")
        return {"character_issues": [], "arc_progressions": [], "summary": "", "_parse_error": True}


# =============================================================================
# Style Consistency Agent
# =============================================================================

def agent_style_consistency_check(
    *,
    chapter_text: str,
    previous_chapters_sample: str,
    temperature: float = 0.4,
) -> dict[str, Any]:
    """Check writing style consistency across chapters.

    This agent detects sudden changes in writing style, tone, or
    AI-generated patterns that might break immersion.

    Args:
        chapter_text: The current chapter to check
        previous_chapters_sample: Sample text from previous chapters
        temperature: LLM temperature

    Returns:
        Dict with 'style_issues' and 'suggestions'
    """
    sys_prompt = """你是文风一致性审核员。检查本章的写作风格是否与前文保持一致。
## 检查维度
- tone: 叙事基调突变（如从严肃变轻浮）
- vocabulary: 用词风格突变（如突然出现大量文言或网络用语）
- pacing: 叙事节奏异常（如突然快慢失衡）
- ai_patterns: AI 腔调（如「值得一提的是」「不禁」「眸光微动」等）
## 输出格式（仅 JSON）
{
  "style_issues": [
    {
      "type": "tone|vocabulary|pacing|ai_patterns",
      "description": "问题描述",
      "examples": ["具体例子1", "具体例子2"],
      "severity": "low|med|high"
    }
  ],
  "ai_pattern_count": 0,
  "suggestions": ["建议1", "建议2"],
  "overall_consistency_score": 0-100,
  "summary": "总评"
}"""

    user_prompt = (
        f"【前文章节样例】\n{previous_chapters_sample[:8000]}\n\n"
        f"【本章正文】\n{chapter_text[:16000]}"
    )

    try:
        raw = chat_completion(
            system=sys_prompt,
            user=user_prompt,
            temperature=temperature,
        )
        result = extract_json_object(raw)
        score = result.get("overall_consistency_score", 0)
        logger.debug(
            "Style consistency check completed",
            extra={"consistency_score": score},
        )
        return result
    except Exception as e:
        logger.error(f"Style consistency check failed: {e}")
        return {
            "style_issues": [],
            "ai_pattern_count": 0,
            "suggestions": [],
            "overall_consistency_score": 50,
            "summary": "",
            "_parse_error": True,
        }


# =============================================================================
# Plot Hole Detection Agent
# =============================================================================

def agent_plot_hole_detection(
    *,
    chapter_text: str,
    plot_summary: str,
    character_states: str,
    temperature: float = 0.35,
) -> dict[str, Any]:
    """Detect plot holes and logical inconsistencies.

    This agent identifies potential plot holes, unresolved threads,
    and logical inconsistencies in the narrative.

    Args:
        chapter_text: The chapter to analyze
        plot_summary: Summary of the overall plot
        character_states: Current states of key characters
        temperature: LLM temperature

    Returns:
        Dict with 'plot_holes', 'unresolved_threads', and 'suggestions'
    """
    sys_prompt = """你是情节漏洞审核员。检查本章是否存在情节漏洞或逻辑矛盾。
## 检查维度
- logic: 事件因果逻辑不通
- information: 角色知道不该知道的信息
- timeline: 时间顺序矛盾
- motivation: 角色动机缺失或不合理
- resolution: 伏笔未回收或过早解决
## 输出格式（仅 JSON）
{
  "plot_holes": [
    {
      "type": "logic|information|timeline|motivation|resolution",
      "description": "漏洞描述",
      "location": "在文中的位置",
      "severity": "low|med|high",
      "fix_suggestion": "修复建议"
    }
  ],
  "unresolved_threads": [
    {
      "thread": "未解决的线索",
      "introduced_chapter": "引入章节",
      "urgency": "low|med|high"
    }
  ],
  "summary": "总评"
}"""

    user_prompt = (
        f"【剧情梗概】\n{plot_summary[:4000]}\n\n"
        f"【角色当前状态】\n{character_states[:3000]}\n\n"
        f"【本章正文】\n{chapter_text[:18000]}"
    )

    try:
        raw = chat_completion(
            system=sys_prompt,
            user=user_prompt,
            temperature=temperature,
        )
        result = extract_json_object(raw)
        logger.debug(
            "Plot hole detection completed",
            extra={"holes_count": len(result.get("plot_holes", []))},
        )
        return result
    except Exception as e:
        logger.error(f"Plot hole detection failed: {e}")
        return {
            "plot_holes": [],
            "unresolved_threads": [],
            "summary": "",
            "_parse_error": True,
        }


# =============================================================================
# Apply Fixes Agent
# =============================================================================

def agent_apply_style_fixes(
    *,
    chapter_text: str,
    style_issues: list[dict[str, Any]],
    temperature: float = 0.45,
) -> str:
    """Apply style fixes to chapter text.

    Args:
        chapter_text: Original chapter text
        style_issues: List of style issues to fix
        temperature: LLM temperature

    Returns:
        Revised chapter text
    """
    if not style_issues:
        return chapter_text

    issues_json = json.dumps(style_issues, ensure_ascii=False, indent=2)

    sys_prompt = """你是小说修订编辑。根据风格问题清单做最小幅度修改，仅消除风格不一致问题。
- 禁止借机重写情节或扩写内容
- 保持原有叙事节奏和结构
- 特别注意消除 AI 腔调（如「值得一提的是」「不禁」「眸光微动」等）
只输出修订后的本章正文全文。"""

    user_prompt = (
        f"【风格问题清单】\n{issues_json[:6000]}\n\n"
        f"【本章正文】\n{chapter_text[:24000]}"
    )

    try:
        return chat_completion(
            system=sys_prompt,
            user=user_prompt,
            temperature=temperature,
        ).strip()
    except Exception as e:
        logger.error(f"Style fix failed: {e}")
        return chapter_text


# =============================================================================
# Comprehensive Chapter Review
# =============================================================================

def run_comprehensive_review(
    *,
    chapter_text: str,
    world_rules: str = "",
    character_profiles: Optional[list[dict[str, Any]]] = None,
    previous_chapters_sample: str = "",
    plot_summary: str = "",
    current_chapter: int = 1,
    temperature: float = 0.35,
) -> dict[str, Any]:
    """Run all specialized agents for comprehensive chapter review.

    Args:
        chapter_text: Chapter to review
        world_rules: World-building rules
        character_profiles: Character profiles
        previous_chapters_sample: Sample from previous chapters
        plot_summary: Overall plot summary
        current_chapter: Chapter number
        temperature: Base temperature for all agents

    Returns:
        Comprehensive review results
    """
    results: dict[str, Any] = {
        "chapter": current_chapter,
        "reviews": {},
        "overall_score": 100,
        "needs_revision": False,
    }

    score_deductions = 0

    # Worldbuilding check
    if world_rules.strip():
        wb_result = agent_worldbuilding_check(
            chapter_text=chapter_text,
            world_rules=world_rules,
            temperature=temperature,
        )
        results["reviews"]["worldbuilding"] = wb_result
        violations = wb_result.get("violations", [])
        score_deductions += len(violations) * 5
        if violations:
            results["needs_revision"] = True

    # Character arc check
    if character_profiles:
        ca_result = agent_character_arc_check(
            chapter_text=chapter_text,
            character_profiles=character_profiles,
            current_chapter=current_chapter,
            temperature=temperature,
        )
        results["reviews"]["character_arc"] = ca_result
        issues = ca_result.get("character_issues", [])
        score_deductions += len(issues) * 4

    # Style consistency check
    if previous_chapters_sample.strip():
        sc_result = agent_style_consistency_check(
            chapter_text=chapter_text,
            previous_chapters_sample=previous_chapters_sample,
            temperature=temperature + 0.05,
        )
        results["reviews"]["style_consistency"] = sc_result
        style_issues = sc_result.get("style_issues", [])
        ai_count = sc_result.get("ai_pattern_count", 0)
        score_deductions += len(style_issues) * 3 + ai_count * 2
        if ai_count > 5:
            results["needs_revision"] = True

    # Plot hole detection
    if plot_summary.strip():
        ph_result = agent_plot_hole_detection(
            chapter_text=chapter_text,
            plot_summary=plot_summary,
            character_states="",  # Could be enhanced
            temperature=temperature,
        )
        results["reviews"]["plot_holes"] = ph_result
        holes = ph_result.get("plot_holes", [])
        high_severity = sum(1 for h in holes if h.get("severity") == "high")
        score_deductions += high_severity * 10 + (len(holes) - high_severity) * 3

    # Calculate overall score
    results["overall_score"] = max(0, 100 - score_deductions)

    logger.info(
        "Comprehensive review completed",
        extra={
            "chapter": current_chapter,
            "score": results["overall_score"],
            "needs_revision": results["needs_revision"],
        },
    )

    return results
