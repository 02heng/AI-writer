from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from pathlib import Path
from queue import Queue
from typing import Optional
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

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
from .pipeline import (
    MAX_PIPELINE_CHAPTERS,
    run_continue_chapters,
    run_continue_next_chapter,
    run_continue_next_chapter_legacy_out,
    run_pipeline_from_title,
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
from .paths import ensure_layout, user_data_root

PACKAGE_DIR = Path(__file__).resolve().parent
THEMES = load_themes(PACKAGE_DIR)

MEMORY_EXTRACT_SYSTEM_PROMPT = (
    "你是小说编辑。请阅读用户给出的章节正文，提取可供后续章节参考的长期记忆要点。"
    "优先：世界观中新增或强化的硬规则；人物关系或状态变化；新出现或推进的伏笔；不可逆事件与时间线节点。"
    "避免：气氛渲染、具体对白、大段描写复述、照抄原文。"
    "输出 5～12 条短句，每条一行，以「- 」开头，写成可检索的事实笔记。"
)

# 允许从 userData 加载 .env（可选）
_ud = os.environ.get("AIWRITER_USER_DATA", "").strip()
if _ud:
    load_dotenv(Path(_ud) / ".env")
load_dotenv()

ROOT = user_data_root()
ensure_layout(ROOT)
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
    use_long_memory: bool = Field(default=True, description="是否注入长期记忆上下文")
    memory_max_chars: int = Field(default=4500, ge=500, le=32000)


class OutlineBody(BaseModel):
    premise: str = Field(..., min_length=1)
    temperature: float = Field(default=0.7, ge=0, le=2)
    theme_id: Optional[str] = Field(default="general")


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
    chapter_count: int = Field(
        default=1,
        ge=1,
        le=20,
        description="续写章数（仅 book_id 模式；旧 out/ 书系仍为 1 章）",
    )


class TrashRestoreBody(BaseModel):
    folder: str = Field(..., min_length=4, max_length=64, description="回收站目录名，通常与书本 ID 相同")


class TrashPurgeBody(BaseModel):
    folder: str = Field(..., min_length=4, max_length=64)


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
API_REVISION = 3


@app.get("/api/health")
def health():
    has_key = bool(os.environ.get("DEEPSEEK_API_KEY", "").strip())
    return {
        "ok": True,
        "api_revision": API_REVISION,
        "max_pipeline_chapters": MAX_PIPELINE_CHAPTERS,
        "pipeline_stream": True,
        "user_data": str(ROOT),
        "books_root": str(books_root(ROOT)),
        "books_root_env": bool(os.environ.get("AIWRITER_BOOKS_ROOT", "").strip()),
        "deepseek_configured": has_key,
    }


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
                    progress_cb=progress,
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
    try:
        if bid:
            cnt = max(1, min(int(body.chapter_count or 1), 20))
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
            )
        else:
            raise HTTPException(400, "请提供 book_id（推荐）或 series_prefix（旧书库）")
    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e
    return result


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
    )
    premise = body.premise.strip()
    if theme_hint:
        premise = f"{theme_hint}\n\n梗概：\n{premise}"

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
