from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from pathlib import Path
from queue import Queue
from typing import Any, Optional
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field, model_validator

from .book_storage import (
    book_dir,
    books_root,
    export_book_plain_text,
    get_chapter_numbers,
    get_meta,
    get_toc,
    list_books_slice,
    list_trashed_books_slice,
    move_book_to_trash,
    purge_book_from_trash,
    read_chapter,
    read_memory_summary,
    restore_book_from_trash,
    write_memory_summary,
)
from .library_fs import list_out_markdown, list_series, safe_out_md_path, safe_series_prefix
from .llm import chat_completion, stream_chat_completion
from .orchestration.supervisor import (
    agent_supervisor_meta_review,
    load_context_for_supervisor_review,
    supervisor_integrity_report,
)
from .pipeline import (
    MAX_CONTINUE_CHAPTERS,
    MAX_PIPELINE_CHAPTERS,
    MAX_PLANNED_TOTAL_CHAPTERS,
    PLANNER_ORIGINALITY_CONTRACT,
    ideation_instruction,
    run_continue_chapters,
    run_continue_next_chapter,
    run_continue_next_chapter_legacy_out,
    run_pipeline_from_title,
    run_rewrite_chapter,
)
from .memory_store import (
    add_entry,
    build_memory_context,
    delete_entry,
    init_db,
    list_entries,
    load_themes,
    read_rollup,
    theme_by_id,
    write_rollup,
)
from .character_profiles import (
    create_character_profile,
    delete_character_profile,
    list_characters,
    load_character_profile,
    update_character_profile,
    build_character_context,
)
from .layered_memory import LayeredMemory
from .llm.providers import list_available_providers
from .analytics_store import (
    analytics_info,
    analytics_raw_path,
    append_metrics_jsonl,
    ensure_analytics_layout,
    list_analytics_items,
    read_analytics_file,
    save_supervisor_review_snapshot,
)
from .paths import analytics_root, ensure_layout, snapshots_library_dir, user_data_root

PACKAGE_DIR = Path(__file__).resolve().parent
THEMES = load_themes(PACKAGE_DIR)

MEMORY_EXTRACT_SYSTEM_PROMPT = (
    "你是小说编辑。请阅读用户给出的章节正文，提取可供**后续与下一章**参考的长期记忆要点。"
    "全章要覆盖，但**至少约一半条数**须直接对应当前章**末尾约三分之一**里的人物状态、"
    "未收束动作、新信息、悬念，便于接笔。优先：硬规则/人物关系变化/伏笔/时间线/不可逆事件。"
    "避免：气氛空泛、大段照抄。输出 5～12 条短句，每行以「- 」开头，可检索事实笔记。"
)

# 允许从 userData 加载 .env（可选）
_ud = os.environ.get("AIWRITER_USER_DATA", "").strip()
if _ud:
    load_dotenv(Path(_ud) / ".env")
load_dotenv()

ROOT = user_data_root()
ensure_layout(ROOT)
ensure_analytics_layout()
init_db(ROOT)


# #region agent log
def _agent_debug(payload: dict) -> None:
    row = {
        **payload,
        "sessionId": "d7648d",
        "timestamp": int(time.time() * 1000),
    }
    line = json.dumps(row, ensure_ascii=False) + "\n"
    paths: list[Path] = []
    ud = os.environ.get("AIWRITER_USER_DATA", "").strip()
    if ud:
        paths.append(Path(ud) / "debug-d7648d.log")
    pr = os.environ.get("AIWRITER_PROJECT_ROOT", "").strip()
    if pr:
        paths.append(Path(pr) / "debug-d7648d.log")
    if not paths:
        paths.append(Path(__file__).resolve().parent.parent.parent / "debug-d7648d.log")
    for log_p in paths:
        try:
            log_p.parent.mkdir(parents=True, exist_ok=True)
            with log_p.open("a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass


# #endregion


def _txt_attachment_disposition(*, book_id: str, title: str) -> str:
    """HTTP 头 filename= 仅允许 latin-1；中文书名需配合 RFC 5987 filename*。"""
    stem = re.sub(r'[<>:"/\\|?*\n\r]+', "", str(title or book_id))[:80] or "novel"
    stem = stem.strip() or book_id
    ascii_stem = re.sub(r"[^\x20-\x7E]+", "_", stem).strip("._") or re.sub(
        r"[^a-f0-9]+", "", book_id.lower()
    )[:16] or "novel"
    utf8_name = f"{stem}.txt"
    return (
        f'attachment; filename="{ascii_stem}.txt"; '
        f"filename*=UTF-8''{quote(utf8_name, safe='')}"
    )


app = FastAPI(title="AI Writer API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class GenerateBody(BaseModel):
    user_message: str = Field(..., min_length=1)
    prompt_name: str = Field(default="writer.md", description="prompts 目录下文件名")
    kb_names: list[str] = Field(default_factory=list, description="kb 目录下要附带的 md 文件名")
    temperature: float = Field(default=0.8, ge=0, le=2)
    stream: bool = False
    theme_id: Optional[str] = Field(default="general", description="小说主题/类型")
    ideation_level: float = Field(
        default=0.5,
        ge=0,
        le=1,
        description="脑洞程度：0 极保守，0.5 正常，1 高创意（须自洽）",
    )
    use_long_memory: bool = Field(default=True, description="是否注入长期记忆上下文")
    memory_max_chars: int = Field(default=4500, ge=500, le=32000)


class OutlineBody(BaseModel):
    premise: str = Field(..., min_length=1)
    temperature: float = Field(default=0.7, ge=0, le=2)
    theme_id: Optional[str] = Field(default="general")
    ideation_level: float = Field(
        default=0.5,
        ge=0,
        le=1,
        description="脑洞程度：0 极保守，0.5 正常，1 高创意",
    )


class MemoryEntryCreate(BaseModel):
    room: str = Field(..., min_length=1, description="宫殿房间：人物/情节/世界观/伏笔 等")
    title: str = Field(..., min_length=1)
    body: str = Field(..., min_length=1)
    chapter_label: Optional[str] = None


class RollupUpdate(BaseModel):
    text: str = Field(default="")


class ExtractMemoryBody(BaseModel):
    text: str = Field(..., min_length=20)
    chapter_label: Optional[str] = None
    temperature: float = Field(default=0.4, ge=0, le=2)


class PipelineFromTitleBody(BaseModel):
    """题目 + 题材 + 篇幅 + 章数 + 主角性别 → 全书初稿写入 books/{id}/。"""

    title: str = Field(..., min_length=1, max_length=200)
    theme_id: Optional[str] = Field(default="general")
    max_chapters: int = Field(default=8, ge=3, le=MAX_PIPELINE_CHAPTERS)
    planned_total_chapters: Optional[int] = Field(
        default=None,
        ge=3,
        le=MAX_PLANNED_TOTAL_CHAPTERS,
        description="全书预定总章数（可大于本轮 max_chapters）；不设则与本轮相同，不跑宏观阶段表",
    )
    length_scale: str = Field(
        default="medium",
        description="short=短篇, medium=中篇, long=长篇",
    )
    protagonist_gender: str = Field(
        default="any",
        description="male=男主, female=女主, any=不限",
    )
    use_long_memory: bool = Field(default=True)
    kb_names: list[str] = Field(default_factory=list)
    planning_temperature: float = Field(default=0.72, ge=0, le=2)
    writing_temperature: float = Field(default=0.82, ge=0, le=2)
    agent_profile: str = Field(
        default="fast",
        description="fast=单 Writer；full=多智能体链（Character/Continuity/Editor/Safety 等）",
    )
    run_reader_test: bool = Field(default=False, description="full 模式下是否追加盲测读者智能体")
    run_reader_driven_revision: bool = Field(
        default=True,
        description="与 run_reader_test 同开时：读者发现篇幅明显不足、人名/称谓与上章矛盾等，是否自动追加一轮 Writer 修订并再过 Safety",
    )
    ideation_level: float = Field(
        default=0.5,
        ge=0,
        le=1,
        description="脑洞程度：0 极保守，0.5 正常，1 高创意（策划与逐章正文均参考）",
    )
    user_book_note: Optional[str] = Field(
        default=None,
        max_length=8000,
        description="可选：用户对整部书的看法、立意、气质或禁忌；写入 meta 与记忆宫殿并参与策划与写作",
    )
    live_supervisor: bool = Field(
        default=False,
        description="为真时每章写入后调用监督智能体快审，并通过流式事件 supervisor_chapter 推送",
    )
    final_supervisor: bool = Field(
        default=False,
        description="为真时全书章完成后运行总监督元审查，写入 orchestration/state.json 的 open_issues，并返回 supervisor_final",
    )
    memory_episodic_keep_last: Optional[int] = Field(
        default=None,
        ge=0,
        le=500,
        description="一键全书：每章后淘汰旧的情节萃取条数上限；None=不淘汰，0 亦表示不淘汰",
    )
    foreshadowing_sync_after_chapter: bool = Field(
        default=False,
        description="一键全书：每章后更新结构化伏笔 JSON（额外 API）",
    )

    @model_validator(mode="after")
    def planned_total_ge_round(self) -> PipelineFromTitleBody:
        if self.planned_total_chapters is not None and self.planned_total_chapters < self.max_chapters:
            raise ValueError("planned_total_chapters 须大于或等于本轮 max_chapters")
        return self


class PipelineContinueBody(BaseModel):
    """在已有书本后续写下一章（优先 book_id；可回退旧 out/ 前缀）。"""

    book_id: Optional[str] = Field(default=None, max_length=32)
    series_prefix: Optional[str] = Field(default=None, max_length=80)
    theme_id: Optional[str] = Field(default="general")
    use_long_memory: bool = Field(default=True)
    kb_names: list[str] = Field(default_factory=list)
    writing_temperature: float = Field(default=0.82, ge=0, le=2)
    agent_profile: str = Field(default="fast")
    run_reader_test: bool = Field(default=False)
    run_reader_driven_revision: bool = Field(
        default=True,
        description="full+盲测时读者发现问题后是否自动让 Writer 改稿一轮（仅 book_id / 旧 out 续写均适用）",
    )
    chapter_count: int = Field(
        default=1,
        ge=1,
        le=MAX_CONTINUE_CHAPTERS,
        description="续写章数（仅 book_id 模式；旧 out/ 书系仍为 1 章）",
    )
    ideation_level: Optional[float] = Field(
        default=None,
        ge=0,
        le=1,
        description="覆盖脑洞程度；不传则沿用书本 plan.meta.ideation_level，缺省 0.5",
    )
    live_supervisor: bool = Field(
        default=False,
        description="为真时每章续写完成后做监督快审（仅 book_id 模式）",
    )
    final_supervisor: bool = Field(
        default=False,
        description="为真时本批续写结束后运行总监督并写入 orchestration/state.json（仅 book_id）",
    )
    continuation_arc_plan: bool = Field(
        default=True,
        description="续写章数>1 时先调用中观规划写回 plan.json（beat/留白/章末钩）；仅 book_id",
    )
    memory_episodic_keep_last: int = Field(
        default=48,
        ge=0,
        le=500,
        description="情节房间自动萃取条目保留最近条数，0=不淘汰；仅 book_id",
    )
    foreshadowing_sync_after_chapter: bool = Field(
        default=True,
        description="每章后续写后同步 memory/foreshadowing.json（开放/已收）；仅 book_id",
    )


class PipelineRewriteChapterBody(BaseModel):
    """覆盖重写某一章正文（默认最后一章）；不新增章号、不改 plan。"""

    book_id: str = Field(..., min_length=4, max_length=32)
    chapter_index: Optional[int] = Field(
        default=None,
        ge=1,
        le=MAX_PIPELINE_CHAPTERS,
        description="要重写的章号；省略则重写当前磁盘上最后一章",
    )
    theme_id: Optional[str] = Field(default="general")
    use_long_memory: bool = Field(default=True)
    kb_names: list[str] = Field(default_factory=list)
    writing_temperature: float = Field(default=0.82, ge=0, le=2)
    agent_profile: str = Field(default="fast")
    run_reader_test: bool = Field(default=False)
    run_reader_driven_revision: bool = Field(
        default=True,
        description="full+盲测时读者发现问题后是否自动让 Writer 改稿一轮",
    )
    ideation_level: Optional[float] = Field(
        default=None,
        ge=0,
        le=1,
        description="不传则沿用书本 plan.meta.ideation_level",
    )
    live_supervisor: bool = Field(default=False)
    rewrite_author_note: Optional[str] = Field(
        default=None,
        max_length=8000,
        description="用户对该章重写的补充意图、想改的方向等，会注入模型提示，不写入 plan",
    )


class TrashRestoreBody(BaseModel):
    folder: str = Field(..., min_length=4, max_length=64, description="回收站目录名，通常与书本 ID 相同")


class TrashPurgeBody(BaseModel):
    folder: str = Field(..., min_length=4, max_length=64)


class SupervisorReviewBody(BaseModel):
    max_run_lines: int = Field(default=40, ge=5, le=300, description="纳入元审查的 agent_runs.jsonl 最近行数")
    save_to_analytics: bool = Field(
        default=False, description="为真时将 integrity + meta_review 写入 Analytics/reviews/*.json"
    )


def _read_text(rel: Path) -> str:
    if not rel.is_file():
        return ""
    try:
        return rel.read_text(encoding="utf-8")
    except OSError:
        return ""


def _build_user_with_kb(user_message: str, kb_names: list[str]) -> str:
    parts: list[str] = []
    for name in kb_names:
        safe = Path(name).name
        content = _read_text(ROOT / "kb" / safe)
        if content:
            parts.append(f"【设定摘录：{safe}】\n{content}\n")
    parts.append(user_message)
    return "\n".join(parts)


def _kb_context_only(kb_names: list[str]) -> str:
    parts: list[str] = []
    for name in kb_names:
        safe = Path(name).name
        content = _read_text(ROOT / "kb" / safe)
        if content:
            parts.append(f"【设定摘录：{safe}】\n{content}\n")
    return "\n\n".join(parts).strip()


def _compose_system(base_system: str, theme_id: Optional[str]) -> str:
    t = theme_by_id(THEMES, theme_id or "general")
    addon = (t or {}).get("system_addon") or ""
    addon = str(addon).strip()
    if not addon:
        return base_system
    return f"{base_system.strip()}\n\n【题材约束】\n{addon}"


# 递增：Electron 启动时用于识别「本机 18765 上是否为当前应用的后端」，避免旧版/他进程占位导致 404。
API_REVISION = 9


@app.get("/api/health")
def health():
    has_key = bool(os.environ.get("DEEPSEEK_API_KEY", "").strip())
    ar = analytics_root()
    snap = snapshots_library_dir()
    return {
        "ok": True,
        "api_revision": API_REVISION,
        "max_pipeline_chapters": MAX_PIPELINE_CHAPTERS,
        "max_continue_chapters": MAX_CONTINUE_CHAPTERS,
        "max_planned_total_chapters": MAX_PLANNED_TOTAL_CHAPTERS,
        "pipeline_stream": True,
        "user_data": str(ROOT),
        "books_root": str(books_root(ROOT)),
        "books_root_env": bool(os.environ.get("AIWRITER_BOOKS_ROOT", "").strip()),
        "deepseek_configured": has_key,
        "analytics_root": str(ar),
        "snapshots_dir": str(snap),
    }


@app.get("/api/analytics/info")
def api_analytics_info():
    return analytics_info()


@app.get("/api/analytics/list")
def api_analytics_list():
    return list_analytics_items()


@app.get("/api/analytics/file")
def api_analytics_file(rel: str):
    return read_analytics_file(rel)


@app.get("/api/analytics/raw")
def api_analytics_raw(rel: str):
    p, media = analytics_raw_path(rel)
    return FileResponse(p, media_type=media, filename=p.name)


@app.post("/api/analytics/metrics/append")
def api_analytics_metrics_append(record: dict[str, Any] = Body(...)):
    """供本地脚本/自动化追加一行指标 JSON（写入 Analytics/metrics/daily.jsonl）。"""
    return append_metrics_jsonl(record)


@app.get("/api/themes")
def get_themes():
    return {"themes": THEMES}


@app.get("/api/kb")
def list_kb():
    kb = ROOT / "kb"
    if not kb.is_dir():
        return {"files": []}
    files = sorted(p.name for p in kb.glob("*.md"))
    return {"files": files}


@app.get("/api/prompts")
def list_prompts():
    d = ROOT / "prompts"
    if not d.is_dir():
        return {"files": []}
    files = sorted(p.name for p in d.glob("*.md"))
    return {"files": files}


@app.get("/api/library/files")
def library_files():
    """已生成小说章节：UserData/out/*.md"""
    return {"files": list_out_markdown(ROOT)}


@app.get("/api/library/read")
def library_read(name: str):
    _agent_debug(
        {
            "hypothesisId": "H5",
            "location": "main.py:library_read:entry",
            "message": "library_read",
            "data": {"name": name, "root": str(ROOT)},
        }
    )
    try:
        p = safe_out_md_path(ROOT, name)
        content = _read_text(p)
        return {"name": p.name, "content": content}
    except Exception as e:
        _agent_debug(
            {
                "hypothesisId": "H5",
                "location": "main.py:library_read:err",
                "message": str(e),
                "data": {"type": type(e).__name__, "name": name},
            }
        )
        raise


@app.get("/api/library/series")
def library_series():
    """按「前缀_第NN章」聚合，供续写选书。"""
    return {"series": list_series(ROOT)}


@app.get("/api/books")
def api_books_list(limit: int = 200, offset: int = 0, q: str = ""):
    return list_books_slice(ROOT, limit=limit, offset=offset, q=q)


@app.get("/api/books/{book_id}")
def api_book_detail(book_id: str):
    meta = get_meta(ROOT, book_id)
    toc = get_toc(ROOT, book_id)
    return {"meta": meta, "toc": toc}


@app.get("/api/books/{book_id}/chapter-ns")
def api_book_chapter_ns(book_id: str):
    book_dir(ROOT, book_id)
    return {"ns": get_chapter_numbers(ROOT, book_id)}


@app.get("/api/books/{book_id}/supervisor/report")
def api_supervisor_report(book_id: str):
    """监督层：章节与 plan 对齐、缺章、编排状态等（不调用 LLM）。"""
    book_dir(ROOT, book_id)
    return supervisor_integrity_report(ROOT, book_id)


@app.post("/api/books/{book_id}/supervisor/review")
def api_supervisor_review(book_id: str, body: SupervisorReviewBody = SupervisorReviewBody()):
    """监督智能体：完整性报告 + 近期子智能体日志 → 元审查 JSON（需 DEEPSEEK_API_KEY）。"""
    book_dir(ROOT, book_id)
    if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        raise HTTPException(status_code=400, detail="未配置 DEEPSEEK_API_KEY，无法运行监督审查")
    integrity, recent = load_context_for_supervisor_review(
        ROOT, book_id, max_run_lines=body.max_run_lines
    )
    meta_review = agent_supervisor_meta_review(integrity=integrity, recent_runs=recent)
    saved: dict[str, Any] | None = None
    if body.save_to_analytics:
        saved = save_supervisor_review_snapshot(book_id, integrity, meta_review)
    return {"integrity": integrity, "meta_review": meta_review, "saved": saved}


@app.get("/api/books/{book_id}/toc")
def api_book_toc(book_id: str, limit: int = 0, offset: int = 0):
    full = get_toc(ROOT, book_id)
    total = len(full)
    if limit and limit > 0:
        off = max(0, int(offset))
        lim = min(max(int(limit), 1), 2500)
        return {"toc": full[off : off + lim], "total": total, "limit": lim, "offset": off}
    return {"toc": full, "total": total}


@app.get("/api/books/{book_id}/chapters/{chapter_n}")
def api_book_chapter_read(book_id: str, chapter_n: int):
    fn, content, title = read_chapter(ROOT, book_id, chapter_n)
    return {"file": fn, "chapter": chapter_n, "title": title, "content": content}


@app.get("/api/books/{book_id}/export.txt")
def api_book_export_txt(book_id: str):
    _agent_debug(
        {
            "hypothesisId": "H3",
            "location": "main.py:export_txt:entry",
            "message": "export start",
            "data": {
                "book_id": book_id,
                "books_root": str(books_root(ROOT)),
                "root": str(ROOT),
            },
        }
    )
    try:
        text = export_book_plain_text(ROOT, book_id)
        meta = get_meta(ROOT, book_id)
        cd = _txt_attachment_disposition(
            book_id=book_id, title=str(meta.get("title") or book_id)
        )
        return PlainTextResponse(
            text,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": cd},
        )
    except Exception as e:
        _agent_debug(
            {
                "hypothesisId": "H3",
                "location": "main.py:export_txt:err",
                "message": str(e),
                "data": {"type": type(e).__name__, "book_id": book_id},
            }
        )
        raise


@app.delete("/api/books/{book_id}")
def api_book_move_to_trash(book_id: str):
    return move_book_to_trash(ROOT, book_id)


@app.get("/api/trash/books")
def api_trash_books_list(limit: int = 200, offset: int = 0, q: str = ""):
    return list_trashed_books_slice(ROOT, limit=limit, offset=offset, q=q)


@app.post("/api/trash/books/restore")
def api_trash_books_restore(body: TrashRestoreBody):
    return restore_book_from_trash(ROOT, body.folder)


@app.post("/api/trash/books/purge")
def api_trash_books_purge(body: TrashPurgeBody):
    purge_book_from_trash(ROOT, body.folder)
    return {"ok": True}


@app.get("/api/books/{book_id}/memory/summary")
def api_book_memory_summary_get(book_id: str):
    book_dir(ROOT, book_id)
    return {"text": read_memory_summary(ROOT, book_id)}


@app.put("/api/books/{book_id}/memory/summary")
def api_book_memory_summary_put(book_id: str, body: RollupUpdate):
    book_dir(ROOT, book_id)
    write_memory_summary(ROOT, book_id, body.text)
    return {"ok": True}


@app.get("/api/books/{book_id}/memory/entries")
def api_book_memory_entries(book_id: str, limit: int = 80):
    root = book_dir(ROOT, book_id)
    return {"entries": list_entries(root, limit=min(limit, 200))}


@app.post("/api/books/{book_id}/memory/entries")
def api_book_memory_entries_create(book_id: str, body: MemoryEntryCreate):
    root = book_dir(ROOT, book_id)
    row = add_entry(
        root,
        room=body.room,
        title=body.title,
        body=body.body,
        chapter_label=body.chapter_label,
    )
    return {"entry": row}


@app.delete("/api/books/{book_id}/memory/entries/{entry_id}")
def api_book_memory_entries_delete(book_id: str, entry_id: int):
    root = book_dir(ROOT, book_id)
    if not delete_entry(root, entry_id):
        raise HTTPException(404, "条目不存在")
    return {"ok": True}


@app.post("/api/books/{book_id}/memory/extract")
def api_book_memory_extract(book_id: str, body: ExtractMemoryBody):
    root = book_dir(ROOT, book_id)
    try:
        bullets = chat_completion(
            system=MEMORY_EXTRACT_SYSTEM_PROMPT,
            user=body.text[:12000],
            temperature=body.temperature,
        )
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(502, f"模型调用失败: {e}") from e
    label = body.chapter_label or "萃取"
    row = add_entry(
        root,
        room="情节",
        title=f"本章要点萃取 · {label}",
        body=bullets.strip(),
        chapter_label=body.chapter_label,
    )
    return {"entry": row, "text": bullets}


@app.post("/api/pipeline/from-title")
def pipeline_from_title(body: PipelineFromTitleBody):
    writer_path = ROOT / "prompts" / "writer.md"
    writer_system = _read_text(writer_path)
    if not writer_system.strip():
        raise HTTPException(400, "缺少或空的 prompts/writer.md")
    th = theme_by_id(THEMES, body.theme_id or "general")
    theme_addon = str((th or {}).get("system_addon") or "")
    kb_block = _kb_context_only(body.kb_names)
    mem_global = ""
    if body.use_long_memory:
        mem_global = build_memory_context(ROOT, max_chars=2800)
    ap = (body.agent_profile or "fast").strip().lower()
    if ap not in ("fast", "full"):
        ap = "fast"
    try:
        ls = body.length_scale.strip().lower()
        if ls not in ("short", "medium", "long"):
            ls = "medium"
        pg = body.protagonist_gender.strip().lower()
        if pg not in ("male", "female", "any"):
            pg = "any"
        result = run_pipeline_from_title(
            root=ROOT,
            title=body.title.strip(),
            theme_addon=theme_addon,
            writer_system=writer_system,
            max_chapters=body.max_chapters,
            length_scale=ls,
            protagonist_gender=pg,
            use_long_memory=body.use_long_memory,
            memory_context_global=mem_global,
            kb_block=kb_block,
            planning_temp=body.planning_temperature,
            writing_temp=body.writing_temperature,
            agent_profile=ap,
            run_reader_test=bool(body.run_reader_test),
            run_reader_driven_revision=bool(body.run_reader_driven_revision),
            planned_total_chapters=body.planned_total_chapters,
            ideation_level=body.ideation_level,
            user_book_note=body.user_book_note,
            live_supervisor=bool(body.live_supervisor),
            final_supervisor=bool(body.final_supervisor),
            memory_episodic_keep_last=(
                int(body.memory_episodic_keep_last)
                if body.memory_episodic_keep_last is not None and int(body.memory_episodic_keep_last) > 0
                else None
            ),
            foreshadowing_sync_after_chapter=bool(body.foreshadowing_sync_after_chapter),
            theme_id=str(body.theme_id or "general"),
        )
    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e
    return result


@app.post("/api/pipeline/from-title/stream")
async def pipeline_from_title_stream(body: PipelineFromTitleBody):
    writer_path = ROOT / "prompts" / "writer.md"
    writer_system = _read_text(writer_path)
    if not writer_system.strip():
        raise HTTPException(400, "缺少或空的 prompts/writer.md")
    th = theme_by_id(THEMES, body.theme_id or "general")
    theme_addon = str((th or {}).get("system_addon") or "")
    kb_block = _kb_context_only(body.kb_names)
    mem_global = ""
    if body.use_long_memory:
        mem_global = build_memory_context(ROOT, max_chars=2800)
    ap = (body.agent_profile or "fast").strip().lower()
    if ap not in ("fast", "full"):
        ap = "fast"
    ls = body.length_scale.strip().lower()
    if ls not in ("short", "medium", "long"):
        ls = "medium"
    pg = body.protagonist_gender.strip().lower()
    if pg not in ("male", "female", "any"):
        pg = "any"

    async def gen():
        q: Queue = Queue()

        def progress(ev: dict) -> None:
            q.put(("ev", ev))

        def run() -> None:
            try:
                r = run_pipeline_from_title(
                    root=ROOT,
                    title=body.title.strip(),
                    theme_addon=theme_addon,
                    writer_system=writer_system,
                    max_chapters=body.max_chapters,
                    length_scale=ls,
                    protagonist_gender=pg,
                    use_long_memory=body.use_long_memory,
                    memory_context_global=mem_global,
                    kb_block=kb_block,
                    planning_temp=body.planning_temperature,
                    writing_temp=body.writing_temperature,
                    agent_profile=ap,
                    run_reader_test=bool(body.run_reader_test),
                    run_reader_driven_revision=bool(body.run_reader_driven_revision),
                    progress_cb=progress,
                    planned_total_chapters=body.planned_total_chapters,
                    ideation_level=body.ideation_level,
                    user_book_note=body.user_book_note,
                    live_supervisor=bool(body.live_supervisor),
                    final_supervisor=bool(body.final_supervisor),
                    memory_episodic_keep_last=(
                        int(body.memory_episodic_keep_last)
                        if body.memory_episodic_keep_last is not None
                        and int(body.memory_episodic_keep_last) > 0
                        else None
                    ),
                    foreshadowing_sync_after_chapter=bool(body.foreshadowing_sync_after_chapter),
                    theme_id=str(body.theme_id or "general"),
                )
                q.put(("done", r))
            except HTTPException as he:
                q.put(("http_err", he))
            except RuntimeError as e:
                q.put(("err", str(e)))
            except Exception as e:
                q.put(("err", str(e)))

        th_run = threading.Thread(target=run, daemon=True)
        th_run.start()
        while True:
            kind, payload = await asyncio.to_thread(q.get)
            if kind == "ev":
                yield json.dumps(payload, ensure_ascii=False) + "\n"
            elif kind == "done":
                yield json.dumps({"event": "done", "result": payload}, ensure_ascii=False) + "\n"
                break
            elif kind == "http_err":
                he = payload
                d = he.detail
                yield json.dumps(
                    {
                        "event": "error",
                        "status": he.status_code,
                        "detail": d if isinstance(d, str) else str(d),
                    },
                    ensure_ascii=False,
                ) + "\n"
                break
            elif kind == "err":
                yield json.dumps({"event": "error", "status": 502, "detail": payload}, ensure_ascii=False) + "\n"
                break
        th_run.join(timeout=2.0)

    return StreamingResponse(gen(), media_type="application/x-ndjson; charset=utf-8")


@app.post("/api/pipeline/continue")
def pipeline_continue(body: PipelineContinueBody):
    writer_path = ROOT / "prompts" / "writer.md"
    writer_system = _read_text(writer_path)
    if not writer_system.strip():
        raise HTTPException(400, "缺少或空的 prompts/writer.md")
    th = theme_by_id(THEMES, body.theme_id or "general")
    theme_addon = str((th or {}).get("system_addon") or "")
    kb_block = _kb_context_only(body.kb_names)
    mem_global = ""
    if body.use_long_memory:
        mem_global = build_memory_context(ROOT, max_chars=2800)
    ap = (body.agent_profile or "fast").strip().lower()
    if ap not in ("fast", "full"):
        ap = "fast"
    bid = (body.book_id or "").strip()
    sp = (body.series_prefix or "").strip()
    mek = int(body.memory_episodic_keep_last)
    episodic_keep = mek if mek > 0 else None
    try:
        if bid:
            cnt = max(1, min(int(body.chapter_count or 1), MAX_CONTINUE_CHAPTERS))
            if cnt > 1:
                result = run_continue_chapters(
                    root=ROOT,
                    book_id=bid,
                    count=cnt,
                    theme_addon=theme_addon,
                    writer_system=writer_system,
                    use_long_memory=body.use_long_memory,
                    memory_context_global=mem_global,
                    kb_block=kb_block,
                    writing_temp=body.writing_temperature,
                    agent_profile=ap,
                    run_reader_test=bool(body.run_reader_test),
                    run_reader_driven_revision=bool(body.run_reader_driven_revision),
                    ideation_level=body.ideation_level,
                    live_supervisor=bool(body.live_supervisor),
                    final_supervisor=bool(body.final_supervisor),
                    continuation_arc_plan=bool(body.continuation_arc_plan),
                    memory_episodic_keep_last=episodic_keep,
                    foreshadowing_sync_after_chapter=bool(body.foreshadowing_sync_after_chapter),
                    theme_id=str(body.theme_id or "general"),
                )
            else:
                result = run_continue_next_chapter(
                    root=ROOT,
                    book_id=bid,
                    theme_addon=theme_addon,
                    writer_system=writer_system,
                    use_long_memory=body.use_long_memory,
                    memory_context_global=mem_global,
                    kb_block=kb_block,
                    writing_temp=body.writing_temperature,
                    agent_profile=ap,
                    run_reader_test=bool(body.run_reader_test),
                    run_reader_driven_revision=bool(body.run_reader_driven_revision),
                    ideation_level=body.ideation_level,
                    live_supervisor=bool(body.live_supervisor),
                    final_supervisor=bool(body.final_supervisor),
                    memory_episodic_keep_last=episodic_keep,
                    foreshadowing_sync_after_chapter=bool(body.foreshadowing_sync_after_chapter),
                    theme_id=str(body.theme_id or "general"),
                )
        elif sp:
            prefix = safe_series_prefix(sp)
            result = run_continue_next_chapter_legacy_out(
                root=ROOT,
                series_prefix=prefix,
                theme_addon=theme_addon,
                writer_system=writer_system,
                use_long_memory=body.use_long_memory,
                memory_context=mem_global,
                kb_block=kb_block,
                writing_temp=body.writing_temperature,
                ideation_level=body.ideation_level,
                agent_profile=ap,
                run_reader_test=bool(body.run_reader_test),
                run_reader_driven_revision=bool(body.run_reader_driven_revision),
                theme_id=str(body.theme_id or "general"),
            )
        else:
            raise HTTPException(400, "请提供 book_id（推荐）或 series_prefix（旧书库）")
    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e
    return result


@app.post("/api/pipeline/rewrite-chapter")
def pipeline_rewrite_chapter(body: PipelineRewriteChapterBody):
    """按 plan 要点（无则兜底 beat）重新生成并覆盖指定章。"""
    writer_path = ROOT / "prompts" / "writer.md"
    writer_system = _read_text(writer_path)
    if not writer_system.strip():
        raise HTTPException(400, "缺少或空的 prompts/writer.md")
    th = theme_by_id(THEMES, body.theme_id or "general")
    theme_addon = str((th or {}).get("system_addon") or "")
    kb_block = _kb_context_only(body.kb_names)
    mem_global = ""
    if body.use_long_memory:
        mem_global = build_memory_context(ROOT, max_chars=2800)
    ap = (body.agent_profile or "fast").strip().lower()
    if ap not in ("fast", "full"):
        ap = "fast"
    bid = (body.book_id or "").strip()
    if not bid:
        raise HTTPException(400, "请提供 book_id")
    try:
        return run_rewrite_chapter(
            root=ROOT,
            book_id=bid,
            chapter_index=body.chapter_index,
            theme_addon=theme_addon,
            writer_system=writer_system,
            use_long_memory=body.use_long_memory,
            memory_context_global=mem_global,
            kb_block=kb_block,
            writing_temp=body.writing_temperature,
            agent_profile=ap,
            run_reader_test=bool(body.run_reader_test),
            run_reader_driven_revision=bool(body.run_reader_driven_revision),
            ideation_level=body.ideation_level,
            live_supervisor=bool(body.live_supervisor),
            theme_id=str(body.theme_id or "general"),
            rewrite_author_note=body.rewrite_author_note,
        )
    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/memory/entries")
def memory_entries_list(limit: int = 80):
    return {"entries": list_entries(ROOT, limit=min(limit, 200))}


@app.post("/api/memory/entries")
def memory_entries_create(body: MemoryEntryCreate):
    row = add_entry(
        ROOT,
        room=body.room,
        title=body.title,
        body=body.body,
        chapter_label=body.chapter_label,
    )
    return {"entry": row}


@app.delete("/api/memory/entries/{entry_id}")
def memory_entries_delete(entry_id: int):
    if not delete_entry(ROOT, entry_id):
        raise HTTPException(404, "条目不存在")
    return {"ok": True}


@app.get("/api/memory/rollup")
def memory_rollup_get():
    return {"text": read_rollup(ROOT)}


@app.put("/api/memory/rollup")
def memory_rollup_put(body: RollupUpdate):
    write_rollup(ROOT, body.text)
    return {"ok": True}


@app.post("/api/memory/extract")
def memory_extract(body: ExtractMemoryBody):
    """用模型从章节正文萃取要点，写入「情节」房间（会消耗 API）。"""
    try:
        bullets = chat_completion(
            system=MEMORY_EXTRACT_SYSTEM_PROMPT,
            user=body.text[:12000],
            temperature=body.temperature,
        )
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(502, f"模型调用失败: {e}") from e

    label = body.chapter_label or "萃取"
    row = add_entry(
        ROOT,
        room="情节",
        title=f"本章要点萃取 · {label}",
        body=bullets.strip(),
        chapter_label=body.chapter_label,
    )
    return {"entry": row, "text": bullets}


@app.post("/api/generate")
def generate(body: GenerateBody):
    system_path = ROOT / "prompts" / Path(body.prompt_name).name
    system = _read_text(system_path)
    if not system:
        raise HTTPException(400, f"找不到或未读取到提示词: {body.prompt_name}")

    system = _compose_system(system, body.theme_id)

    user_full = _build_user_with_kb(body.user_message, body.kb_names)
    user_full = ideation_instruction(body.ideation_level) + "\n\n---\n\n" + user_full
    if body.use_long_memory:
        mem = build_memory_context(ROOT, max_chars=body.memory_max_chars).strip()
        if mem:
            user_full = f"{mem}\n\n---\n\n【本章创作任务】\n{user_full}"

    if not body.stream:
        try:
            text = chat_completion(
                system=system,
                user=user_full,
                temperature=body.temperature,
            )
        except RuntimeError as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:
            raise HTTPException(502, f"模型调用失败: {e}") from e
        return {"text": text}

    def gen():
        try:
            for piece in stream_chat_completion(
                system=system,
                user=user_full,
                temperature=body.temperature,
            ):
                yield piece
        except Exception as e:
            yield f"\n[错误] {e}\n"

    return StreamingResponse(gen(), media_type="text/plain; charset=utf-8")


@app.post("/api/outline")
def outline(body: OutlineBody):
    th = theme_by_id(THEMES, body.theme_id or "general")
    theme_hint = ""
    if th and (th.get("label") or th.get("description")):
        theme_hint = f"题材类型：{th.get('label','')}。{th.get('description','')}".strip()

    sys_prompt = (
        "你是中文小说策划。根据用户一句话梗概，输出简洁分章大纲（8～15 章），"
        "每章一行：「第N章：一句话要点」。不要废话，不要解释写作方法。"
        f" {PLANNER_ORIGINALITY_CONTRACT}"
    )
    premise = body.premise.strip()
    if theme_hint:
        premise = f"{theme_hint}\n\n梗概：\n{premise}"
    premise = f"{ideation_instruction(body.ideation_level)}\n\n{premise}"

    try:
        text = chat_completion(
            system=sys_prompt,
            user=premise,
            temperature=body.temperature,
        )
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(502, f"模型调用失败: {e}") from e
    return {"text": text}


class SaveChapterBody(BaseModel):
    filename: str = Field(default="chapter")
    content: str = Field(default="")


@app.post("/api/save-chapter")
def save_chapter(body: SaveChapterBody):
    title = body.filename.strip() or "chapter"
    safe = Path(title).name
    if not safe.endswith(".md"):
        safe += ".md"
    content = body.content
    out = ROOT / "out" / safe
    try:
        out.write_text(content, encoding="utf-8")
    except OSError as e:
        raise HTTPException(500, f"写入失败: {e}") from e
    return {"path": str(out)}
