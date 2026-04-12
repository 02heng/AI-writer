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
from .memory_store import add_entry, build_memory_context, read_rollup, write_rollup
from .orchestration.runner import orchestrator_bump_state, run_chapter_with_agents
from .schemas import validate_book_plan
from .scene_writer import generate_chapter_with_scenes
from .layered_memory import build_context_for_chapter

logger = get_logger(__name__)

ProgressCb = Optional[Callable[[dict[str, Any]], None]]


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
    lines.append(f"【本章标题】{ch_title}（正文开头须用二级标题呈现：## {ch_title}）")
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


def _plan_from_title(
    *,
    title: str,
    theme_hint: str,
    chapter_count: int,
    length_scale: str,
    protagonist_gender: str,
    temperature: float,
) -> dict[str, Any]:
    n = max(3, min(int(chapter_count), 25))
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
        "每章 beat 必填，其余可选字段能填尽量填以提升后文一致性。"
    )
    user_p = f"题目：{title.strip()}\n"
    user_p += f"总章数（必须严格遵守）：恰好 {n} 章。\n"
    user_p += _scale_instruction(length_scale) + "\n"
    user_p += _protagonist_instruction(protagonist_gender) + "\n"
    if theme_hint:
        user_p += f"题材说明：{theme_hint}\n"
    raw = chat_completion(system=sys_p, user=user_p, temperature=temperature)
    try:
        data = extract_json_object(raw)
    except (json.JSONDecodeError, ValueError) as e:
        raw2 = chat_completion(
            system=sys_p + " 若上次输出有误，这次务必仅输出合法 JSON。",
            user=user_p + "\n上次模型输出无法解析，请重给 JSON。",
            temperature=max(0.3, temperature - 0.2),
        )
        data = extract_json_object(raw2)
    if not isinstance(data, dict):
        raise ValueError("JSON 根须为对象")
    chapters = data.get("chapters")
    if not isinstance(chapters, list) or len(chapters) < 1:
        raise ValueError("chapters 无效")
    return data


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
        n_ch = max(3, min(int(max_chapters), 25))
        plan_raw = _plan_from_title(
            title=title,
            theme_hint=theme_hint,
            chapter_count=n_ch,
            length_scale=length_scale,
            protagonist_gender=protagonist_gender,
            temperature=planning_temp,
        )
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        raise HTTPException(status_code=502, detail=f"策划阶段失败（JSON）：{e}") from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"策划阶段失败：{e}") from e

    book_title = str(plan_raw.get("book_title") or title).strip() or title
    premise = str(plan_raw.get("premise") or "").strip()
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
    n_target = max(3, min(int(max_chapters), 25))
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

    plan_payload = {
        "book_title": book_title,
        "premise": premise,
        "meta": {
            "length_scale": length_scale,
            "protagonist_gender": protagonist_gender,
            "chapter_count": n_target,
        },
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
    if next_n > 99:
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
    n = max(1, min(int(count), 20))
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
    if next_n > 99:
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
