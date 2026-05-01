#!/usr/bin/env python3
"""将记忆宫殿（长记忆）导出为 PNG：总摘要、设定变更 log、SQLite 抽屉条目。

默认扫描：
  - 全局：<UserData>/memory/
  - 各书：<UserData>/books/<id>/memory/

依赖：Pillow（见 backend/requirements.txt）

用法（在 backend 目录下）::
  python scripts/export_memory_png.py
  python scripts/export_memory_png.py --output D:/Exports/my_mem
  AIWRITER_USER_DATA=E:\\data\\UserData python scripts/export_memory_png.py
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Iterable

# 允许从仓库任意 cwd 运行
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.book_storage import _load_index, books_root  # noqa: E402
from app.memory_store import db_path, init_db, rollup_path  # noqa: E402
from app.paths import user_data_root  # noqa: E402

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError as e:
    raise SystemExit(
        "缺少 Pillow，请安装：pip install Pillow\n"
        "或在 backend 目录执行：pip install -r requirements.txt"
    ) from e


_PAGE_W = 1200
_PAGE_MAX_H = 5600
_MARGIN = 40
_LINE_GAP = 6
_FONT_SIZE = 20
_CHARS_PER_LINE = 48


def _windows_font() -> ImageFont.FreeTypeFont:
    candidates = [
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\msyhbd.ttc"),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
        Path(r"C:\Windows\Fonts\simsun.ttc"),
    ]
    for p in candidates:
        if p.is_file():
            return ImageFont.truetype(str(p), _FONT_SIZE)
    return ImageFont.load_default()


def _safe_slug(s: str, max_len: int = 40) -> str:
    s = (s or "").strip() or "untitled"
    s = re.sub(r'[<>:"/\\|?*]', "_", s)
    s = re.sub(r"\s+", "_", s)
    return s[:max_len]


def _wrap_paragraphs(text: str, width: int = _CHARS_PER_LINE) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        if not para:
            lines.append("")
            continue
        while para:
            chunk = para[:width]
            para = para[width:]
            lines.append(chunk)
    return lines


def _paint_pages(lines: Iterable[str], out_prefix: Path, title: str, font: ImageFont.FreeTypeFont) -> int:
    """把多行文字写成 out_prefix_01.png …；返回页数。"""
    draw_lines = [f"【{title}】", ""] + list(lines)
    line_h = _FONT_SIZE + _LINE_GAP
    max_lines_per_page = max(10, (_PAGE_MAX_H - 2 * _MARGIN) // line_h)

    page_idx = 0
    buf: list[str] = []
    written = 0

    def flush() -> None:
        nonlocal page_idx, buf, written
        if not buf:
            return
        page_idx += 1
        h = 2 * _MARGIN + len(buf) * line_h
        img = Image.new("RGB", (_PAGE_W, h), (255, 255, 255))
        draw = ImageDraw.Draw(img)
        y = _MARGIN
        for ln in buf:
            draw.text((_MARGIN, y), ln, fill=(15, 15, 20), font=font)
            y += line_h
        path = Path(f"{out_prefix}_{page_idx:02d}.png")
        img.save(path, format="PNG", optimize=True)
        written += 1
        buf = []

    for ln in draw_lines:
        if len(buf) >= max_lines_per_page:
            flush()
        buf.append(ln)
    flush()
    return written


def _read_text_file(p: Path) -> str:
    if not p.is_file():
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def _fetch_all_entries(book_root: Path) -> list[dict[str, object]]:
    init_db(book_root)
    dbp = db_path(book_root)
    if not dbp.is_file():
        return []
    conn = sqlite3.connect(dbp)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            "SELECT id, created_at, room, title, body, chapter_label "
            "FROM memory_entries ORDER BY id ASC"
        )
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _entries_as_lines(entries: list[dict[str, object]]) -> list[str]:
    if not entries:
        return ["（无抽屉条目）"]
    lines: list[str] = []
    for e in entries:
        lines.append(
            f"— 条目 id={e.get('id')} room={e.get('room')} "
            f"chapter={e.get('chapter_label')!s} —"
        )
        lines.append(f"标题：{e.get('title')}")
        body = str(e.get("body") or "")
        for bl in body.split("\n"):
            lines.extend(_wrap_paragraphs(bl or " ") if bl else [""])
        lines.append("")
    return lines


def export_scope(book_root: Path, out_dir: Path, label: str, font: ImageFont.FreeTypeFont) -> None:
    mem = book_root / "memory"
    if not mem.is_dir():
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix_base = out_dir / "section"

    # palace_summary.md
    rollup = rollup_path(book_root)
    text = _read_text_file(rollup)
    lines = _wrap_paragraphs(text if text.strip() else "（空）")
    n = _paint_pages(lines, prefix_base.parent / f"{label}_01_rollup", f"{label} · 总摘要（衣柜层）", font)
    print(f"  [{label}] 总摘要 → {n} 页")

    # 其它 md（含 canon_changelog）
    md_seen = {rollup.name.lower()}
    for md in sorted(mem.glob("*.md")):
        if md.name.lower() in md_seen:
            continue
        md_seen.add(md.name.lower())
        body = _read_text_file(md)
        slug = _safe_slug(md.stem)
        n = _paint_pages(
            _wrap_paragraphs(body if body.strip() else "（空）"),
            out_dir / f"02_md_{slug}",
            f"{label} · {md.name}",
            font,
        )
        print(f"  [{label}] {md.name} → {n} 页")

    entries = _fetch_all_entries(book_root)
    n = _paint_pages(
        _entries_as_lines(entries),
        out_dir / f"03_entries",
        f"{label} · 抽屉条目（SQLite 全量 id 升序）",
        font,
    )
    print(f"  [{label}] 抽屉条目 {len(entries)} 条 → {n} 页")


def main() -> int:
    ap = argparse.ArgumentParser(description="导出记忆宫殿为 PNG")
    ap.add_argument(
        "--user-data",
        type=str,
        default="",
        help="UserData 根目录；默认与运行时一致（含 AIWRITER_USER_DATA）",
    )
    ap.add_argument(
        "--output",
        type=str,
        default="",
        help="输出目录；默认 <UserData 上一级>/Exports/memory_png_<时间戳>",
    )
    args = ap.parse_args()

    ud = Path(args.user_data).resolve() if args.user_data.strip() else user_data_root()
    ts = time.strftime("%Y%m%d_%H%M%S")
    if args.output.strip():
        export_root = Path(args.output).expanduser().resolve()
    else:
        export_root = (ud.parent / "Exports" / f"memory_png_{ts}").resolve()

    export_root.mkdir(parents=True, exist_ok=True)
    font = _windows_font()

    print(f"UserData: {ud}")
    print(f"导出至: {export_root}")

    export_scope(ud, export_root / "global", "全局记忆宫殿", font)

    data_root = ud  # books 与 index 在 UserData 下
    br = books_root(data_root)
    idx = _load_index(data_root)
    for b in idx.get("books") or []:
        bid = b.get("id")
        if not bid:
            continue
        book_path = br / str(bid)
        if not book_path.is_dir():
            continue
        title = str(b.get("title") or bid)
        slug = _safe_slug(title)
        export_scope(book_path, export_root / f"book_{bid}_{slug}", f"书《{title}》", font)

    print("完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
