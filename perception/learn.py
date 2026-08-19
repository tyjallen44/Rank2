"""Minimal, dependency-free Markdown → HTML renderer for Learn content.

Content is admin-authored but rendered on a public page, so safety matters:
all raw HTML is escaped first, then a limited set of Markdown constructs is
translated into a fixed whitelist of tags. Link targets are restricted to
http/https/mailto and site-relative URLs (no javascript: etc.).

Supported: #..###### headings, unordered lists (-, *, +), ordered lists,
blockquotes, fenced (```) and inline (`) code, horizontal rules (---),
paragraphs, and inline **bold**, *italic*, `code`, [text](url).
"""
from __future__ import annotations

import html
import re

_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_ITALIC = re.compile(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)|(?<!_)_(?!\s)(.+?)(?<!\s)_(?!_)")
_CODE = re.compile(r"`([^`]+?)`")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_HR = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})$")
_UL = re.compile(r"^\s*[-*+]\s+(.*)$")
_OL = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_BQ = re.compile(r"^\s*&gt;\s?(.*)$")  # '>' is HTML-escaped before block parsing


def _safe_href(url: str) -> str:
    u = url.strip()
    low = u.lower()
    if low.startswith(("http://", "https://", "mailto:")) or u.startswith(("/", "#")):
        return html.escape(u, quote=True)
    return "#"


def _inline(text: str) -> str:
    """Apply inline formatting to an already HTML-escaped string."""
    # Links first so their text can still receive emphasis, url is sanitized.
    def _link_sub(m: re.Match) -> str:
        label, url = m.group(1), m.group(2)
        return f'<a href="{_safe_href(html.unescape(url))}" target="_blank" rel="noopener nofollow">{label}</a>'
    text = _LINK.sub(_link_sub, text)
    text = _CODE.sub(lambda m: f"<code>{m.group(1)}</code>", text)
    text = _BOLD.sub(lambda m: f"<strong>{m.group(1) or m.group(2)}</strong>", text)
    text = _ITALIC.sub(lambda m: f"<em>{m.group(1) or m.group(2)}</em>", text)
    return text


def render_markdown(md: str) -> str:
    if not md:
        return ""
    lines = html.escape(md, quote=False).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)
    para: list[str] = []

    def flush_para() -> None:
        if para:
            out.append("<p>" + _inline(" ".join(para).strip()) + "</p>")
            para.clear()

    while i < n:
        line = lines[i]
        stripped = line.strip()

        # Fenced code block
        if stripped.startswith("```"):
            flush_para()
            i += 1
            code: list[str] = []
            while i < n and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1  # skip closing fence
            out.append("<pre><code>" + "\n".join(code) + "</code></pre>")
            continue

        if not stripped:
            flush_para()
            i += 1
            continue

        if _HR.match(stripped):
            flush_para()
            out.append("<hr>")
            i += 1
            continue

        mh = _HEADING.match(stripped)
        if mh:
            flush_para()
            level = len(mh.group(1))
            out.append(f"<h{level}>{_inline(mh.group(2).strip())}</h{level}>")
            i += 1
            continue

        # Unordered / ordered lists (consume consecutive items)
        if _UL.match(line) or _OL.match(line):
            flush_para()
            ordered = bool(_OL.match(line))
            tag = "ol" if ordered else "ul"
            items: list[str] = []
            pat = _OL if ordered else _UL
            while i < n and pat.match(lines[i]):
                items.append("<li>" + _inline(pat.match(lines[i]).group(1).strip()) + "</li>")
                i += 1
            out.append(f"<{tag}>" + "".join(items) + f"</{tag}>")
            continue

        # Blockquote
        if _BQ.match(line):
            flush_para()
            quote: list[str] = []
            while i < n and _BQ.match(lines[i]):
                quote.append(_BQ.match(lines[i]).group(1).strip())
                i += 1
            out.append("<blockquote>" + _inline(" ".join(quote)) + "</blockquote>")
            continue

        para.append(stripped)
        i += 1

    flush_para()
    return "\n".join(out)
