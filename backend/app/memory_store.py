from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

DB_NAME = "palace.sqlite3"
ROLLUP_NAME = "palace_summary.md"


def db_path(root: Path) -> Path:
    return root / "memory" / DB_NAME


def rollup_path(root: Path) -> Path:
    return root / "memory" / ROLLUP_NAME


def init_db(root: Path) -> None:
    (root / "memory").mkdir(parents=True, exist_ok=True)
    p = db_path(root)
    conn = sqlite3.connect(p)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_entries (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              created_at REAL NOT NULL,
              room TEXT NOT NULL,
              title TEXT NOT NULL,
              body TEXT NOT NULL,
              chapter_label TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_created ON memory_entries (created_at DESC)"
        )
        conn.commit()
    finally:
        conn.close()

    rp = rollup_path(root)
    if not rp.exists():
        rp.write_text(
            "（记忆宫殿 · 总摘要 / 衣柜层）\n\n"
            "在此手写全书级压缩摘要：主线、核心人物状态、未解伏笔、已发生关键事件时间线。\n"
            "生成正文时若勾选「引用长期记忆」，此处会与近期条目一并注入模型上下文。\n",
            encoding="utf-8",
        )


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "room": row["room"],
        "title": row["title"],
        "body": row["body"],
        "chapter_label": row["chapter_label"],
    }


def list_entries(root: Path, *, limit: int = 80) -> list[dict[str, Any]]:
    init_db(root)
    conn = sqlite3.connect(db_path(root))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            "SELECT id, created_at, room, title, body, chapter_label "
            "FROM memory_entries ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def add_entry(
    root: Path,
    *,
    room: str,
    title: str,
    body: str,
    chapter_label: Optional[str] = None,
) -> dict[str, Any]:
    init_db(root)
    ts = time.time()
    conn = sqlite3.connect(db_path(root))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(
            "INSERT INTO memory_entries (created_at, room, title, body, chapter_label) VALUES (?,?,?,?,?)",
            (ts, room.strip() or "未分类", title.strip() or "无标题", body.strip(), chapter_label),
        )
        conn.commit()
        cur = conn.execute(
            "SELECT id, created_at, room, title, body, chapter_label FROM memory_entries WHERE id = last_insert_rowid()"
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row else {}
    finally:
        conn.close()


def delete_entry(root: Path, entry_id: int) -> bool:
    init_db(root)
    conn = sqlite3.connect(db_path(root))
    try:
        cur = conn.execute("DELETE FROM memory_entries WHERE id = ?", (entry_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def read_rollup(root: Path) -> str:
    init_db(root)
    p = rollup_path(root)
    if not p.is_file():
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def write_rollup(root: Path, text: str) -> None:
    init_db(root)
    rollup_path(root).write_text(text, encoding="utf-8")


def build_memory_context(root: Path, *, max_chars: int = 4500) -> str:
    """拼接总摘要 + 近期条目，供注入 user 侧上下文。"""
    init_db(root)
    parts: list[str] = []
    rollup = read_rollup(root).strip()
    if rollup:
        parts.append("【记忆宫殿 · 总摘要】\n" + rollup)

    entries = list_entries(root, limit=40)
    if entries:
        lines: list[str] = ["【记忆宫殿 · 近期条目（抽屉层，新→旧）】"]
        budget = max_chars - sum(len(p) + 2 for p in parts)
        used = 0
        for e in entries:
            block = (
                f"- [{e['room']}] {e['title']}"
                + (f"（{e['chapter_label']}）" if e.get("chapter_label") else "")
                + f"\n  {e['body']}"
            )
            if used + len(block) > budget:
                lines.append("…（条目过长已截断，可在界面中整理总摘要）")
                break
            lines.append(block)
            used += len(block) + 1
        parts.append("\n".join(lines))

    text = "\n\n".join(parts).strip()
    if len(text) > max_chars:
        return text[: max_chars - 20] + "\n…(已截断)"
    return text


_MINIMAL_THEMES: list[dict[str, Any]] = [
    {
        "id": "general",
        "label": "通用 / 不限定",
        "description": "不额外强调题材，由梗概与知识库主导。",
        "system_addon": "",
    },
    {
        "id": "realism",
        "label": "现实主义",
        "description": "当代或近代真实感社会背景。",
        "system_addon": "题材为现实主义：注重生活细节与人物动机，避免超自然解释。",
    },
    {
        "id": "fantasy",
        "label": "魔幻 / 西幻",
        "description": "魔法、种族、王国与冒险。",
        "system_addon": "题材为魔幻/西幻：保持魔法与势力设定前后一致。",
    },
    {
        "id": "scifi",
        "label": "科幻",
        "description": "近未来或太空文明。",
        "system_addon": "题材为科幻：技术设定自洽并服务主题。",
    },
]


def load_themes(package_dir: Path) -> list[dict[str, Any]]:
    """从 data/themes.json 加载；若缺失、损坏或为空则回退内置列表。"""
    candidates = [
        package_dir / "data" / "themes.json",
        Path(__file__).resolve().parent / "data" / "themes.json",
    ]
    for p in candidates:
        if not p.is_file():
            continue
        try:
            raw = p.read_text(encoding="utf-8-sig")
            data = json.loads(raw)
            if isinstance(data, list) and len(data) > 0:
                return data
        except (OSError, json.JSONDecodeError, ValueError):
            continue
    return list(_MINIMAL_THEMES)


def theme_by_id(themes: list[dict[str, Any]], theme_id: Optional[str]) -> Optional[dict[str, Any]]:
    if not theme_id:
        return next((t for t in themes if t.get("id") == "general"), None)
    for t in themes:
        if t.get("id") == theme_id:
            return t
    return None
