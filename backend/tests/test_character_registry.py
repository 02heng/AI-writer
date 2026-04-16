"""人物表注入与章节扫描。"""

from __future__ import annotations

from pathlib import Path

from app.character_profiles import (
    CHARACTER_REGISTRY_INSTRUCTION,
    build_character_registry_block,
    bump_character_mentions_from_plain,
    create_character_profile,
)
from app.character_registry_rebuild import sweep_character_chapters_from_plain


def test_build_character_registry_block_empty(temp_data_dir: Path) -> None:
    t = build_character_registry_block(temp_data_dir, max_chars=2000)
    assert "尚无" in t or "index" in t
    assert "人物" in CHARACTER_REGISTRY_INSTRUCTION


def test_sweep_and_bump(temp_data_dir: Path) -> None:
    ch = temp_data_dir / "chapters"
    ch.mkdir(parents=True)
    (ch / "01.md").write_text("## 一\n\n张三与李四说话。\n", encoding="utf-8")
    (ch / "02.md").write_text("## 二\n\n李四独自。\n", encoding="utf-8")
    create_character_profile(
        temp_data_dir,
        name="张三",
        notes="测试",
        first_appear_chapter=99,
        validate=False,
    )
    create_character_profile(
        temp_data_dir,
        name="李四",
        notes="测试",
        first_appear_chapter=99,
        validate=False,
    )
    r = sweep_character_chapters_from_plain(temp_data_dir)
    assert r.get("ok") is True
    assert r.get("profiles_touched", 0) >= 1

    bump_character_mentions_from_plain(temp_data_dir, 2, "李四和王五")
    # 李四应在正文出现
    from app.character_profiles import load_character_profile

    p = load_character_profile(temp_data_dir, "李四")
    assert p is not None
    assert int(p.get("last_mentioned_chapter") or 0) >= 2
