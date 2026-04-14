"""本书「虚拟作者」人设与用户对全书的项目说明（注入写作与记忆宫殿）。"""

from __future__ import annotations

import random
from typing import Any, Optional

# 叙事滤光：人物欲望与作者内在驱动力同频（内化，非元小说直写）
WRITER_VOICE_PHILOSOPHY = (
    "【叙事滤光（须内化，勿在正文直说）】\n"
    "书中主要人物的欲望、缺口与执着，可视为本书虚拟作者在潜意识中的投射："
    "人物所追求或恐惧的，应与这位作者的内在驱动力、经历与执念隐隐同频。"
    "正文仍是角色自己的故事，不要写穿帮式的元小说或突然引入「作家」出场，"
    "除非用户或本章合同明确要求。"
)


_CITIES = (
    "北京",
    "上海",
    "广州",
    "成都",
    "西安",
    "武汉",
    "南京",
    "杭州",
    "重庆",
    "青岛",
    "厦门",
    "昆明",
    "沈阳",
    "兰州",
    "哈尔滨",
    "苏州",
    "长沙",
    "郑州",
    "天津",
    "深圳",
)

_PROFESSIONS = (
    "夜班急诊科护士",
    "中学语文教师",
    "退伍后开出租的司机",
    "独立游戏美术",
    "档案馆管理员",
    "破产边缘的小餐馆老板",
    "驻村第一书记",
    "地下乐队主唱转行的录音师",
    "古籍修复学徒",
    "外卖站长",
    "民宿主理人",
    "化工质检员",
    "自由撰稿人",
    "婚庆摄影师",
    "社区网格员",
)

_PASTS = (
    "少年时曾因一次误会与挚友决裂，至今耿耿于怀",
    "曾在南方沿海打过三年工，见过人情冷暖",
    "父母离异早，由祖父母带大，对「家」既渴望又警惕",
    "高考失利后自考本科，对「被认可」异常敏感",
    "经历过一场大病，对时间与身体有切肤认识",
    "年轻时信过极端理想，后来学会与妥协共处",
    "曾在边疆服过役，习惯把情绪压得很低",
    "创业失败过一次，负债还清后变得谨慎而执拗",
    "长期照顾患病家人，对责任与自我边界拉扯很深",
    "在异乡独居十年，擅长观察陌生人",
)

_TEMPERS = (
    "嘴硬心软",
    "表面随和、内里倔强",
    "急躁但事后会反复自责",
    "寡言、情绪慢热",
    "爱用玩笑挡真心",
    "习惯先行动再解释",
    "遇事习惯独自扛",
    "对细节偏执",
    "怕冷清、又怕被看透",
    "对「公平」近乎执念",
)

_OBSESSIONS = (
    "渴望被认真听见，又害怕期待落空",
    "想抓住「第二次机会」证明自己",
    "对「失而复得」有执念",
    "想逃离某种重复的命运感",
    "想保护某个具体的人，哪怕方式笨拙",
    "想弄清当年某桩事的真相",
    "想与过去的自己和解",
    "想留下一点不会被抹掉的痕迹",
)

_WRITING_HABITS = (
    "习惯从「声音与沉默」切入关系戏",
    "习惯用食物与气味勾连记忆",
    "习惯写身体小动作多于心理旁白",
    "习惯让对话里带未说出口的半句",
    "习惯用天气与路途外化情绪",
    "习惯在章末留一个未接的电话或一条未回的信息",
)


def roll_virtual_author(rng: Optional[random.Random] = None) -> dict[str, Any]:
    """随机生成本书虚拟作者（性别、年龄、经历等），用于全书与续写同一滤光。"""
    r = rng or random.Random()
    gender = r.choice(("男", "女"))
    pronoun = "他" if gender == "男" else "她"
    age = r.randint(24, 68)
    city = r.choice(_CITIES)
    job = r.choice(_PROFESSIONS)
    past = r.choice(_PASTS)
    past2 = r.choice(_PASTS)
    while past2 == past:
        past2 = r.choice(_PASTS)
    temper = r.choice(_TEMPERS)
    obsession = r.choice(_OBSESSIONS)
    habit = r.choice(_WRITING_HABITS)

    card = (
        f"【本书虚拟作者 · 叙事滤光人格】\n"
        f"性别：{gender}，年龄：{age} 岁，现居{city}。曾为 / 现为：{job}。\n"
        f"经历印记：{past}；{past2}。\n"
        f"性格气质：{temper}。内在缺口与欲望：{obsession}。\n"
        f"写作习惯（技法偏好，非正文）：{habit}。\n"
        f"说明：{pronoun}是本书的「笔法滤光镜」——书中人物的欲望与执着应与此作者的内在驱动力隐隐同频；"
        f"正文勿直写作者本人，除非用户或合同要求。"
    )
    return {
        "gender": gender,
        "pronoun": pronoun,
        "age": age,
        "city": city,
        "profession": job,
        "card": card,
    }


def build_voice_prompt_blocks(
    *,
    user_book_note: Optional[str],
    author: Optional[dict[str, Any]],
) -> str:
    """拼入 Writer 用户消息顶部的项目说明 + 虚拟作者 + 滤光哲学。"""
    parts: list[str] = []
    note = (user_book_note or "").strip()
    if note:
        parts.append("【用户全书项目说明（须尊重、可内化）】\n" + note[:6000])
    if author and isinstance(author, dict) and str(author.get("card") or "").strip():
        parts.append(str(author["card"]).strip()[:6000])
    parts.append(WRITER_VOICE_PHILOSOPHY)
    return "\n\n".join(parts).strip()


def format_voice_from_book_meta(meta: dict[str, Any]) -> str:
    """从书本 meta.json 恢复续写用的滤光块（旧书无相关字段时返回空）。"""
    note = str(meta.get("user_book_note") or "").strip() or None
    raw = meta.get("virtual_author")
    author: Optional[dict[str, Any]] = raw if isinstance(raw, dict) else None
    if author is not None and not str(author.get("card") or "").strip():
        author = None
    if not note and author is None:
        return ""
    return build_voice_prompt_blocks(user_book_note=note, author=author)
