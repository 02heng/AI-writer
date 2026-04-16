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
    """Remove line-leading Markdown blockquotes (>), bullets (- * +), and ordered '1. ' lists."""
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
        out.append(s)
    return "\n".join(out)


def strip_comma_hyphen_glitch(text: str) -> str:
    """Turn ASCII `,-` glitches (comma + hyphen) into a Chinese comma for prose."""
    return re.sub(r",\s*-\s*", "，", text)


def strip_aiwriter_prose_noise(text: str) -> str:
    """Strip Markdown line junk and `,-` glitches after ** removal (used on chapter bodies)."""
    t = strip_markdown_line_prefixes(text)
    return strip_comma_hyphen_glitch(t)


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
