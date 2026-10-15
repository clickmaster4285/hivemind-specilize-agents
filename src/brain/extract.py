"""Turn source files into the brain's one internal format: clean Markdown (doc/BRAIN_DESIGN.md §4.2).

Phase 1 inputs: Markdown (incl. Microsoft Learn flavour) and plain text.
HTML arrives with web sources (B5); PDF / Office later — each converter only maps input -> Markdown.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import yaml

_FRONT_MATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n", re.DOTALL)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_FENCE = re.compile(r"^\s*(```|~~~)")
# :::image type="content" source="x.png" alt-text="Diagram of …":::
_MS_IMAGE = re.compile(r':::image\b[^\n]*?alt-text="([^"]*)"[^\n]*?:::', re.IGNORECASE)
_MS_DIRECTIVE_LINE = re.compile(r"^\s*:::\s*[\w-]+.*$")          # ::: zone pivot=…, :::image-end:::, …
_MS_INCLUDE_LINE = re.compile(r"^\s*\[!INCLUDE\b.*$", re.IGNORECASE)
_MS_CALLOUT = re.compile(r"^(\s*>\s*)\[!(NOTE|TIP|IMPORTANT|CAUTION|WARNING)\]\s*$", re.IGNORECASE)
_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_MD_LINK = re.compile(r"(?<!!)\[([^\]]+)\]\([^)]*\)")
_HR_LINE = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")
_H1 = re.compile(r"^#\s+(.+?)\s*#*\s*$", re.MULTILINE)


@dataclass
class Extracted:
    title: str
    content_md: str
    description: str | None = None
    updated_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def _parse_front_matter(text: str) -> tuple[dict[str, Any], str]:
    match = _FRONT_MATTER.match(text)
    if not match:
        return {}, text
    try:
        data = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        data = {}
    return (data if isinstance(data, dict) else {}), text[match.end():]


def _parse_date(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(str(value).strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _clean_line(line: str) -> str | None:
    """Clean one non-code line; None = drop it."""
    if _MS_INCLUDE_LINE.match(line):
        return None
    line = _MS_IMAGE.sub(lambda m: f"*(Image: {m.group(1).strip()})*" if m.group(1).strip() else "", line)
    if _MS_DIRECTIVE_LINE.match(line) or _HR_LINE.match(line):
        return None
    callout = _MS_CALLOUT.match(line)
    if callout:
        return f"{callout.group(1)}**{callout.group(2).capitalize()}:**"
    line = _MD_IMAGE.sub(lambda m: f"*(Image: {m.group(1).strip()})*" if m.group(1).strip() else "", line)
    line = _MD_LINK.sub(r"\1", line)  # keep link text; drop (often relative) targets
    return line.rstrip()


def clean_markdown(body: str) -> str:
    """Remove publishing markup, keep headings, lists, tables and code blocks unchanged."""
    body = _HTML_COMMENT.sub("", body.replace("\r\n", "\n").replace("\r", "\n"))
    out: list[str] = []
    in_code = False
    for line in body.split("\n"):
        if _FENCE.match(line):
            in_code = not in_code
            out.append(line.rstrip())
            continue
        if in_code:
            out.append(line.rstrip())
            continue
        cleaned = _clean_line(line)
        if cleaned is not None:
            out.append(cleaned)
    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def extract_markdown(raw: str, *, fallback_title: str) -> Extracted:
    front, body = _parse_front_matter(raw.lstrip("﻿"))
    content = clean_markdown(body)
    h1 = _H1.search(content)
    title = str(front.get("title") or (h1.group(1) if h1 else "") or fallback_title).strip()
    if not h1:
        content = f"# {title}\n\n{content}"
    description = front.get("description")
    metadata = {
        k: str(front[k])
        for k in ("ms.topic", "ms.date", "author", "ms.author")
        if front.get(k) is not None
    }
    return Extracted(
        title=title,
        content_md=content,
        description=str(description).strip() if description else None,
        updated_at=_parse_date(front.get("ms.date") or front.get("date")),
        metadata=metadata,
    )


def extract_text(raw: str, *, fallback_title: str) -> Extracted:
    body = raw.lstrip("﻿").replace("\r\n", "\n").strip()
    return Extracted(title=fallback_title, content_md=f"# {fallback_title}\n\n{body}\n")


def extract_file(name: str, raw: str, *, fallback_title: str) -> Extracted:
    """Pick the converter by file extension."""
    lower = name.lower()
    if lower.endswith((".md", ".markdown")):
        return extract_markdown(raw, fallback_title=fallback_title)
    if lower.endswith(".txt"):
        return extract_text(raw, fallback_title=fallback_title)
    raise ValueError(f"Unsupported file type for {name} (Phase 1: .md, .markdown, .txt)")
