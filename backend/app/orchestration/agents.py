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
        "5. 文风险喻：在梗概/合同未要求强文学化时，**收束可删的文艺腔**——大而无当或套路化的明喻/拟人/通感（如为炫文采的「像垂死生物」「像臃肿影子」、声音「贴墙爬」等），**删比句或从句、改为白描**；不改变事实、不扩写、不新加比喻。\n"
        "## 输出\n"
        "只输出修订后的本章正文全文，不要解释或标题。"
    )
    user_p = f"【全书梗概摘要】\n{premise[:1200]}\n\n【本章正文】\n{chapter_text[:28000]}"
    return chat_completion(system=sys_p, user=user_p, temperature=temperature)


def agent_prose_tighten(
    *,
    chapter_text: str,
    premise: str = "",
    temperature: float = 0.3,
) -> str:
    """
    文风险喻清洗：专责收束为炫文采而加的明喻/拟人/通感、套路化比附，改为白描或删句，不改情节与人物关系。
    """
    sys_p = (
        "你是**文面清洗员**（只做一事）：把正文里**删去也几乎不损情节**的文艺腔、过浓比喻、拟人、通感叠句，**收束为简单、直接的白描**。\n"
        "## 必须遵守\n"
        "- 不改变已发生的事实、时间顺序、对话含义、人名、称谓与章末走向；不增删主要情节节点。\n"
        "- 以**删从句/换直白词**为主；禁止借机重写风格、加新情节或新比喻。\n"
        "- 保留**多段与空行分段**，禁止把全文揉成单段无换行。\n"
        "## 处理对象（有则动，无则几乎不动）\n"
        "- 套路化、大而无当的比附，例如：无叙事功能却写「像某种垂死的生物」「像一个个臃肿的影子」「像余烬/叹息/幽灵」、声音「贴墙爬」等；把暖气片、灯光、人穿羽绒服等**能一句写实**的，改为事实描写。\n"
        "- 同一句里明喻+拟人+通感**叠用**的，**最多保留一个**有功能的意象，其余改为白描或删。\n"
        "- 若【梗概摘要】能判断全书为诗性/意识流/纯文学实验且本章明显是刻意诗化，**只做轻量收紧**，勿把文体洗成短讯。\n"
        "## 输出\n"
        "只输出**修订后的本章正文全文**；不要解释、标题、JSON 或元说明。"
    )
    p = (premise or "")[:2000]
    user_p = f"【梗概摘要（供判断是否诗性文体外，须遵守上文「勿改事实」）】\n{p}\n\n【本章正文】\n{chapter_text[:28000]}"
    return chat_completion(
        system=sys_p,
        user=user_p,
        temperature=temperature,
        max_tokens=writer_completion_max_tokens(),
    )


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
        "- spatial：同一场景内**空间/物理**不自洽，例如：前桌/后桌与声源方向矛盾，进门后该在视野内的人「消失」或先写对谈后人才出现却无遮挡/无交代，队形与门窗走廊关系混乱等。\n"
        "- register：社会**称谓/礼俗**与身份明显不符，且梗概、KB 或本章未作特殊语境交代；例如**教师/校方在常规课堂、无反讽或喜剧设定**下以「X姐」「X哥」称呼学生，或长辈对晚辈、上级对下级的称呼像平辈网语混用等。\n"
        "## 输出（仅 JSON，无 Markdown 围栏）\n"
        '{"violations":['
        '{"category":"naming|timeline|rules|identity|spatial|register|other",'
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
        "你是小说修订编辑。根据违规清单做**最小幅度**修改，仅消除设定/称谓/章内自洽类冲突（含 social register）。\n"
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
        "## 评估维度（在 comments 中须覆盖以下得分/失分点，尤须指出开篇与代入）\n"
        "- opening_pace：全文首段至前约三分之一**是否**尽快落地具体处境/动作/对话；怀旧、返乡、忆青春等**是否**避免仅靠抽象抒怀、泛化感慨拖节奏。\n"
        "- immersion：是否有**可跟随的细节与人物**（少标签句「他感到…」多具体一句），读者能否在脑中有画面、有事可盼。\n"
        "- payoff：章内**至少一次**可感的情绪、认知、关系或（爽文向的）爽点/反转是否到位、是否过晚过稀；若网文或强情节向，点明爽点/反馈是否**偏少或滞后**。\n"
        "- pacing：中段是否拖沓或跳跃，信息密度。\n"
        "- redundancy：信息、对话是否重复。\n"
        "- show_tell：是否过多标签化情绪而非展示。\n"
        "- hook：章末是否有足够张力或悬念。\n"
        "- web_fiction：若为网文爽文向，爽点/反转是否完整、是否与上章同质化；言情向感情推进是否由事件驱动而非空话。\n"
        "- purple_prose：是否滥用比喻/拟人/通感、可白描或删除而不损情节的文艺腔；若全章偏「散文腔凑氛围」，revised_text 中收束，保留信息密度与分段。\n"
        "## 输出（仅 JSON）\n"
        '{"comments":"string","issues":[{"type":"opening_pace|immersion|payoff|pacing|redundancy|show_tell|hook|purple_prose|web_fiction|other","note":"string"}],'
        '"revised_text":"string 若需全文润色则填入，否则空字符串"}。\n'
        "不要随意更改 KB 级设定；若改文，优先删冗、调序、加强具体细节。"
        " 保持**自然分段**（以空行分隔段落），**禁止**把全文改写成除标题外无换行的一整块。"
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


def agent_reader_blind_test(
    *,
    chapter_text: str,
    temperature: float = 0.5,
    prev_chapter_tail: str = "",
    known_names_hint: str = "",
    target_min_body_chars: int = 0,
) -> dict[str, Any]:
    """
    盲测读者：除单章观感外，可选对照「上章结尾」做人物姓名/称谓一致性提醒，并粗判篇幅是否明显偏短。
    target_min_body_chars>0 时（与合同 2000～4000 同级量纲的软下限）参与判断 length_status。
    """
    cross = ""
    if (prev_chapter_tail or "").strip():
        cross = (
            "\n## 跨章对照（若提供）\n"
            "下方【上章结尾摘录】与【本章】对照：若同一人物在上章末与本章中的**姓名、姓氏、称谓**前后矛盾，"
            "或时间线/地点已变却未交代，请写入 name_consistency_issues；无则写空数组。\n"
        )
    len_hint = ""
    if int(target_min_body_chars or 0) > 0:
        len_hint = (
            f"\n## 篇幅粗判\n"
            f"合同类目标常见为约 2000～4000 汉字；若按你的阅读感受本章**明显不及**约 {target_min_body_chars} 字的有效叙事量，"
            f"length_status 填 short；若明显过长则 long；否则 ok 或 uncertain。\n"
        )
    names = ""
    if (known_names_hint or "").strip():
        names = "\n## 已知人物名参考（勿照抄进 JSON 故事，仅用于对照）\n" + known_names_hint.strip()[:2500] + "\n"

    sys_p = (
        "你是未读完整设定书的普通读者，主要读本章；在提供上章摘录时兼做**前后章人名/称谓**快速对照。\n"
        f"{cross}"
        f"{len_hint}"
        "## 输出（仅 JSON）\n"
        '{"confusion_points":["读不懂或缺上下文的句子或情节"],'
        '"weak_motivation":["动机或转折薄弱处"],'
        '"lore_jarring":["设定突兀或未铺垫信息"],'
        '"scene_spatial_issues":["本章内**空间/位置/声源/视线**明显矛盾，例如已写为前座却写声音从后传来、已进门却同场景人物不在视野且无合理解释等；无则空数组"],'
        '"register_social_issues":["**称谓/身份**明显出戏，例如无特殊叙事理由时**教师/长辈对学生**使用「X姐」「X哥」等，或与人物关系、场景礼俗明显不符的称呼；无则空数组"],'
        '"name_consistency_issues":["与上章或已知人名参考相矛盾之处，无则空数组"],'
        '"length_status":"ok|short|long|uncertain",'
        '"must_rewrite":false,'
        '"one_paragraph_suggestion":"一段可执行的改进建议；若须整章返工才能修复 name 或篇幅，将 must_rewrite 置 true 并在此说明要点",'
        '"revision_brief":"给作者看的极短清单（非正文），无则空字符串"}\n'
        "must_rewrite：当且仅当存在明确人名矛盾、或**scene_spatial_issues 有至少一条且为硬伤**、或**register_social_issues 有至少一条且为硬伤**、或篇幅严重偏短/明显未完成、或跨章断裂必须整章重排时为 true。\n"
        "若无则对应数组为空；length_status 无把握用 uncertain。"
    )
    user_parts = [names] if names else []
    if (prev_chapter_tail or "").strip():
        user_parts.append("【上章结尾摘录】\n" + prev_chapter_tail.strip()[:6000])
    user_parts.append("【本章】\n" + chapter_text[:22000])
    raw = chat_completion(
        system=sys_p,
        user="\n\n".join(user_parts),
        temperature=temperature,
    )
    try:
        return extract_json_object(raw)
    except (ValueError, json.JSONDecodeError):
        return {
            "confusion_points": [],
            "weak_motivation": [],
            "lore_jarring": [],
            "scene_spatial_issues": [],
            "register_social_issues": [],
            "name_consistency_issues": [],
            "length_status": "uncertain",
            "must_rewrite": False,
            "one_paragraph_suggestion": "",
            "revision_brief": "",
            "_parse_error": True,
        }
