from __future__ import annotations

import json
from typing import Any

from ..jsonutil import extract_json_object
from ..llm import chat_completion, writer_completion_max_tokens


def agent_writer_draft(
    *,
    system: str,
    user_payload: str,
    temperature: float,
) -> str:
    return chat_completion(
        system=system,
        user=user_payload,
        temperature=temperature,
        max_tokens=writer_completion_max_tokens(),
    )


def agent_character_polish(*, chapter_text: str, premise: str, temperature: float = 0.55) -> str:
    sys_p = (
        "你是中文小说对白与人物口吻编辑。\n"
        "## 必须遵守\n"
        "- 不改变已发生的情节事实、时间顺序与结局走向。\n"
        "- 不新增主要角色，不改角色核心关系定义。\n"
        "## 审查清单（逐项在心里对照，不必输出清单）\n"
        "1. 口癖与说话节奏：每人是否有可区分的用语习惯、句式长度。\n"
        "2. 关系张力：对话是否体现地位、隐瞒、试探或冲突。\n"
        "3. 信息差：谁此刻知道什么、谁说漏嘴或打哑谜是否合理。\n"
        "4. 言情向（若梗概或本章明显为感情线）：称呼与语气是否随关系变化；是否避免工业糖精式空洞情话堆砌。\n"
        "## 输出\n"
        "只输出修订后的本章正文全文，不要解释或标题。"
    )
    user_p = f"【全书梗概摘要】\n{premise[:1200]}\n\n【本章正文】\n{chapter_text[:28000]}"
    return chat_completion(system=sys_p, user=user_p, temperature=temperature)


def agent_continuity_check(
    *,
    chapter_text: str,
    kb_excerpt: str,
    premise: str,
    temperature: float = 0.35,
) -> dict[str, Any]:
    sys_p = (
        "你是设定与一致性审查（Lore / Continuity）。\n"
        "## 对照源（优先级从高到低）\n"
        "1. 用户提供的【知识库摘录】中的硬设定。\n"
        "2. 【全书梗概】中的事实陈述。\n"
        "3. 本章内部自洽性（人称、时序、称谓前后一致）。\n"
        "## 检查维度（每条 violation 须对应一类）\n"
        "- naming：人名、地名、组织名拼写或称呼不一致。\n"
        "- timeline：时间线、年龄、事件先后顺序矛盾。\n"
        "- rules：能力、科技、魔法、社会规则与 KB 冲突。\n"
        "- identity：人物身份、关系与已知设定不符。\n"
        "## 输出（仅 JSON，无 Markdown 围栏）\n"
        '{"violations":['
        '{"category":"naming|timeline|rules|identity|other",'
        '"point":"string 具体问题",'
        '"evidence":"string 引用或概括本章中的依据片段",'
        '"severity":"low|med|high",'
        '"suggested_fix":"string 最小修改建议"}'
        '],"summary":"string 一两句总评"}。\n'
        "若无问题，violations 为空数组。"
    )
    user_p = f"【梗概】\n{premise[:2000]}\n\n【知识库摘录】\n{kb_excerpt[:8000]}\n\n【本章】\n{chapter_text[:24000]}"
    raw = chat_completion(system=sys_p, user=user_p, temperature=temperature)
    try:
        return extract_json_object(raw)
    except (ValueError, json.JSONDecodeError):
        return {"violations": [], "summary": "", "_parse_error": True}


def agent_apply_continuity_fixes(
    *,
    chapter_text: str,
    violations_json: str,
    temperature: float = 0.45,
) -> str:
    sys_p = (
        "你是小说修订编辑。根据违规清单做**最小幅度**修改，仅消除设定冲突。\n"
        "- 禁止借机重写风格、扩写无关情节或改变章末走向。\n"
        "- 若某条 violation 与正文无关，忽略该条。\n"
        "只输出修订后的本章正文全文。"
    )
    user_p = f"【违规清单 JSON】\n{violations_json[:12000]}\n\n【本章正文】\n{chapter_text[:28000]}"
    return chat_completion(system=sys_p, user=user_p, temperature=temperature)


def agent_editor_pass(
    *,
    chapter_text: str,
    premise: str,
    temperature: float = 0.45,
) -> dict[str, Any]:
    sys_p = (
        "你是小说结构编辑（不负责改世界观硬设定）。\n"
        "## 评估维度（在 comments 中简要提及得分点）\n"
        "- pacing：节奏是否拖沓或跳跃。\n"
        "- redundancy：信息、对话是否重复。\n"
        "- show_tell：是否过多标签化情绪而非展示。\n"
        "- hook：章末是否有足够张力或悬念。\n"
        "- web_fiction：若为网文爽文向，爽点/反转是否完整、是否与上章同质化；言情向感情推进是否由事件驱动而非空话。\n"
        "## 输出（仅 JSON）\n"
        '{"comments":"string","issues":[{"type":"pacing|redundancy|show_tell|hook|other","note":"string"}],'
        '"revised_text":"string 若需全文润色则填入，否则空字符串"}。\n'
        "不要随意更改 KB 级设定；若改文，优先删冗、调序、加强具体细节。"
    )
    user_p = f"【梗概】\n{premise[:1500]}\n\n【本章】\n{chapter_text[:26000]}"
    raw = chat_completion(system=sys_p, user=user_p, temperature=temperature)
    try:
        return extract_json_object(raw)
    except (ValueError, json.JSONDecodeError):
        return {"comments": "", "revised_text": "", "issues": [], "_parse_error": True}


def agent_safety_pass(*, chapter_text: str, temperature: float = 0.25) -> dict[str, Any]:
    sys_p = (
        "你是内容安全与合规审查（偏保守）。\n"
        "## 关注\n"
        "- 明显违反中国大陆常见内容监管红线的描写。\n"
        "- 可能侵犯真实个人/品牌名誉的影射。\n"
        "- 用户应自查的版权敏感段落（大段照搬名著等）。\n"
        "## 输出（仅 JSON）\n"
        '{"level":"ok|warn|block","notes":"string","sanitized_text":"string"}。\n'
        "level=block 且确需降风险时，将全文改写版写入 sanitized_text；否则 sanitized_text 为空。"
    )
    raw = chat_completion(
        system=sys_p,
        user=chapter_text[:28000],
        temperature=temperature,
    )
    try:
        return extract_json_object(raw)
    except (ValueError, json.JSONDecodeError):
        return {"level": "ok", "notes": "", "sanitized_text": "", "_parse_error": True}


def agent_reader_blind_test(*, chapter_text: str, temperature: float = 0.5) -> dict[str, Any]:
    sys_p = (
        "你是未读设定书的普通读者，只读本章。\n"
        "## 输出（仅 JSON）\n"
        '{"confusion_points":["读不懂或缺上下文的句子或情节"],'
        '"weak_motivation":["动机或转折薄弱处"],'
        '"lore_jarring":["设定突兀或未铺垫信息"],'
        '"one_paragraph_suggestion":"一段可执行的改进建议（非改写全文）"}。\n'
        "若无则对应数组为空。"
    )
    raw = chat_completion(
        system=sys_p,
        user=chapter_text[:24000],
        temperature=temperature,
    )
    try:
        return extract_json_object(raw)
    except (ValueError, json.JSONDecodeError):
        return {
            "confusion_points": [],
            "weak_motivation": [],
            "lore_jarring": [],
            "one_paragraph_suggestion": "",
            "_parse_error": True,
        }
