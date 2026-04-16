"""跨章剧情提要：按行累积章摘要，满 N 行后 LLM 压缩写入分层文件，避免 running_summary 线性爆炸。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .core.logging import get_logger
from .llm import chat_completion

logger = get_logger(__name__)

TAIL_JSONL = "chapter_tail_snippets.jsonl"
COMPRESSED_MD = "chapter_tail_compressed.md"


def _memory_dir(book_root: Path) -> Path:
    p = book_root / "memory"
    p.mkdir(parents=True, exist_ok=True)
    return p


def append_chapter_tail_snippet(
    book_root: Path,
    *,
    chapter_n: int,
    chapter_title: str,
    snippet: str,
) -> None:
    """追加一章摘要行（JSONL），供后续压缩与注入提示。"""
    mem = _memory_dir(book_root)
    line = json.dumps(
        {
            "n": int(chapter_n),
            "t": (chapter_title or "")[:120],
            "s": (snippet or "").replace("\n", " ").strip()[:500],
        },
        ensure_ascii=False,
    )
    with (mem / TAIL_JSONL).open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _tail_line_count(book_root: Path) -> int:
    p = _memory_dir(book_root) / TAIL_JSONL
    if not p.is_file():
        return 0
    try:
        return sum(1 for _ in p.open("r", encoding="utf-8", errors="replace") if _.strip())
    except OSError:
        return 0


def _compress_every() -> int:
    raw = os.environ.get("AIWRITER_ROLLUP_COMPRESS_EVERY", "").strip()
    if raw.isdigit():
        return max(20, min(int(raw), 500))
    return 100


def _keep_after_compress() -> int:
    """压缩后保留最近未压入分层文件的原始行数。"""
    raw = os.environ.get("AIWRITER_TAIL_KEEP_RECENT", "").strip()
    if raw.isdigit():
        return max(5, min(int(raw), 80))
    return 20


def maybe_compress_chapter_tail(book_root: Path) -> None:
    """
    当 chapter_tail_snippets.jsonl 行数 >= AIWRITER_ROLLUP_COMPRESS_EVERY（默认 100）时，
    将最旧的一批行交给模型压缩，追加到 chapter_tail_compressed.md，并从 jsonl 中移除已压缩行。
    """
    every = _compress_every()
    if _tail_line_count(book_root) < every:
        return
    mem = _memory_dir(book_root)
    path = mem / TAIL_JSONL
    try:
        lines = [ln.strip() for ln in path.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
    except OSError:
        return
    if len(lines) < every:
        return
    compress_n = len(lines) - _keep_after_compress()
    if compress_n < min(30, every // 2):
        return
    batch = lines[:compress_n]
    rest = lines[compress_n:]
    rows: list[dict[str, Any]] = []
    for ln in batch:
        try:
            rows.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    if not rows:
        return
    n0 = int(rows[0].get("n") or 0)
    n1 = int(rows[-1].get("n") or 0)
    blob = "\n".join(f"第{r.get('n')}章「{r.get('t', '')}」：{r.get('s', '')}" for r in rows)
    blob = blob[:24000]
    sys_p = (
        "你是长篇连载编辑。将下列「各章一句提要」压成一段**给下一任作者/模型用的叙事提要**（800–2200 汉字）。\n"
        "必须保留：主线进展、关键人物关系变化、未收伏笔、不可逆事实、时间线节点。\n"
        "不要列表腔，不要逐章复述；不要编造未出现的剧情。只输出提要正文。"
    )
    try:
        digest = chat_completion(system=sys_p, user=blob, temperature=0.32).strip()
    except Exception as e:
        logger.warning("chapter tail compress skipped: %s", e)
        return
    if len(digest) < 80:
        return
    out_md = mem / COMPRESSED_MD
    block = f"\n\n## 第 {n0}–{n1} 章 · 叙事压缩层（自动生成）\n\n{digest}\n"
    try:
        prev = out_md.read_text(encoding="utf-8") if out_md.is_file() else ""
    except OSError:
        prev = ""
    out_md.write_text((prev + block).strip() + "\n", encoding="utf-8")
    rest_objs: list[dict[str, Any]] = []
    for x in rest:
        try:
            rest_objs.append(json.loads(x))
        except json.JSONDecodeError:
            continue
    try:
        with path.open("w", encoding="utf-8") as f:
            for r in rest_objs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    except OSError:
        pass


def load_chapter_tail_for_prompt(book_root: Path, *, max_chars: int = 6800) -> str:
    """拼接压缩层（尾部）+ 未压缩 jsonl 行，用于注入「跨章剧情提要」。"""
    mem = _memory_dir(book_root)
    parts: list[str] = []
    comp = mem / COMPRESSED_MD
    if comp.is_file():
        try:
            ctext = comp.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            ctext = ""
        if ctext:
            if len(ctext) > max_chars // 2:
                ctext = "…(更早压缩层已省略)\n\n" + ctext[-(max_chars // 2 - 30) :]
            parts.append("【叙事压缩层（多章合并）】\n" + ctext)
    tail_lines: list[str] = []
    p = mem / TAIL_JSONL
    if p.is_file():
        try:
            for ln in p.open("r", encoding="utf-8", errors="replace"):
                s = ln.strip()
                if s:
                    tail_lines.append(s)
        except OSError:
            pass
    if tail_lines:
        recent = tail_lines[-60:]
        lines_out: list[str] = []
        for x in recent:
            try:
                r = json.loads(x)
                lines_out.append(f"第{r.get('n', '?')}章「{r.get('t', '')}」：{r.get('s', '')}")
            except json.JSONDecodeError:
                continue
        if lines_out:
            parts.append("【近期章提要（未压缩）】\n" + "\n".join(lines_out))
    text = "\n\n".join(parts).strip()
    if len(text) > max_chars:
        return text[: max_chars - 24] + "\n…(跨章提要已截断)"
    return text
