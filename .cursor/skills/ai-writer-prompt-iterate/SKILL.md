---
name: ai-writer-prompt-iterate
description: >-
  Iterates and hardens prompts for the AI-writer repo (writer.md, pipeline chapter
  contracts, orchestration agents). Use when editing Chinese fiction LLM prompts,
  planning JSON shapes, or multi-agent rubrics in this project; also use when the
  user asks for prompt quality passes, self-review of skills, or structured
  prompt upgrades for AI-writer.
---

# AI Writer 提示词迭代

## 目标

在 **AI-writer** 仓库内迭代中文小说相关提示词时，保持「策划 JSON → 本章合同 → writer 系统提示 → 多智能体 rubric」一致，并避免破坏旧书库与 `ensure_layout` 拷贝逻辑。

## 工作流

1. **读现状**：打开 `backend/prompts/writer.md`、`backend/app/pipeline.py`（`_plan_from_title`、`_format_chapter_contract`）、`backend/app/orchestration/agents.py`、`prompts/manifest.json`。
2. **改提示**：优先改 `backend/prompts/writer.md` 与 `backend/app/default_prompts/writer.md`（内容保持一致）；编排角色只改 `agents.py` 内字符串，除非要抽成文件。
3. **改结构**：若增加策划字段，必须同步 `_normalize_chapter_entry`、策划 system 文案、manifest 版本。
4. **自检**：使用 [reference/checklist.md](reference/checklist.md) 逐项打勾。
5. **轻量验证**：`python -m compileall backend/app -q`；不强制跑 API。

## 约束

- 用户消息里已有【本书名】【梗概】【合同】等块时，**writer 系统提示不要重复冗长列举**，避免与 user 重复堆叠。
- 策划 JSON 须**向后兼容**：模型若只返回 `beat`，流水线仍能工作。
- 禁止在提示词中写入真实 API 密钥或用户路径；环境说明放在文档而非 system prompt。

## 自我迭代记录

### Round 1（本技能首次落地后自审）

- **问题**：初版 SKILL 未强调 `writer.md` 与 pipeline 中合同标题必须一致，易导致模型收到脱节指令。
- **修正**：在工作流第 1 步明确打开 `_format_chapter_contract`；在 checklist 中增加「合同标题与 writer.md 对齐」检查项。
- **问题**：未提醒 manifest 版本与 `ensure_layout` 对 `manifest.json` 的拷贝行为。
- **修正**：checklist 增加 manifest 与 `paths.py` 拷贝条目。

（后续轮次：在此追加 `### Round N`，保持每条 2～4 句，避免 SKILL 过长。）

## 延伸阅读

- 详细检查项：[reference/checklist.md](reference/checklist.md)

## 网文爽文 / 言情（已融入仓库）

- **Writer**：`writer.md` 含「网文爽文与言情（条件激活）」「平台正文用语规避」「长篇开头入戏期（条件激活）」；策划侧见 `pipeline.py` 中 `PLANNER_ORIGINALITY_CONTRACT`（禁用「经典桥段流水线」类字面于策划提示，改用「桥段硬套」等）。
- **编排**：`agents.py` 中 Character 润色、Editor 评估维度；`supervisor.py` 逐章快审任务含爽文/言情检视。改上述任一处时请同步 checklist 与 manifest 版本。
