"""结构化伏笔（开放/已收）与续写后同步，减轻长期记忆噪声。"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from .jsonutil import extract_json_object
from .llm import chat_completion

FORESHADOWING_FILENAME = "foreshadowing.json"
_MAX_OPEN = 22
_MAX_RESOLVED_KEEP = 10


def foreshadowing_file(book_root: Path) -> Path:
    return book_root / "memory" / FORESHADOWING_FILENAME


def read_foreshadowing_state(book_root: Path) -> dict[str, Any]:
    p = foreshadowing_file(book_root)
    if not p.is_file():
        return {"version": 1, "hooks": []}
    try:
        raw = p.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError, TypeError):
        return {"version": 1, "hooks": []}
    if not isinstance(data, dict):
        return {"version": 1, "hooks": []}
    hooks = data.get("hooks")
    if not isinstance(hooks, list):
        data["hooks"] = []
    data.setdefault("version", 1)
    return data


def write_foreshadowing_state(book_root: Path, data: dict[str, Any]) -> None:
    p = foreshadowing_file(book_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize_hook_row(h: Any) -> dict[str, Any] | None:
    if not isinstance(h, dict):
        return None
    hid = str(h.get("id") or "").strip()
    if not hid:
        hid = "h_" + uuid.uuid4().hex[:10]
    summary = str(h.get("summary") or "").strip()
    if not summary or len(summary) > 400:
        return None
    st = str(h.get("status") or "open").lower()
    if st not in ("open", "resolved"):
        st = "open"
    opened_at = str(h.get("opened_at") or "").strip()[:40]
    ra = h.get("resolved_at")
    resolved_at: str | int | None
    if ra is None or ra == "":
        resolved_at = None
    else:
        resolved_at = str(ra).strip()[:40] if not isinstance(ra, int) else ra
    notes = str(h.get("notes") or "").strip()[:200]
    return {
        "id": hid[:48],
        "summary": summary,
        "status": st,
        "opened_at": opened_at,
        "resolved_at": resolved_at,
        "notes": notes,
    }


def _cap_hooks(hooks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    open_h = [h for h in hooks if h.get("status") == "open"]
    res_h = [h for h in hooks if h.get("status") == "resolved"]
    open_h = open_h[:_MAX_OPEN]
    res_h = res_h[-_MAX_RESOLVED_KEEP:]
    return open_h + res_h


def foreshadowing_open_hooks_block(book_root: Path, *, max_chars: int = 1400) -> str:
    """仅开放伏笔，供注入正文/续写上下文。"""
    data = read_foreshadowing_state(book_root)
    hooks = data.get("hooks") if isinstance(data.get("hooks"), list) else []
    open_rows = [h for h in hooks if isinstance(h, dict) and h.get("status") == "open"]
    if not open_rows:
        return ""
    lines = ["【结构化伏笔 · 仍开放（须在正文中有交代或按计划收回）】"]
    used = len("\n".join(lines))
    for h in open_rows[:18]:
        line = f"- [{h.get('id', '')}] {h.get('summary', '')}"
        if used + len(line) + 2 > max_chars:
            lines.append("…（其余伏笔见 memory/foreshadowing.json）")
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


def sync_foreshadowing_after_chapter(
    *,
    book_root: Path,
    chapter_label: str,
    chapter_plain: str,
    premise: str,
    temperature: float = 0.28,
) -> dict[str, Any]:
    """
    在本章写完后调用：根据正文更新 hooks 的 open/resolved，并可新增开放线。
    失败时返回原状态不动（不写盘）。
    """
    state = read_foreshadowing_state(book_root)
    prev_hooks = state.get("hooks") if isinstance(state.get("hooks"), list) else []
    prev_slim = [
        {
            "id": str(x.get("id", "")),
            "summary": str(x.get("summary", ""))[:200],
            "status": str(x.get("status", "open")),
            "opened_at": str(x.get("opened_at", "")),
            "resolved_at": x.get("resolved_at"),
        }
        for x in prev_hooks
        if isinstance(x, dict)
    ][:30]
    excerpt = (chapter_plain or "").strip()
    if len(excerpt) > 10000:
        excerpt = excerpt[:10000] + "\n…（截断）"
    sys_p = (
        "你是长篇小说的伏笔管理员。根据【既有钩子】与【本章正文】，维护一份 JSON 伏笔表。\n"
        "## 规则\n"
        "1. 保留 id 不变；若某开放线在本章已被明确解释、收回或否定，将 status 改为 resolved，并填 resolved_at 为本章标签。\n"
        "2. 可新增 open：本章新埋的、后文须交代的线（新 id 用 h_ 加 8～12 位小写字母与数字）。\n"
        "3. 不要为细枝末节每条都建钩；只保留对后文有影响或读者会惦记的线。\n"
        "4. 开放条数尽量不超过 20；已 resolved 的只保留最近若干条作档案即可（可删旧 resolved）。\n"
        "## 输出（仅 JSON）\n"
        '{"hooks":[{"id":"string","summary":"一句","status":"open|resolved",'
        '"opened_at":"章标签或空","resolved_at":null或章标签,"notes":""}]}\n'
        f"本章标签建议：{chapter_label}"
    )
    user_p = (
        f"【全书梗概摘要】\n{(premise or '')[:2200]}\n\n"
        f"【既有钩子】\n{json.dumps(prev_slim, ensure_ascii=False)[:8000]}\n\n"
        f"【本章正文】\n{excerpt}"
    )
    try:
        raw = chat_completion(system=sys_p, user=user_p, temperature=temperature)
        out = extract_json_object(raw)
    except Exception:
        return state
    hooks_raw = out.get("hooks")
    if not isinstance(hooks_raw, list):
        return state
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for h in hooks_raw:
        row = _normalize_hook_row(h)
        if not row or row["id"] in seen:
            continue
        seen.add(row["id"])
        merged.append(row)
    merged = _cap_hooks(merged)
    new_state = {"version": 1, "hooks": merged, "updated_at": time.time()}
    try:
        write_foreshadowing_state(book_root, new_state)
    except OSError:
        return state
    return new_state
