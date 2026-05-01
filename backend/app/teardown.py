"""长篇拆文：拼接 oh-story-claudecode（MIT）story-long-analyze 技能与参考资料为一条 system prompt。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

REFERENCE_FILES = (
    "references/output-templates.md",
    "references/material-decomposition.md",
    "references/deconstruction-notes.md",
)

AI_WRITER_INJECTION = """

---

## 本轮执行约束（AI Writer 注入）

- 只根据用户消息中「待拆正文」块内的文字分析，**不得编造**未出现在该块中的情节、章节或人物发展。
- 若正文明显不足三章或篇幅过短，在报告开头**明确注明**，并仅对已有内容按 templates 能做多少做多少。
- **快速模式**：优先完成 `output-templates.md` 中快速 Phase 2～4；不要假设能读取整本书或用户磁盘上的其他文件。
- **深度模式**在单次回复内尽力结构化；若用户粘贴文本过长，可说明合理分次处理建议，但仍先给出本次能做部分。
"""


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def story_long_analyze_dir() -> Path:
    return _repo_root() / "third_party" / "oh-story-claudecode" / "skills" / "story-long-analyze"


def _read(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _strip_yaml_frontmatter(raw: str) -> str:
    t = raw.lstrip("\ufeff")
    if not t.startswith("---"):
        return t
    parts = t.split("---", 2)
    if len(parts) >= 3:
        return parts[2].lstrip("\n")
    return t


def teardown_framework_ok() -> bool:
    d = story_long_analyze_dir()
    return (d / "SKILL.md").is_file() and all((d / rel).is_file() for rel in REFERENCE_FILES)


@lru_cache(maxsize=1)
def build_oh_story_long_analyze_system() -> str:
    base = story_long_analyze_dir()
    skill_path = base / "SKILL.md"
    if not skill_path.is_file():
        raise FileNotFoundError(
            "未找到拆书框架：缺少 third_party/oh-story-claudecode/skills/story-long-analyze/SKILL.md"
        )
    chunks: list[str] = [_strip_yaml_frontmatter(_read(skill_path))]
    for rel in REFERENCE_FILES:
        p = base / rel
        body = _read(p)
        if not body.strip():
            raise FileNotFoundError(f"未找到拆书参考资料：{rel}")
        chunks.append(f"## 附件：{rel}\n\n{body}")
    return "\n\n---\n\n".join(chunks) + AI_WRITER_INJECTION
