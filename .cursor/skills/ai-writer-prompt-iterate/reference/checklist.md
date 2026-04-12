# 提示词改动检查清单

在修改 `backend/prompts/`、`default_prompts/`、`pipeline.py` 用户消息模板或 `orchestration/agents.py` 前逐项确认。

- [ ] `UserData/prompts/writer.md` 与 `backend/prompts/writer.md` / `default_prompts/writer.md` 是否需同步说明（首次安装只拷贝缺失文件）。
- [ ] 策划 JSON 字段是否与 `_normalize_chapter_entry` / `_format_chapter_contract` 一致。
- [ ] `writer.md` 中的章节标题（如【本章写作合同】）是否与 pipeline 注入块一致。
- [ ] 编排智能体若新增 JSON 字段，是否更新 `jsonutil.extract_json_object` 容错与 runner 消费逻辑。
- [ ] 更新 `prompts/manifest.json` 的 `version` 字段（ISO 日期或 semver）。
- [ ] 若新增 `default_prompts` 非 `.md` 文件，确认 `paths.ensure_layout` 会拷贝。
