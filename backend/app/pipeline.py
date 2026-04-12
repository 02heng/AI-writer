from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import HTTPException

from .book_storage import (
    book_dir,
    create_book,
    get_plan,
    read_orchestration_state,
    update_plan,
    write_chapter,
    write_orchestration_state,
)
from .core.logging import get_logger, LogContext
from .jsonutil import extract_json_object
from .llm import chat_completion
from .memory_store import add_entry, build_memory_context, init_db, read_rollup, write_rollup
from .orchestration.runner import orchestrator_bump_state, run_chapter_with_agents
from .scene_writer import generate_chapter_with_scenes
from .layered_memory import build_context_for_chapter

logger = get_logger(__name__)

ProgressCb = Optional[Callable[[dict[str, Any]], None]]

# 本轮一键生成上限；超过 PLAN_SINGLE_SHOT_MAX 章时用分批策划，避免单次 JSON 过大导致模型截断或语法错误。
MAX_PIPELINE_CHAPTERS = 1500
# 用户可声明的「全书预定总章数」上限（可大于本轮生成数，用于宏观阶段表）。
MAX_PLANNED_TOTAL_CHAPTERS = 5000
# 单次「续写」API 可连续生成的章数上限（逐章循环，与一键新书上限分开）。
MAX_CONTINUE_CHAPTERS = 500
PLAN_SINGLE_SHOT_MAX = 20
PLAN_BATCH_SIZE = 20


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
    )
    user_p = (
        f"题目：{title.strip()}\n"
        f"全书预定总章数：{planned_total}（必须按此尺度设计阶段跨度）。\n"
        f"本轮将实际生成正文：第 1–{chapters_this_run} 章。\n"
        f"{_scale_instruction(length_scale)}\n{_protagonist_instruction(protagonist_gender)}\n"
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
    """Strip HTML comments, decorative asterisk lines, and trim."""
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
    return "\n".join(lines_out).strip()


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


def _scale_instruction(length_scale: str) -> str:
    m = {
        "short": "篇幅为短篇：结构紧凑，单线或极少支线，冲突推进快，适合约三万至八万汉字量级的叙事节奏，避免冗长支线。",
        "medium": "篇幅为中篇：可有适度支线与铺陈，节奏介于短篇与长篇之间，注意主线清晰。",
        "long": "篇幅为长篇：允许多线叙事、伏笔与人物弧光充分展开，章节间保持悬念与节奏起伏，避免水文。",
    }
    return m.get(length_scale, m["medium"])


def _protagonist_instruction(gender: str) -> str:
    m = {
        "male": "主角为男性；全文保持视角稳定（若第三人称则以该男性为主要视点人物），不得无交代切换主角。",
        "female": "主角为女性；全文保持视角稳定（若第三人称则以该女性为主要视点人物），不得无交代切换主角。",
        "any": "主角性别与视角由故事自然呈现，但须前后一致，不得中途无解释改变主角核心设定。",
    }
    return m.get(gender, m["any"])


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


def _format_chapter_contract(idx: int, ch: dict[str, Any], *, continuation: bool = False) -> str:
    tail = "（续写：须自然承接上一章语气和事实，勿重述已交代信息）" if continuation else ""
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
    lines.append(
        "【结构提示】开场尽快入戏；中段推进冲突或信息；结尾留情绪落点或悬念，避免「总之/后来」式收尾。"
    )
    return "\n".join(lines)


def _plan_from_title_single(
    *,
    title: str,
    theme_hint: str,
    chapter_count: int,
    length_scale: str,
    protagonist_gender: str,
    temperature: float,
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
        )
    user_p = f"题目：{title.strip()}\n"
    user_p += f"总章数（必须严格遵守）：恰好 {n} 章。\n"
    user_p += _scale_instruction(length_scale) + "\n"
    user_p += _protagonist_instruction(protagonist_gender) + "\n"
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
) -> tuple[str, str]:
    sys_p = (
        "你是中文小说总策划。只输出一个 JSON 对象，禁止 Markdown。"
        '{"book_title":"string","premise":"string 全书梗概 350-700 字"}'
        " 须合法 JSON：无尾逗号，字符串内勿换行。"
    )
    user_p = (
        f"题目：{title.strip()}\n"
        f"全书共 {total_chapters} 章（分章要点将分批生成，此处只输出书名定稿与全书梗概）。\n"
        f"{_scale_instruction(length_scale)}\n"
        f"{_protagonist_instruction(protagonist_gender)}\n"
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
) -> list[dict[str, Any]]:
    k = end_idx - start_idx + 1
    sys_p = (
        "你是中文小说分章策划。只输出一个 JSON 对象："
        '{"chapters":[{"idx":int,"title":"4-12字","beat":"70-130字"},...]}'
        f" chapters 必须恰好 {k} 条，idx 从 {start_idx} 到 {end_idx} 每条唯一且连续。"
        "仅允许 idx、title、beat 三键；beat 为一段无换行文字；禁止尾逗号与 Markdown。"
    )
    user_p = (
        f"原始题目：{title.strip()}\n书名：{book_title}\n【全书梗概】\n{premise}\n"
        f"{_scale_instruction(length_scale)}\n{_protagonist_instruction(protagonist_gender)}\n"
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
) -> dict[str, Any]:
    n = max(3, min(int(chapter_count), MAX_PIPELINE_CHAPTERS))
    book_title, premise = _plan_book_meta(
        title=title,
        theme_hint=theme_hint,
        total_chapters=n,
        length_scale=length_scale,
        protagonist_gender=protagonist_gender,
        temperature=temperature,
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
        )
    return _plan_from_title_batched(
        title=title,
        theme_hint=theme_hint,
        chapter_count=n_run,
        length_scale=length_scale,
        protagonist_gender=protagonist_gender,
        temperature=temperature,
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
    )
    if theme_hint:
        user_p += f"题材说明：{theme_hint}\n"
    if isinstance(macro_outline, dict) and macro_outline:
        user_p += _format_macro_block(macro_outline, chapters_this_run=n_target) + "\n"
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
) -> dict[str, Any]:
    """策划 → 逐章写作 → 写入 books/{book_id}/。
    
    Args:
        use_scene_generation: If True, use scene-level generation for better
            long-text quality. Each chapter is split into scenes before writing.
    """
    theme_hint = (theme_addon or "").strip()
    length_scale = length_scale if length_scale in ("short", "medium", "long") else "medium"
    protagonist_gender = protagonist_gender if protagonist_gender in ("male", "female", "any") else "any"
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
            theme_hint=theme_hint,
            chapter_count=n_ch,
            length_scale=length_scale,
            protagonist_gender=protagonist_gender,
            temperature=planning_temp,
            planned_total_chapters=planned_opt,
            progress_cb=progress_cb,
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
    meta_plan: dict[str, Any] = {
        "length_scale": length_scale,
        "protagonist_gender": protagonist_gender,
        "chapter_count": n_target,
        "chapters_this_run": n_target,
        "planned_total_chapters": planned_stored,
    }
    if macro_for_writing:
        meta_plan["macro_outline"] = macro_for_writing
    plan_payload = {
        "book_title": book_title,
        "premise": premise,
        "meta": meta_plan,
        "chapters": chapters,
    }

    created = create_book(
        root,
        title=book_title,
        premise=premise,
        plan=plan_payload,
        meta_extra={"source_title": title, "agent_profile": agent_profile},
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

    saved: list[str] = []
    running_summary = ""
    orch = read_orchestration_state(root, book_id)
    agent_logs: list[dict[str, Any]] = []

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
        contract = _format_chapter_contract(idx, ch, continuation=False)
        mem_book = ""
        if use_long_memory:
            mem_book = build_memory_context(book_path, max_chars=4200)
        mem_parts: list[str] = []
        if kb_block.strip():
            mem_parts.append(kb_block.strip())
        if use_long_memory:
            if mem_book.strip():
                mem_parts.append(mem_book.strip())
            if memory_context_global.strip():
                mem_parts.append("【全局记忆宫殿（跨书）】\n" + memory_context_global.strip())
        if macro_for_writing:
            mem_parts.append(_macro_phase_note_for_chapter(idx, macro_for_writing))
        mem_parts.append(
            f"【书名】{book_title}\n【全书梗概】\n{premise}\n"
            f"【已生成前文摘要】\n{running_summary or '（这是第一章，无前文。）'}\n"
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
        cleaned = strip_leading_duplicate_chapter_heading(cleaned, ch_title)
        content = _chapter_heading(ch_title, idx) + cleaned + "\n"
        write_chapter(root, book_id, idx, content)
        saved.append(str(book_path / "chapters" / f"{idx:02d}.md"))
        agent_logs.append({"chapter": idx, "log": alog})

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

        snippet = cleaned.replace("\n", " ")[:320]
        running_summary += f"\n第{idx}章「{ch_title}」：{snippet}"
        orch = orchestrator_bump_state(orch, step="chapter_draft", chapter=idx)
        write_orchestration_state(root, book_id, orch)

        if sync_book_memory:
            _append_rollup_chapter_snippet(
                book_path, book_title, idx, snippet, chapter_title=ch_title
            )
            _sync_book_memory_entries(
                book_path, str(idx), cleaned, temperature=0.38, chapter_title=ch_title
            )

    prefix = _safe_filename_prefix(book_title)
    return {
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
        },
        "agent_logs": agent_logs,
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
) -> dict[str, Any]:
    book_path = book_dir(root, book_id)
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
    premise = str(plan_data.get("premise") or "")
    book_title = str(plan_data.get("book_title") or plan_data.get("title") or book_id)
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
        )
        tail = last_text.strip()[-2800:] if len(last_text) > 2800 else last_text.strip()
        user_b = (
            f"书名：{book_title}\n【全书梗概】\n{premise or '（无梗概则根据上文推断风格与线索）'}\n"
            f"上一章为第 {last_n} 章。\n---\n上一章正文（尾部）：\n{tail}\n---\n请给出第 {next_n} 章要点。"
        )
        try:
            beat_next = chat_completion(system=sys_b, user=user_b, temperature=0.62).strip()
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
    contract_block = _format_chapter_contract(next_n, chapter_for_contract, continuation=True)

    system = writer_system.strip()
    if theme_addon.strip():
        system = f"{system}\n\n【题材约束】\n{theme_addon.strip()}"

    mem_book = ""
    if use_long_memory:
        mem_book = build_memory_context(book_path, max_chars=4200)
    parts: list[str] = []
    if kb_block.strip():
        parts.append(kb_block.strip())
    if use_long_memory:
        if mem_book.strip():
            parts.append(mem_book.strip())
        if memory_context_global.strip():
            parts.append("【全局记忆宫殿（跨书）】\n" + memory_context_global.strip())
    prev_for_ctx = last_text.strip()
    if len(prev_for_ctx) > 14000:
        prev_for_ctx = prev_for_ctx[-14000:]
    parts.append(
        f"【书名】{book_title}\n【全书梗概】\n{premise or '（无策划梗概时请紧扣上一章衔接。）'}\n"
        f"【上一章正文】第 {last_n} 章\n{prev_for_ctx}\n"
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
    cleaned = strip_leading_duplicate_chapter_heading(cleaned, title_next)
    content = _chapter_heading(title_next, next_n) + cleaned + "\n"
    write_chapter(root, book_id, next_n, content)

    try:
        if isinstance(chs, list):
            if not any(isinstance(x, dict) and int(x.get("idx", 0)) == next_n for x in chs):
                chs.append({"idx": next_n, "beat": beat_next[:800], "title": title_next})
            else:
                for x in chs:
                    if isinstance(x, dict) and int(x.get("idx", 0)) == next_n:
                        x["beat"] = beat_next[:800]
                        x["title"] = title_next
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

    if sync_book_memory:
        snippet = cleaned.replace("\n", " ")[:320]
        _append_rollup_chapter_snippet(
            book_path, book_title, next_n, snippet, chapter_title=title_next
        )
        _sync_book_memory_entries(
            book_path, str(next_n), cleaned, temperature=0.38, chapter_title=title_next
        )

    return {
        "book_id": book_id,
        "book_title": book_title,
        "chapter_index": next_n,
        "chapter_title": title_next,
        "saved_file": str(book_path / "chapters" / f"{next_n:02d}.md"),
        "series_prefix": _safe_filename_prefix(book_title),
        "agent_log": alog,
    }


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
) -> dict[str, Any]:
    """续写多章：逐章调用 run_continue_next_chapter。"""
    n = max(1, min(int(count), MAX_CONTINUE_CHAPTERS))
    results: list[dict[str, Any]] = []
    last: dict[str, Any] = {}
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
        )
        results.append(
            {
                "chapter_index": last.get("chapter_index"),
                "chapter_title": last.get("chapter_title"),
                "saved_file": last.get("saved_file"),
            }
        )
        if progress_cb:
            progress_cb({"event": "continue_end", "i": i + 1, "total": n, "chapter": last})
    return {
        "book_id": book_id,
        "book_title": last.get("book_title"),
        "chapters_written": n,
        "chapters": results,
        "last": last,
    }


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
) -> dict[str, Any]:
    """兼容旧版 out/ 前缀_第NN章.md。"""
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

    last_text = last_path.read_text(encoding="utf-8")
    if last_text.strip().startswith("<!--"):
        close = last_text.find("-->")
        if close != -1:
            last_text = last_text[close + 3 :].lstrip()

    if not beat_next.strip():
        sys_b = (
            "你是小说编辑。根据梗概与上一章正文，只输出下一章的情节要点（180-260字），"
            "包含场景、冲突推进与章末悬念；不要写小说正文，不要列表套话。"
        )
        tail = last_text.strip()[-2800:] if len(last_text) > 2800 else last_text.strip()
        user_b = (
            f"书名：{book_title}\n【全书梗概】\n{premise or '（无梗概则根据上文推断风格与线索）'}\n"
            f"上一章为第 {last_n} 章。\n---\n上一章正文（尾部）：\n{tail}\n---\n请给出第 {next_n} 章要点。"
        )
        try:
            beat_next = chat_completion(system=sys_b, user=user_b, temperature=0.62).strip()
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
    prev_for_ctx = last_text.strip()
    if len(prev_for_ctx) > 14000:
        prev_for_ctx = prev_for_ctx[-14000:]
    parts.append(
        f"【书名】{book_title}\n【全书梗概】\n{premise or '（无策划梗概时请紧扣上一章衔接。）'}\n"
        f"【上一章正文】第 {last_n} 章\n{prev_for_ctx}\n"
        f"---\n【本章任务】第 {next_n} 章（续写，承接上文）\n{beat_next}\n\n"
        "请写本章完整正文，自然承接；只输出小说正文，不要标题以外的元说明。"
    )
    user_full = "\n\n".join(parts)
    try:
        body = chat_completion(system=system, user=user_full, temperature=writing_temp)
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(502, f"第 {next_n} 章续写失败: {e}") from e

    fname = f"{prefix}_第{next_n:02d}章.md"
    fpath = out_dir / Path(fname).name
    title_line = f"第 {next_n} 章"
    cleaned = sanitize_chapter_body(body)
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
    }
