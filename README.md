# AI Writer

本地优先的中文小说写作助手：**Electron 桌面壳** + **FastAPI 后端**，调用兼容 OpenAI 接口的大模型（默认 DeepSeek）完成策划、续写与多智能体编排。用户数据与书稿保存在本机数据目录，不依赖云端托管正文。

## 功能概览

- **书库与章节**：书本注册表、目录、章节 Markdown 阅读与导航  
- **一键全书 / 续写**：流水线创建书本并逐章生成；支持按 `book_id` 或旧版 `out/` 前缀续写  
- **记忆宫殿**：全局与「按书」记忆（摘要 + SQLite 条目），可参与生成上下文  
- **多智能体编排**：Writer / 角色 / 连续性 / 编辑 / 安全等链路（`fast` 单步与 `full` 完整模式）  
- **知识库与提示词**：`kb/`、`prompts/` 可维护；首次运行会从内置模板同步到用户目录  
- **主题与界面**：多套界面主题；轻量 Markdown 渲染  

更细的目录与模块说明见 **[PROJECT_STRUCTURE.md](./PROJECT_STRUCTURE.md)**。  
**记忆宫殿、Wiki/作者圣经、开书与续写完整流程图（含与 GitHub 的关系）**见 **[docs/ARCHITECTURE_AND_FLOWS.md](./docs/ARCHITECTURE_AND_FLOWS.md)**。

## 技术栈

| 层级 | 技术 |
|------|------|
| 桌面 | Electron 35+ |
| 前端 | 原生 HTML / CSS / JavaScript（`renderer/`） |
| 后端 | Python 3 + FastAPI、Uvicorn |
| 模型 | OpenAI 兼容 HTTP API（如 DeepSeek） |

## 环境要求

- **Node.js**（用于安装依赖与启动 Electron）  
- **Python 3**（Windows 上 Electron 子进程默认使用 `py -3`；可通过环境变量覆盖，见下文）  
- **DEEPSEEK_API_KEY**（或你使用的兼容服务的 API Key），写入用户数据目录下的 `.env`  

## 快速开始

### 1. 克隆与安装

```bash
git clone https://github.com/02heng/AI-writer.git
cd AI-writer
npm install
cd backend
pip install -r requirements.txt
cd ..
```

### 2. 配置 API Key

在运行时用户数据根下的 `UserData/.env` 中配置（首次启动应用后会创建目录结构），例如：

```env
DEEPSEEK_API_KEY=你的密钥
```

也可在启动前通过系统环境变量注入同名变量。

### 3. 启动应用

```bash
npm start
```

Electron 会拉起本地后端，默认监听 **`127.0.0.1:18765`**（健康检查：`GET /api/health`）。若该端口已被其他程序占用，请先释放端口后再启动。

### 仅开发后端（可选）

```bash
npm run backend
```

在仓库 `backend/` 目录下以开发模式运行 FastAPI（需自行保证与 Electron 使用的端口、环境一致）。

## 用户数据目录

默认在 **`D:\AI-writer-data`** 或 **`E:\AI-writer-data`** 下创建 `UserData/` 等布局（与 `package.json` 描述一致）。可通过环境变量覆盖，例如：

| 变量 | 作用 |
|------|------|
| `AIWRITER_DATA_ROOT` | 数据根目录 |
| `AIWRITER_USER_DATA` | 用户数据根（高级用法） |
| `AIWRITER_PYTHON` | 指定 Python 可执行文件路径 |
| `AIWRITER_USE_CWD_USER_DATA=1` | 测试或开发时使用当前工作目录下的用户数据布局 |

完整目录树与 API 摘要见 **[PROJECT_STRUCTURE.md](./PROJECT_STRUCTURE.md)**。

## 打包发布

安装包内已用 **PyInstaller** 将后端打成 `aiwriter-backend`，一般用户**无需再装 Python**。开发时 `npm start` 仍走本机 `uvicorn`。

```bash
# 需本机：Python 3.12+、pip install -r backend/requirements.txt -r backend/requirements-build.txt
npm run build-backend   # 仅打 Python 冻结包 → backend/dist/aiwriter-backend/

npm run pack    # 先 build-backend，再 electron-builder --dir → release/
npm run dist    # 先 build-backend，再打安装包（Windows NSIS / macOS DMG+ZIP 等）
```

CI：推送 `v*` tag 时 workflows 会装好 Python 依赖后执行 `npm run dist`。

## 测试

在 `backend/` 目录：

```bash
pytest
```

## 仓库结构（简图）

```text
AI-writer/
├── electron/          # 主进程、预加载、拉起后端、路径与定时快照等
├── renderer/          # 界面与前端逻辑
├── backend/           # FastAPI 应用、流水线、记忆、编排
├── docs/              # 架构与流程说明（记忆宫殿、Wiki、写作/续写）
├── PROJECT_STRUCTURE.md
└── package.json
```

## 链接

- 源码：<https://github.com/02heng/AI-writer>

---

如在 Issues 中反馈 bug 或需求，请尽量附上操作系统版本、复现步骤与相关日志（`UserData/Logs/` 等）。
