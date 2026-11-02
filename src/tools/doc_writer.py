"""Write a NEW document section by section, checking each one, then convert it to the asked format.

Why not the agentic worker: it had to emit the whole document as one JSON tool call, which the 9B model
cuts off → invalid JSON → a short file or none (ISSUES_LOG O8). Here every model reply is small:
  1. outline  — a tiny JSON plan: topic title, format, sections (what each covers)
  2. sections — one plain-Markdown body per call, streamed into the chat, knowing what the others cover
  3. check    — each section is checked (empty, repeats an earlier one, unbalanced code fence) and rewritten once
  4. file     — the assembled Markdown goes through the same converters as "Download as" (answer_export)
The finished document is also the chat answer, so it can be re-exported in any other format later.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from typing import Any

from src.core.clock import current_time_text
from src.core.config import Config
from src.core.conversation import format_conversation_block
from src.jobs import live
from src.llm.llm_client import LLMClient, LLMStopped
from src.tools.answer_export import FORMATS, export_answer, export_format
from src.tools.artifacts import artifact_event

ROLE = "Document writer"

OUTLINE_SYSTEM = """You plan a document the user asked for. Output ONLY JSON, no prose, no code fence:
{{"title": "...", "format": "...", "sections": [{{"heading": "...", "covers": "..."}}]}}
- title: a short, specific title naming the topic (3-8 words), e.g. "Docker Networking Basics" — never
  "Document", "Report" or the request repeated word for word
- format: the file type the user asked for, one of {formats}, or xlsx for a spreadsheet; "{default}" if not said
- sections: 3 to 8 in a logical order (for slides, one section per slide); "covers" = one line on what
  that section says, so sections do not overlap
- If the request refers to the prior conversation ("this", "what we discussed"), plan from that content.
- Write the title and headings in the language of the request."""

SECTION_SYSTEM = """You write ONE section of the document "{title}". Current date: {now}.
Write only this section's body in Markdown. Do not repeat the section heading (it is added for you).
Use bullets, ### sub-headings, numbered steps or a Markdown table where they help. {style}
Stay on what this section covers; the other sections cover the rest. Never invent sources or links.
Write in the language of the request."""

STYLE = {"pptx": "This is ONE slide: 3-6 short bullets, no paragraphs."}
DEFAULT_STYLE = "Be complete but concise."


def _json_object(text: str) -> dict[str, Any] | None:
    match = re.search(r"\{.*\}", text or "", flags=re.DOTALL)
    try:
        data = json.loads(match.group(0)) if match else None
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def plan_outline(request: str, *, chat: Callable[..., str], model: str, prior: str,
                 default_format: str) -> dict[str, Any] | None:
    """→ {"title", "format", "sections": [{"heading", "covers"}]}, or None when the model gave no usable plan."""
    raw = chat(system=OUTLINE_SYSTEM.format(formats=", ".join(FORMATS), default=default_format),
               user=f"{prior}\n\n## Request\n{request}".strip(), model=model, temperature=0.2, max_retries=1)
    data = _json_object(raw) or {}
    sections = [
        {"heading": str(s.get("heading") or "").strip(), "covers": str(s.get("covers") or "").strip()}
        for s in data.get("sections") or [] if isinstance(s, dict) and str(s.get("heading") or "").strip()
    ]
    title = str(data.get("title") or "").strip().strip('"')
    if not sections or not title:
        return None
    return {"title": title, "format": str(data.get("format") or default_format).strip().lower(),
            "sections": sections}


def _norm(text: str) -> str:
    return re.sub(r"\W+", " ", text).strip().lower()


def tidy_section(body: str, heading: str) -> str:
    """Drop a repeated heading the model put on top and close an unbalanced code fence."""
    lines = body.strip().splitlines()
    if lines and lines[0].lstrip().startswith("#") and _norm(lines[0].lstrip("# ")) == _norm(heading):
        lines = lines[1:]
    text = "\n".join(lines).strip()
    if text.count("```") % 2:
        text += "\n```"
    return text


def section_problem(body: str, earlier: list[str]) -> str | None:
    """Why a written section is not usable (None = fine): empty, or a repeat of an earlier section."""
    if not _norm(body):
        return "it came back empty"
    if any(_norm(body) == _norm(prev) or _norm(body) in _norm(prev) for prev in earlier):
        return "it repeated an earlier section"
    return None


def run_document_writer(query: str, *, job_id: str, config: Config, model: str,
                        conversation: list[dict[str, str]] | None = None,
                        on_progress: Callable[[dict], None] | None = None,
                        format: str | None = None, request: str | None = None,
                        **_: object) -> tuple[str, list[str], float] | None:
    """Plan → write each section (checked) → file. None when this is not ours to write (a spreadsheet,
    or no usable outline) — the caller then hands the request to the agentic worker."""
    client = LLMClient(config)
    emit = on_progress or (lambda event: None)
    started = time.monotonic()
    asked = request or query
    prior = format_conversation_block(conversation or [])
    outline = plan_outline(asked if asked == query else f"{query}\n\n(What the file should contain: {asked})",
                           chat=client.chat, model=model, prior=prior,
                           default_format=export_format(format) or "docx")
    kind = export_format(format) or export_format(outline["format"] if outline else None)
    if outline is None or kind is None:
        return None

    agent = {"agent_id": "assistant", "name": "Assistant", "role": ROLE, "stage": "chat", "job_id": job_id}
    emit({"type": "session_start", "mode": "chat", "query": query})
    emit({"type": "agent_start", **agent, "model": model})
    status = lambda message: emit({"type": "status", "agent_id": "assistant", "message": message})  # noqa: E731
    sections = outline["sections"]
    status(f"Outline for “{outline['title']}”: " + " · ".join(s["heading"] for s in sections))

    errors: list[str] = []
    written: list[str] = []
    doc = f"# {outline['title']}\n\n"
    plan_lines = "\n".join(f"- {s['heading']}: {s['covers']}" for s in sections)
    system = SECTION_SYSTEM.format(title=outline["title"], now=current_time_text(),
                                   style=STYLE.get(kind, DEFAULT_STYLE))
    stopped = False
    for n, section in enumerate(sections, 1):
        head = f"## {section['heading']}\n\n"
        user = (f"{prior}\n\n## Request\n{asked}\n\n## Document plan\n{plan_lines}\n\n"
                f"## Write now: section {n} of {len(sections)} — {section['heading']}\n{section['covers']}").strip()
        body, problem = "", None
        for attempt in range(2):                     # write, check, and rewrite once if the check fails
            status(f"Writing section {n}/{len(sections)}: {section['heading']}"
                   + (f" (again — {problem})" if attempt else "…"))
            try:
                raw = client.chat(system=system, user=user, model=model, temperature=0.4,
                                  on_delta=lambda text: live.set_text(job_id, "assistant", doc + head + text),
                                  should_continue=lambda: not live.is_stopped(job_id))
            except LLMStopped as exc:
                body, stopped = tidy_section(exc.partial or "", section["heading"]), True
                break
            body = tidy_section(raw, section["heading"])
            problem = section_problem(body, written)
            if problem is None:
                break
        if problem and not stopped:
            errors.append(f"section “{section['heading']}”: {problem}")
        if body:
            written.append(body)
            doc += head + body + "\n\n"
        if stopped:
            break

    doc = doc.strip()
    if stopped:
        doc += "\n\n_— stopped —_"
    else:
        status(f"Creating the {kind} file…")
        try:
            meta = export_answer(doc, kind, title=outline["title"], job_id=job_id, config=config)
            emit(artifact_event(meta, job_id=job_id))
        except Exception as exc:  # noqa: BLE001 — the written document stays in the chat either way
            errors.append(f"could not create the {kind} file: {exc}")
    elapsed = time.monotonic() - started
    live.finish_agent(job_id, "assistant")
    emit({"type": "agent_done", **agent, "elapsed_seconds": elapsed, "output": doc})
    emit({"type": "session_done", "total_seconds": elapsed, "errors": errors, "mode": "chat"})
    return doc, errors, elapsed
