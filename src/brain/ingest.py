"""Ingestion pipeline: source files -> documents + chunks + embeddings (doc/BRAIN_DESIGN.md §4).

Usage (from the repo root):
    python -m src.brain.ingest                    sync sources.yaml, ingest every enabled source
    python -m src.brain.ingest --source <slug>    ingest one source
    python -m src.brain.ingest --sync-only        only sync sources.yaml -> brain.sources

Per file: extract -> hash -> unchanged? just mark checked : chunk -> embed -> store a new
version (old one marked 'superseded') in ONE transaction, so a failure never leaves half a
document. Pages missing from the source for 2 runs in a row become 'removed' (kept for audit).
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from pgvector import Vector
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from src.brain.chunking import CHUNKER_VERSION, Chunk, chunk_markdown
from src.brain.db import BrainDBError, connect
from src.brain.embed import EmbedError, embed_texts, passage_text
from src.brain.extract import extract_file
from src.brain.fetch import FetchError, SourceFile, prepare_source_files
from src.brain.migrate import MigrationError, migrate
from src.brain.sources import RegistryError, load_registry, sync_sources
from src.config import Config

Embedder = Callable[[list[str]], list[list[float]]]
REMOVE_AFTER_MISSING_RUNS = 2


@dataclass
class IngestStats:
    slug: str
    new: int = 0
    changed: int = 0
    same: int = 0
    rechunked: int = 0
    removed: int = 0
    failed: int = 0
    chunks: int = 0
    status: str = "running"
    seconds: float = 0.0
    errors: list[str] = field(default_factory=list)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load_source(conn, slug: str) -> dict[str, Any] | None:
    cur = conn.cursor(row_factory=dict_row)
    return cur.execute(
        """SELECT s.*, c.slug AS collection
           FROM brain.sources s JOIN brain.collections c ON c.id = s.collection_id
           WHERE s.slug = %s""",
        (slug,),
    ).fetchone()


def _active_version(conn, source_id: int, url: str, *, lock: bool = False):
    """(id, content_hash, version, chunker version) of the active document, or None."""
    sql = """SELECT id, content_hash, version, metadata->>'chunker' FROM brain.documents
             WHERE source_id = %s AND url = %s AND status = 'active'"""
    return conn.execute(sql + (" FOR UPDATE" if lock else ""), (source_id, url)).fetchone()


def _insert_chunks(conn, doc_id: int, source: dict[str, Any], chunks: list[Chunk], vectors) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO brain.chunks
                 (document_id, chunk_index, heading_path, content, token_count, embedding,
                  collection_id, scope, owner_id, agent_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            [
                (
                    doc_id, c.index, c.heading_path or None, c.content, c.token_count, Vector(v),
                    source["collection_id"], source["scope"], source["owner_id"], source["agent_id"],
                )
                for c, v in zip(chunks, vectors)
            ],
        )


def _ingest_file(
    source: dict[str, Any],
    file: SourceFile,
    *,
    revision: str | None,
    language: str,
    config: Config,
    embed: Embedder,
) -> tuple[str, int]:
    """Returns ('new' | 'changed' | 'same' | 'rechunked', chunks written)."""
    raw = file.path.read_text(encoding="utf-8", errors="replace")
    doc = extract_file(file.path.name, raw, fallback_title=file.path.stem.replace("-", " "))
    content_hash = hashlib.sha256(doc.content_md.encode("utf-8")).hexdigest()

    with connect(config, system=True) as conn:
        current = _active_version(conn, source["id"], file.url)
        unchanged = bool(current and current[1] == content_hash)
        if unchanged and current[3] == str(CHUNKER_VERSION):
            conn.execute(
                "UPDATE brain.documents SET fetched_at = %s, metadata = metadata - 'missing_runs' WHERE id = %s",
                (_now(), current[0]),
            )
            return "same", 0

    # Slow part (embedding) runs outside any transaction
    chunks = chunk_markdown(doc.content_md)
    if not chunks:
        raise ValueError("no text left after cleaning")
    vectors = embed([passage_text(doc.title, c.heading_path, c.content) for c in chunks])

    if unchanged:
        # Same text, made by an older chunker: replace its chunks in place (no new version)
        with connect(config, system=True) as conn:
            current = _active_version(conn, source["id"], file.url, lock=True)
            if not current or current[1] != content_hash:
                raise ValueError("document changed during re-chunking; it will be picked up next run")
            conn.execute("DELETE FROM brain.chunks WHERE document_id = %s", (current[0],))
            _insert_chunks(conn, current[0], source, chunks, vectors)
            conn.execute(
                """UPDATE brain.documents
                   SET fetched_at = %s, metadata = (metadata - 'missing_runs') || %s
                   WHERE id = %s""",
                (_now(), Jsonb({"chunker": str(CHUNKER_VERSION)}), current[0]),
            )
        return "rechunked", len(chunks)

    metadata = {**doc.metadata, "path": file.rel_path, "chunker": str(CHUNKER_VERSION)}
    if doc.description:
        metadata["description"] = doc.description
    if revision:
        metadata["revision"] = revision

    with connect(config, system=True) as conn:
        current = _active_version(conn, source["id"], file.url, lock=True)
        if current and current[1] == content_hash:
            return "same", 0  # another run stored it meanwhile
        if current:
            conn.execute("UPDATE brain.documents SET status = 'superseded' WHERE id = %s", (current[0],))
        doc_id = conn.execute(
            """INSERT INTO brain.documents
                 (source_id, url, title, language, updated_at, fetched_at, content_md, content_hash,
                  version, metadata, collection_id, scope, owner_id, agent_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (
                source["id"], file.url, doc.title, language, doc.updated_at, _now(), doc.content_md,
                content_hash, (current[2] + 1) if current else 1, Jsonb(metadata),
                source["collection_id"], source["scope"], source["owner_id"], source["agent_id"],
            ),
        ).fetchone()[0]
        _insert_chunks(conn, doc_id, source, chunks, vectors)
    return ("changed" if current else "new"), len(chunks)


def _mark_missing(source: dict[str, Any], listed_urls: list[str], config: Config) -> int:
    """Active documents no longer in the source: count the miss; remove after N runs in a row."""
    with connect(config, system=True) as conn:
        rows = conn.execute(
            """UPDATE brain.documents
               SET metadata = jsonb_set(metadata, '{missing_runs}',
                     to_jsonb(coalesce((metadata->>'missing_runs')::int, 0) + 1)),
                   status = CASE WHEN coalesce((metadata->>'missing_runs')::int, 0) + 1 >= %s
                                 THEN 'removed' ELSE status END
               WHERE source_id = %s AND status = 'active' AND NOT (url = ANY(%s))
               RETURNING status""",
            (REMOVE_AFTER_MISSING_RUNS, source["id"], listed_urls),
        ).fetchall()
    return sum(1 for (status,) in rows if status == "removed")


def ingest_source(
    slug: str,
    config: Config | None = None,
    *,
    embed: Embedder | None = None,
    log: Callable[[str], None] = print,
) -> IngestStats:
    """Ingest one enabled source. Records an ingest_runs row whatever happens."""
    config = config or Config.from_env()
    embed = embed or (lambda texts: embed_texts(texts, config))
    started = time.monotonic()
    stats = IngestStats(slug=slug)

    with connect(config, system=True) as conn:
        source = _load_source(conn, slug)
        if source is None:
            raise ValueError(f"unknown source '{slug}' — run the sync first")
        if not source["enabled"]:
            raise ValueError(f"source '{slug}' is disabled")
        run_id = conn.execute(
            "INSERT INTO brain.ingest_runs (source_id, status) VALUES (%s, 'running') RETURNING id",
            (source["id"],),
        ).fetchone()[0]

    files: list[SourceFile] = []
    fatal: str | None = None
    try:
        log(f"[{slug}] fetching…")
        prepared = prepare_source_files(source)
        listed_urls = [f.url for f in prepared.files]
        files = prepared.files[: source["max_pages"]] if source["max_pages"] else prepared.files
        language = (source["config"] or {}).get("language", "en")
        log(f"[{slug}] {len(prepared.files)} files found, ingesting {len(files)}")
        for i, file in enumerate(files, 1):
            try:
                result, n = _ingest_file(
                    source, file, revision=prepared.revision, language=language, config=config, embed=embed
                )
            except (EmbedError, BrainDBError):
                raise  # infrastructure is down: stop the run instead of failing every file
            except Exception as exc:  # noqa: BLE001 — one bad file must not stop the source
                stats.failed += 1
                stats.errors.append(f"{file.rel_path}: {exc}")
                log(f"  [{i}/{len(files)}] FAILED  {file.rel_path}: {exc}")
                continue
            setattr(stats, result, getattr(stats, result) + 1)
            stats.chunks += n
            log(f"  [{i}/{len(files)}] {result:<7} {file.rel_path}" + (f"  ({n} chunks)" if n else ""))
        stats.removed = _mark_missing(source, listed_urls, config)
    except (FetchError, EmbedError, BrainDBError, OSError) as exc:
        fatal = str(exc)
        stats.errors.append(fatal)
        log(f"[{slug}] STOPPED: {exc}")
    finally:
        if fatal or (files and stats.failed == len(files)):
            stats.status = "failed"
        else:
            stats.status = "partial" if stats.failed else "ok"
        stats.seconds = round(time.monotonic() - started, 1)
        error_text = "\n".join(stats.errors[:5])[:2000] or None
        changed = stats.new + stats.changed + stats.removed > 0
        with connect(config, system=True) as conn:
            conn.execute(
                """UPDATE brain.ingest_runs SET finished_at = now(), status = %s, docs_new = %s,
                     docs_changed = %s, docs_same = %s, docs_removed = %s, error = %s
                   WHERE id = %s""",
                (stats.status, stats.new, stats.changed, stats.same, stats.removed, error_text, run_id),
            )
            conn.execute(
                """UPDATE brain.sources SET last_checked_at = now(), last_error = %s,
                     last_changed_at = CASE WHEN %s THEN now() ELSE last_changed_at END
                   WHERE id = %s""",
                (error_text, changed, source["id"]),
            )
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", help="ingest only this source slug")
    parser.add_argument("--sync-only", action="store_true", help="only sync sources.yaml into the database")
    args = parser.parse_args(argv)
    config = Config.from_env()
    try:
        migrate(config)
        report = sync_sources(config)
        print(
            f"Sources synced — added: {report.added or '-'}  updated: {report.updated or '-'}  "
            f"disabled: {report.disabled or '-'}  unchanged: {report.unchanged or '-'}"
        )
        if args.sync_only:
            return 0
        slugs = [args.source] if args.source else [s.slug for s in load_registry() if s.enabled]
        results = [ingest_source(slug, config) for slug in slugs]
    except (RegistryError, MigrationError, BrainDBError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print("\nsource                      status   new  changed  same  rechunked  removed  failed  chunks   time")
    for s in results:
        print(
            f"{s.slug:<27} {s.status:<8} {s.new:>3} {s.changed:>8} {s.same:>5} {s.rechunked:>10} "
            f"{s.removed:>8} {s.failed:>7} {s.chunks:>7} {s.seconds:>6}s"
        )
    return 0 if all(s.status == "ok" for s in results) else 2


if __name__ == "__main__":
    sys.exit(main())
