# AI Writer 项目结构说明

桌面端 **Electron** + 本地 **FastAPI** 后端，前端为纯 HTML/CSS/JS（`renderer/`）。用户数据默认在 `D:\AI-writer-data` 或 `E:\AI-writer-data`（可用环境变量 `AIWRITER_DATA_ROOT` 覆盖）。

## 根目录

| 路径 | 说明 |
|------|------|
| `package.json` | Electron 主工程与 `npm start` 脚本 |
| `electron/` | 主进程、预加载、拉起 uvicorn |
| `renderer/` | 界面：`index.html`、`styles.css`、`themes.css`、`app.js`、`md-lite.js` |
| `backend/` | Python 包与 `requirements.txt`；运行时由 Electron 启动 `uvicorn` |
| `PROJECT_STRUCTURE.md` | 本文件 |

## Electron（`electron/`）

- `main.cjs`：创建窗口、加载 `renderer/index.html`
- `preload.cjs`：暴露 `getBackendUrl`、`getPaths`、`loadSettings`、`saveSettings` 等给渲染进程
- `backend.cjs`：在子进程启动 Python API（默认端口 `18765`）
- `paths.cjs`：解析 UserData 根目录

## 前端（`renderer/`）

- `index.html`：写作台 / 书库双面板；一键生成、续写、记忆宫殿（全局或按书）、界面主题选择
- `styles.css`：布局、组件、阅读区、细滚动条（与主题变量配合）
- `themes.css`：`[data-theme="…"]` 预设配色（余烬金 / 极光青 / 苔原绿 / 夜幕紫 / 拂晓玫）
- `app.js`：调用 REST API；书库三栏（书本 → 目录 → 正文）与上一章/下一章/回目录；`localStorage` 持久化 UI 主题
- `md-lite.js`：轻量 Markdown 渲染

## 后端（`backend/app/`）

| 模块 | 职责 |
|------|------|
| `main.py` | FastAPI 应用：健康检查、题材、KB、提示词、生成、大纲、流水线、**书本与按书记忆 API**、全局记忆宫殿 |
| `paths.py` / `ensure_layout` | 解析数据根目录并创建 `kb/`、`prompts/`、`out/`、`memory/`、`books/` 等 |
| `llm.py` | DeepSeek（OpenAI 兼容）同步/流式调用 |
| `memory_store.py` | 给定任意「书根路径」下的 `memory/`：SQLite 条目 + `palace_summary.md`；`build_memory_context` 拼上下文 |
| `book_storage.py` | `books/index.json` 注册表；`books/<id>/`：`meta.json`、`plan.json`、`chapters/NN.md`、`memory/`、`orchestration/state.json` |
| `library_fs.py` | 旧版 `out/*.md` 列表与安全读取（兼容） |
| `pipeline.py` | **一键全书**：策划 JSON → 创建书本 → 逐章调用编排器 → 写入 `chapters/`；**续写**：按 `book_id` 或旧 `out/` 前缀 |
| `jsonutil.py` | 从模型输出中抽取 JSON 对象 |
| `orchestration/agents.py` | Writer / Character / Lore·Continuity / Editor / Safety / Reader 等单步 LLM 角色 |
| `orchestration/runner.py` | `run_chapter_with_agents`（`fast` 单步 / `full` 多智能体链）、`orchestrator_bump_state` |
| `data/themes.json` | 小说题材列表（可被 UserData 覆盖） |
| `default_kb/`、`default_prompts/` | 首次部署拷贝用模板（含 `manifest.json` 版本说明，由 `ensure_layout` 拷贝） |

## 用户数据目录（运行时，不在仓库内）

典型布局：

```text
<UserData>/
  kb/                    # 设定 Markdown
  prompts/               # 系统提示词，如 writer.md
  out/                   # 旧版平面章节（仍可在界面「展开 out/*.md」阅读）
  memory/                # 全局记忆宫殿（跨书可选注入）
  books/
    index.json           # 书本索引
    <book_id>/
      meta.json
      plan.json
      chapters/01.md …
      memory/            # 本书宫殿（与章节生成同步写入摘要/条目）
      orchestration/state.json
  .env                   # 可选：DEEPSEEK_API_KEY
```

## API 摘要

- 写作：`POST /api/pipeline/from-title`（返回 `book_id`）、`POST /api/pipeline/continue`（`book_id` 或旧 `series_prefix`）
- 书库：`GET /api/books`、`GET /api/books/{id}/toc`、`GET /api/books/{id}/chapters/{n}`
- 本书记忆：`GET/PUT /api/books/{id}/memory/summary`、`GET/POST/DELETE .../memory/entries`、`POST .../memory/extract`
- 全局记忆：原有 `/api/memory/*`
- 兼容：`GET /api/library/files`、`/api/library/series`、`/api/library/read`

## 多智能体说明（后端）

- **Orchestrator**：流水线内以 `orchestration/state.json` 记录步骤与 `draft_version`（按章递增）。
- **fast**：仅 Writer，与早期行为接近（但落盘在 `books/`）。
- **full**：Writer → Character（对白口吻）→ Continuity（违规则一次修订）→ Editor（可选全文替换）→ Safety（block 时替换）→ 可选 Reader 盲测（结果写入 agent 日志，不自动改文）。

前端「编排模式」与「盲测读者」勾选会传入 `agent_profile` / `run_reader_test`（旧库续写仍为单次生成，不传编排参数）。
