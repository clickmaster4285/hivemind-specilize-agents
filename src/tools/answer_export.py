"""Turn an answer that is already in the chat into a file: md, txt, docx, pdf, pptx or html.

No model call: the answer's own markdown goes straight through the document writers (src/agentic/tools.py),
so a long answer is never cut by the history caps or re-typed by the model (ISSUES_LOG O8, BACKLOG L10).
Used by the "Download as" menu under an answer and by "make this a PDF" in chat.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy import select

from src.core.config import Config
from src.tools.artifacts import artifact_event, safe_filename, write_text
from src.tools.doc_builder import markdown_to_plain

FORMATS = ("md", "txt", "docx", "pdf", "pptx", "html")
_ALIASES = {"markdown": "md", "text": "txt", "word": "docx", "doc": "docx", "ppt": "pptx", "powerpoint": "pptx",
            "slides": "pptx", "deck": "pptx", "htm": "html", "web page": "html"}
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
EXPORT_ROLE = "Document"


def export_format(raw: str | None) -> str | None:
    """The export format the user asked for ("Word", ".PDF", "slides" …), or None when we cannot export it."""
    name = str(raw or "").strip().lower().lstrip(".")
    name = _ALIASES.get(name, name)
    return name if name in FORMATS else None


def split_sections(markdown: str, fallback_title: str) -> tuple[str, list[dict[str, str]]]:
    """→ (title, sections) cut at the answer's own top heading level. A lone leading H1 becomes the title;
    text before the first heading is an "Overview" section. Headings inside code fences are not headings."""
    lines = (markdown or "").strip().splitlines()
    headings: list[tuple[int, int, str]] = []          # (line index, level, text)
    fenced = False
    for i, line in enumerate(lines):
        if _FENCE.match(line):
            fenced = not fenced
        elif not fenced and (m := _HEADING.match(line.strip())):
            headings.append((i, len(m.group(1)), m.group(2).strip()))
    title = fallback_title.strip() or "Answer"
    if headings and headings[0][1] == 1 and sum(1 for h in headings if h[1] == 1) == 1 \
            and not "".join(lines[: headings[0][0]]).strip():
        title = headings[0][2]
        lines[headings[0][0]] = ""
        headings = headings[1:]
    if not headings:
        return title, [{"heading": title, "body": "\n".join(lines).strip()}]
    top = min(level for _, level, _ in headings)
    cuts = [(i, text) for i, level, text in headings if level == top]
    sections: list[dict[str, str]] = []
    intro = "\n".join(lines[: cuts[0][0]]).strip()
    if intro:
        sections.append({"heading": "Overview", "body": intro})
    for n, (start, text) in enumerate(cuts):
        end = cuts[n + 1][0] if n + 1 < len(cuts) else len(lines)
        sections.append({"heading": text, "body": "\n".join(lines[start + 1:end]).strip()})
    return title, sections


def _sources_markdown(sources: list[dict[str, Any]] | None) -> str:
    rows = [f"- [{s.get('n')}] {s.get('title') or s.get('url') or ''}" + (f" — {s['url']}" if s.get("url") else "")
            for s in (sources or []) if isinstance(s, dict)]
    return "\n".join(rows)


def export_answer(text: str, fmt: str, *, title: str, job_id: str, config: Config,
                  sources: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Write the answer as a file in the job's downloads → its artifact meta (filename, mime, relpath …)."""
    from src.agentic.tools import run_tool

    kind = export_format(fmt)
    if kind is None:
        raise ValueError(f"cannot export to {fmt!r} (use one of {', '.join(FORMATS)})")
    doc_title, sections = split_sections(text, title)
    cited = _sources_markdown(sources)
    if cited:
        sections.append({"heading": "Sources", "body": cited})
    filename = safe_filename(f"{doc_title[:60]}.{kind}", default=f"answer.{kind}")
    if kind in ("md", "txt"):
        body = "\n\n".join(f"## {s['heading']}\n\n{s['body']}" if s["heading"] != doc_title else s["body"]
                           for s in sections)
        content = f"# {doc_title}\n\n{body}\n"
        return write_text(config, job_id, filename, content if kind == "md" else markdown_to_plain(content) + "\n",
                          kind="document")
    result = json.loads(run_tool(f"write_{kind}", {"filename": filename, "title": doc_title, "sections": sections},
                                 job_id=job_id, config=config))
    return result["artifact"]


def answer_in_events(events: list[dict[str, Any]], text: str | None = None) -> tuple[str, list[dict]] | None:
    """The assistant's answer (and its sources) recorded in a job's events: the one whose text is `text`, or
    the last one. None when the job has no written answer (an earlier export's "here is the file" is skipped,
    so "and as Word too" still finds the real answer)."""
    for event in reversed(events):
        output = str(event.get("output") or "").strip()
        if event.get("type") != "agent_done" or event.get("agent_id") != "assistant" or not output \
                or event.get("role") == EXPORT_ROLE:
            continue
        if text is None or output == text.strip():
            return output, [s for s in event.get("sources") or [] if isinstance(s, dict)]
    return None


def previous_answer(config: Config, job_id: str, user_id: int | None) -> tuple[str, list[dict], str] | None:
    """(answer, sources, question) of the latest earlier answer in the same chat as this job — read in full
    from its events, not from the trimmed history the browser sends."""
    from src.core.db import session_scope
    from src.core.models_db import Job, JobEvent

    with session_scope(config) as session:
        job = session.get(Job, job_id)
        if job is None:
            return None
        thread = json.loads(job.payload_json or "{}").get("thread_id")
        if not thread:
            return None
        earlier = session.scalars(
            select(Job).where(Job.user_id == (user_id if user_id is not None else job.user_id),
                              Job.created_at < job.created_at, Job.deleted_at.is_(None))
            .order_by(Job.created_at.desc()).limit(40)
        ).all()
        for prev in earlier:
            if json.loads(prev.payload_json or "{}").get("thread_id") != thread:
                continue
            rows = session.scalars(select(JobEvent.event_json).where(JobEvent.job_id == prev.id)
                                   .order_by(JobEvent.seq)).all()
            found = answer_in_events([json.loads(r) for r in rows])
            if found:
                return found[0], found[1], prev.query
    return None


def run_answer_export(query: str, *, job_id: str, config: Config, user_id: int | None,
                      on_progress: Callable[[dict], None] | None = None,
                      format: str | None = None, **_: object) -> list[str] | None:
    """"Make this a PDF": write the chat's previous answer as the asked format. None when there is nothing to
    export here (no earlier answer, or a format we only generate, like xlsx) — the caller then generates."""
    kind = export_format(format)
    found = previous_answer(config, job_id, user_id) if kind else None
    if kind is None or found is None:
        return None
    text, sources, question = found
    emit = on_progress or (lambda event: None)
    agent = {"agent_id": "assistant", "name": "Assistant", "role": EXPORT_ROLE, "stage": "chat", "job_id": job_id}
    started = time.monotonic()
    emit({"type": "session_start", "mode": "chat", "query": query})
    emit({"type": "agent_start", **agent, "model": "converter"})
    errors: list[str] = []
    try:
        meta = export_answer(text, kind, title=question, job_id=job_id, config=config, sources=sources)
        emit(artifact_event(meta, job_id=job_id))
        output = f"Here is the previous answer as **{meta['filename']}** — the whole answer, nothing re-written."
    except Exception as exc:  # noqa: BLE001 — a writer failure is reported in the chat, never a crash
        errors.append(str(exc))
        output = f"I could not create the {kind} file: {exc}"
    elapsed = time.monotonic() - started
    emit({"type": "agent_done", **agent, "elapsed_seconds": elapsed, "output": output})
    emit({"type": "session_done", "total_seconds": elapsed, "errors": errors, "mode": "chat"})
    return errors
