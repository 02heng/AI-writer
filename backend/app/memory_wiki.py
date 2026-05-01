"""长篇：记忆宫殿与「作者圣经」Wiki 协作 — 定期编译、设定变更 log。"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Optional

from .core.logging import get_logger
from .llm import chat_completion
from .memory_store import add_entry, init_db, list_entries_for_chapter_range, read_rollup, write_rollup

logger = get_logger(__name__)

WIKI_COMPILE_INTERVAL = 20
CHANGELOG_REL = Path("memory") / "canon_changelog.md"
STATE_REL = Path("memory") / "wiki_compile_state.json"


def changelog_path(book_root: Path) -> Path:
    return (book_root / CHANGELOG_REL).resolve()


def wiki_compile_state_path(book_root: Path) -> Path:
    return (book_root / STATE_REL).resolve()


def _read_wiki_compile_state(book_root: Path) -> dict[str, Any]:
    p = wiki_compile_state_path(book_root)
    if not p.is_file():
        return {"last_compiled_milestone": 0}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return {"last_compiled_milestone": 0}


def _write_wiki_compile_state(book_root: Path, milestone: int) -> None:
    init_db(book_root)
    p = wiki_compile_state_path(book_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    cur = _read_wiki_compile_state(book_root)
    cur["last_compiled_milestone"] = int(milestone)
    cur["updated_at"] = time.time()
    p.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")


def read_changelog_tail(book_root: Path, *, max_chars: int = 1200) -> str:
    """供注入：设定变更 log 尾部摘录。"""
    p = changelog_path(book_root)
    if not p.is_file():
        return ""
    try:
        t = p.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    if len(t) <= max_chars:
        return t
    return "…(更早省略)\n\n" + t[-(max_chars - 20) :]


def long_novel_wiki_memory_instruction() -> str:
    return (
        "【长篇 · 记忆宫殿与作者圣经（Wiki）】\n"
        "1）**作者圣经（KB）**：先注入本书 `books/<id>/kb/author-bible-synthesis.md`，为**结构化**总则（人物卡、年表、规则/世界观、伏笔等，对齐 `author-bible-template`），**不含**记忆宫殿抽屉条目；可再接用户勾选的 `UserData/kb/*.md`。\n"
        "2）**记忆宫殿（后读）**：注入 `palace_summary` 与**按章号线序**的近期条目（约最近 32 章内、章号小→大），承载连载过程与逐章萃取；与总则冲突时以**用户全书项目说明（若本书已保存）**、总则与梗概综合为准并宜人工校订。\n"
        "3）每章成稿后流水线会按**仅本章正文+梗概+旧总则+模板**刷新总则（**不**把宫殿「最近条目」喂进总则生成，避免重复）。环境变量 `AIWRITER_SKIP_KB_SYNTHESIS=1` 可跳过自动总则刷新。\n"
        "4）宫殿内仍宜用「指针」指回 `kb/`，勿把长设定全文堆进抽屉；每约 20 章合并情节萃取入 `palace_summary.md`。\n"
        "5）若上下文含【设定变更 log】：为监督智能体根据章节审查追加的摘要，续写须与之兼容。\n"
    )


def append_canon_changelog_from_supervisor_review(
    book_root: Path,
    *,
    chapter_index: int,
    review: dict[str, Any],
) -> None:
    """根据逐章监督 JSON，将可能涉及设定/规则/世界观的 issue 记入 memory/canon_changelog.md。"""
    issues = review.get("issues")
    if not isinstance(issues, list) or not issues:
        return
    lines_out: list[str] = []
    pat = re.compile(r"设定|规则|世界观|时间线|称谓|吃书|矛盾|伏笔|记忆|设定集|canon", re.I)
    for it in issues:
        if not isinstance(it, dict):
            continue
        sev = str(it.get("severity") or "").lower()
        topic = str(it.get("topic") or "")
        detail = str(it.get("detail") or "").strip()
        tgt = str(it.get("target_agent") or "")
        if sev not in ("med", "high"):
            continue
        if not pat.search(topic + detail) and tgt != "Memory":
            continue
        one = re.sub(r"\s+", " ", detail)[:220]
        if one:
            lines_out.append(f"- 第 {chapter_index} 章（监督·{sev}）：{one}")
    if not lines_out:
        return
    init_db(book_root)
    p = changelog_path(book_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# 设定变更 log（Canon changelog）\n\n"
        "本文件由**逐章监督审查**在长篇模式下自动追加摘要行（非正文）。"
        "人工可在 `kb/` 作者圣经中落笔最终定稿。\n\n"
        "## 记录\n\n"
    )
    if not p.exists():
        p.write_text(header, encoding="utf-8")
    try:
        existing = p.read_text(encoding="utf-8", errors="replace").rstrip() + "\n"
    except OSError:
        existing = header
    block = "\n".join(lines_out) + "\n"
    p.write_text(existing + block, encoding="utf-8")


def maybe_wiki_compile_episodic_batch(
    book_root: Path,
    *,
    milestone_chapter: int,
    book_title: str,
    premise: str,
    temperature: float = 0.35,
) -> dict[str, Any]:
    """
    在 milestone_chapter 为 20 的倍数且 >0 时，将本批 20 章内的「情节·萃取」合并进 palace_summary，并删除已合并的自动萃取行。
    幂等：同一 milestone 不重复执行。
    """
    if milestone_chapter < WIKI_COMPILE_INTERVAL or milestone_chapter % WIKI_COMPILE_INTERVAL != 0:
        return {"skipped": True, "reason": "not_milestone"}
    state = _read_wiki_compile_state(book_root)
    last = int(state.get("last_compiled_milestone") or 0)
    if last >= milestone_chapter:
        return {"skipped": True, "reason": "already_compiled", "milestone": milestone_chapter}

    lo = milestone_chapter - WIKI_COMPILE_INTERVAL + 1
    hi = milestone_chapter
    entries = list_entries_for_chapter_range(book_root, lo, hi, limit=600)
    if not entries:
        _write_wiki_compile_state(book_root, milestone_chapter)
        return {"skipped": True, "reason": "no_entries", "range": [lo, hi]}

    chunks: list[str] = []
    for e in entries:
        lab = str(e.get("chapter_label") or "")
        title = str(e.get("title") or "")
        body = str(e.get("body") or "").strip()
        if not body:
            continue
        chunks.append(f"### 条目 ch{lab} {title}\n{body[:3500]}")
    blob = "\n\n".join(chunks)[:80000]
    if len(blob) < 80:
        _write_wiki_compile_state(book_root, milestone_chapter)
        return {"skipped": True, "reason": "too_short"}

    sys_p = (
        "你是长篇小说设定编辑。下面是从「记忆宫殿」某一连续批次自动萃取的多条条目（可能重复）。"
        "请合并、去重，写成一段可写入「总摘要/衣柜层」的中文（800～1600 字），"
        "只保留后续写作必须记住的事实：人物状态、不可逆事件、开放伏笔、规则边界；"
        "不要评价文笔，不要复述原文对白。输出纯文本段落，不要 Markdown 标题、不要 JSON。"
    )
    user_p = (
        f"【书名】{book_title}\n【全书梗概摘录】\n{(premise or '')[:2800]}\n\n"
        f"【待合并条目（约第 {lo}–{hi} 章相关）】\n{blob}"
    )
    try:
        merged = chat_completion(system=sys_p, user=user_p, temperature=temperature).strip()
    except Exception as e:
        logger.warning("wiki compile LLM failed: %s", e)
        return {"ok": False, "error": str(e)[:400]}

    if len(merged) < 40:
        return {"ok": False, "error": "merge_too_short"}

    rollup = read_rollup(book_root).strip()
    block = (
        f"\n\n---\n\n## 长篇节点合并（第 {lo}–{hi} 章 · 自动）\n\n"
        f"{merged}\n\n"
        f"_（本段由流水线每 {WIKI_COMPILE_INTERVAL} 章合并「情节·萃取」生成；请在 `kb/` 作者圣经中校对硬设定。）_\n"
    )
    write_rollup(book_root, (rollup + block).strip() if rollup else block.strip())

    deleted = _delete_merged_episodic_extractions(book_root, lo, hi)
    add_entry(
        book_root,
        room="情节",
        title=f"【{WIKI_COMPILE_INTERVAL}章编译】第 {lo}–{hi} 章已并入总摘要",
        body=f"已合并约 {len(entries)} 条萃取相关记录入 palace_summary，并清理本批自动萃取 {deleted} 条。",
        chapter_label=str(hi),
    )
    _write_wiki_compile_state(book_root, milestone_chapter)
    return {
        "ok": True,
        "milestone": milestone_chapter,
        "range": [lo, hi],
        "entries_seen": len(entries),
        "deleted_extractions": deleted,
    }


def _delete_merged_episodic_extractions(book_root: Path, lo: int, hi: int) -> int:
    """删除本批章节范围内、流水线自动生成的情节萃取条目。"""
    import sqlite3

    init_db(book_root)
    from .memory_store import db_path

    conn = sqlite3.connect(db_path(book_root))
    try:
        cur = conn.execute(
            """
            DELETE FROM memory_entries
            WHERE room = '情节'
              AND (title LIKE '%· 萃取%' OR title LIKE '%生成同步萃取%')
              AND chapter_label GLOB '[0-9]*'
              AND CAST(chapter_label AS INTEGER) >= ?
              AND CAST(chapter_label AS INTEGER) <= ?
            """,
            (lo, hi),
        )
        conn.commit()
        return int(cur.rowcount or 0)
    except (sqlite3.Error, TypeError, ValueError) as e:
        logger.warning("wiki compile delete episodic failed: %s", e)
        return 0
    finally:
        conn.close()


def maybe_append_changelog_after_supervisor(
    book_root: Path,
    *,
    length_scale: str,
    chapter_index: int,
    supervisor_entry: dict[str, Any] | None,
) -> None:
    if (length_scale or "").strip().lower() != "long":
        return
    if not supervisor_entry or not isinstance(supervisor_entry, dict):
        return
    rev = supervisor_entry.get("review")
    if not isinstance(rev, dict):
        return
    try:
        append_canon_changelog_from_supervisor_review(
            book_root, chapter_index=chapter_index, review=rev
        )
    except OSError:
        logger.debug("changelog append failed", exc_info=True)
