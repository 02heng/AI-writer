from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

from .agents import (
    agent_apply_continuity_fixes,
    agent_character_polish,
    agent_continuity_check,
    agent_editor_pass,
    agent_prose_tighten,
    agent_reader_blind_test,
    agent_safety_pass,
    agent_writer_draft,
)

_READER_REVISION_ENV = "AIWRITER_READER_DRIVEN_REVISION"
_PROSE_WASH_ENV = "AIWRITER_PROSE_WASH"


def _env_prose_wash_enabled() -> bool:
    v = (os.environ.get(_PROSE_WASH_ENV) or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _env_reader_driven_revision_enabled() -> bool:
    v = (os.environ.get(_READER_REVISION_ENV) or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _plain_body_char_count(text: str) -> int:
    """正文字符数（不计空白），用于篇幅粗判。"""
    return len(re.sub(r"\s+", "", text or "", flags=re.MULTILINE))


def _should_run_reader_driven_revision(
    reader: dict[str, Any],
    body_chars: int,
    min_body_chars: int,
) -> bool:
    if not _env_reader_driven_revision_enabled():
        return False
    if min_body_chars > 0 and body_chars < int(min_body_chars * 0.72):
        return True
    if reader.get("must_rewrite") is True:
        return True
    issues = reader.get("name_consistency_issues")
    if isinstance(issues, list) and len(issues) > 0:
        return True
    spatial = reader.get("scene_spatial_issues")
    if isinstance(spatial, list) and len(spatial) > 0:
        return True
    reg = reader.get("register_social_issues")
    if isinstance(reg, list) and len(reg) > 0:
        return True
    ls = str(reader.get("length_status") or "").lower()
    if ls in ("short", "likely_short", "明显偏短"):
        return True
    return False


def _apply_prose_tighten(
    text: str,
    premise: str,
    log: dict[str, Any],
    *,
    run_prose_wash: bool,
) -> str:
    if not (run_prose_wash and _env_prose_wash_enabled()):
        return text
    try:
        washed = agent_prose_tighten(
            chapter_text=text,
            premise=premise,
            temperature=0.3,
        )
        if washed.strip() and len(washed) > 80:
            log["steps"].append({"agent": "ProseTighten", "ok": True})
            return washed.strip()
        log["steps"].append({"agent": "ProseTighten", "ok": True, "note": "unchanged_or_short"})
    except Exception as e:
        log["steps"].append({"agent": "ProseTighten", "ok": False, "error": str(e)})
    return text


def run_chapter_with_agents(
    *,
    system: str,
    user_payload: str,
    writing_temp: float,
    premise: str,
    kb_block: str,
    agent_profile: str = "fast",
    run_reader_test: bool = False,
    run_reader_driven_revision: bool = True,
    reader_prev_chapter_tail: str = "",
    reader_known_names_hint: str = "",
    reader_target_min_body_chars: int = 0,
    run_prose_wash: bool = True,
) -> tuple[str, dict[str, Any]]:
    """
    fast: 单次 Writer。
    full: Writer → Character 润色 → Continuity（有则修）→ Editor（可选替换）→ ProseTighten（文风险喻/文艺腔收束，可关）→
          Safety（block 则替换）→ 可选 Reader；读者驱动二稿后再次 ProseTighten，再经 Safety。
    可选：Reader 发现篇幅/人名等问题时追加一轮 Writer（再经 Safety），见 run_reader_driven_revision。
    环境变量 AIWRITER_PROSE_WASH=0 时跳过 ProseTighten；run_prose_wash=False 时亦跳过。
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

    text = _apply_prose_tighten(text, premise, log, run_prose_wash=run_prose_wash)

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
            reader = agent_reader_blind_test(
                chapter_text=text,
                temperature=0.5,
                prev_chapter_tail=reader_prev_chapter_tail,
                known_names_hint=reader_known_names_hint,
                target_min_body_chars=int(reader_target_min_body_chars or 0),
            )
            log["reader_test"] = reader
            log["steps"].append({"agent": "ReaderTest", "ok": True})
        except Exception as e:
            log["reader_test"] = {"_error": str(e)[:500]}
            log["steps"].append({"agent": "ReaderTest", "ok": False, "error": str(e)})

        if (
            (agent_profile or "fast").lower() == "full"
            and run_reader_driven_revision
            and isinstance(log.get("reader_test"), dict)
        ):
            body_n = _plain_body_char_count(text)
            if _should_run_reader_driven_revision(log["reader_test"], body_n, int(reader_target_min_body_chars or 0)):
                try:
                    rb = log["reader_test"]
                    brief = {
                        "reader_test": {
                            k: rb[k]
                            for k in (
                                "confusion_points",
                                "weak_motivation",
                                "lore_jarring",
                                "scene_spatial_issues",
                                "register_social_issues",
                                "name_consistency_issues",
                                "length_status",
                                "must_rewrite",
                                "one_paragraph_suggestion",
                                "revision_brief",
                            )
                            if k in rb
                        },
                        "deterministic_note": (
                            f"成稿约 {body_n} 字；若合同要求约 2000～4000 字而明显不足，须扩写到完整一章。"
                            if reader_target_min_body_chars and body_n < int(reader_target_min_body_chars * 0.85)
                            else ""
                        ),
                    }
                    fb = json.dumps(brief, ensure_ascii=False)[:8000]
                    rev_user = (
                        user_payload
                        + "\n\n【须根据盲测读者反馈修订】\n"
                        "以下为 JSON：请**输出修订后的完整本章正文**（仅小说），须消除人名/称谓矛盾、**修正不符合身份与礼俗的称呼**、**修正场景空间与声源/视线矛盾**、"
                        "补足篇幅与章末收束；勿输出本块或任何元说明。\n"
                        + fb
                    )
                    text = agent_writer_draft(
                        system=system,
                        user_payload=rev_user,
                        temperature=max(0.45, float(writing_temp) - 0.12),
                    )
                    log["reader_driven_revision"] = True
                    log["steps"].append({"agent": "WriterReaderRevision", "ok": True})
                    text = _apply_prose_tighten(
                        text, premise, log, run_prose_wash=run_prose_wash
                    )
                    try:
                        safe2 = agent_safety_pass(chapter_text=text, temperature=0.25)
                        lv2 = str(safe2.get("level") or "ok").lower()
                        san2 = str(safe2.get("sanitized_text") or "").strip()
                        if lv2 == "block" and san2:
                            text = san2
                        log["steps"].append({"agent": "Safety", "ok": True, "after": "reader_revision", "level": lv2})
                    except Exception as e:
                        log["steps"].append(
                            {"agent": "Safety", "ok": False, "after": "reader_revision", "error": str(e)}
                        )
                except Exception as e:
                    log["steps"].append({"agent": "WriterReaderRevision", "ok": False, "error": str(e)})

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
