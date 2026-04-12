from __future__ import annotations

import json
from typing import Any, Optional

from .agents import (
    agent_apply_continuity_fixes,
    agent_character_polish,
    agent_continuity_check,
    agent_editor_pass,
    agent_reader_blind_test,
    agent_safety_pass,
    agent_writer_draft,
)


def run_chapter_with_agents(
    *,
    system: str,
    user_payload: str,
    writing_temp: float,
    premise: str,
    kb_block: str,
    agent_profile: str = "fast",
    run_reader_test: bool = False,
) -> tuple[str, dict[str, Any]]:
    """
    fast: 单次 Writer。
    full: Writer → Character 润色 → Continuity（有则修）→ Editor（可选替换）→ Safety（block 则替换）→ 可选 Reader。
    """
    log: dict[str, Any] = {"profile": agent_profile, "steps": []}

    text = agent_writer_draft(system=system, user_payload=user_payload, temperature=writing_temp)
    log["steps"].append({"agent": "Writer", "ok": True})

    if (agent_profile or "fast").lower() != "full":
        return text.strip(), log

    try:
        polished = agent_character_polish(chapter_text=text, premise=premise, temperature=0.55)
        if polished.strip() and len(polished) > 80:
            text = polished
            log["steps"].append({"agent": "Character", "ok": True})
    except Exception as e:
        log["steps"].append({"agent": "Character", "ok": False, "error": str(e)})

    cont: dict[str, Any] = {}
    try:
        cont = agent_continuity_check(
            chapter_text=text,
            kb_excerpt=kb_block or "",
            premise=premise,
            temperature=0.35,
        )
        viol = cont.get("violations") if isinstance(cont.get("violations"), list) else []
        if viol:
            fix = agent_apply_continuity_fixes(
                chapter_text=text,
                violations_json=json.dumps(cont, ensure_ascii=False)[:12000],
                temperature=0.45,
            )
            if fix.strip():
                text = fix
        log["steps"].append({"agent": "Lore/Continuity", "ok": True, "violations_count": len(viol)})
    except Exception as e:
        log["steps"].append({"agent": "Lore/Continuity", "ok": False, "error": str(e)})

    try:
        ed = agent_editor_pass(chapter_text=text, premise=premise, temperature=0.45)
        revised = str(ed.get("revised_text") or "").strip()
        if revised and len(revised) > 200:
            text = revised
        log["steps"].append({"agent": "Editor", "ok": True, "comments": (ed.get("comments") or "")[:500]})
    except Exception as e:
        log["steps"].append({"agent": "Editor", "ok": False, "error": str(e)})

    try:
        safe = agent_safety_pass(chapter_text=text, temperature=0.25)
        level = str(safe.get("level") or "ok").lower()
        san = str(safe.get("sanitized_text") or "").strip()
        if level == "block" and san:
            text = san
        log["steps"].append({"agent": "Safety", "ok": True, "level": level})
    except Exception as e:
        log["steps"].append({"agent": "Safety", "ok": False, "error": str(e)})

    if run_reader_test:
        try:
            reader = agent_reader_blind_test(chapter_text=text, temperature=0.5)
            log["reader_test"] = reader
            log["steps"].append({"agent": "ReaderTest", "ok": True})
        except Exception as e:
            log["steps"].append({"agent": "ReaderTest", "ok": False, "error": str(e)})

    return text.strip(), log


def orchestrator_bump_state(
    state: dict[str, Any],
    *,
    step: str,
    chapter: int,
    issues: Optional[list[str]] = None,
) -> dict[str, Any]:
    st = dict(state)
    st["step"] = step
    st["chapter"] = int(chapter)
    dv = int(st.get("draft_version") or 0) + 1
    st["draft_version"] = dv
    if issues is not None:
        st["open_issues"] = list(issues)
    return st
