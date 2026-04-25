from __future__ import annotations

import re


def strip_markdown_double_asterisk_bold(text: str) -> str:
    """Remove Markdown double-asterisk bold wrappers; strip orphan ** pairs."""
    t = text
    while True:
        next_t = re.sub(r"\*\*([^*]+)\*\*", r"\1", t)
        if next_t == t:
            break
        t = next_t
    return t.replace("**", "")


def strip_markdown_line_prefixes(text: str) -> str:
    """Remove line-leading Markdown blockquotes (>), bullets (- * +), ordered '1. ' lists, ATX # headings, and any remaining '#'."""
    out: list[str] = []
    for line in text.split("\n"):
        s = line
        while True:
            m = re.match(r"^([ \t]*)>+[ \t]?", s)
            if not m:
                break
            s = m.group(1) + s[m.end() :]
        m = re.match(r"^([ \t]*)[-*+][ \t]+", s)
        if m:
            s = m.group(1) + s[m.end() :]
        m = re.match(r"^([ \t]*)([1-9]\d?)\.[ \t]+", s)
        if m:
            s = m.group(1) + s[m.end() :]
        m = re.match(r"^([ \t]*)(#{1,6})([ \t]*)(.*)$", s)
        if m:
            s = m.group(1) + m.group(4)
        s = s.replace("#", "")
        out.append(s)
    return "\n".join(out)


def strip_comma_hyphen_glitch(text: str) -> str:
    """Turn ASCII `,-` glitches (comma + hyphen) into a Chinese comma for prose."""
    return re.sub(r",\s*-\s*", "，", text)


def collapse_ascii_quote_linebreaks(text: str) -> str:
    """
    合并模型在 ASCII 双引号对话里多余的硬换行。

    阅读器 md-lite 会把同一段落内的换行渲染成 <br/>；LLM 常在「："\n」或引号内断行，导致视觉上像「双引号处回车」。
    """
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r'([：:，,])\s*\n\s*"', r'\1"', t)
    t = re.sub(r'\n\s*([。！？…])\s*"\s*(?=\n|\Z)', r'\1"', t)
    out: list[str] = []
    i = 0
    start = 0
    in_quote = False
    while i < len(t):
        if t[i] == '"':
            chunk = t[start:i]
            if in_quote:
                chunk = re.sub(r"\s*\n\s*", "", chunk)
            out.append(chunk)
            out.append('"')
            in_quote = not in_quote
            i += 1
            start = i
        else:
            i += 1
    out.append(t[start:])
    return "".join(out)


def relax_runon_cjk_prose_to_paragraphs(text: str) -> str:
    """
    将「几乎无换行的一整块长文」粗分为多段（按句对合并），不改动本就分段良好的正文。

    模型或 Editor 偶发输出除标题外整章一段，影响阅读与渲染；在启发式认为属于 run-on 时做恢复。
    """
    s = (text or "").strip()
    if len(s) < 1000:
        return text
    # 已有多段空行，不动
    if s.count("\n\n") >= 2:
        return text
    # 换行已足够多
    n_nl = s.count("\n")
    if n_nl >= max(10, len(s) // 500):
        return text
    if s.count("。") < 6 and s.count("！") + s.count("？") < 2:
        return text
    parts = re.split(r"(?<=[。！？…])", s)
    parts = [p for p in (x.strip() for x in parts) if p]
    if len(parts) < 4:
        return text
    out: list[str] = []
    for i in range(0, len(parts), 2):
        out.append("".join(parts[i : i + 2]).strip())
    merged = "\n\n".join(out)
    if len(merged) < len(s) * 0.9:
        return text
    return merged


def strip_aiwriter_prose_noise(text: str) -> str:
    """Strip Markdown line junk and `,-` glitches after ** removal (used on chapter bodies)."""
    t = strip_markdown_line_prefixes(text)
    t = strip_comma_hyphen_glitch(t)
    return collapse_ascii_quote_linebreaks(t)


def strip_common_prefix_with_previous_opening(
    previous_chapter_plain: str,
    new_chapter_plain: str,
    *,
    min_chars: int = 180,
    min_kept_after_strip: int = 80,
) -> str:
    """Drop recycled opening when the new chapter copies the previous chapter's beginning verbatim."""
    pa = previous_chapter_plain.strip()
    nb = new_chapter_plain.strip()
    if len(pa) < min_chars or len(nb) < min_chars:
        return new_chapter_plain
    lo, hi = min_chars, min(len(pa), len(nb))
    best = 0
    while lo <= hi:
        mid = (lo + hi) // 2
        if pa[:mid] == nb[:mid]:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    if best < min_chars:
        return new_chapter_plain
    # Prefer cutting after a paragraph break so we do not split mid-sentence when two openings differ slightly.
    sep = nb.rfind("\n\n", 0, best)
    if sep != -1:
        cand = sep + 2
        if cand >= min_chars:
            best = cand
    rest = nb[best:].lstrip()
    if len(rest) < min_kept_after_strip:
        return new_chapter_plain
    return rest
