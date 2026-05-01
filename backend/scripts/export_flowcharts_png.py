#!/usr/bin/env python3
"""生成「多智能体编排」与「长记忆形成」流程图 PNG（与 backend/app 行为一致）。

依赖：Pillow（项目里通常已通过其它包间接安装；否则 pip install Pillow）

用法（建议在 backend 目录下）::
  python scripts/export_flowcharts_png.py
  python scripts/export_flowcharts_png.py --output D:/Exports/flowcharts
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Tuple

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.memory_wiki import WIKI_COMPILE_INTERVAL  # noqa: E402
from app.paths import user_data_root  # noqa: E402

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError as e:
    raise SystemExit(
        "缺少 Pillow：pip install Pillow\n或在 backend 目录执行 pip install -r requirements.txt（若仍缺则单独装 Pillow）"
    ) from e

Color = Tuple[int, int, int]

# 多智能体编排图宽度（内容较多，单独加宽）
ORCH_DIAGRAM_WIDTH = 1480


def _font(size: int) -> ImageFont.FreeTypeFont:
    for p in (
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\msyhbd.ttc"),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
    ):
        if p.is_file():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default()


def _text_wh(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> tuple[int, int]:
    x0, y0, x1, y1 = draw.textbbox((0, 0), text, font=font)
    return x1 - x0, y1 - y0


def _draw_box(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int, int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: Color,
    border: Color,
) -> None:
    draw.rounded_rectangle(xy, radius=8, fill=fill, outline=border, width=2)
    x1, y1, x2, y2 = xy
    lines = text.split("\n")
    line_heights: list[int] = []
    line_widths: list[int] = []
    for ln in lines:
        tw, th = _text_wh(draw, ln, font)
        line_widths.append(tw)
        line_heights.append(th)
    gap = 2
    total_h = sum(line_heights) + gap * max(0, len(lines) - 1)
    ty = (y1 + y2 - total_h) // 2
    for i, ln in enumerate(lines):
        tw = line_widths[i]
        th = line_heights[i]
        tx = (x1 + x2 - tw) // 2
        draw.text((tx, ty), ln, fill=(20, 24, 32), font=font)
        ty += th + gap


def _draw_diamond(
    draw: ImageDraw.ImageDraw,
    cx: int,
    cy: int,
    rw: int,
    rh: int,
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: Color,
    border: Color,
) -> None:
    pts = [(cx, cy - rh), (cx + rw, cy), (cx, cy + rh), (cx - rw, cy)]
    draw.polygon(pts, fill=fill, outline=border, width=2)
    tw, th = _text_wh(draw, text, font)
    draw.text((cx - tw // 2, cy - th // 2), text, fill=(20, 24, 32), font=font)


def _arrow_v(
    draw: ImageDraw.ImageDraw, x: int, y0: int, y1: int, color: Color = (60, 72, 92)
) -> None:
    draw.line((x, y0, x, y1), fill=color, width=2)
    # head
    draw.polygon([(x - 7, y1 - 12), (x + 7, y1 - 12), (x, y1)], fill=color)


def _box_height_for_text(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, pad_v: int = 18
) -> int:
    lines = text.split("\n")
    h = pad_v
    for ln in lines:
        _, lh = _text_wh(draw, ln or " ", font)
        h += lh + 4
    return max(52, h)


def _paint_orchestration(
    draw: ImageDraw.ImageDraw,
    width: int,
    y0: int,
    title_font: ImageFont.FreeTypeFont,
    body_font: ImageFont.FreeTypeFont,
) -> int:
    """多智能体编排详图：与 orchestration/runner.run_chapter_with_agents 逐步一致。"""
    detail = _font(15)
    small = _font(14)
    x_mid = width // 2
    margin = 72
    bx1, bx2 = margin, width - margin
    y = y0 + 20
    gap = 12

    def put_box(label: str, fill: Color, border: Color, fh: ImageFont.FreeTypeFont = detail) -> None:
        nonlocal y
        h = _box_height_for_text(draw, label, fh)
        _draw_box(draw, (bx1, y, bx2, y + h), label, fh, fill, border)
        y += h + gap
        _arrow_v(draw, x_mid, y - gap + 3, y - 1)

    draw.text(
        (margin, y),
        "函数：run_chapter_with_agents ｜ 文件：backend/app/orchestration/runner.py",
        fill=(70, 78, 98),
        font=small,
    )
    y += 28

    put_box(
        "① Writer\n"
        "agent_writer_draft(system, user_payload, temperature=writing_temp)\n"
        "→ log.steps 记录 agent: Writer",
        (235, 242, 255),
        (72, 96, 140),
    )

    d_y = y + 36
    _draw_diamond(draw, x_mid, d_y, 138, 36, 'profile.lower() == "full" ?', detail, (255, 244, 230), (160, 118, 50))
    draw.rectangle((margin, d_y - 42, margin + 300, d_y + 50), fill=(250, 252, 255), outline=(190, 200, 215), width=1)
    draw.text(
        (margin + 10, d_y - 32),
        "【fast 分支】若否：\n本函数立即 return\n(text.strip(), log)\n"
        "后续由 pipeline 调用\nsanitize_chapter_body、\n落盘、记忆同步等",
        fill=(110, 75, 75),
        font=small,
    )
    y = d_y + 44 + gap

    draw.text((margin, y), "【full 分支】依次执行下列智能体（仅摘录与代码一致的写回条件）", fill=(55, 65, 90), font=small)
    y += 26

    put_box(
        "② Character\n"
        "agent_character_polish(chapter_text, premise, temperature=0.55)\n"
        "仅当 polished 非空且 len(polished)>80 才替换 text；异常则保留上一步正文",
        (235, 242, 255),
        (72, 96, 140),
    )
    put_box(
        "③ Lore / Continuity\n"
        "agent_continuity_check（kb_excerpt, premise）→ 得 violations 列表\n"
        "若非空：agent_apply_continuity_fixes（violations JSON 截断≤12000）→ fix 非空则替换 text",
        (235, 242, 255),
        (72, 96, 140),
    )
    put_box(
        "④ Editor\n"
        "agent_editor_pass → revised_text\n"
        "仅当 revised 非空且 len(revised)>200 才用修订稿替换 text",
        (235, 242, 255),
        (72, 96, 140),
    )
    put_box(
        "⑤ ProseTighten（文面收束）\n"
        "_apply_prose_tighten → agent_prose_tighten\n"
        "跳过：参数 run_prose_wash=False，或环境变量 AIWRITER_PROSE_WASH=0（与 _env_prose_wash_enabled）",
        (240, 248, 255),
        (80, 110, 140),
    )
    put_box(
        "⑥ Safety（首轮）\n"
        "agent_safety_pass → level / sanitized_text\n"
        "若 level==\"block\" 且 sanitized_text 非空 → 用 sanitized 替换 text",
        (235, 242, 255),
        (72, 96, 140),
    )

    y -= gap
    hrb = _box_height_for_text(
        draw, "若 run_reader_test==False：跳过 ⑦～⑪（整块 Reader 与二稿），直接至末尾 return", detail
    )
    _draw_box(
        draw,
        (bx1, y, bx2, y + hrb),
        "若 run_reader_test==False：跳过 ⑦～⑪（整块 Reader 与二稿），直接至末尾 return",
        detail,
        (255, 250, 235),
        (160, 130, 70),
    )
    y += hrb + gap
    _arrow_v(draw, x_mid, y - gap + 3, y - 1)

    put_box(
        "⑦ ReaderTest（盲测）\n"
        "agent_reader_blind_test（prev_chapter_tail, known_names_hint, target_min_body_chars …）\n"
        "结果 → log.reader_test；异常时 log 记 _error",
        (255, 248, 235),
        (130, 100, 60),
    )

    put_box(
        "⑧ Reader 驱动二稿（条件全部满足才执行）\n"
        "· profile==full 且 run_reader_driven_revision=True\n"
        "· log.reader_test 为有效 dict\n"
        "· _should_run_reader_driven_revision(reader, 正文字符数, target_min_chars)\n"
        "  含：must_rewrite、人名/空间/语域 issues、length_status、偏短、或低于 target_min×0.72 等\n"
        "· 环境 AIWRITER_READER_DRIVEN_REVISION=0 时本函数首行即返回 False（不触发二稿）",
        (255, 244, 248),
        (140, 70, 90),
    )
    put_box(
        "⑨ WriterReaderRevision\n"
        "再次 agent_writer_draft：user_payload 追加「须根据盲测读者反馈修订」+ reader JSON（节选字段）\n"
        "temperature = max(0.45, writing_temp - 0.12)",
        (235, 242, 255),
        (72, 96, 140),
    )
    put_box(
        "⑩ ProseTighten（二稿后，同⑤）",
        (240, 248, 255),
        (80, 110, 140),
    )
    put_box(
        "⑪ Safety（二稿后）\n"
        "再次 agent_safety_pass；log.steps 带 after: reader_revision",
        (235, 242, 255),
        (72, 96, 140),
    )

    y -= gap
    hret = _box_height_for_text(
        draw,
        "末尾 return text.strip(), log\n"
        "（sanitize_chapter_body、chapters 落盘、监督审查等在 pipeline.py，见下方附注）",
        detail,
    )
    _draw_box(
        draw,
        (bx1, y, bx2, y + hret),
        "末尾 return text.strip(), log\n"
        "（sanitize_chapter_body、chapters 落盘、监督审查等在 pipeline.py，见下方附注）",
        detail,
        (232, 245, 232),
        (70, 120, 80),
    )
    y += hret + gap + 10

    rx1, rx2 = margin, width - margin
    foot_lines = [
        "【pipeline 另线 · 不在 run_chapter_with_agents 内部】",
        "监督快审后若 should_run_supervisor_local_revision(review) 为真 → run_supervisor_local_rewrite：",
        "WriterSupervisorLocal（agent_writer_draft + 监督 JSON + 当前正文缩略）→ ProseTighten → Safety。",
        "环境变量 AIWRITER_SUPERVISOR_LOCAL_REWRITE=0 可关闭。",
    ]
    foot_h = 16 + sum(_text_wh(draw, ln, small)[1] + 6 for ln in foot_lines)
    draw.rectangle((rx1, y, rx2, y + foot_h), fill=(246, 247, 252), outline=(180, 188, 205), width=1)
    ty = y + 10
    for ln in foot_lines:
        draw.text((rx1 + 12, ty), ln, fill=(75, 80, 95), font=small)
        ty += _text_wh(draw, ln, small)[1] + 6
    y += foot_h + 20

    note = "更完整的章节级流程（含监督、记忆）见 docs/ARCHITECTURE_AND_FLOWS.md §3–§5"
    nw, nh = _text_wh(draw, note, small)
    draw.text(((width - nw) // 2, y), note, fill=(100, 105, 120), font=small)
    return y + nh + 28


def _paint_long_memory(
    draw: ImageDraw.ImageDraw,
    width: int,
    y0: int,
    title_font: ImageFont.FreeTypeFont,
    body_font: ImageFont.FreeTypeFont,
) -> int:
    x_mid = width // 2
    y = y0 + 24
    gap = 14
    bh = 44
    interval = WIKI_COMPILE_INTERVAL

    blocks = [
        (
            "输入侧（进入模型上下文时可勾选拼装）",
            "kb/*.md 作者圣经 · 全局 UserData/memory · 本书 memory/\n（palace_summary.md + palace.sqlite3 · canon_changelog.md）",
            (245, 248, 255),
            (72, 96, 140),
        ),
        (
            "",
            f"build_memory_context：把摘要/抽屉条目等压进提示词（memory_store.py）",
            (235, 242, 255),
            (72, 96, 140),
        ),
        (
            "",
            "章节生成：run_chapter_with_agents → 清洗 sanitize_chapter_body",
            (235, 242, 255),
            (72, 96, 140),
        ),
        (
            "",
            "落盘：books/<id>/chapters/NN.md",
            (232, 245, 232),
            (70, 120, 80),
        ),
        (
            "若 sync_book_memory",
            "_sync_book_memory_entries：SQLite memory_entries + 总摘要片段\n可选：伏笔块 / episodic 条目裁剪等（pipeline.py）",
            (255, 246, 240),
            (140, 90, 60),
        ),
        (
            f"长篇 length_scale=long",
            f"每 {interval} 章（章节号整除）：maybe_wiki_compile_episodic_batch\n合并本段萃取 → 更新 palace_summary.md（memory_wiki.py）",
            (255, 244, 230),
            (160, 118, 50),
        ),
        (
            "",
            "全书监督 / 审查命中设定类问题：追加 canon_changelog.md（与 memory_wiki 协作）",
            (240, 248, 255),
            (60, 100, 130),
        ),
    ]

    for i, (subtitle, body, fill, border) in enumerate(blocks):
        lines = [subtitle, body] if subtitle else [body]
        text = "\n".join(lines)
        # multi-line box height
        h_text = 0
        for ln in text.split("\n"):
            wln, hln = _text_wh(draw, ln or " ", body_font)
            h_text += hln + 4
        box_h = max(bh, h_text + 24)
        x1, x2 = x_mid - 380, x_mid + 380
        draw.rounded_rectangle((x1, y, x2, y + box_h), radius=8, fill=fill, outline=border, width=2)
        yy = y + 12
        for ln in text.split("\n"):
            wln, hln = _text_wh(draw, ln or " ", body_font)
            draw.text((x_mid - wln // 2, yy), ln, fill=(20, 24, 32), font=body_font)
            yy += hln + 4
        y += box_h + gap
        if i < len(blocks) - 1:
            _arrow_v(draw, x_mid, y - gap + 2, y - 2)

    note = f"Wiki 合并间隔 WIKI_COMPILE_INTERVAL = {interval}（memory_wiki.py）；与 sync_book_memory 开关配合 · 详见 docs/ARCHITECTURE_AND_FLOWS.md"
    nf = _font(14)
    tw, th = _text_wh(draw, note, nf)
    draw.rectangle((40, y + 8, width - 40, y + 8 + th + 16), fill=(248, 249, 252), outline=(200, 205, 220), width=1)
    draw.text(((width - tw) // 2, y + 16), note, fill=(90, 95, 110), font=nf)
    return y + th + 56


def main() -> int:
    ap = argparse.ArgumentParser(description="导出多智能体编排与长记忆流程 PNG")
    ap.add_argument("--output", type=str, default="", help="输出目录；默认 UserData 上级/Exports 或 backend/exports")
    args = ap.parse_args()

    ts = time.strftime("%Y%m%d_%H%M%S")
    if args.output.strip():
        root = Path(args.output).expanduser().resolve()
    else:
        try:
            ud = user_data_root()
            root = (ud.parent / "Exports" / f"flowcharts_{ts}").resolve()
        except Exception:
            root = (_BACKEND / "exports" / f"flowcharts_{ts}").resolve()

    # Two-pass height measure for orchestration
    def measure_orch() -> int:
        probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
        return _paint_orchestration(probe, ORCH_DIAGRAM_WIDTH, 72, _font(24), _font(17))

    def measure_mem() -> int:
        probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
        return _paint_long_memory(probe, 1280, 72, _font(24), _font(17))

    h1 = measure_orch() + 160
    h2 = measure_mem() + 120
    root.mkdir(parents=True, exist_ok=True)

    title_font = _font(24)
    body_font = _font(17)

    # orchestration（加宽画布以容纳分步说明）
    img1 = Image.new("RGB", (ORCH_DIAGRAM_WIDTH, h1), (252, 253, 255))
    d1 = ImageDraw.Draw(img1)
    t1 = "AI-writer · 多智能体编排详图（run_chapter_with_agents · runner.py）"
    tw, _ = _text_wh(d1, t1, title_font)
    d1.text(((ORCH_DIAGRAM_WIDTH - tw) // 2, 28), t1, fill=(16, 20, 40), font=title_font)
    _paint_orchestration(d1, ORCH_DIAGRAM_WIDTH, 72, title_font, body_font)
    p1 = root / "01_multi_agent_orchestration.png"
    img1.save(p1, "PNG", optimize=True)
    print(f"已写入：{p1}")

    img2 = Image.new("RGB", (1280, h2), (252, 253, 255))
    d2 = ImageDraw.Draw(img2)
    t2 = "AI-writer · 长记忆形成与写入（书本 memory/）"
    tw, _ = _text_wh(d2, t2, title_font)
    d2.text(((1280 - tw) // 2, 28), t2, fill=(16, 20, 40), font=title_font)
    _paint_long_memory(d2, 1280, 72, title_font, body_font)
    p2 = root / "02_long_term_memory_pipeline.png"
    img2.save(p2, "PNG", optimize=True)
    print(f"已写入：{p2}")
    print(f"输出目录：{root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
