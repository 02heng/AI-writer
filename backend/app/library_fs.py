from __future__ import annotations

import re
from pathlib import Path

from fastapi import HTTPException


def safe_out_md_path(root: Path, name: str) -> Path:
    """仅允许读取 userData/out/ 下的 .md 文件。"""
    safe = Path(name).name
    if not safe.lower().endswith(".md"):
        raise HTTPException(status_code=400, detail="仅支持 .md 文件")
    out_root = (root / "out").resolve()
    target = (out_root / safe).resolve()
    if not str(target).startswith(str(out_root)):
        raise HTTPException(status_code=400, detail="非法路径")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return target


def list_out_markdown(root: Path) -> list[dict]:
    out = root / "out"
    if not out.is_dir():
        return []
    items: list[dict] = []
    for p in sorted(out.glob("*.md"), key=lambda x: x.name.lower()):
        try:
            st = p.stat()
            items.append(
                {
                    "name": p.name,
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                }
            )
        except OSError:
            continue
    return items


_CHAPTER_FILE = re.compile(r"^(.+)_第(\d+)章\.md$")
_PLAN_FILE = re.compile(r"^(.+)_策划\.json$")


def safe_series_prefix(raw: str) -> str:
    s = Path(raw.strip()).name
    s = re.sub(r'[<>:"/\\|?*\n\r\t]', "", s).strip()[:80]
    if not s:
        raise HTTPException(status_code=400, detail="无效的书系前缀")
    return s


def list_series(root: Path) -> list[dict]:
    """按「前缀_第NN章.md」聚合书库中的系列，供续写选择。"""
    out = root / "out"
    if not out.is_dir():
        return []

    by_prefix: dict[str, list[tuple[int, str]]] = {}
    for p in out.glob("*.md"):
        m = _CHAPTER_FILE.match(p.name)
        if not m:
            continue
        prefix, n_str = m.group(1), m.group(2)
        try:
            n = int(n_str)
        except ValueError:
            continue
        by_prefix.setdefault(prefix, []).append((n, p.name))

    plan_names: dict[str, str] = {}
    for p in out.glob("*_策划.json"):
        m = _PLAN_FILE.match(p.name)
        if m:
            plan_names[m.group(1)] = p.name

    rows: list[dict] = []
    for prefix, pairs in by_prefix.items():
        pairs.sort(key=lambda x: x[0])
        last_n, last_file = pairs[-1]
        rows.append(
            {
                "prefix": prefix,
                "chapter_count": len(pairs),
                "last_index": last_n,
                "last_chapter_file": last_file,
                "has_plan": prefix in plan_names,
                "plan_file": plan_names.get(prefix),
                "chapters": [{"n": n, "file": fn} for n, fn in pairs],
            }
        )
    rows.sort(key=lambda x: x["prefix"].lower())
    return rows
