from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from .memory_hooks import foreshadowing_open_hooks_block
from .memory_relevance import rank_memory_entries, semantic_memory_enabled

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
            "# 记忆宫殿 · 总摘要（衣柜层）\n\n"
            "本区与「近期条目」在勾选「注入长期记忆」时会进入模型上下文（有字数上限）。"
            "请优先写**后续写错代价高**的信息；少写可在正文或书本 `kb/*.md` 里随时查阅的长描写。\n\n"
            "## 建议写入总摘要\n\n"
            "1. **世界观硬规则**：力量/社会/科技或魔法边界、绝不能前后矛盾的设定。\n"
            "2. **人物恒定锚点**：称谓与关系、核心动机、口癖或说话习惯、标志性外貌或道具（短句即可）。\n"
            "3. **开放伏笔**：尚未收回的坑、双关、未解释的物件或承诺（可标注「未解释」或预计收回阶段）。\n"
            "4. **不可逆事实与时间线**：生死、立场转变、大战结果、关键日期与地点（年表式短句）。\n\n"
            "## 尽量少占长期记忆位\n\n"
            "- 本章气氛、具体对白、大段描写（交给上文窗口与章节正文）。\n"
            "- `kb/` 已有设定：此处只写**指针**（如「详见 kb/某某」），避免重复粘贴长文。\n"
            "- 已收尾且无后患的情节：一行标注「某线已闭合」即可。\n\n"
            "## 与近期条目的分工\n\n"
            "- **总摘要**：全书级压缩；流水线会为**每章**追加**极简智能摘要**（概括句，**不**贴原文；**章末收束点**短写清，利接笔）。\n"
            "- **近期条目**：按「房间」存放单条事实、伏笔、人物卡片段；尽量填写「关联章节」便于溯源。\n",
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


def list_entries_for_chapter_range(
    root: Path, chapter_lo: int, chapter_hi: int, *, limit: int = 500
) -> list[dict[str, Any]]:
    """列出 chapter_label 为数字且在 [chapter_lo, chapter_hi] 内的条目（按章序、id）。"""
    init_db(root)
    lo = int(chapter_lo)
    hi = int(chapter_hi)
    if hi < lo or lo < 1:
        return []
    conn = sqlite3.connect(db_path(root))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            """
            SELECT id, created_at, room, title, body, chapter_label
            FROM memory_entries
            WHERE chapter_label GLOB '[0-9]*'
              AND CAST(chapter_label AS INTEGER) >= ?
              AND CAST(chapter_label AS INTEGER) <= ?
            ORDER BY CAST(chapter_label AS INTEGER) ASC, id ASC
            LIMIT ?
            """,
            (lo, hi, int(limit)),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def max_numeric_chapter_label(root: Path) -> int:
    """记忆条目中 chapter_label 为纯数字时的最大章号；无则 0。"""
    init_db(root)
    conn = sqlite3.connect(db_path(root))
    try:
        cur = conn.execute(
            "SELECT MAX(CAST(chapter_label AS INTEGER)) FROM memory_entries "
            "WHERE chapter_label GLOB '[0-9]*'"
        )
        row = cur.fetchone()
        if row and row[0] is not None:
            return int(row[0])
    except (TypeError, ValueError):
        pass
    finally:
        conn.close()
    return 0


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


def prune_episodic_extraction_entries(root: Path, *, keep_last: int) -> dict[str, Any]:
    """
    淘汰「情节」房间中由流水线自动写入的萃取条目（标题含「· 萃取」或「生成同步萃取」），
    按时间新→旧保留 keep_last 条，删除更旧的记录以控制 token。
    """
    if keep_last < 1:
        return {"deleted": 0, "reason": "keep_last<1"}
    init_db(root)
    conn = sqlite3.connect(db_path(root))
    try:
        cur = conn.execute(
            "SELECT id FROM memory_entries WHERE room = ? AND "
            "(title LIKE '%· 萃取%' OR title LIKE '%生成同步萃取%') "
            "ORDER BY created_at DESC, id DESC",
            ("情节",),
        )
        ids = [int(r[0]) for r in cur.fetchall()]
        if len(ids) <= keep_last:
            return {"deleted": 0, "matched": len(ids)}
        drop_ids = ids[keep_last:]
        conn.executemany("DELETE FROM memory_entries WHERE id = ?", [(i,) for i in drop_ids])
        conn.commit()
        return {"deleted": len(drop_ids), "matched": len(ids)}
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


def build_memory_context(
    root: Path,
    *,
    max_chars: int = 4500,
    semantic_query: Optional[str] = None,
    fetch_pool: int = 360,
    inject_foreshadowing: bool = True,
    linear_chapter_window: Optional[int] = None,
) -> str:
    """拼接总摘要 + 近期条目，供注入 user 侧上下文。

    当设置环境变量 AIWRITER_MEMORY_SEMANTIC=1（默认开启）且传入 semantic_query 时，
    从较多条目中按与 query 的字符重叠得分排序后取预算内条目，减轻「上千条只取时间最近」的噪声。

    若 ``linear_chapter_window`` 为正整数（本书流水线默认使用）：**忽略**语义粗排，改为按**章号升序**
    拉取最近若干章内的记忆条目（线性时间线），标题为「近期各章线序」。
    """
    init_db(root)
    parts: list[str] = []
    rollup = read_rollup(root).strip()
    if rollup:
        parts.append("【记忆宫殿 · 总摘要】\n" + rollup)

    if inject_foreshadowing:
        hook_blk = foreshadowing_open_hooks_block(root)
        if hook_blk.strip():
            parts.append(hook_blk)

    use_linear = linear_chapter_window is not None and int(linear_chapter_window) > 0
    use_sem = (
        not use_linear
        and bool(semantic_query and semantic_query.strip() and semantic_memory_enabled())
    )
    entries: list[dict[str, Any]] = []
    section_title = ""

    if use_linear:
        win = max(1, min(int(linear_chapter_window), 500))
        hi = max_numeric_chapter_label(root)
        if hi >= 1:
            lo = max(1, hi - win + 1)
            entries = list_entries_for_chapter_range(root, lo, hi, limit=500)
            section_title = (
                f"【记忆宫殿 · 近期各章线序（约第 {lo}–{hi} 章内条目，章号小→大）】"
            )
        else:
            section_title = "【记忆宫殿 · 近期各章线序（尚无带章号条目，回退为时间新→旧）】"
            entries = list_entries(root, limit=40)
    else:
        entry_limit = fetch_pool if use_sem else 40
        entries = list_entries(root, limit=entry_limit)
        if use_sem:
            entries = rank_memory_entries(entries, semantic_query.strip())
            section_title = "【记忆宫殿 · 与当前任务相关条目（语义粗排）】"
        else:
            section_title = "【记忆宫殿 · 近期条目（抽屉层，新→旧）】"

    if entries:
        lines: list[str] = [section_title]
        budget = max_chars - sum(len(p) + 2 for p in parts)
        used = 0
        for e in entries:
            block = (
                f"- [{e['room']}] {e['title']}"
                + (f"（第{e['chapter_label']}章）" if e.get("chapter_label") else "")
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
        "category": "main",
    },
    {
        "id": "realism",
        "label": "现实主义",
        "description": "当代或近代真实感社会背景。",
        "system_addon": "题材为现实主义：注重生活细节与人物动机，避免超自然解释。",
        "category": "backdrop",
    },
    {
        "id": "fantasy",
        "label": "魔幻 / 西幻",
        "description": "魔法、种族、王国与冒险。",
        "system_addon": "题材为魔幻/西幻：保持魔法与势力设定前后一致。",
        "category": "main",
    },
    {
        "id": "scifi",
        "label": "科幻",
        "description": "近未来或太空文明。",
        "system_addon": "题材为科幻：技术设定自洽并服务主题。",
        "category": "main",
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


_MAX_THEME_TAGS = 14


def normalize_theme_id_list(
    themes: list[dict[str, Any]],
    *,
    theme_ids: Optional[list[str]] = None,
    theme_id: Optional[str] = None,
) -> list[str]:
    """去重并保持顺序；仅保留 themes 中存在的 id；若全无则退回 theme_id / general。"""
    known = {str(t.get("id") or "").strip().lower() for t in themes if t.get("id")}
    out: list[str] = []
    seen: set[str] = set()
    if theme_ids:
        for x in theme_ids:
            tid = str(x or "").strip().lower()
            if not tid or tid not in known or tid in seen:
                continue
            seen.add(tid)
            out.append(tid)
            if len(out) >= _MAX_THEME_TAGS:
                break
    if not out:
        one = str(theme_id or "general").strip().lower()
        if one not in known:
            one = "general"
        out = [one]
    return out


def compose_merged_system_addon(themes: list[dict[str, Any]], ids: list[str]) -> str:
    """按顺序合并多个题材的 system_addon，跳过空串与完全相同段落。"""
    parts: list[str] = []
    seen_text: set[str] = set()
    for tid in ids:
        row = theme_by_id(themes, tid)
        if not row:
            continue
        a = str(row.get("system_addon") or "").strip()
        if not a or a in seen_text:
            continue
        seen_text.add(a)
        parts.append(a)
    return "\n\n".join(parts)


def compose_outline_theme_hints(themes: list[dict[str, Any]], ids: list[str]) -> str:
    """策划/大纲用：多题材的标签与简述。"""
    lines: list[str] = []
    for tid in ids:
        th = theme_by_id(themes, tid)
        if not th:
            continue
        label = str(th.get("label") or tid).strip()
        desc = str(th.get("description") or "").strip()
        if label and desc:
            lines.append(f"{label}：{desc}")
        elif label:
            lines.append(label)
    return " ".join(lines).strip()


def resolve_story_theme_ids(
    plan_data: Optional[dict[str, Any]],
    themes: list[dict[str, Any]],
    *,
    request_theme_ids: Optional[list[str]] = None,
    request_theme_id: Optional[str] = None,
) -> list[str]:
    """续写 / 重写：plan.meta.theme_ids → theme_id → 请求多选 → 请求单选。"""
    if isinstance(plan_data, dict):
        meta = plan_data.get("meta")
        if isinstance(meta, dict):
            raw = meta.get("theme_ids")
            if isinstance(raw, list) and raw:
                out: list[str] = []
                seen: set[str] = set()
                for x in raw:
                    tid = str(x or "").strip().lower()
                    if tid and tid not in seen:
                        seen.add(tid)
                        out.append(tid)
                if out:
                    return normalize_theme_id_list(themes, theme_ids=out, theme_id=None)
            tmeta = str(meta.get("theme_id") or "").strip().lower()
            if tmeta:
                return normalize_theme_id_list(themes, theme_ids=None, theme_id=tmeta)
    return normalize_theme_id_list(
        themes, theme_ids=request_theme_ids, theme_id=request_theme_id
    )
