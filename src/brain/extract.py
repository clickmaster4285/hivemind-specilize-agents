"""Turn source files into the brain's one internal format: clean Markdown (doc/BRAIN_DESIGN.md §4.2).

Inputs: Markdown (incl. Microsoft Learn flavour), plain text, HTML web pages (main content only).
PDF / Office later — each converter only maps input -> Markdown.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import yaml
from bs4 import BeautifulSoup
from markdownify import MarkdownConverter

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


# Where the real content of a documentation page usually lives, most specific first
_MAIN_SELECTORS = ("main", "article", "[role=main]", "div.body", "div#content", "div.content")
# Page furniture that is never knowledge
_BOILERPLATE = (
    "script, style, noscript, template, iframe, svg, form, button, nav, header, footer, aside, "
    "a.headerlink, .headerlink, .sphinxsidebar, .related, .breadcrumbs, .toc, #toc, "
    "[role=navigation], [role=banner], [role=contentinfo], [aria-hidden=true]"
)
_DATE_META = ("article:modified_time", "last-modified", "dcterms.modified", "date")


class _DocsMarkdown(MarkdownConverter):
    """markdownify with fenced code blocks that keep their language (```python)."""

    def convert_pre(self, el, text, parent_tags):  # noqa: ARG002 — markdownify hook signature
        lang = ""
        classes = (el.get("class") or []) + ((el.parent.get("class") or []) if el.parent else [])
        for cls in classes:
            match = re.match(r"(?:language|highlight|lang)-([\w+#-]+)", cls)
            if match:
                lang = match.group(1)
                break
        code = el.get_text().rstrip("\n")
        return f"\n\n```{lang}\n{code}\n```\n\n"


def _html_date(soup: BeautifulSoup) -> datetime | None:
    for name in _DATE_META:
        tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            value = tag["content"].strip()
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                parsed = _parse_date(value[:10])
                if parsed:
                    return parsed
    return None


def http_date(value: str | None) -> datetime | None:
    """Parse an HTTP Last-Modified header."""
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None


def extract_html(raw: str, *, fallback_title: str) -> Extracted:
    """Main content of a web page -> clean Markdown (navigation, scripts, anchors removed)."""
    soup = BeautifulSoup(raw, "html.parser")
    page_title = soup.title.get_text(" ", strip=True) if soup.title else ""
    description_tag = soup.find("meta", attrs={"name": "description"})
    updated = _html_date(soup)
    main = next((m for m in (soup.select_one(s) for s in _MAIN_SELECTORS) if m is not None), None)
    main = main or soup.body or soup
    for tag in main.select(_BOILERPLATE):
        tag.decompose()
    h1 = main.find("h1")
    title = (h1.get_text(" ", strip=True) if h1 else "") or page_title or fallback_title
    markdown = _DocsMarkdown(heading_style="ATX", bullets="-", strip=["img"]).convert_soup(main)
    content = clean_markdown(markdown)
    if not _H1.search(content):
        content = f"# {title}\n\n{content}"
    return Extracted(
        title=title.replace("¶", "").strip(),
        content_md=content,
        description=description_tag.get("content", "").strip() or None if description_tag else None,
        updated_at=updated,
        metadata={"page_title": page_title} if page_title else {},
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
    if lower.endswith((".html", ".htm")):
        return extract_html(raw, fallback_title=fallback_title)
    raise ValueError(f"Unsupported file type for {name} (supported: .md, .markdown, .txt, .html)")
