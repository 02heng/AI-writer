"""从已写章节批量补全/合并本书 characters/ 人物表（须配置 LLM API）。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from .book_storage import clean_stored_chapter_text
from .character_profiles import (
    create_character_profile,
    list_characters,
    load_character_profile,
    update_character_profile,
)
from .jsonutil import extract_json_object
from .llm import chat_completion


def _chapter_plain(raw_md: str) -> str:
    t = clean_stored_chapter_text(raw_md)
    t = re.sub(r"^#+\s*[^\n]+\n+", "", t.strip(), count=1)
    return t.strip()


def _chapter_numbers(chapters_dir: Path) -> list[int]:
    out: list[int] = []
    for p in chapters_dir.glob("*.md"):
        m = re.match(r"^(\d+)\.md$", p.name)
        if m:
            out.append(int(m.group(1)))
    out.sort()
    return out


def sweep_character_chapters_from_plain(book_root: Path) -> dict[str, Any]:
    """
    不调用 LLM：对已建档专名，在 chapters/*.md 正文中扫描首次/最近出现章号并写回档案。
    """
    ch_dir = book_root / "chapters"
    nums = _chapter_numbers(ch_dir)
    if not nums:
        return {"ok": False, "error": "无章节"}
    names = [str(c.get("name") or "").strip() for c in list_characters(book_root)]
    names = [n for n in names if len(n) >= 2]
    names.sort(key=len, reverse=True)
    first: dict[str, int] = {}
    last: dict[str, int] = {}
    for n in nums:
        p = ch_dir / f"{n:02d}.md"
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        plain = _chapter_plain(raw)
        for name in names:
            if name not in plain:
                continue
            last[name] = n
            if name not in first:
                first[name] = n
    fixed = 0
    for name in names:
        if name not in first:
            continue
        prof = load_character_profile(book_root, name)
        if not prof:
            continue
        try:
            update_character_profile(
                book_root,
                name,
                {
                    "first_appear_chapter": int(first[name]),
                    "last_mentioned_chapter": int(last.get(name, first[name])),
                },
            )
            fixed += 1
        except Exception:
            pass
    return {"ok": True, "chapters": len(nums), "profiles_touched": fixed}


def rebuild_character_table_from_chapters(
    book_root: Path,
    *,
    batch_chapters: int = 6,
    temperature: float = 0.22,
) -> dict[str, Any]:
    """
    按章节批调用模型抽取人物专名，写入/合并到 characters/*.json。
    已存在则合并 first_appear（取更小）、补 notes。
    """
    ch_dir = book_root / "chapters"
    if not ch_dir.is_dir():
        return {"ok": False, "error": "无 chapters 目录"}
    nums = _chapter_numbers(ch_dir)
    if not nums:
        return {"ok": False, "error": "无章节文件"}

    merged: dict[str, dict[str, Any]] = {}

    sys_p = (
        "你是中文出版编辑。下面给出若干章小说正文摘录（每段前有章号）。\n"
        "请提取**叙事中出现的人物专名**（2～8 个汉字为宜），含主要配角；"
        "可含历史上真实存在的姓名若正文已当作角色使用。\n"
        "排除：无姓名的纯官职/身份泛称（如单独出现的「一个小太监」「那侍卫」）、仅地名、仅器物名。\n"
        "若同一人有尊称与全名（如「冯公公」与「冯保」），只输出 canonical 全名一条，在 role 里用括号注明常见称呼。\n"
        "输出**仅 JSON**："
        '{"characters":[{"name":"专名","role":"8～30字身份或职能","first_ch":整数章号},...]}'
        "characters 最多 40 条；无则 {\"characters\":[]}。"
    )

    for i in range(0, len(nums), batch_chapters):
        batch = nums[i : i + batch_chapters]
        chunks: list[str] = []
        for n in batch:
            p = ch_dir / f"{n:02d}.md"
            try:
                raw = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            plain = _chapter_plain(raw)[:9000]
            chunks.append(f"=====第{n}章=====\n{plain}")
        user = "\n\n".join(chunks)
        if len(user.strip()) < 120:
            continue
        try:
            raw_llm = chat_completion(system=sys_p, user=user, temperature=temperature)
            data = extract_json_object(raw_llm)
        except Exception:
            continue
        arr = data.get("characters")
        if not isinstance(arr, list):
            continue
        for it in arr:
            if not isinstance(it, dict):
                continue
            name = str(it.get("name") or "").strip()
            if len(name) < 2 or len(name) > 24:
                continue
            role = str(it.get("role") or "").strip()[:120]
            try:
                fc = int(it.get("first_ch"))
            except (TypeError, ValueError):
                fc = batch[0]
            if name not in merged:
                merged[name] = {"role": role, "first_ch": fc}
            else:
                prev = merged[name]
                prev["first_ch"] = min(int(prev["first_ch"]), fc)
                if role and role not in str(prev.get("role") or ""):
                    prev["role"] = (str(prev.get("role") or "") + "；" + role).strip("；")[:200]

    created = 0
    updated = 0
    for name, info in sorted(merged.items(), key=lambda x: (x[1].get("first_ch", 999), x[0])):
        fc = int(info.get("first_ch") or 1)
        role = str(info.get("role") or "").strip()
        prof = load_character_profile(book_root, name)
        if prof:
            try:
                old_fc = int(prof.get("first_appear_chapter") or fc)
            except (TypeError, ValueError):
                old_fc = fc
            new_fc = min(old_fc, fc)
            notes = str(prof.get("notes") or "").strip()
            if role and role not in notes:
                notes = (notes + "；" + role).strip("；")[:500] if notes else role[:500]
            try:
                update_character_profile(
                    book_root,
                    name,
                    {
                        "first_appear_chapter": new_fc,
                        "notes": notes or prof.get("notes", ""),
                    },
                )
                updated += 1
            except Exception:
                pass
        else:
            try:
                create_character_profile(
                    book_root,
                    name=name,
                    notes=role or "（由章节扫描生成，待作者补全）",
                    first_appear_chapter=max(1, fc),
                    arc_stage="setup",
                    validate=False,
                )
                created += 1
                existing.add(name)
            except Exception:
                pass

    return {
        "ok": True,
        "chapters_scanned": len(nums),
        "unique_names": len(merged),
        "profiles_created": created,
        "profiles_updated": updated,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="从 chapters/*.md 批量补全本书 characters/ 人物表")
    ap.add_argument("book_root", type=Path, help="书本根目录（含 chapters/）")
    ap.add_argument("--batch", type=int, default=6, help="每批章节数（仅 LLM 模式）")
    ap.add_argument(
        "--sweep-only",
        action="store_true",
        help="不调用 LLM，仅按正文扫描已建档专名的首见章/最近章并写回（推荐先跑）",
    )
    args = ap.parse_args()
    root = args.book_root.resolve()
    if not root.is_dir():
        print("目录不存在", root, file=sys.stderr)
        sys.exit(1)
    if args.sweep_only:
        r = sweep_character_chapters_from_plain(root)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return
    r = rebuild_character_table_from_chapters(root, batch_chapters=max(2, min(int(args.batch), 12)))
    print(json.dumps(r, ensure_ascii=False, indent=2))
    if r.get("unique_names") == 0:
        print(
            "\n提示：LLM 抽取未得到人物（多为未配置 API 或解析失败）。"
            "可先 `python -m app.character_registry_rebuild <书根> --sweep-only` 校正已建档专名的章号。",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
