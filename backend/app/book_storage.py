from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException

from .memory_store import init_db as init_memory_db


def books_root(data_root: Path) -> Path:
    p = data_root / "books"
    p.mkdir(parents=True, exist_ok=True)
    return p


def index_path(data_root: Path) -> Path:
    return books_root(data_root) / "index.json"


def _load_index(data_root: Path) -> dict[str, Any]:
    p = index_path(data_root)
    if not p.is_file():
        return {"books": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"books": []}


def _save_index(data_root: Path, data: dict[str, Any]) -> None:
    index_path(data_root).write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def create_book(
    data_root: Path,
    *,
    title: str,
    premise: str,
    plan: dict[str, Any],
    meta_extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    book_id = uuid.uuid4().hex[:12]
    root = books_root(data_root) / book_id
    if root.exists():
        book_id = uuid.uuid4().hex[:12]
        root = books_root(data_root) / book_id
    (root / "chapters").mkdir(parents=True, exist_ok=True)
    (root / "memory").mkdir(parents=True, exist_ok=True)
    (root / "orchestration").mkdir(parents=True, exist_ok=True)

    now = time.time()
    meta = {
        "id": book_id,
        "title": title,
        "created_at": now,
        "updated_at": now,
        "premise": premise,
    }
    if meta_extra:
        meta.update(meta_extra)
    (root / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (root / "plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    init_memory_db(root)
    rollup = root / "memory" / "palace_summary.md"
    if rollup.exists():
        try:
            cur = rollup.read_text(encoding="utf-8")
            if "本书记忆宫殿" not in cur:
                rollup.write_text(
                    "（本书记忆宫殿 · 总摘要）\n\n随章节生成同步更新；可在写作台选择本书后编辑。\n\n" + cur,
                    encoding="utf-8",
                )
        except OSError:
            pass

    idx = _load_index(data_root)
    ch_count = len(plan.get("chapters") or []) if isinstance(plan.get("chapters"), list) else 0
    idx.setdefault("books", []).append(
        {
            "id": book_id,
            "title": title,
            "created_at": now,
            "updated_at": now,
            "chapter_count": ch_count,
        }
    )
    _save_index(data_root, idx)
    return {"book_id": book_id, "path": str(root)}


def list_books(data_root: Path) -> list[dict[str, Any]]:
    idx = _load_index(data_root)
    books = list(idx.get("books") or [])
    books.sort(key=lambda b: float(b.get("updated_at") or b.get("created_at") or 0), reverse=True)
    out = []
    for b in books:
        bid = b.get("id")
        if not bid:
            continue
        root = books_root(data_root) / str(bid)
        if not root.is_dir():
            continue
        try:
            toc = get_toc(data_root, str(bid))
        except HTTPException:
            continue
        out.append(
            {
                "id": bid,
                "title": b.get("title") or bid,
                "chapter_count": len(toc),
                "updated_at": b.get("updated_at"),
            }
        )
    return out


def book_dir(data_root: Path, book_id: str) -> Path:
    safe = re.sub(r"[^a-f0-9]", "", book_id.lower())[:16]
    if len(safe) < 8:
        raise HTTPException(status_code=400, detail="无效的书本 ID")
    p = books_root(data_root) / safe
    if not p.is_dir():
        raise HTTPException(status_code=404, detail="书本不存在")
    return p


def get_meta(data_root: Path, book_id: str) -> dict[str, Any]:
    mp = book_dir(data_root, book_id) / "meta.json"
    if not mp.is_file():
        raise HTTPException(404, "缺少 meta.json")
    return json.loads(mp.read_text(encoding="utf-8"))


def get_plan(data_root: Path, book_id: str) -> dict[str, Any]:
    pp = book_dir(data_root, book_id) / "plan.json"
    if not pp.is_file():
        raise HTTPException(404, "缺少 plan.json")
    return json.loads(pp.read_text(encoding="utf-8"))


def get_toc(data_root: Path, book_id: str) -> list[dict[str, Any]]:
    root = book_dir(data_root, book_id)
    ch_dir = root / "chapters"
    if not ch_dir.is_dir():
        return []
    rows: list[tuple[int, Path]] = []
    for p in ch_dir.glob("*.md"):
        m = re.match(r"^(\d+)\.md$", p.name)
        if m:
            rows.append((int(m.group(1)), p))
    rows.sort(key=lambda x: x[0])
    return [{"n": n, "file": p.name} for n, p in rows]


def read_chapter(data_root: Path, book_id: str, chapter_n: int) -> tuple[str, str]:
    root = book_dir(data_root, book_id)
    fn = f"{int(chapter_n):02d}.md"
    p = root / "chapters" / fn
    if not p.is_file():
        raise HTTPException(404, f"第 {chapter_n} 章不存在")
    return fn, p.read_text(encoding="utf-8")


def write_chapter(data_root: Path, book_id: str, chapter_n: int, content: str) -> Path:
    root = book_dir(data_root, book_id)
    ch = root / "chapters"
    ch.mkdir(parents=True, exist_ok=True)
    fn = f"{int(chapter_n):02d}.md"
    p = ch / fn
    p.write_text(content, encoding="utf-8")
    _touch_book_index(data_root, book_id)
    return p


def update_plan(data_root: Path, book_id: str, plan: dict[str, Any]) -> None:
    book_dir(data_root, book_id)
    p = book_dir(data_root, book_id) / "plan.json"
    p.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    _touch_book_index(data_root, book_id)


def _touch_book_index(data_root: Path, book_id: str) -> None:
    idx = _load_index(data_root)
    now = time.time()
    for b in idx.get("books", []):
        if b.get("id") == book_id:
            b["updated_at"] = now
            toc = get_toc(data_root, book_id)
            b["chapter_count"] = len(toc)
            break
    _save_index(data_root, idx)


def read_orchestration_state(data_root: Path, book_id: str) -> dict[str, Any]:
    p = book_dir(data_root, book_id) / "orchestration" / "state.json"
    if not p.is_file():
        return {"step": "idle", "chapter": 0, "draft_version": 0, "open_issues": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"step": "idle", "chapter": 0, "draft_version": 0, "open_issues": []}


def write_orchestration_state(data_root: Path, book_id: str, state: dict[str, Any]) -> None:
    root = book_dir(data_root, book_id) / "orchestration"
    root.mkdir(parents=True, exist_ok=True)
    (root / "state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def read_memory_summary(data_root: Path, book_id: str) -> str:
    p = book_dir(data_root, book_id) / "memory" / "palace_summary.md"
    if not p.is_file():
        return ""
    return p.read_text(encoding="utf-8")


def write_memory_summary(data_root: Path, book_id: str, text: str) -> None:
    p = book_dir(data_root, book_id) / "memory" / "palace_summary.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
