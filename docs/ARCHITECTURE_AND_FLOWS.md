# AI Writer：项目结构、记忆宫殿、Wiki 与写作流程

本文说明**代码仓库**、**本机用户数据**、**单本书**三者的关系，以及「开新书 → 续写 → 记忆 / Wiki 协作」在流水线中的走向。文末单独说明**什么推送到 GitHub**、**书稿如何备份或开源**。

---

## 1. 三层结构总览

```mermaid
flowchart TB
  subgraph repo["代码仓库（GitHub）"]
    R1[electron / renderer]
    R2[backend/app]
    R3[默认模板 default_kb / default_prompts]
  end

  subgraph ud["用户数据目录 UserData（默认 D:/ 或 E:/ AI-writer-data）"]
    U1[kb 作者圣经 · Wiki]
    U2[prompts 系统提示词]
    U3[memory 全局记忆宫殿]
    U4[books 书库索引与各书目录]
    U5[.env API Key]
  end

  subgraph book["单本书 books/book_id/"]
    B1[meta.json / plan.json]
    B2[chapters NN.md]
    B3[memory 本书宫殿 + canon_changelog]
    B4[orchestration state.json]
  end

  repo -->|首次运行 ensure_layout 拷贝缺失模板| ud
  ud --> book
```

| 层级 | 路径（概念） | 作用 |
|------|----------------|------|
| 仓库 | 克隆的 `AI-writer/` | Electron + FastAPI **程序**；不含你的书稿与密钥 |
| 用户数据 | `…/UserData/`（可用 `AIWRITER_USER_DATA` 等覆盖） | 全局 `kb/`、`prompts/`、`memory/`、`books/` |
| 单本书 | `UserData/books/<book_id>/` | 本书策划、章节正文、本书记忆、编排状态 |

---

## 2. 知识库（Wiki / 作者圣经）与记忆宫殿的分工

```mermaid
flowchart LR
  KB["kb/*.md\n作者圣经\n人物 / 规则 / 年表"]
  MP["本书 memory/\npalace_summary.md\n衣柜层总摘要"]
  DB["本书 memory/\npalace.sqlite3\n按房间条目"]
  CL["memory/\ncanon_changelog.md\n设定变更 log"]

  KB -->|"硬设定、可互链 Markdown"| CTX["写入章节时的上下文"]
  MP --> CTX
  DB --> CTX
  CL --> CTX
```

- **`UserData/kb/`**（首次会从 `backend/app/default_kb/` 拷贝缺失文件）：长期、可人工编辑的「作者圣经」——人物卡、规则、时间线等；生成请求里可按勾选把摘录拼进模型上下文（见 `main.py` 中 `_kb_context_only` / `_build_user_with_kb`）。
- **本书 `memory/palace_summary.md`**：全书级压缩摘要（「衣柜层」），与 SQLite 条目一起在勾选「注入长期记忆」时进入 `build_memory_context`。
- **本书 `memory/palace.sqlite3`**：`memory_entries` 表，按「房间」存条目；流水线会在策划后写入开笔备案、每章后同步萃取等。
- **`memory/canon_changelog.md`**：长篇模式下，监督审查命中设定类 issue 时自动追加摘要，续写需与之兼容（见 `memory_wiki.append_canon_changelog_from_supervisor_review`）。

模块对应关系（便于查代码）：

| 概念 | 主要代码 |
|------|-----------|
| 记忆条目与总摘要 | `memory_store.py` |
| 伏笔块、语义检索开关等 | `memory_hooks.py`、`memory_relevance.py` |
| 每约 20 章合并萃取 → 总摘要 | `memory_wiki.py`（`maybe_wiki_compile_episodic_batch`） |
| 用户目录布局与默认 KB 拷贝 | `paths.py` → `ensure_layout` |

---

## 3. 开新书：一键全书流水线（`run_pipeline_from_title`）

```mermaid
flowchart TD
  A[输入题目 / 题材 / 选项] --> B[策划：宏观 + 分章要点]
  B --> C[create_book 落盘 meta / plan / orchestration]
  C --> D{sync_book_memory?}
  D -->|是| E[开笔备案写入本书记忆宫殿]
  D -->|否| F[跳过记忆种子]
  E --> G[单章：拼上下文 + Writer 或多智能体链]
  F --> G
  G --> H[清洗并写入 chapters/NN.md]
  H --> I{sync_book_memory?}
  I -->|是| J[总摘要片段 + SQLite 条目同步 / 伏笔等]
  I -->|否| K[仅文件]
  J --> L{长篇且章号为 WIKI_COMPILE_INTERVAL 倍数?}
  L -->|是| M[Wiki 编译：合并本段萃取进 palace_summary]
  L -->|否| N{还有下一章?}
  K --> N
  M --> N
  N -->|是| G
  N -->|否| O{final_supervisor?}
  O -->|是| P[全书元审查]
  O -->|否| Q[返回 book_id 等]
  P --> Q
```

要点：

- API 层为 `POST /api/pipeline/from-title`（前端「一键生成」对应该流程）。
- **`planned_total_chapters`** 可与本轮实际生成章数不同：策划会按「全书尺度」留白，正文仍按本轮 `max_chapters` 写。
- **长篇 `length_scale == "long"`** 且开启本书记忆时，每 **`WIKI_COMPILE_INTERVAL`（默认 20）** 章触发一次「Wiki 协作」侧的批量合并（`maybe_wiki_compile_episodic_batch`，见 `memory_wiki.py`）。

---

## 4. 续写：单章与多章

### 4.1 单章下一章（`run_continue_next_chapter`）

```mermaid
flowchart TD
  X1[选定 book_id] --> X2[扫描 chapters 取最大章号 last_n]
  X2 --> X3[next = last_n + 1]
  X3 --> X4{plan 里是否有第 next 章 beat?}
  X4 -->|无| X5[模型根据梗概 + 上章尾部生成要点]
  X4 -->|有| X6[使用策划 beat]
  X5 --> X7[拼上下文并走 Writer / 多智能体链]
  X6 --> X7
  X7 --> X8[写入 chapters/NN.md + 记忆同步]
  X8 --> X9{long 且 next % WIKI_COMPILE_INTERVAL == 0?}
  X9 -->|是| X10[maybe_wiki_compile_episodic_batch]
  X9 -->|否| X11[结束]
  X10 --> X11
```

- API：`POST /api/pipeline/continue`（单章）等（详见 `PROJECT_STRUCTURE.md` API 摘要）。

### 4.2 多章续写（`run_continue_chapters`）

```mermaid
flowchart LR
  M1[可选 plan_continuation_arc\n多章弧光] --> M2[for i in 1..count]
  M2 --> M3[run_continue_next_chapter]
  M3 --> M2
  M2 --> M4[可选 final_supervisor]
```

- 多章时可选 **`continuation_arc_plan`**：先做一次弧光级规划，再逐章续写，减少章节间漂移。

### 4.3 旧版平面章节（`out/*.md`）

- `run_continue_next_chapter_legacy_out`：兼容早期 `out/前缀_第NN章.md`，无 `books/` 注册表时的续写路径。

---

## 5. 多智能体链（与写作流程的关系）

```mermaid
flowchart LR
  W[Writer 草稿] --> C{profile == full?}
  C -->|否| OUT[定稿进入清洗与落盘]
  C -->|是| CHR[Character]
  CHR --> CO[Continuity + 可选修复]
  CO --> ED[Editor]
  ED --> SA[Safety]
  SA --> RT{run_reader_test?}
  RT -->|是| RE[Reader 盲测写日志]
  RT -->|否| OUT
  RE --> OUT
```

实现见 `orchestration/runner.py` 中 `run_chapter_with_agents`。

---

## 6. 「写完后推送到 GitHub」指什么？

这是两类完全不同的东西，避免混淆：

| 内容 | 是否默认进 GitHub | 说明 |
|------|-------------------|------|
| **本仓库代码**（`electron/`、`backend/`、`renderer/` 等） | 是 | `git push` 推送的是你克隆的 **AI-writer 项目源码** |
| **书稿与用户数据**（`UserData/books/`、`kb/`、`.env`） | **否** | 默认在 `D:\AI-writer-data` 或 `E:\AI-writer-data`，**不在**仓库工作区里；**不要**把含 API Key 的 `.env` 提交到 Git |

若要把**某本书**公开或备份到 GitHub：

1. 新建**单独仓库**（或私有库），**不要**与含密钥的 UserData 混在一个未审查的提交里。  
2. 只拷贝需要的目录，例如 `books/<book_id>/chapters/`、`plan.json`（按需脱敏）、以及你愿意公开的 `kb` 摘录。  
3. 在**新仓库**里 `git init` → `git add` → `commit` → `push`。

若你修改了 **应用本身**（例如改了 `README`、后端、前端），则在 **AI-writer** 仓库根目录照常：

```bash
git add -A
git status   # 确认没有 .env 或整盘 UserData
git commit -m "docs: 架构与流程说明"
git push origin master
```

---

## 7. 延伸阅读

- 目录级文件说明：[PROJECT_STRUCTURE.md](../PROJECT_STRUCTURE.md)  
- 快速安装与运行：[README.md](../README.md)
