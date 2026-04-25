"""本书 `kb/author-bible-synthesis.md`：按模板结构自动维护的作者圣经总则（每章后刷新）。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .core.logging import get_logger
from .llm import chat_completion

logger = get_logger(__name__)

AUTHOR_BIBLE_SYNTHESIS_NAME = "author-bible-synthesis.md"

_SYNTHESIS_HEADER = (
    "【作者圣经·总则（本书，按 author-bible-template 结构自动维护；"
    "编写正文时优先于「记忆宫殿」采信；与梗概冲突时以梗概与用户说明为准）】"
)


def synthesis_path(book_path: Path) -> Path:
    return (book_path / "kb" / AUTHOR_BIBLE_SYNTHESIS_NAME).resolve()


def default_template_path() -> Path:
    return Path(__file__).resolve().parent / "default_kb" / "author-bible-template.md"


def merge_writer_kb_block(book_path: Path, user_kb_extras: str) -> str:
    """
    拼接注入 writer 的 KB 段：顺序为 **本书结构化总则**（若已有）→ **用户勾选的 UserData/kb 摘录**。
    总则-only、不含记忆宫殿抽屉条目；宫殿（总摘要 + 按章线序条目）由 pipeline 在 KB 之后另段注入。
    """
    parts: list[str] = []
    p = synthesis_path(book_path)
    if p.is_file():
        try:
            text = p.read_text(encoding="utf-8", errors="replace").strip()
            if text:
                parts.append(_SYNTHESIS_HEADER + "\n" + text)
        except OSError:
            pass
    u = (user_kb_extras or "").strip()
    if u:
        parts.append(u)
    return "\n\n".join(parts).strip()


def _skip_synthesis_llm() -> bool:
    return os.environ.get("AIWRITER_SKIP_KB_SYNTHESIS", "").strip() in ("1", "true", "yes")


def refresh_author_bible_synthesis_after_chapter(
    book_path: Path,
    *,
    book_title: str,
    premise: str,
    chapter_index: int,
    chapter_title: str,
    chapter_plain: str,
    temperature: float = 0.32,
) -> dict[str, Any]:
    """
    每章成稿并同步记忆后调用：用 LLM 按 author-bible-template 结构重写本书总则，写入 books/.../kb/author-bible-synthesis.md。
    失败不抛，只打日志，避免拖垮流水线。
    """
    if _skip_synthesis_llm():
        return {"skipped": True, "reason": "AIWRITER_SKIP_KB_SYNTHESIS"}

    tpl_p = default_template_path()
    try:
        template = tpl_p.read_text(encoding="utf-8", errors="replace").strip()
    except OSError as e:
        logger.warning("kb synthesis: no template at %s: %s", tpl_p, e)
        return {"ok": False, "error": "no_template"}

    sp = synthesis_path(book_path)
    sp.parent.mkdir(parents=True, exist_ok=True)
    prev = ""
    if sp.is_file():
        try:
            prev = sp.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            prev = ""

    ch = (chapter_plain or "").strip()
    if len(ch) > 10000:
        ch = ch[:10000] + "…（中略）"

    sys_p = (
        "你是长篇小说作者圣经编辑。必须根据给定「结构模板」输出**一份完整、可独立阅读**的 Markdown 作者圣经总则，"
        "用于后续章节写作时优先于记忆宫殿采信。\n"
        "总则**只**允许包含模板内的结构化栏目：人物卡、年表、规则与世界观、伏笔与未决等；"
        "**不要**从「记忆宫殿」抽屉/条目/总摘要复制流水账，也不要单独写「近期章节线序」类块（那些只在记忆宫殿侧注入）。\n"
        "要求：\n"
        "1）结构须覆盖模板中的各级标题与表格骨架，可增不可删主标题；"
        "表格用 Markdown 表格语法，单元格内用短句；无信息可写「待补」或留空但保留表头。\n"
        "2）在旧总则基础上**合并**本章正文中**可沉淀为设定**的新增/变更：人物、年表、规则、伏笔等；"
        "不要大段复述正文场景描写；不要评价文笔；不要输出 JSON 或应酬话。\n"
        "3）若与【全书梗概】冲突，以梗概为最高锚点，总则中注明待人工核对。\n"
        "4）只输出 Markdown 正文，不要外层代码围栏。"
    )
    user_p = (
        f"【书名】{book_title}\n"
        f"【全书梗概】\n{(premise or '')[:5000]}\n\n"
        f"【结构模板（须遵循其章节骨架；同仓库 `author-bible-template.md`）】\n{template}\n\n"
        f"【上一版总则（无则当首次撰写；请合并而非丢弃旧信息）】\n{prev or '（尚无。请据梗概与本章建立初版。）'}\n\n"
        f"【当前成稿章节（从中提炼入表、勿整段照抄）】第 {chapter_index} 章「{chapter_title}」\n{ch}\n"
    )

    try:
        out = chat_completion(system=sys_p, user=user_p, temperature=temperature).strip()
    except Exception as e:
        logger.warning("kb synthesis LLM failed: %s", e)
        return {"ok": False, "error": str(e)[:300]}

    if len(out) < 80:
        return {"ok": False, "error": "synthesis_too_short"}

    try:
        sp.write_text(out, encoding="utf-8")
    except OSError as e:
        logger.warning("kb synthesis write failed: %s", e)
        return {"ok": False, "error": str(e)[:200]}

    return {"ok": True, "path": str(sp), "chars": len(out)}
