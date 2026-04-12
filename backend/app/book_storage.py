from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException

from .memory_store import init_db as init_memory_db


def books_root(data_root: Path) -> Path:
    """Active books directory. Override with env AIWRITER_BOOKS_ROOT (absolute path)."""
    raw = os.environ.get("AIWRITER_BOOKS_ROOT", "").strip()
    if raw:
        p = Path(raw).expanduser().resolve()
    else:
        p = (data_root / "books").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def trash_root(data_root: Path) -> Path:
    p = (data_root / "books_trash").resolve()
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
    """书库列表（不逐本扫描章节文件，避免上千本书时卡死；章数以 index 为准，必要时回退为 glob 计数）。"""
    idx = _load_index(data_root)
    books = list(idx.get("books") or [])
    books.sort(key=lambda b: float(b.get("updated_at") or b.get("created_at") or 0), reverse=True)
    out = []
    br = books_root(data_root)
    for b in books:
        bid = b.get("id")
        if not bid:
            continue
        root = br / str(bid)
        if not root.is_dir():
            continue
        cc_raw = b.get("chapter_count")
        try:
            cc = int(cc_raw) if cc_raw is not None else -1
        except (TypeError, ValueError):
            cc = -1
        if cc < 0:
            chd = root / "chapters"
            cc = len(list(chd.glob("*.md"))) if chd.is_dir() else 0
        out.append(
            {
                "id": bid,
                "title": b.get("title") or bid,
                "chapter_count": cc,
                "updated_at": b.get("updated_at"),
            }
        )
    return out


def list_books_slice(
    data_root: Path,
    *,
    limit: int = 200,
    offset: int = 0,
    q: str = "",
) -> dict[str, Any]:
    rows = list_books(data_root)
    qq = (q or "").strip().lower()
    if qq:
        rows = [
            b
            for b in rows
            if qq in str(b.get("title") or "").lower() or qq in str(b.get("id") or "").lower()
        ]
    total = len(rows)
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    page = rows[offset : offset + limit]
    return {"books": page, "total": total, "limit": limit, "offset": offset, "q": qq}


def list_trashed_books_slice(
    data_root: Path,
    *,
    limit: int = 200,
    offset: int = 0,
    q: str = "",
) -> dict[str, Any]:
    rows = list_trashed_books(data_root)
    qq = (q or "").strip().lower()
    if qq:
        rows = [
            it
            for it in rows
            if qq in str(it.get("title") or "").lower()
            or qq in str(it.get("id") or "").lower()
            or qq in str(it.get("folder") or "").lower()
        ]
    total = len(rows)
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    page = rows[offset : offset + limit]
    return {"items": page, "total": total, "limit": limit, "offset": offset, "q": qq}


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


def clean_stored_chapter_text(text: str) -> str:
    """Remove legacy HTML comment headers and trim."""
    t = text
    if t.strip().startswith("<!--"):
        close = t.find("-->")
        if close != -1:
            t = t[close + 3 :].lstrip()
    return t.strip()


def _title_from_chapter_file(chapter_path: Path) -> str:
    try:
        raw = chapter_path.read_text(encoding="utf-8")
        body = clean_stored_chapter_text(raw)
        for line in body.split("\n"):
            s = line.strip()
            if s.startswith("#"):
                return re.sub(r"^#+\s*", "", s).strip()[:120] or ""
            if s:
                return ""
    except OSError:
        pass
    return ""


def get_chapter_numbers(data_root: Path, book_id: str) -> list[int]:
    """仅扫描章节文件名得到序号，用于上千章时的上一章/下一章导航，避免加载整本目录元数据。"""
    root = book_dir(data_root, book_id)
    ch_dir = root / "chapters"
    if not ch_dir.is_dir():
        return []
    ns: list[int] = []
    for p in ch_dir.glob("*.md"):
        m = re.match(r"^(\d+)\.md$", p.name)
        if m:
            ns.append(int(m.group(1)))
    ns.sort()
    return ns


def get_toc(data_root: Path, book_id: str) -> list[dict[str, Any]]:
    root = book_dir(data_root, book_id)
    ch_dir = root / "chapters"
    if not ch_dir.is_dir():
        return []
    titles_by_n: dict[int, str] = {}
    try:
        plan = json.loads((root / "plan.json").read_text(encoding="utf-8"))
        chs = plan.get("chapters")
        if isinstance(chs, list):
            for c in chs:
                if not isinstance(c, dict):
                    continue
                try:
                    idx = int(c.get("idx", 0))
                except (TypeError, ValueError):
                    continue
                if idx < 1:
                    continue
                tt = str(c.get("title") or "").strip()
                if tt:
                    titles_by_n[idx] = tt
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass

    rows: list[tuple[int, Path]] = []
    for p in ch_dir.glob("*.md"):
        m = re.match(r"^(\d+)\.md$", p.name)
        if m:
            rows.append((int(m.group(1)), p))
    rows.sort(key=lambda x: x[0])
    out: list[dict[str, Any]] = []
    for n, p in rows:
        title = titles_by_n.get(n) or _title_from_chapter_file(p) or f"第 {n} 章"
        out.append({"n": n, "file": p.name, "title": title})
    return out


def read_chapter(data_root: Path, book_id: str, chapter_n: int) -> tuple[str, str, str]:
    root = book_dir(data_root, book_id)
    fn = f"{int(chapter_n):02d}.md"
    p = root / "chapters" / fn
    if not p.is_file():
        raise HTTPException(404, f"第 {chapter_n} 章不存在")
    raw = p.read_text(encoding="utf-8")
    toc = get_toc(data_root, book_id)
    title = ""
    for row in toc:
        if int(row["n"]) == int(chapter_n):
            title = str(row.get("title") or "")
            break
    display = clean_stored_chapter_text(raw)
    return fn, display, title


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


def _remove_book_from_index(data_root: Path, book_id: str) -> None:
    idx = _load_index(data_root)
    books = [b for b in idx.get("books", []) if b.get("id") != book_id]
    idx["books"] = books
    _save_index(data_root, idx)


def move_book_to_trash(data_root: Path, book_id: str) -> dict[str, Any]:
    safe = re.sub(r"[^a-f0-9]", "", book_id.lower())[:16]
    if len(safe) < 8:
        raise HTTPException(status_code=400, detail="无效的书本 ID")
    br = books_root(data_root)
    src = br / safe
    if not src.is_dir():
        raise HTTPException(status_code=404, detail="书本不存在")
    tr = trash_root(data_root)
    dst = tr / safe
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    shutil.move(str(src), str(dst))
    _remove_book_from_index(data_root, safe)
    return {"ok": True, "book_id": safe}


def list_trashed_books(data_root: Path) -> list[dict[str, Any]]:
    tr = trash_root(data_root)
    out: list[dict[str, Any]] = []
    for p in tr.iterdir():
        if not p.is_dir():
            continue
        mp = p / "meta.json"
        if not mp.is_file():
            continue
        try:
            meta = json.loads(mp.read_text(encoding="utf-8"))
            bid = str(meta.get("id") or p.name)
            st = p.stat()
            out.append(
                {
                    "id": bid,
                    "folder": p.name,
                    "title": meta.get("title") or bid,
                    "deleted_at": st.st_mtime,
                }
            )
        except (OSError, json.JSONDecodeError):
            continue
    out.sort(key=lambda x: float(x.get("deleted_at") or 0), reverse=True)
    return out


def restore_book_from_trash(data_root: Path, folder: str) -> dict[str, Any]:
    folder_safe = Path(folder).name
    tr = trash_root(data_root)
    src = tr / folder_safe
    if not src.is_dir():
        raise HTTPException(status_code=404, detail="回收站中无此项")
    mp = src / "meta.json"
    if not mp.is_file():
        raise HTTPException(status_code=400, detail="缺少 meta.json，无法还原")
    meta = json.loads(mp.read_text(encoding="utf-8"))
    bid = str(meta.get("id") or folder_safe)
    bid = re.sub(r"[^a-f0-9]", "", bid.lower())[:16]
    if len(bid) < 8:
        raise HTTPException(status_code=400, detail="无效的书本 ID")
    br = books_root(data_root)
    dst = br / bid
    if dst.exists():
        raise HTTPException(status_code=400, detail="书本目录已存在，请先处理冲突后再还原")
    shutil.move(str(src), str(dst))
    now = time.time()
    idx = _load_index(data_root)
    idx.setdefault("books", []).append(
        {
            "id": bid,
            "title": meta.get("title") or bid,
            "created_at": float(meta.get("created_at") or now),
            "updated_at": now,
            "chapter_count": len(get_toc(data_root, bid)),
        }
    )
    _save_index(data_root, idx)
    return {"ok": True, "book_id": bid}


def purge_book_from_trash(data_root: Path, folder: str) -> None:
    folder_safe = Path(folder).name
    tr = trash_root(data_root)
    src = tr / folder_safe
    if not src.is_dir():
        raise HTTPException(status_code=404, detail="回收站中无此项")
    shutil.rmtree(src)


def _md_to_plainish(md: str) -> str:
    lines: list[str] = []
    for line in md.split("\n"):
        lines.append(re.sub(r"^#+\s*", "", line.rstrip()))
    return "\n".join(lines).strip()


def export_book_plain_text(data_root: Path, book_id: str) -> str:
    meta = get_meta(data_root, book_id)
    title = str(meta.get("title") or book_id)
    premise = str(meta.get("premise") or "").strip()
    toc = get_toc(data_root, book_id)
    parts: list[str] = [f"《{title}》", ""]
    if premise:
        parts.append(premise)
        parts.extend(["", "———", ""])
    for row in toc:
        n = int(row["n"])
        ch_title = str(row.get("title") or f"第 {n} 章")
        _fn, content, _t = read_chapter(data_root, book_id, n)
        parts.append(f"第 {n} 章 {ch_title}")
        parts.append("")
        parts.append(_md_to_plainish(content))
        parts.extend(["", ""])
    return "\n".join(parts).strip() + "\n"
