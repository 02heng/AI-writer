"""根据章节正文自动创建空壳角色档案（可后续在界面补全）。"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .character_profiles import create_character_profile, list_characters, load_character_profile
from .core.logging import get_logger
from .jsonutil import extract_json_object
from .llm import chat_completion

logger = get_logger(__name__)


def character_auto_seed_enabled() -> bool:
    v = os.environ.get("AIWRITER_CHARACTER_AUTO_SEED", "").strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    return True


def _existing_names(book_root: Path) -> set[str]:
    names: set[str] = set()
    try:
        for c in list_characters(book_root):
            n = str((c or {}).get("name") or "").strip()
            if n:
                names.add(n)
    except Exception:
        pass
    return names


def suggest_new_character_names(
    *,
    chapter_text: str,
    existing: set[str],
    temperature: float = 0.28,
) -> list[str]:
    """LLM 提取本章值得建档的新专名（配角），排除已有。"""
    ex_list = sorted(existing)[:120]
    sys_p = (
        "你是中文小说编辑。阅读本章正文，找出**本章首次重点刻画、且可能在后文反复出现**的人物专名"
        "（有对白或多次描写者）。勿收录仅一笔带过、明显不会再出现的龙套。\n"
        "排除以下已有档案人物（姓名须完全匹配）：\n"
        + json.dumps(ex_list, ensure_ascii=False)
        + "\n只输出 JSON：{\"names\":[\"张三\",\"李四\"]}，最多 6 个；若无则 {\"names\":[]}。\n"
        "不要输出其它键；姓名须与正文中写法一致。"
    )
    raw = (chapter_text or "")[:10000]
    try:
        raw_llm = chat_completion(system=sys_p, user=raw, temperature=temperature)
        obj = extract_json_object(raw_llm)
        names = obj.get("names")
        if not isinstance(names, list):
            return []
        out: list[str] = []
        for x in names:
            s = str(x).strip()
            if 1 <= len(s) <= 24 and s not in existing:
                out.append(s)
        return out[:6]
    except Exception as e:
        logger.debug("suggest_new_character_names: %s", e)
        return []


def auto_seed_characters_after_chapter(
    book_root: Path,
    *,
    chapter_idx: int,
    chapter_plain_text: str,
) -> list[str]:
    """生成/续写成功后调用：为模型认为的新人物创建空壳档案。"""
    if not character_auto_seed_enabled():
        return []
    if len((chapter_plain_text or "").strip()) < 200:
        return []
    existing = _existing_names(book_root)
    names = suggest_new_character_names(chapter_text=chapter_plain_text, existing=existing)
    created: list[str] = []
    for name in names:
        if load_character_profile(book_root, name):
            continue
        try:
            create_character_profile(
                book_root,
                name=name,
                personality=[],
                notes=f"由第 {chapter_idx} 章正文自动生成骨架；请在界面补全设定。",
                first_appear_chapter=int(chapter_idx),
                validate=False,
            )
            created.append(name)
        except Exception as e:
            logger.warning("auto_seed character %s: %s", name, e)
    return created
