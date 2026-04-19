# 书库连贯性审核

将审核智能体输出的 Markdown / JSON 报告放在本目录（例如 `book-xxx-2026-04-12.md`）。
分析页「书本监督」可一键写入 `supervisor-<书本ID>-<时间>.json`。
分析页「分析」会列出并预览这些文件。

数据根目录与写作 `UserData` 同级，默认在 `D:/AI-writer-data/Analytics/`。
可通过环境变量 `AIWRITER_ANALYTICS_ROOT` 覆盖。
