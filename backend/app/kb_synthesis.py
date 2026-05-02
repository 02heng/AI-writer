"""本书 `kb/author-bible-synthesis.md`：按模板结构自动维护的作者圣经总则（每章后刷新）。"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

from .core.logging import get_logger
from .llm import chat_completion

logger = get_logger(__name__)

AUTHOR_BIBLE_SYNTHESIS_NAME = "author-bible-synthesis.md"

_SYNTHESIS_HEADER = (
    "【作者圣经·总则（本书，按 author-bible-template 结构自动维护；"
    "编写正文时优先于「记忆宫殿」采信；与梗概冲突时以**用户全书项目说明（若存在）**优先于梗概，其次梗概）】"
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


def _user_book_note_from_meta(book_path: Path) -> str:
    mp = (book_path / "meta.json").resolve()
    if not mp.is_file():
        return ""
    try:
        meta = json.loads(mp.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(meta.get("user_book_note") or "").strip()


def _skip_synthesis_llm() -> bool:
    return os.environ.get("AIWRITER_SKIP_KB_SYNTHESIS", "").strip() in ("1", "true", "yes")


def _year_section_span(md: str) -> Optional[tuple[int, int]]:
    """「## 年表」起始下一节同类 `## ` 之前的字符区间（含本节首行）。"""
    if not md or "## 年表" not in md:
        return None
    m0 = re.search(r"(?m)^## 年表\s*(?:\n|$)", md)
    if not m0:
        return None
    tail = md[m0.end() :]
    m1 = re.search(r"(?m)^## (?!(?:年表)(?:\s|$))[^\n]+\s*", tail)
    if m1:
        end = m0.end() + m1.start()
    else:
        end = len(md)
    return (m0.start(), end)


def _is_md_table_sep_line(line: str) -> bool:
    if not line.strip().startswith("|"):
        return False
    inner = line.strip().strip("|").replace("|", " ")
    if not inner.strip():
        return False
    return all(c == "-" or c == ":" or c.isspace() for c in inner)


def _row_has_fact_cells(line: str) -> bool:
    if not line.strip().startswith("|"):
        return False
    if _is_md_table_sep_line(line):
        return False
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    return any(len(c) >= 2 for c in cells)


def _count_year_fact_rows(span_text: str) -> int:
    return sum(1 for ln in (span_text or "").splitlines() if _row_has_fact_cells(ln))


def _extract_year_body_after_heading(block_with_h2: str) -> tuple[str, list[str]]:
    """除去首行 ## 年表 → (表前文说明, 连续的 | 表格行列表)"""
    ls = block_with_h2.strip("\n").splitlines()
    if not ls:
        return "", []
    prelude: list[str] = []
    i = 1
    while i < len(ls):
        st = ls[i].strip()
        if st.startswith("|"):
            break
        prelude.append(ls[i])
        i += 1
    tbl: list[str] = []
    while i < len(ls):
        if ls[i].strip().startswith("|"):
            tbl.append(ls[i].rstrip())
            i += 1
            continue
        break
    return ("\n".join(prelude).rstrip("\n"), tbl)


def _split_md_table(hdr_sep_body_lines: list[str]) -> tuple[list[str], Optional[str], list[str]]:
    if not hdr_sep_body_lines:
        return [], None, []
    hdr: list[str] = []
    sep_ln: Optional[str] = None
    body_start = 0
    hdr.append(hdr_sep_body_lines[0].rstrip())
    j = 1
    while j < len(hdr_sep_body_lines):
        ln = hdr_sep_body_lines[j]
        if _is_md_table_sep_line(ln):
            sep_ln = ln.rstrip()
            body_start = j + 1
            break
        hdr.append(ln.rstrip())
        j += 1
    else:
        return hdr, None, hdr_sep_body_lines[1:] if len(hdr_sep_body_lines) > 1 else []

    bodies = [ln.rstrip() for ln in hdr_sep_body_lines[body_start:] if ln.strip()]
    return hdr, sep_ln, bodies


def _row_key_for_dedupe(line: str) -> tuple[str, ...]:
    if not _row_has_fact_cells(line):
        return ()
    cells = tuple(c.strip().lower() for c in line.strip().strip("|").split("|"))
    return cells


def _merge_year_sections(prev_md: str, out_md: str) -> tuple[str, bool]:
    """检测到新版「## 年表里」史实表格行明显减少或整节缺失时，合并回填上一版的行后再写入。"""
    old_sp = _year_section_span(prev_md or "")
    if not old_sp:
        return out_md, False

    old_block = (prev_md or "")[old_sp[0] : old_sp[1]]
    oc = _count_year_fact_rows(old_block)
    if oc < 2:
        return out_md, False

    new_sp = _year_section_span(out_md or "")
    new_block = ""
    if new_sp:
        new_block = (out_md or "")[new_sp[0] : new_sp[1]]
    nc = _count_year_fact_rows(new_block)

    if nc >= oc:
        return out_md, False

    p_o, tbl_o = _extract_year_body_after_heading(old_block)
    p_n, tbl_n = _extract_year_body_after_heading(new_block) if new_block else ("", [])
    prelude = p_n if p_n.strip() else p_o

    h_o, s_o, b_o = _split_md_table(tbl_o)
    h_n, s_n, b_n = _split_md_table(tbl_n)
    hdr_txt = ("\n".join(h_n)).strip() if h_n else ("\n".join(h_o)).strip()
    sep_txt = s_n or s_o or ""
    keys: set[tuple[str, ...]] = set()
    merged_lines: list[str] = []

    def _consume(rows: list[str]) -> None:
        for r in rows:
            if not _row_has_fact_cells(r):
                continue
            kk = _row_key_for_dedupe(r)
            if not kk:
                continue
            if kk in keys:
                continue
            keys.add(kk)
            merged_lines.append(r.strip())

    _consume(list(b_o))
    _consume(list(b_n))

    table_chunk_parts: list[str] = []
    if hdr_txt:
        table_chunk_parts.append(hdr_txt)
    if sep_txt:
        table_chunk_parts.append(sep_txt)
    table_chunk_parts.append("\n".join(merged_lines))
    table_chunk = "\n".join(p for p in table_chunk_parts if p)

    rebuilt = "## 年表\n"
    if prelude:
        rebuilt += "\n" + prelude + "\n"
    rebuilt += "\n" + table_chunk.rstrip("\n") + "\n"

    if not new_sp:
        ins_block = rebuilt.rstrip("\n") + "\n\n"
        md = out_md or ""
        for anchor in ("\n## 规则与世界观", "\n## 伏笔与未决", "\n---"):
            ix = md.find(anchor)
            if ix != -1:
                return md[:ix].rstrip("\n") + "\n\n" + ins_block + md[ix:].lstrip("\n"), True
        return md.rstrip("\n") + "\n\n" + ins_block, True

    patched = out_md[: new_sp[0]] + rebuilt + out_md[new_sp[1] :]
    return patched, True


def refresh_author_bible_synthesis_after_chapter(
    book_path: Path,
    *,
    book_title: str,
    premise: str,
    chapter_index: int,
    chapter_title: str,
    chapter_plain: str,
    temperature: float = 0.32,
    chapter_was_rewrite: bool = False,
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

    ubn = _user_book_note_from_meta(book_path)
    note_block = ""
    if ubn:
        note_block = f"【用户全书项目说明（与梗概或旧总则冲突时以此为最高优先级）】\n{ubn[:4500]}\n\n"

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
        "2b）**年表**：「## 年表」下 Markdown 表中，**必须把上一版总则里已有的实质性史实行逐个保留**，"
        "仅在本章出现后追加新时间线行，或对与梗概/用户说明相悖处做极小删改。**禁止**用空表或未填占位符行替换掉旧的已填行；禁止只根据「当前一章」重写整条年导致其它卷期消失。\n"
        "3）若与【全书梗概】或【用户全书项目说明】冲突：**以用户全书项目说明为最高锚点**，其次梗概；总则中注明待人工核对。\n"
        "4）只输出 Markdown 正文，不要外层代码围栏。"
    )
    user_p = (
        f"【书名】{book_title}\n"
        f"{note_block}"
        f"【全书梗概】\n{(premise or '')[:5000]}\n\n"
        f"【结构模板（须遵循其章节骨架；同仓库 `author-bible-template.md`）】\n{template}\n\n"
        f"【上一版总则（无则当首次撰写；请合并而非丢弃旧信息）】\n{prev or '（尚无。请据梗概与本章建立初版。）'}\n\n"
        f"【当前成稿章节（从中提炼入表、勿整段照抄）】第 {chapter_index} 章「{chapter_title}」\n{ch}\n"
    )
    if chapter_was_rewrite:
        user_p += (
            "\n【重要】本次为「单章重写」触发的总则刷新：本章正文不一定包含全书时间线索。"
            "年表必须与【上一版总则】逐行对齐保留，仅能补充/微调与重写结果直接相关的日期或事件；不得以本章未提及为由删减更早史实行。\n"
        )

    try:
        out = chat_completion(system=sys_p, user=user_p, temperature=temperature).strip()
    except Exception as e:
        logger.warning("kb synthesis LLM failed: %s", e)
        return {"ok": False, "error": str(e)[:300]}

    if len(out) < 80:
        return {"ok": False, "error": "synthesis_too_short"}

    out_patch, yt_merged = _merge_year_sections(prev, out)
    out_final = out_patch
    if yt_merged:
        logger.info("kb synthesis: 已程序合并回填「## 年表」（模型输出史实行少于上一版总则）")

    try:
        sp.write_text(out_final, encoding="utf-8")
    except OSError as e:
        logger.warning("kb synthesis write failed: %s", e)
        return {"ok": False, "error": str(e)[:200]}

    return {"ok": True, "path": str(sp), "chars": len(out_final), "year_table_merged": yt_merged}
