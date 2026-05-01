"""拆书 v2：拆开头 + 蒸馏作者，生成可注入 AI-writer 记忆与独立 SKILL。"""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from .core.logging import get_logger
from .llm import chat_completion

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 拆开头：分析作品开头的写法、笔法、激发读者兴趣的技巧
# ---------------------------------------------------------------------------

def build_opening_teardown_system() -> str:
    """拆开头 system prompt：聚焦作品前几章的写法拆解。"""
    return (
        "你是一位资深中文网文编辑与写作教练，擅长拆解作品开头的写作技巧。\n\n"
        "## 任务\n\n"
        "根据用户提供的作品开头节选（通常为前1～3章），从以下维度进行深度拆解分析：\n\n"
        "### 1. 开篇钩子设计\n"
        "- 前300字内用了什么手法勾住读者？（悬念/冲突/奇观/情感/反差/信息差）\n"
        "- 读者的第一个「翻页动机」是什么？延迟兑现了多久？\n"
        "- 是否有「日常裂口」或「世界观裂口」？裂口的具体呈现方式？\n\n"
        "### 2. 入戏节奏与结构\n"
        "- 开头几章的节奏曲线（快起/慢热/波浪式）\n"
        "- 每章内的「爽点/情绪回报」出现在什么位置？间隔大约多少字？\n"
        "- 章末钩子类型（悬念/反转/情感/信息差揭露）\n"
        "- 从第一章到第三章，读者认知递进了几层？\n\n"
        "### 3. 笔法与叙事技巧\n"
        "- 句式特征：短句推进 vs 长句铺氛围的比例\n"
        "- 对话与描写的比例\n"
        "- 叙事距离：贴近角色内心 vs 冷眼旁白\n"
        "- 信息露出方式：通过冲突/对话/动作/内心独白\n"
        "- 环境描写的克制程度与手法\n\n"
        "### 4. 人物立人设技巧\n"
        "- 主角在前几章如何立住？（通过什么事件/选择/细节让读者记住）\n"
        "- 配角出场方式与功能\n"
        "- 人物关系的建立与张力\n\n"
        "### 5. 可执行规则提炼（Do / Don't）\n"
        "- 从样本中归纳出 3～5 条「Do」（必须做到的）\n"
        "- 从样本中归纳出 3～5 条「Don't」（必须避免的）\n"
        "- 这些规则须可独立执行，不依赖「读过原书才懂」\n\n"
        "### 6. 开头模板（填空式）\n"
        "给出一个可复用的开头模板，格式：\n"
        "「当需要写<类型>开头时：先…再…最后…」\n\n"
        "---\n\n"
        "## 输出要求\n\n"
        "- 用中文输出\n"
        "- 使用 Markdown 格式，带清晰小标题\n"
        "- 每个维度给出具体引用（标注段落位置）\n"
        "- Do/Don't 必须可独立执行，不依赖读过原书\n"
        "- 结尾附一段「如果我要模仿这个开头」的实操建议（150字内）\n"
        "- 不要输出 JSON\n"
    )


# ---------------------------------------------------------------------------
# 蒸馏作者：从作品全文/大量节选中提取作者画像
# ---------------------------------------------------------------------------

def build_author_distill_system() -> str:
    """蒸馏作者 system prompt：从作品中提炼作者风格画像。"""
    return (
        "你是一位专业的文学研究者与写作风格分析师。你的任务是从用户提供的小说全文或大段节选中，"
        "蒸馏出原作者的写作风格画像，用于后续创作中作为「虚拟作者」滤光镜。\n\n"
        "## 分析维度\n\n"
        "### 1. 作者经历推测\n"
        "- 从作品中可以推测出作者什么样的经历背景？（职业、地域、知识领域）\n"
        "- 作者在哪些领域有明显的一手经验？（写作中自然流露的专业知识）\n"
        "- 作者的情感模式：偏好什么类型的情感表达？\n\n"
        "### 2. 笔法特征\n"
        "- **句式偏好**：短句/长句比例、断句习惯、段落长度分布\n"
        "- **叙事视角**：常用的叙事距离与视角切换习惯\n"
        "- **信息密度**：每段推进信息的习惯（高密度/低密度/波浪式）\n"
        "- **环境描写**：描写的克制度与方式（白描/工笔/点到即止）\n"
        "- **对话风格**：对话占比、潜台词使用频率、对话节奏特征\n"
        "- **比喻与修辞**：使用频率、偏好类型、克制程度\n"
        "- **幽默/吐槽**：有无幽默感，什么类型（冷幽默/吐槽/黑色幽默/无）\n\n"
        "### 3. 用词特征\n"
        "- **高频用词**：列出10～20个作者偏好使用的词或表达方式\n"
        "- **禁用词倾向**：作者明显回避的表达（如不用成语、不用四字词等）\n"
        "- **语气词习惯**：对话中常用的语气词特征\n"
        "- **文风标签**：用3～5个关键词概括此作者的文风\n\n"
        "### 4. 节奏与结构习惯\n"
        "- 章节长度偏好\n"
        "- 节奏控制手法（快切/慢抒/波浪式推进）\n"
        "- 章末收束习惯\n\n"
        "### 5. 情感滤光镜\n"
        "- 作者内在驱动力推测：从人物欲望/恐惧中推测作者本人的执念\n"
        "- 偏好的情感类型（孤独/暗恋/使命感/被轻视/和解等）\n"
        "- 处理情感的方式（内敛/外放/克制/浓烈）\n\n"
        "---\n\n"
        "## 输出格式\n\n"
        "用中文输出，Markdown 格式，带清晰小标题。\n\n"
        "在输出末尾，生成一段**虚拟作者人设卡片**（200字内），格式如下：\n"
        "```\n"
        "【虚拟作者·蒸馏画像】\n"
        "文风标签：<标签1>、<标签2>、<标签3>…\n"
        "叙事滤光：<一句话概括此作者看世界的独特视角>\n"
        "笔法摘要：<此作者最核心的3个写作习惯>\n"
        "情感基调：<此作者作品的情感底色>\n"
        "```\n\n"
        "不要输出 JSON，不要复述原作情节大段内容。\n"
    )


def build_author_skill_template(
    author_name: str,
    distill_text: str,
    book_title: str = "",
) -> str:
    """从蒸馏结果生成一个独立的 SKILL.md 内容。"""
    # 提取虚拟作者人设卡片
    card_match = re.search(
        r"【虚拟作者[·.]蒸馏画像】[\s\S]*?(?=\n##|\n---|\Z)",
        distill_text,
        re.DOTALL,
    )
    card_section = card_match.group(0).strip() if card_match else ""

    # 提取文风标签
    tags_match = re.search(r"文风标签[：:]\s*(.+?)(?:\n|$)", distill_text)
    style_tags = tags_match.group(1).strip() if tags_match else "待补充"

    # 提取笔法摘要
    technique_match = re.search(r"笔法摘要[：:]\s*(.+?)(?:\n|$)", distill_text)
    technique_summary = technique_match.group(1).strip() if technique_match else "待补充"

    # 提取情感基调
    emotion_match = re.search(r"情感基调[：:]\s*(.+?)(?:\n|$)", distill_text)
    emotion_tone = emotion_match.group(1).strip() if emotion_match else "待补充"

    # 提取叙事滤光
    filter_match = re.search(r"叙事滤光[：:]\s*(.+?)(?:\n|$)", distill_text)
    narrative_filter = filter_match.group(1).strip() if filter_match else "待补充"

    skill_md = (
        f"---\n"
        f"name: author-distill-{_slugify(author_name)}\n"
        f"description: >-\n"
        f"  蒸馏自《{book_title}》作者「{author_name}」的写作风格画像。"
        f"用于虚拟作者滤光镜——在写作台中调用此 SKILL，使 AI 写出的文字带有此作者的笔法、节奏和情感底色。\n"
        f"tags: [{style_tags}]\n"
        f"---\n\n"
        f"# 虚拟作者：{author_name}\n\n"
        f"## 使用说明\n\n"
        f"本 SKILL 蒸馏自{author_name}的写作风格。在写作台中调用后，AI 将以此作者的笔法和情感滤光进行创作。\n\n"
        f"## 画像速览\n\n"
        f"- **文风标签**：{style_tags}\n"
        f"- **叙事滤光**：{narrative_filter}\n"
        f"- **笔法摘要**：{technique_summary}\n"
        f"- **情感基调**：{emotion_tone}\n\n"
        f"## 虚拟作者人设卡片\n\n"
        f"{card_section}\n\n"
        f"## 详细蒸馏报告\n\n"
        f"{distill_text}\n\n"
        f"## 写作时注入规则\n\n"
        f"当调用此 SKILL 时，AI 应遵守以下规则：\n\n"
        f"1. **句式与节奏**：参照上方「笔法特征」中描述的句式偏好和节奏习惯\n"
        f"2. **用词风格**：参照上方「用词特征」中的高频用词和禁用词倾向\n"
        f"3. **情感底色**：参照上方「情感滤光镜」中的情感基调和处理方式\n"
        f"4. **叙事距离**：参照上方「笔法特征」中的叙事视角偏好\n"
        f"5. **对话风格**：参照上方「笔法特征」中的对话风格描述\n\n"
        f"## 约束\n\n"
        f"- 此 SKILL 仅为风格参考，**不得**照搬原作情节或原句\n"
        f"- 若用户全书项目说明与本 SKILL 冲突，以用户说明为准\n"
        f"- 本 SKILL 可与主题（theme）叠加使用\n"
    )
    return skill_md


def _slugify(name: str) -> str:
    """将中文作者名转为安全的文件名 slug。"""
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "-", name).strip("-")
    return s[:60] or "unknown"


# ---------------------------------------------------------------------------
# 标签 -> 主题匹配
# ---------------------------------------------------------------------------

def match_themes_by_tags(
    tags: list[str],
    themes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """根据用户输入的标签，匹配现有主题中相近或相同的。"""
    if not tags:
        return []
    matched: list[dict[str, Any]] = []
    tag_set = {t.strip().lower() for t in tags if t.strip()}
    for theme in themes:
        tid = str(theme.get("id", "")).lower()
        label = str(theme.get("label", "")).lower()
        desc = str(theme.get("description", "")).lower()
        addon = str(theme.get("system_addon", "")).lower()
        searchable = f"{tid} {label} {desc} {addon}"
        for tag in tag_set:
            if tag in searchable or any(
                kw in searchable for kw in _expand_tag(tag)
            ):
                matched.append(theme)
                break
    return matched


def _expand_tag(tag: str) -> list[str]:
    """将标签扩展为同义关键词列表。"""
    expansions: dict[str, list[str]] = {
        "都市": ["都市", "职场", "现代", "城市"],
        "言情": ["言情", "情感", "恋爱", "爱情", "甜宠", "虐恋"],
        "玄幻": ["玄幻", "奇幻", "魔幻"],
        "仙侠": ["仙侠", "修真", "修仙"],
        "科幻": ["科幻", "未来", "太空", "技术"],
        "悬疑": ["悬疑", "推理", "侦探", "惊悚", "恐怖"],
        "历史": ["历史", "古代", "宫廷", "朝堂"],
        "末世": ["末世", "废土", "末日"],
        "游戏": ["游戏", "无限流", "副本"],
        "穿越": ["穿越", "重生", "系统"],
        "赛博": ["赛博", "赛博朋克", "cyberpunk"],
        "克苏鲁": ["克苏鲁", "宇宙恐怖", "lovecraft"],
        "同人": ["同人", "衍生", "二创", "fanwork"],
        "现实": ["现实", "写实", "现实主义"],
    }
    result = [tag]
    for key, synonyms in expansions.items():
        if tag in synonyms or tag == key:
            result.extend(synonyms)
    return result


def build_new_theme_from_tags(
    tags: list[str],
    book_title: str = "",
    author_name: str = "",
) -> dict[str, Any]:
    """当标签没有匹配到现有主题时，根据标签自动生成新主题配置。"""
    tag_str = ", ".join(tags[:5])
    tid = _slugify("-".join(tags[:3])) or "custom"
    return {
        "id": f"teardown_{tid}",
        "label": f"拆书·{tag_str}",
        "description": (
            f"来源：拆书分析"
            f"{f'《{book_title}》' if book_title else ''}"
            f"{f' 作者：{author_name}' if author_name else ''}。"
            f"标签：{tag_str}"
        ),
        "system_addon": "",
    }


# ---------------------------------------------------------------------------
# LLM 调用封装
# ---------------------------------------------------------------------------

def teardown_opening(
    excerpt: str,
    *,
    book_title: str = "",
    author: str = "",
    tags: list[str] | None = None,
    temperature: float = 0.35,
) -> dict[str, Any]:
    """拆开头：分析作品开头的写作技巧。"""
    system = build_opening_teardown_system()
    parts: list[str] = []
    if book_title:
        parts.append(f"【书名】{book_title}")
    if author:
        parts.append(f"【作者】{author}")
    if tags:
        parts.append(f"【用户标签】{', '.join(tags)}")
    parts.append(f"\n=== 待拆正文节选 ===\n\n{excerpt.strip()}")
    user = "\n".join(parts)

    try:
        text = chat_completion(
            system=system,
            user=user,
            temperature=temperature,
        )
    except RuntimeError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": f"模型调用失败: {e}"}

    return {"ok": True, "text": text.strip()}


def distill_author(
    excerpt: str,
    *,
    book_title: str = "",
    author_name: str = "",
    tags: list[str] | None = None,
    temperature: float = 0.38,
) -> dict[str, Any]:
    """蒸馏作者：从作品中提炼作者风格画像。"""
    system = build_author_distill_system()

    # 智能截断：超长文本取头/中/尾样本，保留作者风格的多样性
    raw = excerpt.strip()
    total_chars = len(raw)
    MAX_CHARS = 60_000  # 送入 LLM 的上限
    if total_chars > MAX_CHARS:
        chunk = MAX_CHARS // 3
        head = raw[:chunk]
        mid_start = (total_chars - chunk) // 2
        middle = raw[mid_start:mid_start + chunk]
        tail = raw[-chunk:]
        sample = (
            f"【开头部分（约前 {chunk} 字）】\n{head}\n\n"
            f"【中间部分（约第 {mid_start}–{mid_start + chunk} 字）】\n{middle}\n\n"
            f"【结尾部分（约末 {chunk} 字）】\n{tail}"
        )
        truncation_note = (
            f"\n\n⚠️ 原文共 {total_chars:,} 字，已截取头/中/尾各约 {chunk:,} 字作为样本。"
            f"请基于此样本尽可能准确地蒸馏作者风格。"
        )
    else:
        sample = raw
        truncation_note = ""

    parts: list[str] = []
    if book_title:
        parts.append(f"【书名】{book_title}")
    if author_name:
        parts.append(f"【作者署名】{author_name}")
    if tags:
        parts.append(f"【用户标签】{', '.join(tags)}")
    parts.append(f"\n=== 待蒸馏正文（请从以下文字中提炼作者画像）==={truncation_note}\n\n{sample}")
    user = "\n".join(parts)

    try:
        text = chat_completion(
            system=system,
            user=user,
            temperature=temperature,
        )
    except RuntimeError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": f"模型调用失败: {e}"}

    # 生成 SKILL.md 内容
    skill_content = build_author_skill_template(
        author_name=author_name or "未知作者",
        distill_text=text.strip(),
        book_title=book_title,
    )

    return {
        "ok": True,
        "distill_text": text.strip(),
        "skill_content": skill_content,
        "author_name": author_name or "未知作者",
    }


# ---------------------------------------------------------------------------
# 蒸馏历史存储：按作者名管理多篇作品的蒸馏结果
# ---------------------------------------------------------------------------

AUTHOR_DISTILLS_DIR = "author_distills"  # 存放在 UserData 下


def _distill_index_path(root: Path) -> Path:
    """蒸馏索引文件路径：记录每位作者的多篇蒸馏。"""
    return root / AUTHOR_DISTILLS_DIR / "_index.json"


def _load_distill_index(root: Path) -> dict[str, list[dict[str, Any]]]:
    """加载索引。返回 {author_name: [蒸馏记录, ...]}。"""
    p = _distill_index_path(root)
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_distill_index(root: Path, index: dict[str, list[dict[str, Any]]]) -> None:
    p = _distill_index_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")


def save_distill_record(
    root: Path,
    *,
    author_name: str,
    book_title: str,
    distill_text: str,
    skill_content: str,
) -> dict[str, Any]:
    """保存一次蒸馏结果到历史记录。返回记录元数据。"""
    record_id = uuid.uuid4().hex[:12]
    ts = time.time()

    # 保存详细报告
    detail_dir = root / AUTHOR_DISTILLS_DIR / _slugify(author_name)
    detail_dir.mkdir(parents=True, exist_ok=True)
    detail_path = detail_dir / f"{record_id}.md"
    detail_path.write_text(distill_text, encoding="utf-8")

    record = {
        "id": record_id,
        "author_name": author_name,
        "book_title": book_title,
        "timestamp": ts,
        "detail_file": str(detail_path.relative_to(root)),
        "merged": False,
    }

    index = _load_distill_index(root)
    if author_name not in index:
        index[author_name] = []
    index[author_name].append(record)
    _save_distill_index(root, index)

    return record


def save_merged_distill_record(
    root: Path,
    *,
    author_name: str,
    merged_text: str,
    skill_content: str,
    source_record_ids: list[str],
) -> dict[str, Any]:
    """保存合并蒸馏结果，替换已有的合并记录。"""
    record_id = uuid.uuid4().hex[:12]
    ts = time.time()

    detail_dir = root / AUTHOR_DISTILLS_DIR / _slugify(author_name)
    detail_dir.mkdir(parents=True, exist_ok=True)
    detail_path = detail_dir / f"{record_id}.md"
    detail_path.write_text(merged_text, encoding="utf-8")

    record = {
        "id": record_id,
        "author_name": author_name,
        "book_title": f"合并蒸馏（{len(source_record_ids)} 篇）",
        "timestamp": ts,
        "detail_file": str(detail_path.relative_to(root)),
        "merged": True,
        "source_record_ids": source_record_ids,
    }

    index = _load_distill_index(root)
    if author_name not in index:
        index[author_name] = []
    # 移除旧的合并记录（同一作者只保留最新一次合并）
    index[author_name] = [r for r in index[author_name] if not r.get("merged")]
    index[author_name].append(record)
    _save_distill_index(root, index)

    return record


def list_distill_records(root: Path, author_name: str) -> list[dict[str, Any]]:
    """列出某作者的所有蒸馏记录。"""
    index = _load_distill_index(root)
    return index.get(author_name, [])


def list_all_distill_authors(root: Path) -> list[dict[str, Any]]:
    """列出所有已蒸馏过的作者及其作品数。"""
    index = _load_distill_index(root)
    result = []
    for author_name, records in index.items():
        result.append({
            "author_name": author_name,
            "count": len(records),
            "books": [r.get("book_title", "") for r in records],
        })
    return result


def read_distill_detail(root: Path, author_name: str, record_id: str) -> str:
    """读取某次蒸馏的详细报告。"""
    index = _load_distill_index(root)
    records = index.get(author_name, [])
    for r in records:
        if r.get("id") == record_id:
            detail_file = r.get("detail_file", "")
            if detail_file:
                p = root / detail_file
                if p.is_file():
                    return p.read_text(encoding="utf-8", errors="replace")
    return ""


# ---------------------------------------------------------------------------
# 合并蒸馏：将同一作者的多篇蒸馏结果合并为一个综合画像
# ---------------------------------------------------------------------------

def build_merge_distill_system() -> str:
    """合并蒸馏的 system prompt。"""
    return (
        "你是一位专业的文学研究者与写作风格分析师。\n\n"
        "## 任务\n\n"
        "以下是对同一位作者不同作品的多次蒸馏报告。请将这些报告合并为一个**统一、完整**的作者风格画像。\n\n"
        "## 合并规则\n\n"
        "1. **去重**：相同或高度相似的笔法特征、用词习惯只保留一次\n"
        "2. **取交集**：多篇作品中**反复出现**的特征权重最高（如作者的稳定风格）\n"
        "3. **取并集**：不同作品中展现的不同面向都要纳入（如某作者早期和后期风格差异）\n"
        "4. **标注意外**：如果某篇作品的蒸馏与其他篇明显矛盾，在报告中标注\n"
        "5. **综合人设卡片**：最终人设卡片应反映此作者**最稳定、最核心**的风格特征\n\n"
        "## 输出要求\n\n"
        "- 使用 Markdown 格式\n"
        "- 维度与单篇蒸馏相同（经历推测、笔法特征、用词特征、节奏结构、情感滤光）\n"
        "- 每个维度注明「综合自 N 篇作品」\n"
        "- 末尾生成合并后的**虚拟作者人设卡片**\n"
        "- 不要输出 JSON\n"
    )


def merge_distill_reports(
    distill_texts: list[str],
    *,
    author_name: str,
    book_titles: list[str],
    temperature: float = 0.38,
) -> dict[str, Any]:
    """将同一作者的多篇蒸馏报告合并为一个综合画像。"""
    if not distill_texts:
        return {"ok": False, "error": "没有可合并的蒸馏报告"}

    if len(distill_texts) == 1:
        # 只有一篇，直接返回
        skill_content = build_author_skill_template(
            author_name=author_name,
            distill_text=distill_texts[0],
            book_title=book_titles[0] if book_titles else "",
        )
        return {
            "ok": True,
            "merged_text": distill_texts[0],
            "skill_content": skill_content,
            "author_name": author_name,
            "merged_count": 1,
        }

    system = build_merge_distill_system()

    parts: list[str] = []
    for i, (text, title) in enumerate(zip(distill_texts, book_titles), 1):
        parts.append(f"### 蒸馏报告 {i}：《{title or '未知作品'}》\n\n{text}")

    user = (
        f"【作者】{author_name}\n"
        f"【已蒸馏作品数】{len(distill_texts)} 篪\n\n"
        + "\n\n---\n\n".join(parts)
        + "\n\n---\n\n请将以上所有蒸馏报告合并为一个统一的作者风格画像。"
    )

    try:
        merged_text = chat_completion(
            system=system,
            user=user,
            temperature=temperature,
        )
    except RuntimeError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": f"模型调用失败: {e}"}

    skill_content = build_author_skill_template(
        author_name=author_name,
        distill_text=merged_text.strip(),
        book_title="、".join(book_titles[:5]),
    )

    return {
        "ok": True,
        "merged_text": merged_text.strip(),
        "skill_content": skill_content,
        "author_name": author_name,
        "merged_count": len(distill_texts),
    }
