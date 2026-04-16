"""D 盘（与 AI-writer-data 同级）Analytics 目录：连贯性审核、指标 JSON、说明文件；只读列表与受控读文件。"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from .paths import analytics_root, snapshots_library_dir

_README_REVIEWS = """# 书库连贯性审核

将审核智能体输出的 Markdown / JSON 报告放在本目录（例如 `book-xxx-2026-04-12.md`）。
分析页「书本监督」可一键写入 `supervisor-<书本ID>-<时间>.json`。
分析页「分析」会列出并预览这些文件。

数据根目录与写作 `UserData` 同级，默认在 `D:/AI-writer-data/Analytics/`。
可通过环境变量 `AIWRITER_ANALYTICS_ROOT` 覆盖。
"""

_README_METRICS = """# 读者与平台指标

将 DOM/API 抓取脚本导出的 JSON / JSONL 放在本目录，便于「分析」页统一查看。

桌面版「作家后台 · 定时 DOM 抓取」会在本目录追加 `dom-scrape.jsonl`（每行一条抓取记录）。
"""


def _safe_rel(rel: str) -> str:
    s = (rel or "").strip().replace("\\", "/").lstrip("/")
    if not s or ".." in s.split("/"):
        raise HTTPException(400, "无效路径")
    return s


def _resolve_under(root: Path, rel: str) -> Path:
    root = root.resolve()
    p = (root / rel).resolve()
    try:
        p.relative_to(root)
    except ValueError as e:
        raise HTTPException(400, "路径越界") from e
    return p


def ensure_analytics_layout() -> Path:
    root = analytics_root()
    for sub in ("reviews", "metrics", "state"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    rv = root / "reviews" / "_README.md"
    if not rv.is_file():
        rv.write_text(_README_REVIEWS, encoding="utf-8")
    mt = root / "metrics" / "_README.md"
    if not mt.is_file():
        mt.write_text(_README_METRICS, encoding="utf-8")
    return root


def analytics_info() -> dict[str, Any]:
    root = ensure_analytics_layout()
    snap = snapshots_library_dir()
    return {
        "analytics_root": str(root),
        "snapshots_dir": str(snap),
        "snapshots_exists": snap.is_dir(),
    }


def _file_entry(rel: str, p: Path) -> dict[str, Any]:
    st = p.stat()
    return {
        "rel_path": rel.replace("\\", "/"),
        "name": p.name,
        "is_dir": p.is_dir(),
        "size": st.st_size if p.is_file() else 0,
        "mtime": st.st_mtime,
    }


def list_analytics_items() -> dict[str, Any]:
    root = ensure_analytics_layout()
    sections: list[dict[str, Any]] = []

    def scan_sub(name: str, exts: frozenset[str]) -> list[dict[str, Any]]:
        d = root / name
        out: list[dict[str, Any]] = []
        if not d.is_dir():
            return out
        for p in sorted(d.iterdir(), key=lambda x: x.name):
            if p.name.startswith("."):
                continue
            if p.is_file() and exts and p.suffix.lower() not in exts:
                continue
            rel = f"{name}/{p.name}"
            out.append(_file_entry(rel, p))
        return out

    sections.append({"id": "reviews", "title": "连贯性审核", "items": scan_sub("reviews", frozenset({".md", ".json", ".txt"}))})
    sections.append({"id": "metrics", "title": "指标与抓取记录", "items": scan_sub("metrics", frozenset({".md", ".json", ".jsonl", ".txt"}))})
    sections.append({"id": "state", "title": "调度状态", "items": scan_sub("state", frozenset({".json"}))})

    snap_items: list[dict[str, Any]] = []
    snap = snapshots_library_dir()
    if snap.is_dir():
        for p in sorted(snap.iterdir(), key=lambda x: x.name, reverse=True):
            if not p.is_dir():
                continue
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", p.name):
                continue
            rel = f"__snapshots__/{p.name}"
            snap_items.append(_file_entry(rel, p))
            for f in sorted(p.glob("*.png")):
                relf = f"__snapshots__/{p.name}/{f.name}"
                snap_items.append(_file_entry(relf, f))

    sections.append({"id": "snapshots", "title": "页面快照（按日期）", "items": snap_items})

    return {"sections": sections, "analytics_root": str(root), "snapshots_dir": str(snap)}


def save_supervisor_review_snapshot(
    book_id: str, integrity: dict[str, Any], meta_review: dict[str, Any]
) -> dict[str, Any]:
    """将监督审查结果写入 Analytics/reviews/（JSON）。"""
    root = ensure_analytics_layout()
    reviews = root / "reviews"
    reviews.mkdir(parents=True, exist_ok=True)
    safe_bid = re.sub(r"[^a-f0-9]", "", book_id.lower())[:16] or "unknown"
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    name = f"supervisor-{safe_bid}-{ts}.json"
    p = reviews / name
    payload = {"book_id": book_id, "integrity": integrity, "meta_review": meta_review}
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "filename": name, "rel_path": f"reviews/{name}"}


def analytics_raw_path(rel: str) -> tuple[Path, str]:
    """受控解析二进制预览路径（截图等）。"""
    rel_n = _safe_rel(rel)
    if rel_n.startswith("__snapshots__/"):
        inner = rel_n[len("__snapshots__/") :]
        root = snapshots_library_dir().resolve()
        p = _resolve_under(root, inner)
    else:
        root = analytics_root().resolve()
        p = _resolve_under(root, rel_n)
    if p.is_dir():
        raise HTTPException(400, "不能读取目录")
    if not p.is_file():
        raise HTTPException(404, "文件不存在")
    ext = p.suffix.lower()
    media = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(ext, "application/octet-stream")
    return p, media


def read_analytics_file(rel: str, *, max_bytes: int = 1_500_000) -> dict[str, Any]:
    rel_n = _safe_rel(rel)
    if rel_n.startswith("__snapshots__/"):
        inner = rel_n[len("__snapshots__/") :]
        root = snapshots_library_dir().resolve()
        p = _resolve_under(root, inner)
    else:
        root = analytics_root().resolve()
        p = _resolve_under(root, rel_n)
    if p.is_dir():
        raise HTTPException(400, "不能读取目录")
    if not p.is_file():
        raise HTTPException(404, "文件不存在")
    if p.stat().st_size > max_bytes:
        raise HTTPException(413, "文件过大")
    suffix = p.suffix.lower()
    if suffix == ".json":
        try:
            return {"rel_path": rel_n, "kind": "json", "data": json.loads(p.read_text(encoding="utf-8"))}
        except json.JSONDecodeError:
            return {"rel_path": rel_n, "kind": "text", "content": p.read_text(encoding="utf-8", errors="replace")}
    return {"rel_path": rel_n, "kind": "text", "content": p.read_text(encoding="utf-8", errors="replace")}


def append_metrics_jsonl(record: dict[str, Any]) -> dict[str, Any]:
    """供后续浏览器自动化脚本调用：追加一行 JSON 到 metrics/daily.jsonl。"""
    root = ensure_analytics_layout()
    line_path = root / "metrics" / "daily.jsonl"
    line = json.dumps(record, ensure_ascii=False) + "\n"
    with open(line_path, "a", encoding="utf-8") as f:
        f.write(line)
    return {"ok": True, "path": str(line_path)}
