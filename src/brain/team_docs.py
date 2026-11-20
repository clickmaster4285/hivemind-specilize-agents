"""Team knowledge (BACKLOG L30 T4; the user's design, 2026-11-20): documents an admin uploads for one team — runbooks,
policies — found by that team's members only, in the knowledge base's TEAM scope (migration 005).

Each team has one source (`team-<name>`, kind upload, scope team, owner 'team:<name>'); each file is one document of
it, read into Markdown here (PDF with pypdf, Word with python-docx, Markdown, text, HTML), cut into chunks and
embedded on the CPU like any other knowledge. The same file name again replaces it (a new version); removing it
removes its chunks. Every search of a member of that team finds it (src/brain/db.py sets brain.team).
"""

from __future__ import annotations

import hashlib
import io
import re
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from psycopg.types.json import Jsonb

from src.brain.chunking import CHUNKER_VERSION, chunk_markdown
from src.brain.db import connect
from src.brain.embed import embed_texts, passage_text
from src.brain.extract import Extracted, extract_file
from src.core.config import Config

MAX_UPLOAD_BYTES = 20 * 1024 * 1024        # a long manual as PDF; bigger ones are better split
TEXT_KINDS = (".md", ".markdown", ".txt", ".html", ".htm")
SUPPORTED = (".pdf", ".docx", *TEXT_KINDS)
SOURCE_PREFIX = "team-"
UPLOAD_URL = "upload://{name}"
# Word paragraph styles that are headings: "Heading 2" → "##"
WORD_HEADING_RE = re.compile(r"^Heading (\d)$")


class TeamDocError(ValueError):
    """A file that cannot become team knowledge: wrong kind, too big, no text in it."""


def owner_of(team: str) -> str:
    return f"team:{team}"


def _slug(team: str) -> str:
    return SOURCE_PREFIX + (re.sub(r"[^a-z0-9]+", "-", team.lower()).strip("-") or "team")


def read_upload(name: str, data: bytes) -> Extracted:
    """The file's text as Markdown, by its kind."""
    lower = name.lower()
    title = re.sub(r"\.[^.]+$", "", name).replace("_", " ").replace("-", " ").strip() or name
    if not lower.endswith(SUPPORTED):
        raise TeamDocError(f"{name}: use PDF, Word (.docx), Markdown, text or HTML")
    if len(data) > MAX_UPLOAD_BYTES:
        raise TeamDocError(f"{name} is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB — split it")
    if lower.endswith(".pdf"):
        doc = Extracted(title=title, content_md=f"# {title}\n\n" + _pdf_text(data))
    elif lower.endswith(".docx"):
        doc = Extracted(title=title, content_md=f"# {title}\n\n" + _word_text(data))
    else:
        doc = extract_file(name, data.decode("utf-8", errors="replace"), fallback_title=title)
    if len(re.findall(r"\w+", doc.content_md)) < len(re.findall(r"\w+", title)) + 3:
        raise TeamDocError(f"{name} has no readable text (a scanned PDF needs OCR first)")
    return doc


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except (PdfReadError, ValueError, KeyError) as exc:
        raise TeamDocError(f"the PDF could not be read: {exc}") from exc
    return "\n\n".join(f"## Page {n}\n\n{text}" for n, text in enumerate(pages, 1) if text)


def _word_text(data: bytes) -> str:
    import docx

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001 — python-docx raises many kinds for a broken file
        raise TeamDocError(f"the Word file could not be read: {type(exc).__name__}") from exc
    lines = []
    for para in document.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        level = WORD_HEADING_RE.match(para.style.name if para.style is not None else "")
        lines.append(f"{'#' * (int(level.group(1)) + 1)} {text}" if level else text)
    for table in document.tables:                    # tables as Markdown rows, after the text
        for row in table.rows:
            lines.append("| " + " | ".join(cell.text.strip() for cell in row.cells) + " |")
    return "\n\n".join(lines)


def _source(conn, team: str) -> dict[str, Any]:
    """The team's source row, made on its first upload."""
    slug = _slug(team)
    row = conn.execute("SELECT id, collection_id FROM brain.sources WHERE slug = %s", (slug,)).fetchone()
    if row is None:
        row = conn.execute(
            """INSERT INTO brain.sources (slug, kind, title, publisher, reason, added_by, collection_id, scope,
                                          owner_id, enabled)
               VALUES (%s, 'upload', %s, %s, 'documents an admin uploaded for this team', 'admin',
                       (SELECT id FROM brain.collections WHERE slug = 'general'), 'team', %s, false)
               RETURNING id, collection_id""",
            (slug, f"{team} team documents", team, owner_of(team))).fetchone()
    return {"id": row[0], "collection_id": row[1]}


def add(config: Config, team: str, name: str, data: bytes, *, uploaded_by: str,
        embed: Callable[[list[str]], list[list[float]]] | None = None) -> dict[str, Any]:
    """Store one file as the team's knowledge (a new version when the same name was uploaded before)."""
    doc = read_upload(name, data)
    chunks = chunk_markdown(doc.content_md)
    if not chunks:
        raise TeamDocError(f"{name} has no readable text")
    vectors = (embed or (lambda texts: embed_texts(texts, config)))(
        [passage_text(doc.title, c.heading_path, c.content) for c in chunks])
    url, owner = UPLOAD_URL.format(name=name), owner_of(team)
    content_hash = hashlib.sha256(doc.content_md.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)
    with connect(config, system=True, team=team) as conn:
        source = _source(conn, team)
        current = conn.execute("""SELECT id, version FROM brain.documents
                                  WHERE source_id = %s AND url = %s AND status = 'active'""",
                               (source["id"], url)).fetchone()
        if current:
            conn.execute("DELETE FROM brain.documents WHERE id = %s", (current[0],))     # its chunks go with it
        doc_id = conn.execute(
            """INSERT INTO brain.documents (source_id, url, title, fetched_at, updated_at, content_md, content_hash,
                                            version, metadata, collection_id, scope, owner_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'team', %s) RETURNING id""",
            (source["id"], url, doc.title[:500], now, now, doc.content_md, content_hash,
             (current[1] + 1) if current else 1,
             Jsonb({"file": name, "uploaded_by": uploaded_by, "bytes": len(data), "chunker": str(CHUNKER_VERSION)}),
             source["collection_id"], owner)).fetchone()[0]
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO brain.chunks (document_id, chunk_index, heading_path, content, token_count, embedding,
                                             collection_id, scope, owner_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, 'team', %s)""",
                [(doc_id, c.index, c.heading_path or None, c.content, c.token_count, _vector(v),
                  source["collection_id"], owner) for c, v in zip(chunks, vectors)])
    return {"id": doc_id, "file": name, "title": doc.title, "chunks": len(chunks), "replaced": bool(current)}


def _vector(values):
    from pgvector import Vector

    return Vector(values)


def listing(config: Config, team: str) -> list[dict[str, Any]]:
    """The team's documents, newest first."""
    with connect(config, team=team) as conn:
        rows = conn.execute(
            """SELECT d.id, d.title, d.metadata, d.fetched_at, count(c.id)
               FROM brain.documents d JOIN brain.sources s ON s.id = d.source_id
               LEFT JOIN brain.chunks c ON c.document_id = d.id
               WHERE s.slug = %s AND d.status = 'active'
               GROUP BY d.id ORDER BY d.fetched_at DESC""", (_slug(team),)).fetchall()
    return [{"id": i, "title": title, "file": meta.get("file", ""), "uploaded_by": meta.get("uploaded_by", ""),
             "bytes": meta.get("bytes", 0), "uploaded_at": at.isoformat(), "chunks": chunks}
            for i, title, meta, at, chunks in rows]


def remove(config: Config, team: str, doc_id: int) -> bool:
    """Remove one of the team's documents (its chunks go with it). False when it is not this team's."""
    with connect(config, system=True, team=team) as conn:
        return conn.execute(
            """DELETE FROM brain.documents d USING brain.sources s
               WHERE d.id = %s AND s.id = d.source_id AND s.slug = %s RETURNING d.id""",
            (doc_id, _slug(team))).fetchone() is not None
