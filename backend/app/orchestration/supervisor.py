"""监督智能体（元层）：监控编排子智能体运行、书本与策划一致性，并输出可执行的迭代建议。

- 确定性报告：`supervisor_integrity_report`（续写书目 / 章节文件 vs plan、编排状态）。
- LLM 元审查：`agent_supervisor_meta_review`（基于报告 + 近期 agent_runs 摘要）。
- 流水线逐章审查：`agent_supervisor_live_chapter_review`（每章落地后轻量 JSON，供流式 UI 与后续章提示）。
- 终局落盘：`append_supervisor_final_to_orchestration_state`（把元审查写入 orchestration/state.json）。"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..book_storage import (
    book_dir,
    get_chapter_numbers,
    get_meta,
    get_plan,
    get_toc,
    read_orchestration_state,
)
from ..jsonutil import extract_json_object
from ..llm import chat_completion


def compact_agent_log(alog: dict[str, Any]) -> dict[str, Any]:
    """压缩单章编排日志，便于 jsonl 存储与监督模型消费。"""
    steps = alog.get("steps") if isinstance(alog.get("steps"), list) else []
    slim: list[dict[str, Any]] = []
    for s in steps:
        if not isinstance(s, dict):
            continue
        slim.append(
            {
                "agent": s.get("agent"),
                "ok": s.get("ok"),
                "error": (str(s.get("error") or ""))[:400],
                "violations_count": s.get("violations_count"),
                "level": s.get("level"),
            }
        )
    out: dict[str, Any] = {"profile": alog.get("profile"), "steps": slim}
    if alog.get("reader_driven_revision"):
        out["reader_driven_revision"] = True
    if isinstance(alog.get("reader_test"), dict):
        rt = alog["reader_test"]
        out["reader_test_keys"] = list(rt.keys())[:12]
    return out


def _read_agent_runs_tail(book_path: Path, max_lines: int) -> list[dict[str, Any]]:
    p = book_path / "orchestration" / "agent_runs.jsonl"
    if not p.is_file():
        return []
    try:
        raw = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    tail = lines[-max_lines:] if len(lines) > max_lines else lines
    out: list[dict[str, Any]] = []
    for ln in tail:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


def supervisor_integrity_report(data_root: Path, book_id: str) -> dict[str, Any]:
    """检查 plan、磁盘章节、目录与 orchestration/state 的一致性（不调用 LLM）。"""
    root = book_dir(data_root, book_id)
    meta = get_meta(data_root, book_id)
    plan = get_plan(data_root, book_id)
    nums = get_chapter_numbers(data_root, book_id)
    toc = get_toc(data_root, book_id)
    orch = read_orchestration_state(data_root, book_id)

    chs = plan.get("chapters")
    plan_indices: list[int] = []
    if isinstance(chs, list):
        for c in chs:
            if not isinstance(c, dict):
                continue
            try:
                ix = int(c.get("idx", 0))
            except (TypeError, ValueError):
                continue
            if ix >= 1:
                plan_indices.append(ix)
    plan_indices.sort()
    plan_set = set(plan_indices)
    num_set = set(nums)

    missing_files: list[int] = []
    for idx in plan_indices:
        fn = root / "chapters" / f"{idx:02d}.md"
        if not fn.is_file():
            missing_files.append(idx)

    extra_on_disk: list[int] = [n for n in nums if n not in plan_set] if plan_set else []

    max_plan = max(plan_indices) if plan_indices else 0
    max_disk = max(nums) if nums else 0
    gaps_in_sequence: list[int] = []
    if nums:
        lo, hi = min(nums), max(nums)
        present = set(nums)
        for n in range(lo, hi + 1):
            if n not in present:
                gaps_in_sequence.append(n)

    orch_ch = int(orch.get("chapter") or 0)
    state_step = str(orch.get("step") or "idle")

    warnings: list[str] = []
    if missing_files:
        warnings.append(f"策划中已有 {len(missing_files)} 个章节号尚未落盘为 .md")
    if extra_on_disk and plan_set:
        warnings.append(f"磁盘上存在 {len(extra_on_disk)} 个未出现在 plan.chapters 中的章节号（常见于续写超前于策划）")
    if gaps_in_sequence:
        warnings.append(f"章节序号在 {min(gaps_in_sequence)}–{max(gaps_in_sequence)} 范围内存在空洞")
    if orch_ch and max_disk and orch_ch != max_disk:
        warnings.append(f"编排 state 记录最近章节为 {orch_ch}，与磁盘最大章节 {max_disk} 不一致（可能中断或手工改文件）")
    if len(toc) != len(nums):
        warnings.append("目录条数与章节文件数不一致（异常，请检查 chapters/）")
    if not plan_indices and nums:
        warnings.append("plan.chapters 无可解析 idx，但磁盘已有章节（常见于仅续写、尚未写回策划）")

    planned_total = len(plan_indices)
    written_count = len(nums)
    completion_ratio = (written_count / planned_total) if planned_total else None

    integrity_ok = not missing_files and not gaps_in_sequence
    needs_attention = (not integrity_ok) or bool(warnings)

    return {
        "book_id": book_id,
        "title": str(meta.get("title") or book_id),
        "planned_chapter_indices": plan_indices,
        "planned_count": planned_total,
        "chapter_files_on_disk": nums,
        "written_count": written_count,
        "max_plan_index": max_plan,
        "max_disk_index": max_disk,
        "missing_files": missing_files,
        "extra_on_disk_not_in_plan": extra_on_disk,
        "gaps_in_sequence": gaps_in_sequence,
        "orchestration_state": {"step": state_step, "chapter": orch_ch, "draft_version": orch.get("draft_version")},
        "open_issues": orch.get("open_issues") if isinstance(orch.get("open_issues"), list) else [],
        "completion_ratio": completion_ratio,
        "warnings": warnings,
        "integrity_ok": integrity_ok,
        "needs_attention": needs_attention,
        "agent_runs_path": str(root / "orchestration" / "agent_runs.jsonl"),
        "recent_run_count": len(_read_agent_runs_tail(root, 500)),
    }


def agent_supervisor_meta_review(
    *,
    integrity: dict[str, Any],
    recent_runs: list[dict[str, Any]],
    temperature: float = 0.25,
) -> dict[str, Any]:
    """
    监督智能体：根据完整性报告与近期子智能体运行摘要，输出对提示词/编排/续写策略的迭代建议。
    仅 JSON，便于程序消费或写入 Analytics/reviews。
    """
    sys_p = (
        "你是 AI 写作流水线的**总监督（元智能体）**，不直接写小说正文。\n"
        "## 职责\n"
        "1. 根据「完整性报告」判断续写书目与章节文件、策划是否脱节。\n"
        "2. 根据「近期子智能体日志」判断 Writer / Continuity / Editor / Safety 等是否反复失败或异常。\n"
        "3. 产出可执行的迭代建议：改哪类提示词、是否应切换 fast/full、是否应补 plan、是否应人工检查某章。\n"
        "## 输出（仅 JSON，无 Markdown）\n"
        '{"health_score":0-100,'
        '"summary":"一两句总评",'
        '"risks":[{"level":"low|med|high","topic":"string","detail":"string"}],'
        '"prompt_iteration_hints":["针对 writer.md / 系统提示 的可执行修改方向"],'
        '"agent_chain_hints":["针对多智能体链顺序、温度、是否启用 ReaderTest 等"],'
        '"continuation_hints":["针对续写与 plan 同步、缺章补写顺序"],'
        '"next_actions":["用户或开发者下一步具体动作"]}\n'
        "若无风险，risks 可为空数组；hints 可简短。"
    )
    user_p = (
        "【完整性报告 JSON】\n"
        + json.dumps(integrity, ensure_ascii=False)[:14000]
        + "\n\n【近期 agent_runs 记录（截断）】\n"
        + json.dumps(recent_runs[-25:], ensure_ascii=False)[:12000]
    )
    raw = chat_completion(system=sys_p, user=user_p, temperature=temperature)
    try:
        return extract_json_object(raw)
    except (ValueError, json.JSONDecodeError):
        return {
            "health_score": 50,
            "summary": "模型输出非 JSON，解析失败",
            "risks": [],
            "prompt_iteration_hints": [],
            "agent_chain_hints": [],
            "continuation_hints": [],
            "next_actions": ["重试审查或检查 DEEPSEEK_API_KEY / 模型输出"],
            "_parse_error": True,
        }


def load_context_for_supervisor_review(
    data_root: Path, book_id: str, *, max_run_lines: int = 40
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    integrity = supervisor_integrity_report(data_root, book_id)
    root = book_dir(data_root, book_id)
    recent = _read_agent_runs_tail(root, max_run_lines)
    return integrity, recent


_MAX_OPEN_ISSUES = 48


def append_supervisor_final_to_orchestration_state(
    state: dict[str, Any],
    *,
    integrity: dict[str, Any],
    meta_review: dict[str, Any],
) -> dict[str, Any]:
    """在既有 orchestration state 上追加一条「全书完成后的总监督」记录，供续写或人工迭代时参考。"""
    st = dict(state)
    cur = st.get("open_issues")
    issues: list[Any] = list(cur) if isinstance(cur, list) else []

    def _str_list(key: str, cap: int) -> list[str]:
        raw = meta_review.get(key)
        if not isinstance(raw, list):
            return []
        out: list[str] = []
        for x in raw:
            s = str(x).strip()
            if s:
                out.append(s[:600])
            if len(out) >= cap:
                break
        return out

    entry: dict[str, Any] = {
        "kind": "supervisor_final",
        "ts": time.time(),
        "health_score": meta_review.get("health_score"),
        "summary": str(meta_review.get("summary") or "")[:1200],
        "integrity_ok": bool(integrity.get("integrity_ok")),
        "needs_attention": bool(integrity.get("needs_attention")),
        "prompt_iteration_hints": _str_list("prompt_iteration_hints", 24),
        "agent_chain_hints": _str_list("agent_chain_hints", 16),
        "continuation_hints": _str_list("continuation_hints", 16),
        "next_actions": _str_list("next_actions", 16),
        "risks": meta_review.get("risks") if isinstance(meta_review.get("risks"), list) else [],
    }
    issues.append(entry)
    st["open_issues"] = issues[-_MAX_OPEN_ISSUES:]
    st["last_supervisor_final_ts"] = entry["ts"]
    return st


def agent_supervisor_live_chapter_review(
    *,
    book_title: str,
    chapter_index: int,
    chapter_title: str,
    beat: str,
    premise: str,
    chapter_plain: str,
    agent_chain_compact: dict[str, Any],
    temperature: float = 0.28,
) -> dict[str, Any]:
    """
    流水线「实时」监督：本章已写入磁盘后调用，对照策划 beat 与梗概，指出问题并标注**应由哪类子智能体**在后续迭代中加强。
    输出短 JSON，成本低于正文的 Continuity 全量链，可与 full 模式并存（元层视角不同）。
    """
    ch_excerpt = (chapter_plain or "").strip()
    if len(ch_excerpt) > 9000:
        ch_excerpt = ch_excerpt[:9000] + "\n…（以下截断）"
    sys_p = (
        "你是中文小说写作流水线的**监督审查智能体**（元层），不改写正文。\n"
        "## 时机\n"
        "本章正文已由 Writer（及可能的 Character / Lore / Editor / Safety）链生成并即将定稿；你只做**读後快审**。\n"
        "## 任务\n"
        "1. 对照【本章策划 beat】与【全书梗概】：本章是否完成 beat 承诺、有无明显跑题或伏笔烂尾。\n"
        "2. 若梗概或 beat 体现网文爽文/言情：爽点或感情推进是否落地；章末是否有钩子或情绪落点；言情是否空洞甜话堆砌。\n"
        "3. 扫一眼正文：人称/时序/称谓是否有**一眼可见**的不一致（不必穷尽）。\n"
        "4. 结合【本子智能体链摘要】：若某步失败或违规多，指出下一轮应对**哪类智能体**调参或改提示。\n"
        "## 输出（仅 JSON，无 Markdown）\n"
        '{"summary":"一两句总评",'
        '"beat_alignment":"strong|ok|weak|off",'
        '"issues":['
        '{"severity":"low|med|high","topic":"string","detail":"string",'
        '"target_agent":"Writer|Character|Continuity|Editor|Safety|Planner|Memory|none"}'
        "],"
        '"next_chapter_notes":["给下一章 Writer 的简短提醒，可为空数组"],'
        '"chain_feedback":["对编排/温度/是否 full 的一句话建议，可为空"]}\n'
        "若无问题，issues 可为空；target_agent 必须是上述枚举之一。"
    )
    user_p = (
        f"【书名】{book_title}\n【章序】第 {chapter_index} 章\n【章题】{chapter_title}\n"
        f"【全书梗概】\n{(premise or '')[:2200]}\n\n"
        f"【本章策划 beat】\n{(beat or '')[:1600]}\n\n"
        f"【本子智能体链摘要 JSON】\n{json.dumps(agent_chain_compact, ensure_ascii=False)[:3500]}\n\n"
        f"【本章正文摘录】\n{ch_excerpt}"
    )
    raw = chat_completion(system=sys_p, user=user_p, temperature=temperature)
    try:
        return extract_json_object(raw)
    except (ValueError, json.JSONDecodeError):
        return {
            "summary": "监督模型输出非 JSON",
            "beat_alignment": "ok",
            "issues": [],
            "next_chapter_notes": [],
            "chain_feedback": [],
            "_parse_error": True,
        }
