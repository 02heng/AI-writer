from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import HTTPException

from .author_persona import build_voice_prompt_blocks, format_voice_from_book_meta, roll_virtual_author
from .book_storage import (
    append_agent_orchestration_log,
    book_dir,
    clean_stored_chapter_text,
    create_book,
    get_chapter_numbers,
    get_meta,
    get_plan,
    get_toc,
    read_orchestration_state,
    update_plan,
    write_chapter,
    write_orchestration_state,
)
from .core.logging import get_logger, LogContext
from .jsonutil import extract_json_object
from .text_sanitize import (
    strip_aiwriter_prose_noise,
    strip_common_prefix_with_previous_opening,
    strip_markdown_double_asterisk_bold,
)
from .llm import LLMTransportError, chat_completion
from .long_context_tail import append_chapter_tail_snippet, load_chapter_tail_for_prompt, maybe_compress_chapter_tail
from .memory_hooks import foreshadowing_open_hooks_block, sync_foreshadowing_after_chapter
from .memory_wiki import (
    long_novel_wiki_memory_instruction,
    maybe_append_changelog_after_supervisor,
    maybe_wiki_compile_episodic_batch,
    read_changelog_tail,
)
from .memory_store import (
    add_entry,
    build_memory_context,
    init_db,
    prune_episodic_extraction_entries,
    read_rollup,
    write_rollup,
)
from .orchestration.runner import orchestrator_bump_state, run_chapter_with_agents
from .character_auto_seed import auto_seed_characters_after_chapter
from .character_profiles import (
    CHARACTER_REGISTRY_INSTRUCTION,
    build_character_registry_block,
    bump_character_mentions_from_plain,
)
from .orchestration.supervisor import (
    agent_supervisor_live_chapter_review,
    agent_supervisor_meta_review,
    append_supervisor_final_to_orchestration_state,
    compact_agent_log,
    load_context_for_supervisor_review,
)
from .scene_writer import generate_chapter_with_scenes
from .layered_memory import build_context_for_chapter

logger = get_logger(__name__)

ProgressCb = Optional[Callable[[dict[str, Any]], None]]


def _live_supervisor_after_chapter(
    *,
    live_supervisor: bool,
    book_title: str,
    chapter_index: int,
    chapter_title: str,
    beat: str,
    premise: str,
    chapter_plain: str,
    alog: dict[str, Any],
    progress_cb: ProgressCb,
) -> dict[str, Any] | None:
    """逐章监督快审；返回可写入 live_supervisor 列表的一条记录。"""
    if not live_supervisor:
        return None
    try:
        live_rev = agent_supervisor_live_chapter_review(
            book_title=book_title,
            chapter_index=chapter_index,
            chapter_title=chapter_title,
            beat=(beat or "").strip(),
            premise=premise,
            chapter_plain=chapter_plain,
            agent_chain_compact=compact_agent_log(alog),
        )
        entry: dict[str, Any] = {"chapter": chapter_index, "review": live_rev}
        if progress_cb:
            progress_cb(
                {
                    "event": "supervisor_chapter",
                    "index": chapter_index,
                    "title": chapter_title,
                    "review": live_rev,
                }
            )
        return entry
    except Exception as e:
        logger.warning("live supervisor chapter %s failed: %s", chapter_index, e)
        err = str(e)[:500]
        entry = {"chapter": chapter_index, "error": err}
        if progress_cb:
            progress_cb(
                {
                    "event": "supervisor_chapter",
                    "index": chapter_index,
                    "title": chapter_title,
                    "error": err,
                }
            )
        return entry


def _final_supervisor_for_book(
    *,
    root: Path,
    book_id: str,
    progress_cb: ProgressCb = None,
    max_run_lines: int = 80,
) -> dict[str, Any]:
    """全书/续写批次结束后的总监督：落盘 state.json，返回 API 用 payload。"""
    try:
        integrity, recent = load_context_for_supervisor_review(
            root, book_id, max_run_lines=max_run_lines
        )
        meta_review = agent_supervisor_meta_review(integrity=integrity, recent_runs=recent)
        orch = read_orchestration_state(root, book_id)
        orch = append_supervisor_final_to_orchestration_state(
            orch, integrity=integrity, meta_review=meta_review
        )
        write_orchestration_state(root, book_id, orch)
        supervisor_final: dict[str, Any] = {
            "integrity": {
                "integrity_ok": integrity.get("integrity_ok"),
                "needs_attention": integrity.get("needs_attention"),
                "warnings": integrity.get("warnings"),
                "written_count": integrity.get("written_count"),
                "planned_count": integrity.get("planned_count"),
            },
            "meta_review": meta_review,
        }
        if progress_cb:
            progress_cb({"event": "supervisor_final", **supervisor_final})
        return supervisor_final
    except Exception as e:
        logger.warning("final supervisor failed: %s", e)
        err = str(e)[:800]
        if progress_cb:
            progress_cb({"event": "supervisor_final", "error": err})
        return {"error": err}


# 本轮一键生成上限；超过 PLAN_SINGLE_SHOT_MAX 章时用分批策划，避免单次 JSON 过大导致模型截断或语法错误。
MAX_PIPELINE_CHAPTERS = 1500
# 用户可声明的「全书预定总章数」上限（可大于本轮生成数，用于宏观阶段表）。
MAX_PLANNED_TOTAL_CHAPTERS = 5000
# 单次「续写」API 可连续生成的章数上限（逐章循环，与一键新书上限分开）。
MAX_CONTINUE_CHAPTERS = 500
PLAN_SINGLE_SHOT_MAX = 20
PLAN_BATCH_SIZE = 20

# 策划用：原创、反套作与剧情起伏（写入各 planner 的 system 提示，可被 main 大纲接口复用）
PLANNER_ORIGINALITY_CONTRACT = (
    "原创与节奏硬约束：全书与分章构思须独立原创，禁止对任何已有作品（出版读物、网文、影视等）进行情节复刻、名场面换皮、人设套壳或名句仿写；"
    "禁止依赖读者极易辨认的「经典桥段流水线」拼凑过关。叙事须有清晰起伏：阶段与章级均应有冲突、阻碍、信息差或意外转折，避免流水账与平铺直叙。"
    " 若题材说明或题目体现网文爽文、言情、甜宠、逆袭、打脸等通俗叙事倾向：分章 beat 须写清冲突推进与章末悬念或留白；"
    "言情向须在梗概与分章要点中体现感情线阶段（吸引、试探、阻碍、确认或拉扯反复），避免连续多章复用同一套打脸或误会桥段。"
)


def _format_macro_block(macro: dict[str, Any], *, chapters_this_run: int) -> str:
    """注入分章策划与正文：提醒总盘子与本批范围。"""
    total = int(macro.get("planned_total_chapters") or 0)
    lines = [
        f"【全书预定总尺度】全书约 {total} 章（用户设定）。本轮只生成第 1–{chapters_this_run} 章要点与正文，"
        "不得在本轮内写完全书终局；后段须留白。",
        "【阶段路线图】",
    ]
    for p in macro.get("phases") or []:
        if not isinstance(p, dict):
            continue
        name = str(p.get("phase_name") or "").strip() or "（未命名阶段）"
        try:
            a = int(p.get("chapter_from", 0))
            b = int(p.get("chapter_to", 0))
        except (TypeError, ValueError):
            continue
        summ = re.sub(r"\s+", " ", str(p.get("summary") or "").strip())[:220]
        lines.append(f"- 第{a}–{b}章「{name}」：{summ}")
    ed = re.sub(r"\s+", " ", str(macro.get("ending_direction") or "").strip())[:400]
    if ed:
        lines.append(f"【终点走向】{ed}")
    return "\n".join(lines)


def _macro_phase_note_for_chapter(idx: int, macro: dict[str, Any]) -> str:
    """写作时提示本章在宏观阶段中的位置。"""
    total = macro.get("planned_total_chapters", "?")
    phases = macro.get("phases") if isinstance(macro.get("phases"), list) else []
    for p in phases:
        if not isinstance(p, dict):
            continue
        try:
            a = int(p.get("chapter_from", 0))
            b = int(p.get("chapter_to", 0))
        except (TypeError, ValueError):
            continue
        if a <= idx <= b:
            name = str(p.get("phase_name") or "").strip() or "本阶段"
            summ = re.sub(r"\s+", " ", str(p.get("summary") or "").strip())[:280]
            return (
                f"【宏观位置】全书约 {total} 章；当前第 {idx} 章，处于阶段「{name}」（约第{a}–{b}章）。"
                f"阶段要点：{summ}"
            )
    return f"【宏观位置】全书约 {total} 章；当前第 {idx} 章。请勿提前收官，保留后续卷宗空间。"


def _plan_macro_scale(
    *,
    title: str,
    theme_hint: str,
    planned_total: int,
    chapters_this_run: int,
    length_scale: str,
    protagonist_gender: str,
    temperature: float,
    ideation_level: float = 0.5,
) -> tuple[str, str, dict[str, Any]]:
    """两阶策划·宏观：全书总尺度 + 阶段路线图（不生成逐章 beat）。"""
    sys_p = (
        "你是超长篇小说总策划。只输出一个 JSON 对象，禁止 Markdown 围栏。"
        '{"book_title":"string","premise":"string","phases":[{"phase_name":"string","chapter_from":1,"chapter_to":200,"summary":"string"},...],"ending_direction":"string"}'
        f" premise 为全书梗概，约 400-1100 字，用分号连接短句，字符串内禁止换行与未转义英文双引号。"
        f" phases 必须 6-16 项；chapter_from、chapter_to 为整数，阶段按章号递增，整体覆盖 1 至 {planned_total}（可略有重叠或衔接缝隙但不要大片遗漏）；"
        f"每段 summary 80-220 字，写阶段目标与关键转折，禁止尾逗号。"
        f" ending_direction 写终局气质与主线落点（勿剧透细节），80-200 字。"
        f" 用户本轮只写第 1–{chapters_this_run} 章，前面若干阶段须为后文留白，禁止在首阶段内完结全书。"
        f" {PLANNER_ORIGINALITY_CONTRACT}"
    )
    user_p = (
        f"题目：{title.strip()}\n"
        f"全书预定总章数：{planned_total}（必须按此尺度设计阶段跨度）。\n"
        f"本轮将实际生成正文：第 1–{chapters_this_run} 章。\n"
        f"{_scale_instruction(length_scale)}\n{_protagonist_instruction(protagonist_gender)}\n"
        f"{ideation_instruction(ideation_level)}\n"
    )
    if theme_hint:
        user_p += f"题材说明：{theme_hint}\n"
    data = _chat_plan_json(sys_p, user_p, temperature, attempts=3)
    book_title = str(data.get("book_title") or "").strip() or title.strip()
    premise = str(data.get("premise") or "").strip()
    if len(premise) < 100:
        raise ValueError("宏观策划梗概过短")
    phases_raw = data.get("phases")
    out_phases: list[dict[str, Any]] = []
    if isinstance(phases_raw, list):
        for p in phases_raw:
            if not isinstance(p, dict):
                continue
            try:
                a = int(p.get("chapter_from", 0))
                b = int(p.get("chapter_to", 0))
            except (TypeError, ValueError):
                continue
            if a < 1 or b < a:
                continue
            name = str(p.get("phase_name") or "").strip() or "阶段"
            summ = re.sub(r"\s+", " ", str(p.get("summary") or "").strip())
            if summ:
                out_phases.append(
                    {"phase_name": name, "chapter_from": a, "chapter_to": b, "summary": summ[:500]}
                )
    if len(out_phases) < 4:
        raise ValueError("宏观策划有效阶段不足")
    ending_direction = re.sub(r"\s+", " ", str(data.get("ending_direction") or "").strip())[:500]
    macro: dict[str, Any] = {
        "planned_total_chapters": planned_total,
        "phases": out_phases,
        "ending_direction": ending_direction,
    }
    return book_title, premise, macro


def _chat_plan_json(system: str, user: str, temperature: float, *, attempts: int = 3) -> dict[str, Any]:
    """调用模型并解析 JSON 对象；失败则降温重试，提示避免尾逗号与非法换行。"""
    last_err: Exception | None = None
    for i in range(max(1, attempts)):
        strict = ""
        if i > 0:
            strict = (
                " 上次输出无法解析：只输出一个 JSON 对象；键值对之间与数组元素末尾禁止多余逗号；"
                "字符串内双引号须转义为 \\\"；字符串内禁止真实换行（改用 \\\\n）。"
            )
        u = user if i == 0 else user + "\n【重试】仅输出合法 JSON，不要其它文字。"
        try:
            raw = chat_completion(
                system=system + strict,
                user=u,
                temperature=max(0.28, temperature - 0.08 * i),
            )
            data = extract_json_object(raw)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            last_err = e
            continue
    assert last_err is not None
    raise last_err


def _batched_chapter_plan_slices(
    *,
    title: str,
    book_title: str,
    premise: str,
    theme_hint: str,
    n: int,
    length_scale: str,
    protagonist_gender: str,
    temperature: float,
    macro_block: str = "",
    ideation_level: float = 0.5,
) -> list[dict[str, Any]]:
    """从第 1 章起分批生成共 n 章的分章要点。"""
    n = max(3, min(int(n), MAX_PIPELINE_CHAPTERS))
    all_ch: list[dict[str, Any]] = []
    start = 1
    while start <= n:
        end = min(start + PLAN_BATCH_SIZE - 1, n)
        prev_tail = ""
        if all_ch:
            tail = all_ch[-2:] if len(all_ch) >= 2 else all_ch[-1:]
            lines = [
                f"第 {c['idx']} 章「{str(c.get('title', ''))[:40]}」：{str(c.get('beat', ''))[:120]}"
                for c in tail
            ]
            prev_tail = "【上一批末章摘要（须衔接）】\n" + "\n".join(lines) + "\n"
        batch = _plan_chapters_slice(
            title=title,
            book_title=book_title,
            premise=premise,
            theme_hint=theme_hint,
            start_idx=start,
            end_idx=end,
            length_scale=length_scale,
            protagonist_gender=protagonist_gender,
            temperature=temperature,
            prev_tail_hint=prev_tail,
            macro_block=macro_block,
            ideation_level=ideation_level,
        )
        all_ch.extend(batch)
        start = end + 1

    if len(all_ch) != n:
        raise ValueError(f"分批策划章数不足：需要 {n}，得到 {len(all_ch)}")
    all_ch.sort(key=lambda x: int(x["idx"]))
    return all_ch


def _heading_titles_equal(line_title: str, want: str) -> bool:
    """Loose match for model ## line vs planned chapter title (handles spacing / 第 N 章)."""
    a = re.sub(r"\s+", "", (line_title or "").strip())
    b = re.sub(r"\s+", "", (want or "").strip())
    if not a or not b:
        return False
    if a == b:
        return True
    if a in b or b in a:
        return len(min(a, b, key=len)) >= 4
    return False


def strip_leading_duplicate_chapter_heading(body: str, ch_title: str) -> str:
    """Remove leading ##… lines that repeat the planned title (model often echoes contract)."""
    t = body.strip()
    want = (ch_title or "").strip()
    if not t or not want:
        return body
    atx = re.compile(r"^#{1,6}\s*([^\n]+?)\s*(?:\n+|$)")
    while True:
        m = atx.match(t)
        if not m:
            break
        line_title = m.group(1).strip()
        if _heading_titles_equal(line_title, want):
            t = t[m.end() :].lstrip()
            continue
        break
    return t


def sanitize_chapter_body(body: str) -> str:
    """Strip HTML comments, decorative lines, Markdown **bold**, line-leading > / list marks, and `,-` glitches."""
    t = body.strip()
    t = re.sub(r"<!--[\s\S]*?-->", "", t)
    lines_out: list[str] = []
    for line in t.split("\n"):
        stripped = line.strip()
        if not stripped:
            lines_out.append(line)
            continue
        if stripped in ("***", "* * *", "—", "——", "———", "===", "---"):
            continue
        if re.fullmatch(r"[\*\s─\-═]+", stripped):
            continue
        lines_out.append(line)
    out = "\n".join(lines_out).strip()
    out = strip_markdown_double_asterisk_bold(out)
    return strip_aiwriter_prose_noise(out)


def _chapter_body_plain_from_file(raw_md: str) -> str:
    """Chapter file text without HTML header, ## title line, for dedupe vs next chapter."""
    t = clean_stored_chapter_text(raw_md)
    t = re.sub(r"^#+\s*[^\n]+\n+", "", t.strip(), count=1)
    return t.strip()


def _chapter_heading(title: str, idx: int) -> str:
    tt = (title or "").strip() or f"第 {idx} 章"
    return f"## {tt}\n\n"


def _fallback_chapter_title(ch: dict[str, Any], idx: int) -> str:
    t = str(ch.get("title") or "").strip()
    if t:
        return t[:80]
    beat = str(ch.get("beat") or "").strip().replace("\n", " ")
    if len(beat) > 36:
        return beat[:36].rstrip("，。；;,. ") + "…"
    return beat or f"第 {idx} 章"


def _safe_filename_prefix(title: str) -> str:
    raw = title.strip() or "novel"
    cleaned = re.sub(r'[<>:"/\\|?*\n\r\t]+', "", raw)
    cleaned = cleaned.strip(". ")[:48] or "novel"
    return cleaned


def _short_story_reader_engagement_instruction() -> str:
    """短篇：爽点密度、前置与轮换（策划与正文合同共用）。"""
    return (
        "【短篇读者节奏】"
        "（1）开篇约三百至八百汉字内至少一次「情绪回报」：按题材择一呈现——身份反转、打脸、金手指露头、危机兑现、秘密揭露等；"
        "此区间内避免纯设定说明或氛围散文堆砌。"
        "（2）爽点类型须轮换：同一章勿用同一种「爽」反复灌水；采用「小爽→略压或顿挫→更大爽」的微型波浪，整体节奏比中长篇更紧。"
        "（3）信息前置：读者追读的悬念或利害关系须在标题意象或首段可被感知，勿把核心钩子推迟到大量铺陈之后。"
        "（4）首次强情绪或信息反馈尽量前移，勿让读者划行过久才得到 payoff；可先给阶段性满足，再以伏笔拉长后文期待，勿倒置。"
    )


def _scale_instruction(length_scale: str) -> str:
    m = {
        "short": "篇幅为短篇：结构紧凑，单线或极少支线，冲突推进快，适合约三万至八万汉字量级的叙事节奏，避免冗长支线。",
        "medium": "篇幅为中篇：可有适度支线与铺陈，节奏介于短篇与长篇之间，注意主线清晰。",
        "long": "篇幅为长篇：允许多线叙事、伏笔与人物弧光充分展开，章节间保持悬念与节奏起伏，避免水文。",
    }
    base = m.get(length_scale, m["medium"])
    if length_scale == "short":
        return base + "\n" + _short_story_reader_engagement_instruction()
    return base


def _protagonist_instruction(gender: str) -> str:
    m = {
        "male": "主角为男性；全文保持视角稳定（若第三人称则以该男性为主要视点人物），不得无交代切换主角。",
        "female": "主角为女性；全文保持视角稳定（若第三人称则以该女性为主要视点人物），不得无交代切换主角。",
        "any": "主角性别与视角由故事自然呈现，但须前后一致，不得中途无解释改变主角核心设定。",
    }
    return m.get(gender, m["any"])


def ideation_instruction(level: float) -> str:
    """脑洞程度：0≈极保守套路，0.5≈正常平衡，1≈高创意（仍须因果自洽）。用于策划与正文提示。"""
    w = max(0.0, min(1.0, float(level)))
    if w < 0.25:
        tone = (
            "叙事与设定取向：**极稳健**。优先经典结构与直白可验的因果，人物反应符合常见预期；"
            "避免猎奇设定与为反转而反转，重在把人情事理写扎实。"
        )
    elif w < 0.42:
        tone = (
            "叙事取向：**偏保守**。转折须有铺垫与依据，可有一点新意，不要为出奇而扭曲基本逻辑。"
        )
    elif w <= 0.58:
        tone = (
            "叙事取向：**常规平衡（约 0.5）**。在前后自洽前提下允许常见戏剧性与适度惊喜；"
            "既不完全套路化，也不宜为脑洞牺牲因果。"
        )
    elif w < 0.78:
        tone = (
            "叙事取向：**偏高创意**。鼓励非常规切入点、反套路冲突与令人耳目一新的推演；"
            "每条大胆设定须能在故事内说圆，避免随机堆砌。"
        )
    else:
        tone = (
            "叙事取向：**高脑洞（接近 1）**。鼓励非常规因果链、隐喻式转折与非常见结构；"
            "**仍须内部逻辑自洽**，怪要有怪的道理，禁止无因果的诡异碎片拼贴。"
        )
    return f"【脑洞程度】数值 {w:.2f}（范围 0～1；0.5 为正常水平）。{tone}"


def _normalize_chapter_entry(c: dict[str, Any], fallback_idx: int) -> Optional[dict[str, Any]]:
    try:
        idx = int(c.get("idx", fallback_idx))
    except (TypeError, ValueError):
        idx = fallback_idx
    beat = str(c.get("beat", "")).strip()
    beat = re.sub(r"\s+", " ", beat)
    if not beat:
        return None
    title_raw = str(c.get("title") or "").strip()
    out: dict[str, Any] = {"idx": idx, "beat": beat}
    if title_raw:
        out["title"] = title_raw[:120]
    pov = str(c.get("pov", "")).strip()
    if pov:
        out["pov"] = pov
    hook = str(c.get("hook_end", "")).strip()
    if hook:
        out["hook_end"] = hook
    sfl = str(c.get("space_for_later") or c.get("留白") or "").strip()
    if sfl:
        out["space_for_later"] = sfl[:500]
    conflict = str(c.get("conflict", "")).strip()
    if conflict:
        out["conflict"] = conflict
    scenes = c.get("scenes")
    if isinstance(scenes, list):
        clean = [str(x).strip() for x in scenes if str(x).strip()]
        if clean:
            out["scenes"] = clean[:5]
    elif isinstance(scenes, str) and scenes.strip():
        out["scenes"] = [scenes.strip()]
    tags = c.get("kb_tags")
    if isinstance(tags, list):
        tt = [str(x).strip() for x in tags if str(x).strip()]
        if tt:
            out["kb_tags"] = tt[:12]
    chars = c.get("characters_present")
    if isinstance(chars, list):
        cc = [str(x).strip() for x in chars if str(x).strip()]
        if cc:
            out["characters_present"] = cc[:16]
    return out


def _format_chapter_contract(
    idx: int,
    ch: dict[str, Any],
    *,
    continuation: bool = False,
    length_scale: Optional[str] = None,
) -> str:
    tail = (
        "（续写：须自然承接上一章语气和事实，勿重述已交代信息；若上章末为险情或未结动作，须先写清直接后果再转入新场景，禁止无过渡跳切。）"
        if continuation
        else ""
    )
    ch_title = _fallback_chapter_title(ch, idx)
    lines: list[str] = [f"【本章写作合同】第 {idx} 章{tail}"]
    lines.append(
        f"【本章标题】{ch_title}（标题由系统写入章节文件首行，**禁止**你在正文开头再输出 `##` 章节标题；请直接从叙事或对白起笔。）"
    )
    lines.append(f"【节拍/要点】\n{ch.get('beat', '')}")
    pov = str(ch.get("pov", "")).strip()
    if pov:
        lines.append(f"【叙事视角】{pov}")
    conflict = str(ch.get("conflict", "")).strip()
    if conflict:
        lines.append(f"【本章核心冲突】{conflict}")
    scenes = ch.get("scenes")
    if isinstance(scenes, list) and scenes:
        lines.append("【场景清单（须覆盖，顺序可调）】\n" + "\n".join(f"- {s}" for s in scenes))
    cp = ch.get("characters_present")
    if isinstance(cp, list) and cp:
        lines.append("【出场人物（勿随意改名或加未交代人设）】" + "、".join(cp))
    kt = ch.get("kb_tags")
    if isinstance(kt, list) and kt:
        lines.append("【须与知识库/记忆照应的关键词】" + "、".join(kt))
    hook = str(ch.get("hook_end", "")).strip()
    if hook:
        lines.append(f"【章末钩子】{hook}")
    sfl = str(ch.get("space_for_later") or ch.get("留白") or "").strip()
    if sfl:
        lines.append(f"【为后文留白 / 埋钩】\n{sfl}")
    ls = (length_scale or "").strip().lower()
    if ls == "short":
        lines.append(
            "【结构提示·短篇】除须满足上文【短篇读者节奏】外：开场即陷入可感冲突或悬念；"
            "中段维持微型波浪；结尾仍须情绪落点或悬念，避免「总之/后来」式收尾。"
        )
    else:
        lines.append(
            "【结构提示】开场尽快入戏；中段推进冲突或信息；结尾留情绪落点或悬念，避免「总之/后来」式收尾。"
        )
    lines.append(
        "【篇幅目标】本章完整正文约 **2000～4000 汉字**（含标点、对话与描写）。"
        "以叙事完整为先，可贴近区间上下沿；禁止清单体、作者按语、复述合同或重复铺垫注水凑字。"
    )
    return "\n".join(lines)


def _continuation_prev_chapter_bridge_instruction(last_chapter_index: int) -> str:
    """续写时注入：避免上章末险情/断钩与本章策划场景无过渡跳切。"""
    return (
        "【续写衔接·强制】\n"
        f"上文「上一章正文」为第 {last_chapter_index} 章。若该章以险情、对峙、负伤未稳、对话或动作未收束、生死未卜等收笔，"
        "本章开篇必须在**连续时间线**内先写清直接后果（脱险、救治、晕厥转醒、一方退走、对峙暂歇等均可），篇幅约占全章一成至三成为宜，视烈度自定。\n"
        "若本章【节拍/要点】要求的新场景、新时段与上章末镜不同，须有**可见的叙事过渡**（时间标注、空间转移的动机与过程），且不得与上章已发生事实矛盾；"
        "禁止开篇即另起炉灶、风和日丽，仿佛上章末段从未发生。\n"
    )


def plan_continuation_arc(
    *,
    root: Path,
    book_id: str,
    arc_length: int,
    temperature: float = 0.55,
    ideation_level: float = 0.5,
    user_book_note: str = "",
) -> dict[str, Any]:
    """
    批量续写前：结合梗概、总摘要、跨章提要、结构化开放伏笔与当前 plan，
    为接下来 arc_length 章生成分章要点（beat / 留白 / 章末钩），并写回 plan.json。
    """
    book_path = book_dir(root, book_id)
    nums = get_chapter_numbers(root, book_id)
    if not nums:
        raise HTTPException(404, "本书尚无章节，无法规划续写弧")
    last_n = max(nums)
    start_idx = last_n + 1
    arc_length = max(1, min(int(arc_length), MAX_CONTINUE_CHAPTERS))
    end_idx = start_idx + arc_length - 1

    plan_data = get_plan(root, book_id)
    premise = str(plan_data.get("premise") or "")
    book_title = str(plan_data.get("book_title") or plan_data.get("title") or book_id)
    meta_p = plan_data.get("meta")
    planned_total: Optional[int] = None
    if isinstance(meta_p, dict) and meta_p.get("planned_total_chapters") is not None:
        try:
            planned_total = int(meta_p["planned_total_chapters"])
        except (TypeError, ValueError):
            planned_total = None

    chs_in = plan_data.get("chapters")
    tail_lines: list[str] = []
    if isinstance(chs_in, list):
        sorted_ch = []
        for c in chs_in:
            if isinstance(c, dict):
                try:
                    ix = int(c.get("idx", 0))
                except (TypeError, ValueError):
                    continue
                sorted_ch.append((ix, c))
        sorted_ch.sort(key=lambda x: x[0])
        for ix, c in sorted_ch[-8:]:
            bt = re.sub(r"\s+", " ", str(c.get("beat", "")).strip())[:160]
            tl = str(c.get("title") or "")[:40]
            tail_lines.append(f"第{ix}章「{tl}」：{bt}")
    tail_block = "\n".join(tail_lines) if tail_lines else "（plan 中无分章要点）"

    rollup_ex = read_rollup(book_path).strip()[:4200]
    story_tail = load_chapter_tail_for_prompt(book_path, max_chars=5200) or ""
    hook_blk = foreshadowing_open_hooks_block(book_path, max_chars=1200)
    note_s = (user_book_note or "").strip()
    note_block = f"【用户全书说明】\n{note_s[:2000]}\n" if note_s else ""

    budget_hint = ""
    if planned_total is not None:
        budget_hint = f"全书预定总章数为 {planned_total}；当前已写到第 {last_n} 章，本次规划第 {start_idx}–{end_idx} 章，勿超出全书节奏合理性。"

    hook_section = ""
    if hook_blk.strip():
        hook_section = "【结构化开放伏笔】\n" + hook_blk + "\n\n"

    last_ch_tail_block = ""
    last_ch_path = book_path / "chapters" / f"{last_n:02d}.md"
    if last_ch_path.is_file():
        try:
            raw_last = last_ch_path.read_text(encoding="utf-8")
            plain_last = _chapter_body_plain_from_file(raw_last).strip()
            if plain_last:
                tail_plain = plain_last[-2400:] if len(plain_last) > 2400 else plain_last
                last_ch_tail_block = (
                    f"【已写正文·第 {last_n} 章尾部（第 {start_idx} 章 beat 须先承接再推进）】\n{tail_plain}\n\n"
                )
        except OSError:
            pass

    sys_p = (
        "你是长篇小说中观策划。请在既有故事框架下，为接下来连续若干章制定写作要点。\n"
        "## 必须遵守\n"
        "1. 每章一条 beat：场景、冲突推进、人物目标变化；与上一阶段 plan 与正文提要衔接。\n"
        f"1b. 本批第一章为 idx={start_idx}：若上文「已写正文尾部」以险情、对峙或未收束动作结尾，该章 beat 必须先用一两句写清直接后果，再转入新场景，禁止无过渡跳切。\n"
        "2. space_for_later：本批内为**更后章节**埋的悬念或留白（非本章内细节），可写「无」。\n"
        "3. hook_end：章末情绪或信息悬念，一句话。\n"
        "4. 章序 idx 必须从给定起始连续递增，禁止跳号或重复。\n"
        "## 输出（仅 JSON）\n"
        '{"arc_notes":"本批弧光与主线推进一两句",'
        '"chapters":['
        '{"idx":int,"title":"6~14字章名","beat":"180~280字要点","space_for_later":"string","hook_end":"string"}'
        "]}\n"
        "chapters 长度必须等于要求的章数。"
    )
    user_p = (
        f"书名：{book_title}\n{budget_hint}\n"
        f"{ideation_instruction(ideation_level)}\n"
        f"{note_block}"
        f"{last_ch_tail_block}"
        f"【全书梗概】\n{premise[:3200]}\n\n"
        f"【plan 近期分章摘要】\n{tail_block}\n\n"
        f"【记忆宫殿总摘要（摘录）】\n{rollup_ex}\n\n"
        f"【跨章剧情提要】\n{story_tail[:4500]}\n\n"
        f"{hook_section}"
        f"请规划第 {start_idx} 章到第 {end_idx} 章，共 {arc_length} 章；输出 chapters 数组。"
    )
    raw = chat_completion(system=sys_p, user=user_p, temperature=temperature)
    try:
        data = extract_json_object(raw)
    except (ValueError, json.JSONDecodeError, TypeError) as e:
        raise HTTPException(502, f"续写弧规划 JSON 解析失败：{e}") from e

    raw_chapters = data.get("chapters")
    if not isinstance(raw_chapters, list) or len(raw_chapters) != arc_length:
        raise HTTPException(
            502,
            f"续写弧规划章数不符：需要 {arc_length}，得到 {len(raw_chapters) if isinstance(raw_chapters, list) else 0}",
        )

    merged_chapters: list[dict[str, Any]] = []
    if isinstance(chs_in, list):
        for c in chs_in:
            if isinstance(c, dict):
                merged_chapters.append(dict(c))
    else:
        merged_chapters = []

    def _idx_of(m: list[dict[str, Any]], idx: int) -> int:
        for i, x in enumerate(m):
            try:
                if int(x.get("idx", 0)) == idx:
                    return i
            except (TypeError, ValueError):
                continue
        return -1

    updated_indices: list[int] = []
    for item in raw_chapters:
        if not isinstance(item, dict):
            continue
        try:
            ix = int(item.get("idx", 0))
        except (TypeError, ValueError):
            continue
        if ix < start_idx or ix > end_idx:
            continue
        norm = _normalize_chapter_entry(item, ix)
        if not norm:
            continue
        if "title" not in norm and item.get("title"):
            norm["title"] = str(item.get("title")).strip()[:120]
        sfl = str(item.get("space_for_later") or item.get("留白") or "").strip()
        if sfl:
            norm["space_for_later"] = sfl[:500]
        he = str(item.get("hook_end") or "").strip()
        if he:
            norm["hook_end"] = he[:300]
        pos = _idx_of(merged_chapters, ix)
        if pos >= 0:
            merged_chapters[pos] = norm
        else:
            merged_chapters.append(norm)
        updated_indices.append(ix)

    merged_chapters.sort(key=lambda x: int(x.get("idx", 0)))
    plan_data["chapters"] = merged_chapters
    update_plan(root, book_id, plan_data)

    return {
        "arc_notes": str(data.get("arc_notes") or "").strip()[:1200],
        "start_chapter": start_idx,
        "end_chapter": end_idx,
        "updated_indices": sorted(set(updated_indices)),
    }


def _plan_from_title_single(
    *,
    title: str,
    theme_hint: str,
    chapter_count: int,
    length_scale: str,
    protagonist_gender: str,
    temperature: float,
    ideation_level: float = 0.5,
) -> dict[str, Any]:
    n = max(3, min(int(chapter_count), PLAN_SINGLE_SHOT_MAX))
    compact = n > 10
    if compact:
        sys_p = (
            "你是中文小说总策划。只输出一个 JSON 对象，禁止 Markdown 代码围栏与任何解释。"
            '{"book_title":"string","premise":"string 全书梗概 200-380 字",'
            '"chapters":[{"idx":1,"title":"4-12字","beat":"80-150字"},...]}'
            f" chapters 必须恰好 {n} 条，idx 从 1 到 {n} 连续无跳号。"
            "每章仅有 idx、title、beat 三个键；beat 为一段连续文字，场景+冲突+悬念，"
            "禁止在字符串值内换行；禁止英文双引号出现在字符串值中（用单引号或书名号代替）；禁止数组/对象尾逗号。"
            f" {PLANNER_ORIGINALITY_CONTRACT}"
        )
    else:
        sys_p = (
            "你是中文小说总策划。用户会给出题目、篇幅、主角性别与固定章数。"
            "你要完整构思：书名定稿、全书梗概、分章结构（每章含可执行写作合同字段）。"
            "只输出一个 JSON 对象，禁止 Markdown 代码围栏，禁止解释。"
            "格式严格为："
            '{"book_title":"string","premise":"string 200-400字梗概",'
            '"chapters":['
            '{"idx":1,'
            '"title":"string 必填，本章标题4-14字，用于目录与记忆检索，勿用标点堆砌",'
            '"beat":"string 必填，每章120-200字内要点：场景目标+冲突推进+章末悬念",'
            '"pov":"string 可选，叙事视角如 第三人称限定主角/全知 等",'
            '"scenes":["string 可选1-3条，具体场景或节拍"],'
            '"conflict":"string 可选，本章核心冲突一句",'
            '"hook_end":"string 可选，章末钩子一句",'
            '"kb_tags":["string 可选，需与设定照应的关键词"],'
            '"characters_present":["string 可选，本章出场人物简称"]'
            "},...]}"
            f"严格要求：chapters 数组长度必须恰好等于 {n}，idx 从 1 到 {n} 连续无跳号；"
            "每章 beat 必填，其余可选字段能填尽量填以提升后文一致性；不要使用尾逗号。"
            f" {PLANNER_ORIGINALITY_CONTRACT}"
        )
    user_p = f"题目：{title.strip()}\n"
    user_p += f"总章数（必须严格遵守）：恰好 {n} 章。\n"
    user_p += _scale_instruction(length_scale) + "\n"
    user_p += _protagonist_instruction(protagonist_gender) + "\n"
    user_p += ideation_instruction(ideation_level) + "\n"
    if theme_hint:
        user_p += f"题材说明：{theme_hint}\n"

    last_err: Exception | None = None
    for attempt in range(3):
        try:
            if attempt == 0:
                raw = chat_completion(system=sys_p, user=user_p, temperature=temperature)
            elif attempt == 1:
                raw = chat_completion(
                    system=sys_p + " 输出必须是严格合法 JSON（RFC 8259），勿尾逗号，字符串内勿未转义换行。",
                    user=user_p + "\n上次输出无法解析。请仅重发完整 JSON 对象。",
                    temperature=max(0.32, temperature - 0.22),
                )
            else:
                raw = chat_completion(
                    system=sys_p + " 仅输出 JSON；若须缩短请优先缩短 beat，仍保持合法 JSON。",
                    user=user_p + "\n已连续解析失败。请极度精简每章 beat，确保整段可被 json.loads 解析。",
                    temperature=0.28,
                )
            data = extract_json_object(raw)
            if not isinstance(data, dict):
                raise ValueError("JSON 根须为对象")
            chapters = data.get("chapters")
            if not isinstance(chapters, list) or len(chapters) < 1:
                raise ValueError("chapters 无效")
            return data
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            last_err = e
            continue
    raise last_err if last_err else ValueError("策划 JSON 解析失败")


def _plan_book_meta(
    *,
    title: str,
    theme_hint: str,
    total_chapters: int,
    length_scale: str,
    protagonist_gender: str,
    temperature: float,
    ideation_level: float = 0.5,
) -> tuple[str, str]:
    sys_p = (
        "你是中文小说总策划。只输出一个 JSON 对象，禁止 Markdown。"
        '{"book_title":"string","premise":"string 全书梗概 350-700 字"}'
        " 须合法 JSON：无尾逗号，字符串内勿换行。"
        f" {PLANNER_ORIGINALITY_CONTRACT}"
    )
    user_p = (
        f"题目：{title.strip()}\n"
        f"全书共 {total_chapters} 章（分章要点将分批生成，此处只输出书名定稿与全书梗概）。\n"
        f"{_scale_instruction(length_scale)}\n"
        f"{_protagonist_instruction(protagonist_gender)}\n"
        f"{ideation_instruction(ideation_level)}\n"
    )
    if theme_hint:
        user_p += f"题材说明：{theme_hint}\n"

    last_err: Exception | None = None
    for attempt in range(2):
        try:
            raw = chat_completion(
                system=sys_p,
                user=user_p if attempt == 0 else user_p + "\n上次 JSON 无效，请仅输出合法 JSON 对象。",
                temperature=max(0.35, temperature - 0.12 * attempt),
            )
            data = extract_json_object(raw)
            book_title = str(data.get("book_title") or "").strip() or title.strip()
            premise = str(data.get("premise") or "").strip()
            if len(premise) < 80:
                raise ValueError("梗概过短")
            return book_title, premise
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            last_err = e
    raise last_err if last_err else ValueError("书名/梗概策划失败")


def _plan_chapters_slice(
    *,
    title: str,
    book_title: str,
    premise: str,
    theme_hint: str,
    start_idx: int,
    end_idx: int,
    length_scale: str,
    protagonist_gender: str,
    temperature: float,
    prev_tail_hint: str,
    macro_block: str = "",
    ideation_level: float = 0.5,
) -> list[dict[str, Any]]:
    k = end_idx - start_idx + 1
    sys_p = (
        "你是中文小说分章策划。只输出一个 JSON 对象："
        '{"chapters":[{"idx":int,"title":"4-12字","beat":"70-130字"},...]}'
        f" chapters 必须恰好 {k} 条，idx 从 {start_idx} 到 {end_idx} 每条唯一且连续。"
        "仅允许 idx、title、beat 三键；beat 为一段无换行文字；禁止尾逗号与 Markdown。"
        f" {PLANNER_ORIGINALITY_CONTRACT}"
    )
    user_p = (
        f"原始题目：{title.strip()}\n书名：{book_title}\n【全书梗概】\n{premise}\n"
        f"{_scale_instruction(length_scale)}\n{_protagonist_instruction(protagonist_gender)}\n"
        f"{ideation_instruction(ideation_level)}\n"
    )
    if theme_hint:
        user_p += f"题材说明：{theme_hint}\n"
    if macro_block.strip():
        user_p += macro_block.strip() + "\n"
    user_p += f"请给出第 {start_idx} 章至第 {end_idx} 章的写作要点（须与全书梗概及叙事节奏一致）。\n"
    if prev_tail_hint.strip():
        user_p += prev_tail_hint

    need = set(range(start_idx, end_idx + 1))
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            raw = chat_completion(
                system=sys_p,
                user=user_p
                if attempt == 0
                else user_p + "\n上次输出无法解析为 JSON。请重发，beat 内勿换行、勿尾逗号。",
                temperature=max(0.28, temperature - 0.1 * attempt),
            )
            data = extract_json_object(raw)
            chs = data.get("chapters")
            if not isinstance(chs, list):
                raise ValueError("chapters 无效")
            out: list[dict[str, Any]] = []
            seen: set[int] = set()
            for c in chs:
                if not isinstance(c, dict):
                    continue
                norm = _normalize_chapter_entry(c, 0)
                if not norm:
                    continue
                ix = int(norm["idx"])
                if ix in seen or ix not in need:
                    continue
                seen.add(ix)
                out.append(norm)
            if seen != need:
                raise ValueError(f"本批需 idx {sorted(need)}，实际 {sorted(seen)}")
            out.sort(key=lambda x: int(x["idx"]))
            return out
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            last_err = e
    raise last_err if last_err else ValueError("分批策划 JSON 失败")


def _plan_from_title_batched(
    *,
    title: str,
    theme_hint: str,
    chapter_count: int,
    length_scale: str,
    protagonist_gender: str,
    temperature: float,
    ideation_level: float = 0.5,
) -> dict[str, Any]:
    n = max(3, min(int(chapter_count), MAX_PIPELINE_CHAPTERS))
    book_title, premise = _plan_book_meta(
        title=title,
        theme_hint=theme_hint,
        total_chapters=n,
        length_scale=length_scale,
        protagonist_gender=protagonist_gender,
        temperature=temperature,
        ideation_level=ideation_level,
    )
    all_ch = _batched_chapter_plan_slices(
        title=title,
        book_title=book_title,
        premise=premise,
        theme_hint=theme_hint,
        n=n,
        length_scale=length_scale,
        protagonist_gender=protagonist_gender,
        temperature=temperature,
        macro_block="",
        ideation_level=ideation_level,
    )
    return {"book_title": book_title, "premise": premise, "chapters": all_ch}


def _plan_from_title(
    *,
    title: str,
    theme_hint: str,
    chapter_count: int,
    length_scale: str,
    protagonist_gender: str,
    temperature: float,
    planned_total_chapters: Optional[int] = None,
    progress_cb: ProgressCb = None,
    ideation_level: float = 0.5,
) -> dict[str, Any]:
    n_run = max(3, min(int(chapter_count), MAX_PIPELINE_CHAPTERS))
    if planned_total_chapters is not None:
        n_total = max(3, min(int(planned_total_chapters), MAX_PLANNED_TOTAL_CHAPTERS))
        if n_total < n_run:
            n_total = n_run
    else:
        n_total = n_run

    if n_total > n_run:
        if progress_cb:
            progress_cb(
                {
                    "event": "phase",
                    "phase": "macro_planning",
                    "message": f"正在策划全书宏观结构（预定约 {n_total} 章，本轮生成 {n_run} 章）…",
                }
            )
        book_title, premise, macro = _plan_macro_scale(
            title=title,
            theme_hint=theme_hint,
            planned_total=n_total,
            chapters_this_run=n_run,
            length_scale=length_scale,
            protagonist_gender=protagonist_gender,
            temperature=temperature,
            ideation_level=ideation_level,
        )
        mb = _format_macro_block(macro, chapters_this_run=n_run)
        if progress_cb:
            progress_cb(
                {
                    "event": "phase",
                    "phase": "micro_planning",
                    "message": f"正在分批生成分章要点（第 1–{n_run} 章）…",
                }
            )
        chapters = _batched_chapter_plan_slices(
            title=title,
            book_title=book_title,
            premise=premise,
            theme_hint=theme_hint,
            n=n_run,
            length_scale=length_scale,
            protagonist_gender=protagonist_gender,
            temperature=temperature,
            macro_block=mb,
            ideation_level=ideation_level,
        )
        return {
            "book_title": book_title,
            "premise": premise,
            "chapters": chapters,
            "macro_outline": macro,
        }

    if n_run <= PLAN_SINGLE_SHOT_MAX:
        return _plan_from_title_single(
            title=title,
            theme_hint=theme_hint,
            chapter_count=n_run,
            length_scale=length_scale,
            protagonist_gender=protagonist_gender,
            temperature=temperature,
            ideation_level=ideation_level,
        )
    return _plan_from_title_batched(
        title=title,
        theme_hint=theme_hint,
        chapter_count=n_run,
        length_scale=length_scale,
        protagonist_gender=protagonist_gender,
        temperature=temperature,
        ideation_level=ideation_level,
    )


def _append_rollup_chapter_snippet(
    book_root: Path,
    book_title: str,
    idx: int,
    snippet: str,
    *,
    chapter_title: str = "",
) -> None:
    cur = read_rollup(book_root).strip()
    head = f"第 {idx} 章「{chapter_title}」" if chapter_title else f"第 {idx} 章"
    line = f"\n\n## {head}摘要（自动生成）\n{snippet.strip()[:900]}"
    if "## 第" in cur and f"第 {idx} 章摘要" in cur:
        write_rollup(book_root, cur)
        return
    write_rollup(book_root, (cur + line).strip() + "\n")


def _sync_book_memory_entries(
    book_root: Path,
    chapter_label: str,
    chapter_text: str,
    temperature: float,
    *,
    chapter_title: str = "",
) -> None:
    sys_p = (
        "你是小说编辑。请阅读章节正文，提取可供后续章节参考的长期记忆要点。"
        "输出 5～8 条短句，每条一行，以「- 」开头；只写剧情/人物状态/伏笔/时间线，不要评论文笔。"
        "不要重复原文句子。"
    )
    try:
        bullets = chat_completion(
            system=sys_p,
            user=chapter_text[:12000],
            temperature=temperature,
        )
    except Exception:
        return
    mem_title = (
        f"「{chapter_title}」· 第 {chapter_label} 章 · 萃取"
        if chapter_title
        else f"第 {chapter_label} 章 · 生成同步萃取"
    )
    add_entry(
        book_root,
        room="情节",
        title=mem_title,
        body=bullets.strip(),
        chapter_label=chapter_label,
    )


def _compact_outline_for_canon(chapters: list[dict[str, Any]], n: int) -> str:
    """缩略分章信息供开笔前约束备案使用，避免数百章要点撑爆上下文。"""

    def line(c: dict[str, Any]) -> str:
        i = int(c["idx"])
        t = str(c.get("title", ""))[:36]
        b = re.sub(r"\s+", " ", str(c.get("beat", "")).strip())[:120]
        return f"{i}.「{t}」{b}"

    if n <= 60:
        return "\n".join(line(c) for c in chapters)

    head_n = min(40, max(20, n // 8))
    tail_n = min(40, max(20, n // 8))
    head = chapters[:head_n]
    tail = chapters[-tail_n:]
    middle = chapters[head_n : n - tail_n]
    step = max(len(middle) // 20, 1)
    samples = [middle[i] for i in range(0, len(middle), step)][:20]
    parts: list[str] = ["【开篇】" + "\n".join(line(c) for c in head)]
    if samples:
        parts.append("【中段抽样】" + "\n".join(line(c) for c in samples))
    parts.append("【后段】" + "\n".join(line(c) for c in tail))
    return "\n\n".join(parts)


def _seed_author_project_memory_entries(
    book_path: Path,
    *,
    user_book_note: str,
    author_card: str,
) -> None:
    """将用户全书说明与虚拟作者写入本书记忆宫殿（长期记忆条目）。"""
    init_db(book_path)
    if user_book_note.strip():
        add_entry(
            book_path,
            room="情节",
            title="用户全书项目说明",
            body=user_book_note.strip()[:12000],
            chapter_label="策划",
        )
    if author_card.strip():
        add_entry(
            book_path,
            room="人物",
            title="本书虚拟作者 · 叙事滤光人格",
            body=author_card.strip()[:12000],
            chapter_label="策划",
        )


def _seed_series_canon_memory(
    *,
    book_path: Path,
    book_title: str,
    premise: str,
    theme_hint: str,
    length_scale: str,
    protagonist_gender: str,
    chapters: list[dict[str, Any]],
    n_target: int,
    temperature: float,
    macro_outline: Optional[dict[str, Any]] = None,
    ideation_level: float = 0.5,
    extra_voice_context: str = "",
) -> None:
    """开笔前：世界观/人物/伏笔/时间线写入本书记忆宫殿与条目（与长期记忆约定一致）。"""
    compact = _compact_outline_for_canon(chapters, n_target)
    sys_p = (
        "你是长篇小说设定总监。根据书名、梗概与分章缩略，整理四类「长程写作约束」，"
        "后续数百章须遵守以防吃书。只输出一个 JSON 对象，禁止 Markdown 围栏。"
        '{"world_rules":"","character_anchors":"","open_loops":"","timeline_irreversible":""}'
        "四键均为字符串，用中文短句，句间用中文分号；不要剧情散文复述。"
        "world_rules：世界观硬规则、力量体系、社会结构、科技/魔法边界、绝不能自相矛盾的设定。"
        "character_anchors：姓名拼写、年龄辈分关系、核心动机、口癖或说话习惯、标志性外貌或道具，防OOC。"
        "open_loops：尚未收回的伏笔与承诺，谁承诺了什么、双关、物件未解释等，附状态如未解释或预计第N章收回。"
        "timeline_irreversible：生死、叛变、大战结果、关键日期地点等不可逆事实，年表式短句。"
        "信息不足可写「待正文补充」；字符串内禁止未转义英文双引号；禁止尾逗号。"
    )
    plan_note = f"计划总章数：{n_target}"
    if isinstance(macro_outline, dict) and macro_outline.get("planned_total_chapters") is not None:
        try:
            pt = int(macro_outline["planned_total_chapters"])
            plan_note = f"全书预定总章数：{pt}；本轮分章要点覆盖第 1–{n_target} 章"
        except (TypeError, ValueError):
            pass
    user_p = (
        f"书名：{book_title}\n{plan_note}\n【全书梗概】\n{premise}\n"
        f"{_scale_instruction(length_scale)}\n{_protagonist_instruction(protagonist_gender)}\n"
        f"{ideation_instruction(ideation_level)}\n"
    )
    if theme_hint:
        user_p += f"题材说明：{theme_hint}\n"
    if isinstance(macro_outline, dict) and macro_outline:
        user_p += _format_macro_block(macro_outline, chapters_this_run=n_target) + "\n"
    if (extra_voice_context or "").strip():
        user_p += (extra_voice_context or "").strip() + "\n"
    user_p += f"【分章缩略】\n{compact[:16000]}\n"

    data: dict[str, Any] | None = None
    last_err: Exception | None = None
    for att in range(2):
        try:
            raw = chat_completion(
                system=sys_p,
                user=user_p if att == 0 else user_p + "\n上次输出无法解析为 JSON，请仅输出合法 JSON。",
                temperature=max(0.32, temperature - 0.18 * att),
            )
            data = extract_json_object(raw)
            break
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            last_err = e
    if not isinstance(data, dict):
        logger.warning("series canon memory skipped: %s", last_err)
        return

    wr = str(data.get("world_rules", "")).strip() or "（待正文补充）"
    ca = str(data.get("character_anchors", "")).strip() or "（待正文补充）"
    ol = str(data.get("open_loops", "")).strip() or "（待正文补充）"
    ti = str(data.get("timeline_irreversible", "")).strip() or "（待正文补充）"

    try:
        init_db(book_path)
        existing = read_rollup(book_path).strip()
        stamp = time.strftime("%Y-%m-%d %H:%M")
        block = (
            f"# 全书约束备案（开笔前自动生成 · {stamp}）\n\n"
            f"## 世界观硬规则\n{wr}\n\n"
            f"## 人物恒定锚点\n{ca}\n\n"
            f"## 开放伏笔与承诺\n{ol}\n\n"
            f"## 不可逆事实与时间线\n{ti}\n"
        )
        if existing:
            write_rollup(book_path, block + "\n\n---\n\n" + existing)
        else:
            write_rollup(book_path, block)

        cap = 12000
        add_entry(
            book_path,
            room="世界观",
            title="开笔备案 · 硬规则与边界",
            body=wr[:cap],
            chapter_label="策划",
        )
        add_entry(
            book_path,
            room="人物",
            title="开笔备案 · 人物锚点",
            body=ca[:cap],
            chapter_label="策划",
        )
        add_entry(
            book_path,
            room="伏笔",
            title="开笔备案 · 开放伏笔与承诺",
            body=ol[:cap],
            chapter_label="策划",
        )
        add_entry(
            book_path,
            room="情节",
            title="开笔备案 · 不可逆事实与时间线",
            body=ti[:cap],
            chapter_label="策划",
        )
    except OSError as e:
        logger.warning("series canon memory write failed: %s", e)


def run_pipeline_from_title(
    *,
    root: Path,
    title: str,
    theme_addon: str,
    writer_system: str,
    max_chapters: int,
    length_scale: str,
    protagonist_gender: str,
    use_long_memory: bool,
    memory_context_global: str,
    kb_block: str,
    planning_temp: float,
    writing_temp: float,
    agent_profile: str = "fast",
    sync_book_memory: bool = True,
    run_reader_test: bool = False,
    use_scene_generation: bool = False,
    progress_cb: ProgressCb = None,
    planned_total_chapters: Optional[int] = None,
    ideation_level: float = 0.5,
    user_book_note: Optional[str] = None,
    live_supervisor: bool = False,
    final_supervisor: bool = False,
    memory_episodic_keep_last: Optional[int] = None,
    foreshadowing_sync_after_chapter: bool = False,
) -> dict[str, Any]:
    """策划 → 逐章写作 → 写入 books/{book_id}/。
    
    Args:
        use_scene_generation: If True, use scene-level generation for better
            long-text quality. Each chapter is split into scenes before writing.
    """
    theme_hint = (theme_addon or "").strip()
    note_s = (user_book_note or "").strip()
    theme_for_plan = theme_hint
    if note_s:
        block = f"【用户全书项目说明（策划须尊重、可内化）】\n{note_s[:4000]}"
        theme_for_plan = f"{theme_hint}\n\n{block}" if theme_hint else block
    length_scale = length_scale if length_scale in ("short", "medium", "long") else "medium"
    protagonist_gender = protagonist_gender if protagonist_gender in ("male", "female", "any") else "any"
    ideation_w = max(0.0, min(1.0, float(ideation_level)))
    t0 = time.perf_counter()
    if progress_cb:
        progress_cb({"event": "phase", "phase": "planning", "message": "正在策划全书结构…"})

    try:
        n_ch = max(3, min(int(max_chapters), MAX_PIPELINE_CHAPTERS))
        planned_opt: Optional[int] = None
        if planned_total_chapters is not None:
            planned_opt = max(3, min(int(planned_total_chapters), MAX_PLANNED_TOTAL_CHAPTERS))
            if planned_opt < n_ch:
                planned_opt = n_ch
        plan_raw = _plan_from_title(
            title=title,
            theme_hint=theme_for_plan,
            chapter_count=n_ch,
            length_scale=length_scale,
            protagonist_gender=protagonist_gender,
            temperature=planning_temp,
            planned_total_chapters=planned_opt,
            progress_cb=progress_cb,
            ideation_level=ideation_w,
        )
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        raise HTTPException(status_code=502, detail=f"策划阶段失败（JSON）：{e}") from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"策划阶段失败：{e}") from e

    book_title = str(plan_raw.get("book_title") or title).strip() or title
    premise = str(plan_raw.get("premise") or "").strip()
    macro_for_writing = plan_raw.get("macro_outline")
    if not isinstance(macro_for_writing, dict):
        macro_for_writing = None
    chapters_raw = plan_raw.get("chapters")
    if not isinstance(chapters_raw, list):
        raise HTTPException(502, "策划结果 chapters 格式错误")
    chapters: list[dict[str, Any]] = []
    for c in chapters_raw:
        if not isinstance(c, dict):
            continue
        norm = _normalize_chapter_entry(c, len(chapters) + 1)
        if norm:
            if "title" not in norm:
                norm["title"] = _fallback_chapter_title(norm, norm["idx"])
            chapters.append(norm)
    chapters.sort(key=lambda x: x["idx"])
    if len(chapters) < 1:
        raise HTTPException(502, "未能得到有效分章列表")
    n_target = max(3, min(int(max_chapters), MAX_PIPELINE_CHAPTERS))
    seen_idx: set[int] = set()
    deduped: list[dict[str, Any]] = []
    for c in chapters:
        i = int(c["idx"])
        if i < 1 or i > n_target or i in seen_idx:
            continue
        seen_idx.add(i)
        deduped.append(c)
    deduped.sort(key=lambda x: x["idx"])
    chapters = deduped[:n_target]
    if len(chapters) < n_target:
        raise HTTPException(
            502,
            f"策划仅返回 {len(chapters)} 章有效要点，少于要求的 {n_target} 章，请重试或略减章数。",
        )

    planned_stored = planned_opt if planned_opt is not None else n_target
    author_roll = roll_virtual_author()
    author_meta: dict[str, Any] = {
        "gender": author_roll["gender"],
        "pronoun": author_roll["pronoun"],
        "age": author_roll["age"],
        "city": author_roll["city"],
        "profession": author_roll["profession"],
        "card": author_roll["card"],
    }
    canon_voice = ""
    if note_s:
        canon_voice += f"【用户全书项目说明】\n{note_s[:2000]}\n\n"
    canon_voice += (
        f"【本书虚拟作者（人物欲望与叙事重心须与此人格滤光一致）】\n{str(author_roll.get('card') or '')[:2800]}\n"
    )

    meta_plan: dict[str, Any] = {
        "length_scale": length_scale,
        "protagonist_gender": protagonist_gender,
        "chapter_count": n_target,
        "chapters_this_run": n_target,
        "planned_total_chapters": planned_stored,
        "ideation_level": ideation_w,
        "virtual_author": author_meta,
    }
    if note_s:
        meta_plan["user_book_note"] = note_s
    if macro_for_writing:
        meta_plan["macro_outline"] = macro_for_writing
    plan_payload = {
        "book_title": book_title,
        "premise": premise,
        "meta": meta_plan,
        "chapters": chapters,
    }

    meta_extra: dict[str, Any] = {
        "source_title": title,
        "agent_profile": agent_profile,
        "virtual_author": author_meta,
    }
    if note_s:
        meta_extra["user_book_note"] = note_s

    created = create_book(
        root,
        title=book_title,
        premise=premise,
        plan=plan_payload,
        meta_extra=meta_extra,
    )
    book_id = str(created["book_id"])
    book_path = book_dir(root, book_id)

    if sync_book_memory:
        if progress_cb:
            progress_cb(
                {
                    "event": "phase",
                    "phase": "canon_memory",
                    "message": "正在生成全书约束并写入记忆宫殿（开笔备案）…",
                }
            )
        _seed_series_canon_memory(
            book_path=book_path,
            book_title=book_title,
            premise=premise,
            theme_hint=theme_hint,
            length_scale=length_scale,
            protagonist_gender=protagonist_gender,
            chapters=chapters,
            n_target=n_target,
            temperature=planning_temp,
            macro_outline=macro_for_writing,
            ideation_level=ideation_w,
            extra_voice_context=canon_voice,
        )
        _seed_author_project_memory_entries(
            book_path,
            user_book_note=note_s,
            author_card=str(author_roll.get("card") or ""),
        )

    plan_ms = int((time.perf_counter() - t0) * 1000)
    if progress_cb:
        progress_cb(
            {
                "event": "planned",
                "book_id": book_id,
                "book_title": book_title,
                "total_chapters": len(chapters),
                "plan_ms": plan_ms,
            }
        )

    system = writer_system.strip()
    if theme_addon.strip():
        system = f"{system}\n\n【题材约束】\n{theme_addon.strip()}"

    voice_block = build_voice_prompt_blocks(
        user_book_note=note_s if note_s else None,
        author=author_meta,
    )

    saved: list[str] = []
    orch = read_orchestration_state(root, book_id)
    agent_logs: list[dict[str, Any]] = []
    live_supervisor_logs: list[dict[str, Any]] = []

    chapter_times: list[float] = []
    for ch in chapters:
        idx = ch["idx"]
        ch_title = _fallback_chapter_title(ch, idx)
        if progress_cb:
            avg_ms = int((sum(chapter_times) / len(chapter_times)) * 1000) if chapter_times else None
            remaining = len(chapters) - len(chapter_times)
            eta_ms = int(avg_ms * remaining) if avg_ms is not None and remaining else None
            progress_cb(
                {
                    "event": "chapter_begin",
                    "index": idx,
                    "title": ch_title,
                    "done": len(chapter_times),
                    "total": len(chapters),
                    "eta_ms": eta_ms,
                }
            )
        ch_start = time.perf_counter()
        contract = _format_chapter_contract(idx, ch, continuation=False, length_scale=length_scale)
        sem_q = f"{premise[:900]}\n{contract}"
        mem_book = ""
        if use_long_memory:
            mem_book = build_memory_context(book_path, max_chars=4200, semantic_query=sem_q)
        mem_parts: list[str] = []
        if kb_block.strip():
            mem_parts.append(kb_block.strip())
        if use_long_memory:
            if mem_book.strip():
                mem_parts.append(mem_book.strip())
            if memory_context_global.strip():
                mem_parts.append("【全局记忆宫殿（跨书）】\n" + memory_context_global.strip())
        if voice_block.strip():
            mem_parts.append(voice_block.strip()[:7000])
        mem_parts.append(ideation_instruction(ideation_w))
        if macro_for_writing:
            mem_parts.append(_macro_phase_note_for_chapter(idx, macro_for_writing))
        if length_scale == "short":
            mem_parts.append(_short_story_reader_engagement_instruction())
        if length_scale == "long":
            cl = read_changelog_tail(book_path, max_chars=1200)
            if cl.strip():
                mem_parts.append("【设定变更 log（摘录·最近）】\n" + cl.strip())
            mem_parts.append(long_novel_wiki_memory_instruction())
        reg_block = build_character_registry_block(book_path, max_chars=3800)
        mem_parts.append(CHARACTER_REGISTRY_INSTRUCTION + "\n" + reg_block)
        story_tail = load_chapter_tail_for_prompt(book_path, max_chars=6800)
        mem_parts.append(
            f"【书名】{book_title}\n【全书梗概】\n{premise}\n"
            f"【跨章剧情提要】\n{story_tail or '（为首章或尚无累积提要。）'}\n"
            f"---\n{contract}\n\n"
            "请写本章完整正文；只输出小说正文，不要标题以外的元说明。"
        )
        user_full = "\n\n".join(mem_parts)
        try:
            body, alog = run_chapter_with_agents(
                system=system,
                user_payload=user_full,
                writing_temp=writing_temp,
                premise=premise,
                kb_block=kb_block,
                agent_profile=agent_profile,
                run_reader_test=run_reader_test,
            )
        except RuntimeError as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:
            raise HTTPException(502, f"第 {idx} 章生成失败: {e}") from e

        cleaned = sanitize_chapter_body(body)
        if idx > 1:
            prev_path = book_path / "chapters" / f"{idx - 1:02d}.md"
            if prev_path.is_file():
                try:
                    prev_plain = _chapter_body_plain_from_file(prev_path.read_text(encoding="utf-8"))
                    cleaned = strip_common_prefix_with_previous_opening(prev_plain, cleaned)
                except OSError:
                    pass
        cleaned = strip_leading_duplicate_chapter_heading(cleaned, ch_title)
        content = _chapter_heading(ch_title, idx) + cleaned + "\n"
        write_chapter(root, book_id, idx, content)
        saved.append(str(book_path / "chapters" / f"{idx:02d}.md"))
        agent_logs.append({"chapter": idx, "log": alog})
        try:
            append_agent_orchestration_log(
                root,
                book_id,
                {"chapter": idx, "ts": time.time(), "log": compact_agent_log(alog)},
            )
        except OSError:
            logger.debug("append_agent_orchestration_log failed", exc_info=True)

        elapsed = time.perf_counter() - ch_start
        chapter_times.append(elapsed)
        if progress_cb:
            progress_cb(
                {
                    "event": "chapter_end",
                    "index": idx,
                    "title": ch_title,
                    "duration_ms": int(elapsed * 1000),
                    "done": len(chapter_times),
                    "total": len(chapters),
                }
            )

        ent = _live_supervisor_after_chapter(
            live_supervisor=live_supervisor,
            book_title=book_title,
            chapter_index=idx,
            chapter_title=ch_title,
            beat=str(ch.get("beat") or ""),
            premise=premise,
            chapter_plain=cleaned,
            alog=alog,
            progress_cb=progress_cb,
        )
        if ent is not None:
            live_supervisor_logs.append(ent)
            try:
                maybe_append_changelog_after_supervisor(
                    book_path,
                    length_scale=length_scale,
                    chapter_index=idx,
                    supervisor_entry=ent,
                )
            except Exception:
                logger.debug("canon changelog append failed", exc_info=True)

        snippet = cleaned.replace("\n", " ")[:320]
        orch = orchestrator_bump_state(orch, step="chapter_draft", chapter=idx)
        write_orchestration_state(root, book_id, orch)

        if sync_book_memory:
            _append_rollup_chapter_snippet(
                book_path, book_title, idx, snippet, chapter_title=ch_title
            )
            _sync_book_memory_entries(
                book_path, str(idx), cleaned, temperature=0.38, chapter_title=ch_title
            )
        try:
            append_chapter_tail_snippet(
                book_path, chapter_n=idx, chapter_title=ch_title, snippet=snippet
            )
            maybe_compress_chapter_tail(book_path)
        except OSError:
            logger.debug("chapter tail append/compress failed", exc_info=True)
        try:
            auto_seed_characters_after_chapter(
                book_path, chapter_idx=idx, chapter_plain_text=cleaned
            )
        except Exception:
            logger.debug("character auto_seed failed", exc_info=True)
        try:
            bump_character_mentions_from_plain(book_path, idx, cleaned)
        except Exception:
            logger.debug("character mention bump failed", exc_info=True)

        if foreshadowing_sync_after_chapter and sync_book_memory:
            try:
                sync_foreshadowing_after_chapter(
                    book_root=book_path,
                    chapter_label=str(idx),
                    chapter_plain=cleaned,
                    premise=premise,
                    temperature=0.28,
                )
            except Exception:
                logger.debug("foreshadowing sync failed", exc_info=True)
        if memory_episodic_keep_last is not None and int(memory_episodic_keep_last) >= 1 and sync_book_memory:
            try:
                prune_episodic_extraction_entries(
                    book_path, keep_last=int(memory_episodic_keep_last)
                )
            except Exception:
                logger.debug("memory episodic prune failed", exc_info=True)

        if length_scale == "long" and sync_book_memory and idx > 0 and idx % 50 == 0:
            try:
                cr = maybe_wiki_compile_episodic_batch(
                    book_path,
                    milestone_chapter=idx,
                    book_title=book_title,
                    premise=premise,
                    temperature=0.35,
                )
                if progress_cb and isinstance(cr, dict) and cr.get("ok"):
                    progress_cb({"event": "wiki_compile", "chapter": idx, "wiki_compile": cr})
            except Exception as e:
                logger.warning("wiki compile at chapter %s: %s", idx, e)

    supervisor_final: dict[str, Any] | None = None
    if final_supervisor:
        supervisor_final = _final_supervisor_for_book(
            root=root, book_id=book_id, progress_cb=progress_cb
        )

    prefix = _safe_filename_prefix(book_title)
    out: dict[str, Any] = {
        "book_id": book_id,
        "book_title": book_title,
        "premise": premise,
        "chapters_planned": len(chapters),
        "saved_files": saved,
        "series_prefix": prefix,
        "plan_file": str(book_path / "plan.json"),
        "meta": {
            "length_scale": length_scale,
            "protagonist_gender": protagonist_gender,
            "agent_profile": agent_profile,
            "ideation_level": ideation_w,
            "virtual_author": author_meta,
            "user_book_note": note_s,
        },
        "virtual_author": author_meta,
        "user_book_note": note_s,
        "agent_logs": agent_logs,
    }
    if live_supervisor_logs:
        out["live_supervisor"] = live_supervisor_logs
    if supervisor_final is not None:
        out["supervisor_final"] = supervisor_final
    return out


_REWRITE_TASK_NOTE = (
    "【本章为重写】旧稿将被覆盖；须输出**整章完整正文**，满足下方篇幅与章末张力，"
    "禁止只写补丁、脑内提纲或未完片段。"
)


def run_rewrite_chapter(
    *,
    root: Path,
    book_id: str,
    chapter_index: Optional[int],
    theme_addon: str,
    writer_system: str,
    use_long_memory: bool,
    memory_context_global: str,
    kb_block: str,
    writing_temp: float,
    agent_profile: str = "fast",
    run_reader_test: bool = False,
    ideation_level: Optional[float] = None,
    live_supervisor: bool = False,
    progress_cb: ProgressCb = None,
) -> dict[str, Any]:
    """按既有 plan 要点（无则兜底）重新生成并覆盖某一章；chapter_index 为 None 时重写当前最后一章。"""
    book_path = book_dir(root, book_id)
    nums = get_chapter_numbers(root, book_id)
    if not nums:
        raise HTTPException(status_code=404, detail="本书暂无章节文件")
    idx = int(chapter_index) if chapter_index is not None else int(max(nums))
    if idx not in nums:
        raise HTTPException(status_code=404, detail=f"第 {idx} 章不存在")

    plan_data = get_plan(root, book_id)
    premise = str(plan_data.get("premise") or "")
    book_title = str(plan_data.get("book_title") or plan_data.get("title") or book_id)

    length_scale = "medium"
    macro_for_writing: Optional[dict[str, Any]] = None
    meta_p = plan_data.get("meta")
    if isinstance(meta_p, dict):
        ls0 = str(meta_p.get("length_scale") or "").strip().lower()
        if ls0 in ("short", "medium", "long"):
            length_scale = ls0
        mo = meta_p.get("macro_outline")
        if isinstance(mo, dict):
            macro_for_writing = mo

    iw_raw = ideation_level
    if iw_raw is None and isinstance(meta_p, dict) and meta_p.get("ideation_level") is not None:
        try:
            iw_raw = float(meta_p["ideation_level"])
        except (TypeError, ValueError):
            iw_raw = 0.5
    if iw_raw is None:
        iw_raw = 0.5
    ideation_w = max(0.0, min(1.0, float(iw_raw)))

    ch_row: Optional[dict[str, Any]] = None
    chs_pl = plan_data.get("chapters")
    if isinstance(chs_pl, list):
        for c in chs_pl:
            if isinstance(c, dict) and int(c.get("idx", 0)) == idx:
                ch_row = _normalize_chapter_entry(c, idx)
                break
    if ch_row is None:
        title_guess = ""
        for row in get_toc(root, book_id):
            if int(row["n"]) == idx:
                title_guess = str(row.get("title") or "")
                break
        ch_row = {
            "idx": idx,
            "beat": (
                f"（plan 未载第 {idx} 章要点）请据全书梗概与跨章提要重写本章；"
                f"标题意象约「{title_guess or f'第 {idx} 章'}」；完整正文，章末须有情绪落点或悬念。"
            ),
        }
        if title_guess:
            ch_row["title"] = title_guess

    ch_title = _fallback_chapter_title(ch_row, idx)
    contract = _format_chapter_contract(
        idx, ch_row, continuation=(idx > 1), length_scale=length_scale
    )

    system = writer_system.strip()
    if theme_addon.strip():
        system = f"{system}\n\n【题材约束】\n{theme_addon.strip()}"

    voice_block = format_voice_from_book_meta(get_meta(root, book_id))
    sem_q = f"{premise[:900]}\n{contract}"
    mem_book = ""
    if use_long_memory:
        mem_book = build_memory_context(book_path, max_chars=4200, semantic_query=sem_q)
    mem_parts: list[str] = []
    if kb_block.strip():
        mem_parts.append(kb_block.strip())
    if use_long_memory:
        if mem_book.strip():
            mem_parts.append(mem_book.strip())
        if memory_context_global.strip():
            mem_parts.append("【全局记忆宫殿（跨书）】\n" + memory_context_global.strip())
    if voice_block.strip():
        mem_parts.append(voice_block.strip()[:7000])
    mem_parts.append(ideation_instruction(ideation_w))
    mem_parts.append(_REWRITE_TASK_NOTE)
    if macro_for_writing:
        mem_parts.append(_macro_phase_note_for_chapter(idx, macro_for_writing))
    if length_scale == "short":
        mem_parts.append(_short_story_reader_engagement_instruction())
    if length_scale == "long":
        cl = read_changelog_tail(book_path, max_chars=1200)
        if cl.strip():
            mem_parts.append("【设定变更 log（摘录·最近）】\n" + cl.strip())
        mem_parts.append(long_novel_wiki_memory_instruction())
    reg_block = build_character_registry_block(book_path, max_chars=3800)
    mem_parts.append(CHARACTER_REGISTRY_INSTRUCTION + "\n" + reg_block)
    story_tail = load_chapter_tail_for_prompt(book_path, max_chars=6800)
    mem_parts.append(
        f"【书名】{book_title}\n【全书梗概】\n{premise}\n"
        f"【跨章剧情提要】\n{story_tail or '（尚无累积提要。）'}\n"
        f"---\n{contract}\n\n"
        "请写本章完整正文；只输出小说正文，不要标题以外的元说明。"
    )
    user_full = "\n\n".join(mem_parts)

    if progress_cb:
        progress_cb(
            {
                "event": "chapter_begin",
                "index": idx,
                "title": ch_title,
                "rewrite": True,
                "done": 0,
                "total": 1,
            }
        )

    try:
        body, alog = run_chapter_with_agents(
            system=system,
            user_payload=user_full,
            writing_temp=writing_temp,
            premise=premise,
            kb_block=kb_block,
            agent_profile=agent_profile,
            run_reader_test=run_reader_test,
        )
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(502, f"第 {idx} 章重写失败: {e}") from e

    cleaned = sanitize_chapter_body(body)
    if idx > 1:
        prev_path = book_path / "chapters" / f"{idx - 1:02d}.md"
        if prev_path.is_file():
            try:
                prev_plain = _chapter_body_plain_from_file(prev_path.read_text(encoding="utf-8"))
                cleaned = strip_common_prefix_with_previous_opening(prev_plain, cleaned)
            except OSError:
                pass
    cleaned = strip_leading_duplicate_chapter_heading(cleaned, ch_title)
    content = _chapter_heading(ch_title, idx) + cleaned + "\n"
    write_chapter(root, book_id, idx, content)
    try:
        append_agent_orchestration_log(
            root,
            book_id,
            {"chapter": idx, "ts": time.time(), "rewrite": True, "log": compact_agent_log(alog)},
        )
    except OSError:
        logger.debug("append_agent_orchestration_log failed", exc_info=True)

    _live_supervisor_after_chapter(
        live_supervisor=live_supervisor,
        book_title=book_title,
        chapter_index=idx,
        chapter_title=ch_title,
        beat=str(ch_row.get("beat") or ""),
        premise=premise,
        chapter_plain=cleaned,
        alog=alog,
        progress_cb=progress_cb,
    )

    orch = read_orchestration_state(root, book_id)
    orch = orchestrator_bump_state(orch, step="chapter_rewrite", chapter=idx)
    write_orchestration_state(root, book_id, orch)

    if progress_cb:
        progress_cb(
            {
                "event": "chapter_end",
                "index": idx,
                "title": ch_title,
                "rewrite": True,
                "done": 1,
                "total": 1,
            }
        )

    return {
        "book_id": book_id,
        "book_title": book_title,
        "chapter_index": idx,
        "chapter_title": ch_title,
        "saved_file": str(book_path / "chapters" / f"{idx:02d}.md"),
        "agent_log": alog,
    }


def run_continue_next_chapter(
    *,
    root: Path,
    book_id: str,
    theme_addon: str,
    writer_system: str,
    use_long_memory: bool,
    memory_context_global: str,
    kb_block: str,
    writing_temp: float,
    agent_profile: str = "fast",
    sync_book_memory: bool = True,
    run_reader_test: bool = False,
    ideation_level: Optional[float] = None,
    live_supervisor: bool = False,
    final_supervisor: bool = False,
    progress_cb: ProgressCb = None,
    memory_episodic_keep_last: Optional[int] = None,
    foreshadowing_sync_after_chapter: bool = False,
) -> dict[str, Any]:
    book_path = book_dir(root, book_id)
    voice_block = ""
    try:
        voice_block = format_voice_from_book_meta(get_meta(root, book_id))
    except HTTPException:
        voice_block = ""
    ch_dir = book_path / "chapters"
    nums: list[tuple[int, Path]] = []
    for p in ch_dir.glob("*.md"):
        m = re.match(r"^(\d+)\.md$", p.name)
        if m:
            nums.append((int(m.group(1)), p))
    if not nums:
        raise HTTPException(status_code=404, detail="本书暂无章节文件")
    nums.sort(key=lambda x: x[0])
    last_n, last_path = nums[-1]
    next_n = last_n + 1
    if next_n > MAX_PIPELINE_CHAPTERS:
        raise HTTPException(status_code=400, detail="章节序号过大")

    plan_data = get_plan(root, book_id)
    iw_raw = ideation_level
    if iw_raw is None:
        meta_p = plan_data.get("meta")
        if isinstance(meta_p, dict) and meta_p.get("ideation_level") is not None:
            try:
                iw_raw = float(meta_p["ideation_level"])
            except (TypeError, ValueError):
                iw_raw = 0.5
        else:
            iw_raw = 0.5
    ideation_w = max(0.0, min(1.0, float(iw_raw)))
    premise = str(plan_data.get("premise") or "")
    book_title = str(plan_data.get("book_title") or plan_data.get("title") or book_id)
    length_scale_cont = "medium"
    meta_ls = plan_data.get("meta")
    if isinstance(meta_ls, dict):
        ls0 = str(meta_ls.get("length_scale") or "").strip().lower()
        if ls0 in ("short", "medium", "long"):
            length_scale_cont = ls0
    beat_next = ""
    plan_chapter: Optional[dict[str, Any]] = None
    chs = plan_data.get("chapters")
    if isinstance(chs, list):
        for c in chs:
            if isinstance(c, dict) and int(c.get("idx", 0)) == next_n:
                plan_chapter = _normalize_chapter_entry(c, next_n)
                if plan_chapter:
                    beat_next = plan_chapter["beat"]
                break

    last_text = last_path.read_text(encoding="utf-8")
    if last_text.strip().startswith("<!--"):
        close = last_text.find("-->")
        if close != -1:
            last_text = last_text[close + 3 :].lstrip()

    if not beat_next.strip():
        sys_b = (
            "你是小说编辑。根据梗概与上一章正文，只输出下一章的情节要点（180-260字），"
            "包含场景、冲突推进与章末悬念；不要写小说正文，不要列表套话。"
            "若上一章尾部为险情、对峙或未收束动作，要点中须先用一两句交代「承接上章末的直接后果」，再写本章新推进，不得从无关新场景零过渡起笔。"
            f" {PLANNER_ORIGINALITY_CONTRACT}"
        )
        tail = last_text.strip()[-2800:] if len(last_text) > 2800 else last_text.strip()
        user_b = (
            f"书名：{book_title}\n【全书梗概】\n{premise or '（无梗概则根据上文推断风格与线索）'}\n"
            f"{ideation_instruction(ideation_w)}\n"
            f"上一章为第 {last_n} 章。\n---\n上一章正文（尾部）：\n{tail}\n---\n请给出第 {next_n} 章要点。"
        )
        if voice_block.strip():
            user_b = user_b + "\n\n---\n" + voice_block.strip()[:4000]
        try:
            beat_next = chat_completion(system=sys_b, user=user_b, temperature=0.62).strip()
        except LLMTransportError as e:
            raise HTTPException(502, str(e)) from e
        except RuntimeError as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:
            raise HTTPException(502, f"续写要点生成失败: {e}") from e

    title_next = str((plan_chapter or {}).get("title") or "").strip()
    if not title_next:
        try:
            title_next = chat_completion(
                system=(
                    "根据下列「下一章情节要点」，输出一个 6～14 字的章名。"
                    "不要书名号、引号或解释，只一行标题。"
                ),
                user=beat_next[:900],
                temperature=0.45,
            ).strip()[:80]
        except Exception:
            title_next = ""
    if not title_next:
        title_next = f"第 {next_n} 章"

    if plan_chapter:
        chapter_for_contract = {**plan_chapter, "idx": next_n, "beat": beat_next, "title": title_next}
    else:
        chapter_for_contract = {"idx": next_n, "beat": beat_next, "title": title_next}
    contract_block = _format_chapter_contract(
        next_n, chapter_for_contract, continuation=True, length_scale=length_scale_cont
    )

    system = writer_system.strip()
    if theme_addon.strip():
        system = f"{system}\n\n【题材约束】\n{theme_addon.strip()}"

    sem_q = f"{(premise or '')[:900]}\n{contract_block}\n{beat_next[:700]}"
    mem_book = ""
    if use_long_memory:
        mem_book = build_memory_context(book_path, max_chars=4200, semantic_query=sem_q)
    parts: list[str] = []
    if kb_block.strip():
        parts.append(kb_block.strip())
    if use_long_memory:
        if mem_book.strip():
            parts.append(mem_book.strip())
        if memory_context_global.strip():
            parts.append("【全局记忆宫殿（跨书）】\n" + memory_context_global.strip())
    if voice_block.strip():
        parts.append(voice_block.strip()[:7000])
    parts.append(ideation_instruction(ideation_w))
    if length_scale_cont == "short":
        parts.append(_short_story_reader_engagement_instruction())
    if length_scale_cont == "long":
        clc = read_changelog_tail(book_path, max_chars=1200)
        if clc.strip():
            parts.append("【设定变更 log（摘录·最近）】\n" + clc.strip())
        parts.append(long_novel_wiki_memory_instruction())
    reg_block = build_character_registry_block(book_path, max_chars=3800)
    parts.append(CHARACTER_REGISTRY_INSTRUCTION + "\n" + reg_block)
    story_tail = load_chapter_tail_for_prompt(book_path, max_chars=6800)
    if story_tail.strip():
        parts.append("【跨章剧情提要】\n" + story_tail.strip())
    prev_for_ctx = last_text.strip()
    if len(prev_for_ctx) > 14000:
        prev_for_ctx = prev_for_ctx[-14000:]
    bridge = _continuation_prev_chapter_bridge_instruction(last_n)
    parts.append(
        f"【书名】{book_title}\n【全书梗概】\n{premise or '（无策划梗概时请紧扣上一章衔接。）'}\n"
        f"【上一章正文】第 {last_n} 章\n{prev_for_ctx}\n"
        f"{bridge}"
        f"---\n{contract_block}\n\n"
        "请写本章完整正文，自然承接；只输出小说正文，不要标题以外的元说明。"
    )
    user_full = "\n\n".join(parts)
    try:
        body, alog = run_chapter_with_agents(
            system=system,
            user_payload=user_full,
            writing_temp=writing_temp,
            premise=premise,
            kb_block=kb_block,
            agent_profile=agent_profile,
            run_reader_test=run_reader_test,
        )
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(502, f"第 {next_n} 章续写失败: {e}") from e

    cleaned = sanitize_chapter_body(body)
    try:
        prev_plain = _chapter_body_plain_from_file(last_path.read_text(encoding="utf-8"))
        cleaned = strip_common_prefix_with_previous_opening(prev_plain, cleaned)
    except OSError:
        pass
    cleaned = strip_leading_duplicate_chapter_heading(cleaned, title_next)
    content = _chapter_heading(title_next, next_n) + cleaned + "\n"
    write_chapter(root, book_id, next_n, content)
    try:
        append_agent_orchestration_log(
            root,
            book_id,
            {"chapter": next_n, "ts": time.time(), "log": compact_agent_log(alog)},
        )
    except OSError:
        logger.debug("append_agent_orchestration_log failed", exc_info=True)

    try:
        if isinstance(chs, list):
            if not any(isinstance(x, dict) and int(x.get("idx", 0)) == next_n for x in chs):
                row: dict[str, Any] = {"idx": next_n, "beat": beat_next[:800], "title": title_next}
                if plan_chapter:
                    if plan_chapter.get("space_for_later"):
                        row["space_for_later"] = str(plan_chapter["space_for_later"])[:500]
                    if plan_chapter.get("hook_end"):
                        row["hook_end"] = str(plan_chapter["hook_end"])[:300]
                chs.append(row)
            else:
                for x in chs:
                    if isinstance(x, dict) and int(x.get("idx", 0)) == next_n:
                        x["beat"] = beat_next[:800]
                        x["title"] = title_next
                        if plan_chapter:
                            if plan_chapter.get("space_for_later"):
                                x["space_for_later"] = str(plan_chapter["space_for_later"])[:500]
                            if plan_chapter.get("hook_end"):
                                x["hook_end"] = str(plan_chapter["hook_end"])[:300]
                        break
            plan_data["chapters"] = chs
            update_plan(root, book_id, plan_data)
    except Exception:
        pass

    orch = orchestrator_bump_state(
        read_orchestration_state(root, book_id),
        step="continue_draft",
        chapter=next_n,
    )
    write_orchestration_state(root, book_id, orch)

    ent = _live_supervisor_after_chapter(
        live_supervisor=live_supervisor,
        book_title=book_title,
        chapter_index=next_n,
        chapter_title=title_next,
        beat=beat_next,
        premise=premise,
        chapter_plain=cleaned,
        alog=alog,
        progress_cb=progress_cb,
    )
    if ent is not None:
        try:
            maybe_append_changelog_after_supervisor(
                book_path,
                length_scale=length_scale_cont,
                chapter_index=next_n,
                supervisor_entry=ent,
            )
        except Exception:
            logger.debug("canon changelog append failed", exc_info=True)

    if sync_book_memory:
        snippet = cleaned.replace("\n", " ")[:320]
        _append_rollup_chapter_snippet(
            book_path, book_title, next_n, snippet, chapter_title=title_next
        )
        _sync_book_memory_entries(
            book_path, str(next_n), cleaned, temperature=0.38, chapter_title=title_next
        )
    try:
        append_chapter_tail_snippet(
            book_path,
            chapter_n=next_n,
            chapter_title=title_next,
            snippet=cleaned.replace("\n", " ")[:320],
        )
        maybe_compress_chapter_tail(book_path)
    except OSError:
        logger.debug("chapter tail append/compress failed", exc_info=True)
    try:
        auto_seed_characters_after_chapter(
            book_path, chapter_idx=next_n, chapter_plain_text=cleaned
        )
    except Exception:
        logger.debug("character auto_seed failed", exc_info=True)
    try:
        bump_character_mentions_from_plain(book_path, next_n, cleaned)
    except Exception:
        logger.debug("character mention bump failed", exc_info=True)

    if foreshadowing_sync_after_chapter and sync_book_memory:
        try:
            sync_foreshadowing_after_chapter(
                book_root=book_path,
                chapter_label=str(next_n),
                chapter_plain=cleaned,
                premise=premise,
                temperature=0.28,
            )
        except Exception:
            logger.debug("foreshadowing sync failed", exc_info=True)
    if memory_episodic_keep_last is not None and int(memory_episodic_keep_last) >= 1 and sync_book_memory:
        try:
            prune_episodic_extraction_entries(
                book_path, keep_last=int(memory_episodic_keep_last)
            )
        except Exception:
            logger.debug("memory episodic prune failed", exc_info=True)

    if length_scale_cont == "long" and sync_book_memory and next_n > 0 and next_n % 50 == 0:
        try:
            cr = maybe_wiki_compile_episodic_batch(
                book_path,
                milestone_chapter=next_n,
                book_title=book_title,
                premise=premise,
                temperature=0.35,
            )
            if progress_cb and isinstance(cr, dict) and cr.get("ok"):
                progress_cb({"event": "wiki_compile", "chapter": next_n, "wiki_compile": cr})
        except Exception as e:
            logger.warning("wiki compile at chapter %s: %s", next_n, e)

    out: dict[str, Any] = {
        "book_id": book_id,
        "book_title": book_title,
        "chapter_index": next_n,
        "chapter_title": title_next,
        "saved_file": str(book_path / "chapters" / f"{next_n:02d}.md"),
        "series_prefix": _safe_filename_prefix(book_title),
        "agent_log": alog,
    }
    if ent is not None:
        out["live_supervisor"] = [ent]
    if final_supervisor:
        out["supervisor_final"] = _final_supervisor_for_book(
            root=root, book_id=book_id, progress_cb=progress_cb
        )
    return out


def run_continue_chapters(
    *,
    root: Path,
    book_id: str,
    count: int,
    theme_addon: str,
    writer_system: str,
    use_long_memory: bool,
    memory_context_global: str,
    kb_block: str,
    writing_temp: float,
    agent_profile: str = "fast",
    sync_book_memory: bool = True,
    run_reader_test: bool = False,
    progress_cb: ProgressCb = None,
    ideation_level: Optional[float] = None,
    live_supervisor: bool = False,
    final_supervisor: bool = False,
    continuation_arc_plan: bool = True,
    memory_episodic_keep_last: Optional[int] = 48,
    foreshadowing_sync_after_chapter: bool = True,
) -> dict[str, Any]:
    """续写多章：逐章调用 run_continue_next_chapter。"""
    n = max(1, min(int(count), MAX_CONTINUE_CHAPTERS))
    plan0 = get_plan(root, book_id)
    iw_raw = ideation_level
    if iw_raw is None:
        meta_p0 = plan0.get("meta")
        if isinstance(meta_p0, dict) and meta_p0.get("ideation_level") is not None:
            try:
                iw_raw = float(meta_p0["ideation_level"])
            except (TypeError, ValueError):
                iw_raw = 0.5
        else:
            iw_raw = 0.5
    ideation_w = max(0.0, min(1.0, float(iw_raw)))

    user_arc_note = ""
    um = get_meta(root, book_id)
    if isinstance(um, dict):
        user_arc_note = str(um.get("user_book_note") or "").strip()
    if not user_arc_note:
        mp0 = plan0.get("meta")
        if isinstance(mp0, dict):
            user_arc_note = str(mp0.get("user_book_note") or "").strip()

    arc_meta: dict[str, Any] | None = None
    if continuation_arc_plan and n > 1:
        try:
            arc_meta = plan_continuation_arc(
                root=root,
                book_id=book_id,
                arc_length=n,
                temperature=min(0.68, float(writing_temp)),
                ideation_level=ideation_w,
                user_book_note=user_arc_note,
            )
            if progress_cb:
                progress_cb({"event": "continuation_arc", "plan": arc_meta})
        except HTTPException:
            raise
        except Exception as e:
            logger.warning("plan_continuation_arc failed: %s", e)

    results: list[dict[str, Any]] = []
    last: dict[str, Any] = {}
    live_supervisor_logs: list[dict[str, Any]] = []
    for i in range(n):
        if progress_cb:
            progress_cb({"event": "continue_begin", "i": i + 1, "total": n})
        last = run_continue_next_chapter(
            root=root,
            book_id=book_id,
            theme_addon=theme_addon,
            writer_system=writer_system,
            use_long_memory=use_long_memory,
            memory_context_global=memory_context_global,
            kb_block=kb_block,
            writing_temp=writing_temp,
            agent_profile=agent_profile,
            sync_book_memory=sync_book_memory,
            run_reader_test=run_reader_test,
            ideation_level=ideation_level,
            live_supervisor=live_supervisor,
            final_supervisor=False,
            progress_cb=progress_cb,
            memory_episodic_keep_last=memory_episodic_keep_last,
            foreshadowing_sync_after_chapter=foreshadowing_sync_after_chapter,
        )
        ls = last.get("live_supervisor")
        if isinstance(ls, list):
            live_supervisor_logs.extend(ls)
        results.append(
            {
                "chapter_index": last.get("chapter_index"),
                "chapter_title": last.get("chapter_title"),
                "saved_file": last.get("saved_file"),
            }
        )
        if progress_cb:
            progress_cb({"event": "continue_end", "i": i + 1, "total": n, "chapter": last})
    last_public = {
        k: v for k, v in last.items() if k not in ("live_supervisor", "supervisor_final")
    }
    out: dict[str, Any] = {
        "book_id": book_id,
        "book_title": last.get("book_title"),
        "chapters_written": n,
        "chapters": results,
        "last": last_public,
    }
    if arc_meta is not None:
        out["continuation_arc"] = arc_meta
    if live_supervisor_logs:
        out["live_supervisor"] = live_supervisor_logs
    if final_supervisor:
        out["supervisor_final"] = _final_supervisor_for_book(
            root=root, book_id=book_id, progress_cb=progress_cb
        )
    return out


def run_continue_next_chapter_legacy_out(
    *,
    root: Path,
    series_prefix: str,
    theme_addon: str,
    writer_system: str,
    use_long_memory: bool,
    memory_context: str,
    kb_block: str,
    writing_temp: float,
    ideation_level: Optional[float] = None,
    agent_profile: str = "fast",
    run_reader_test: bool = False,
) -> dict[str, Any]:
    """兼容旧版 out/ 前缀_第NN章.md；编排模式与书本续写一致（fast / full）。"""
    out_dir = root / "out"
    prefix = series_prefix
    nums: list[tuple[int, Path]] = []
    esc = re.escape(prefix)
    for p in out_dir.glob("*.md"):
        m = re.match(rf"^{esc}_第(\d+)章\.md$", p.name)
        if m:
            nums.append((int(m.group(1)), p))
    if not nums:
        raise HTTPException(status_code=404, detail="未找到该系列的章节文件")
    nums.sort(key=lambda x: x[0])
    last_n, last_path = nums[-1]
    next_n = last_n + 1
    if next_n > MAX_PIPELINE_CHAPTERS:
        raise HTTPException(status_code=400, detail="章节序号过大，请新建书系")

    plan_path = out_dir / f"{prefix}_策划.json"
    premise = ""
    book_title = prefix
    beat_next = ""
    plan_data: dict[str, Any] = {}
    if plan_path.is_file():
        try:
            plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
            premise = str(plan_data.get("premise") or "")
            book_title = str(plan_data.get("book_title") or prefix)
            chs = plan_data.get("chapters")
            if isinstance(chs, list):
                for c in chs:
                    if isinstance(c, dict) and int(c.get("idx", 0)) == next_n:
                        beat_next = str(c.get("beat", "")).strip()
                        break
        except (OSError, json.JSONDecodeError, ValueError, TypeError):
            plan_data = {}

    iw_raw = ideation_level
    if iw_raw is None:
        meta_l = plan_data.get("meta")
        if isinstance(meta_l, dict) and meta_l.get("ideation_level") is not None:
            try:
                iw_raw = float(meta_l["ideation_level"])
            except (TypeError, ValueError):
                iw_raw = 0.5
        else:
            iw_raw = 0.5
    ideation_w = max(0.0, min(1.0, float(iw_raw)))

    length_scale_legacy = "medium"
    meta_lg = plan_data.get("meta")
    if isinstance(meta_lg, dict):
        lsg = str(meta_lg.get("length_scale") or "").strip().lower()
        if lsg in ("short", "medium", "long"):
            length_scale_legacy = lsg

    last_text = last_path.read_text(encoding="utf-8")
    if last_text.strip().startswith("<!--"):
        close = last_text.find("-->")
        if close != -1:
            last_text = last_text[close + 3 :].lstrip()

    if not beat_next.strip():
        sys_b = (
            "你是小说编辑。根据梗概与上一章正文，只输出下一章的情节要点（180-260字），"
            "包含场景、冲突推进与章末悬念；不要写小说正文，不要列表套话。"
            "若上一章尾部为险情、对峙或未收束动作，要点中须先用一两句交代「承接上章末的直接后果」，再写本章新推进，不得从无关新场景零过渡起笔。"
            f" {PLANNER_ORIGINALITY_CONTRACT}"
        )
        tail = last_text.strip()[-2800:] if len(last_text) > 2800 else last_text.strip()
        user_b = (
            f"书名：{book_title}\n【全书梗概】\n{premise or '（无梗概则根据上文推断风格与线索）'}\n"
            f"{ideation_instruction(ideation_w)}\n"
            f"上一章为第 {last_n} 章。\n---\n上一章正文（尾部）：\n{tail}\n---\n请给出第 {next_n} 章要点。"
        )
        try:
            beat_next = chat_completion(system=sys_b, user=user_b, temperature=0.62).strip()
        except LLMTransportError as e:
            raise HTTPException(502, str(e)) from e
        except RuntimeError as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:
            raise HTTPException(502, f"续写要点生成失败: {e}") from e

    system = writer_system.strip()
    if theme_addon.strip():
        system = f"{system}\n\n【题材约束】\n{theme_addon.strip()}"

    parts: list[str] = []
    if kb_block.strip():
        parts.append(kb_block.strip())
    if use_long_memory and memory_context.strip():
        parts.append(memory_context.strip())
    parts.append(ideation_instruction(ideation_w))
    if length_scale_legacy == "short":
        parts.append(_short_story_reader_engagement_instruction())
    prev_for_ctx = last_text.strip()
    if len(prev_for_ctx) > 14000:
        prev_for_ctx = prev_for_ctx[-14000:]
    bridge = _continuation_prev_chapter_bridge_instruction(last_n)
    parts.append(
        f"【书名】{book_title}\n【全书梗概】\n{premise or '（无策划梗概时请紧扣上一章衔接。）'}\n"
        f"【上一章正文】第 {last_n} 章\n{prev_for_ctx}\n"
        f"{bridge}"
        f"---\n【本章任务】第 {next_n} 章（续写，承接上文）\n{beat_next}\n\n"
        "请写本章完整正文，自然承接；只输出小说正文，不要标题以外的元说明。"
    )
    user_full = "\n\n".join(parts)
    ap = (agent_profile or "fast").strip().lower()
    if ap not in ("fast", "full"):
        ap = "fast"
    try:
        body, alog = run_chapter_with_agents(
            system=system,
            user_payload=user_full,
            writing_temp=writing_temp,
            premise=premise,
            kb_block=kb_block,
            agent_profile=ap,
            run_reader_test=run_reader_test,
        )
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(502, f"第 {next_n} 章续写失败: {e}") from e

    fname = f"{prefix}_第{next_n:02d}章.md"
    fpath = out_dir / Path(fname).name
    title_line = f"第 {next_n} 章"
    cleaned = sanitize_chapter_body(body)
    try:
        prev_plain = _chapter_body_plain_from_file(last_path.read_text(encoding="utf-8"))
        cleaned = strip_common_prefix_with_previous_opening(prev_plain, cleaned)
    except OSError:
        pass
    cleaned = strip_leading_duplicate_chapter_heading(cleaned, title_line)
    out_text = f"## {title_line}\n\n{cleaned}\n"
    try:
        fpath.write_text(out_text, encoding="utf-8")
    except OSError as e:
        raise HTTPException(500, f"写入失败: {e}") from e

    if plan_path.is_file() or plan_data:
        try:
            if not plan_data:
                plan_data = {"book_title": book_title, "premise": premise, "chapters": []}
            chs = plan_data.setdefault("chapters", [])
            if isinstance(chs, list):
                if not any(isinstance(x, dict) and int(x.get("idx", 0)) == next_n for x in chs):
                    chs.append({"idx": next_n, "beat": beat_next[:800]})
            plan_path.write_text(
                json.dumps(plan_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except (OSError, TypeError):
            pass

    return {
        "book_title": book_title,
        "chapter_index": next_n,
        "saved_file": str(fpath),
        "series_prefix": prefix,
        "agent_log": alog,
    }
