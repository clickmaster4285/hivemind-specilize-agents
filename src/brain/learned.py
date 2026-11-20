"""Knowledge learned from web searches (BACKLOG L31; the user's ask, 2026-11-20).

The first time a question needs the web, SearXNG is searched and the top pages are read (~2 s + 3–6 s); the
answer cites some of them. Those cited pages are kept here — page text only, public, nothing about who asked —
as documents of the `learned-from-web` source (collection `web`, migration 004), embedded on the CPU after the
answer. The next similar question, from anyone:
  - on the Web path, `recall` finds them and the answer is written from them without searching (status line:
    "Answering from pages saved from an earlier search");
  - on the knowledge-base path, the normal search finds them like any other document.

Each page has a freshness class, from the question that brought it: news (1 day), things that change — "latest",
versions, releases, prices (7 days), the rest (90 days). Search never returns an expired page
(retrieve.NOT_EXPIRED); `recall` also refuses a page older than THIS question allows (a news question does not take
a how-to page saved last month). Expired pages are deleted PURGE_AFTER after they expire.

Later (the user's order: pages first, then answers): whole answers reused for near-identical general questions.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from psycopg.types.json import Jsonb

from src.brain.chunking import CHUNKER_VERSION, chunk_markdown
from src.brain.db import connect
from src.brain.embed import embed_texts, passage_text
from src.core.config import Config

log = logging.getLogger(__name__)

SOURCE_SLUG = "learned-from-web"

NEWS = ("news", timedelta(days=1))
CHANGING = ("changing", timedelta(days=7))
LASTING = ("lasting", timedelta(days=90))
# words that say the answer changes with time: a version, a release, a price, "latest", "today"…
CHANGING_RE = re.compile(
    r"\b(latest|newest|current(?:ly)?|today|tonight|yesterday|this (?:week|month|year)|right now|now|"
    r"versions?|releases?|released|updates?|updated|prices?|costs?|rates?|exchange|stocks?|scores?|weather|"
    r"forecast|deadline|schedule|vs\.?|versus|20\d\d)\b", re.I)

# Recall is stricter than the knowledge-base gate (retrieve.MIN_SIMILARITY 0.49): the kept pages must be about
# THIS question, or the web search should run. Tune with the similarity logged by each recall.
RECALL_MIN_SIMILARITY = 0.70
RECALL_MIN_PAGES = 2           # one page alone is too thin to replace a search of four
MIN_PAGE_WORDS = 40            # a search snippet or a cookie banner is not worth keeping
PURGE_AFTER = timedelta(days=30)


@dataclass(frozen=True)
class Freshness:
    name: str
    keep: timedelta


def freshness(question: str, *, news: bool) -> Freshness:
    """How long what this question finds stays true enough to reuse."""
    if news:
        return Freshness(*NEWS)
    if CHANGING_RE.search(question):
        return Freshness(*CHANGING)
    return Freshness(*LASTING)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _source(conn) -> tuple[int, int] | None:
    """(source id, collection id) of the learned-pages source, or None before migration 004."""
    return conn.execute("SELECT id, collection_id FROM brain.sources WHERE slug = %s", (SOURCE_SLUG,)).fetchone()


def learn(config: Config, pages: Sequence, *, question: str, news: bool,
          embed: Callable[[list[str]], list[list[float]]] | None = None, now: datetime | None = None) -> int:
    """Keep these web pages (web_search.Passage: title, url, heading, text, published) for later questions.
    A page already kept with the same text gets its new expiry; changed text becomes a new version. Returns the
    number of pages stored or refreshed. Never raises: learning is a bonus, the answer is already written."""
    try:
        return _learn(config, pages, question=question, news=news, embed=embed, now=now)
    except Exception as exc:  # noqa: BLE001
        log.warning("learning from the web failed: %s", exc)
        return 0


def _learn(config: Config, pages: Sequence, *, question: str, news: bool,
           embed: Callable[[list[str]], list[list[float]]] | None, now: datetime | None) -> int:
    now = now or _now()
    fresh = freshness(question, news=news)
    meta = {"expires_at": (now + fresh.keep).isoformat(), "freshness": fresh.name, "chunker": str(CHUNKER_VERSION)}
    embed = embed or (lambda texts: embed_texts(texts, config))
    kept = 0
    with connect(config, system=True) as conn:
        found = _source(conn)
    if found is None:
        log.info("learning from the web: migration 004 not applied yet")
        return 0
    source_id, collection_id = found
    for page in pages:
        text = (page.text or "").strip()
        if len(text.split()) < MIN_PAGE_WORDS or not page.url:
            continue
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        with connect(config, system=True) as conn:
            current = conn.execute(
                """SELECT id, content_hash, version FROM brain.documents
                   WHERE source_id = %s AND url = %s AND status = 'active'""", (source_id, page.url)).fetchone()
            if current and current[1] == content_hash:
                conn.execute("UPDATE brain.documents SET fetched_at = %s, metadata = metadata || %s WHERE id = %s",
                             (now, Jsonb(meta), current[0]))
                kept += 1
                continue
        chunks = chunk_markdown(text)
        if not chunks:
            continue
        title = page.title or page.url
        vectors = embed([passage_text(title, c.heading_path or page.heading or "", c.content) for c in chunks])
        with connect(config, system=True) as conn:
            if current:
                conn.execute("UPDATE brain.documents SET status = 'superseded' WHERE id = %s", (current[0],))
            doc_id = conn.execute(
                """INSERT INTO brain.documents
                     (source_id, url, title, updated_at, fetched_at, content_md, content_hash, version, metadata,
                      collection_id, scope)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'public') RETURNING id""",
                (source_id, page.url, title[:500], _day(page.published), now, text, content_hash,
                 (current[2] + 1) if current else 1, Jsonb(meta), collection_id),
            ).fetchone()[0]
            with conn.cursor() as cur:
                cur.executemany(
                    """INSERT INTO brain.chunks (document_id, chunk_index, heading_path, content, token_count,
                                                 embedding, collection_id, scope)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, 'public')""",
                    [(doc_id, c.index, c.heading_path or page.heading or None, c.content, c.token_count,
                      _vector(v), collection_id) for c, v in zip(chunks, vectors)],
                )
        kept += 1
    purge(config, now=now)
    log.info("learned from the web: %d of %d pages kept (%s, %s)", kept, len(pages), fresh.name, fresh.keep)
    return kept


def _vector(values):
    from pgvector import Vector

    return Vector(values)


def _day(published: str | None) -> datetime | None:
    if not published:
        return None
    try:
        return datetime.fromisoformat(published).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def purge(config: Config, *, now: datetime | None = None) -> int:
    """Delete learned pages that expired more than PURGE_AFTER ago, and their old versions (chunks go with them)."""
    cutoff = (now or _now()) - PURGE_AFTER
    with connect(config, system=True) as conn:
        found = _source(conn)
        if found is None:
            return 0
        rows = conn.execute(
            """DELETE FROM brain.documents
               WHERE source_id = %s AND (status <> 'active' OR (metadata->>'expires_at')::timestamptz < %s)
               RETURNING id""", (found[0], cutoff)).fetchall()
    return len(rows)


def recall(question: str, config: Config, *, news: bool, search=None) -> list | None:
    """Pages kept from earlier web answers that answer this question, as web_search.Passage — or None (search the
    web). They must match closely, be at least RECALL_MIN_PAGES pages, and be young enough for this question."""
    from src.brain.retrieve import search as kb_search
    from src.tools.web_search import Passage as WebPassage

    search = search or kb_search
    result = search(question, config=config, learned="only")
    best = result.best_similarity or 0.0
    if not result.enough_evidence or best < RECALL_MIN_SIMILARITY:
        log.info("learned recall: no (best similarity %.2f)", best)
        return None
    oldest = _now() - freshness(question, news=news).keep
    ages = _fetched(config, sorted({p.document_id for p in result.passages}))
    pages: dict[str, list] = {}
    for p in result.passages:
        fetched = ages.get(p.document_id)
        if fetched is None or fetched < oldest:
            continue
        pages.setdefault(p.url or "", []).append(p)
    pages.pop("", None)
    if len(pages) < RECALL_MIN_PAGES:
        log.info("learned recall: no (%d fresh pages, best similarity %.2f)", len(pages), best)
        return None
    log.info("learned recall: yes (%d pages, best similarity %.2f)", len(pages), best)
    return [WebPassage(parts[0].title, url, parts[0].heading_path or "", "\n\n".join(p.content for p in parts),
                       parts[0].updated_at.date().isoformat() if parts[0].updated_at else None)
            for url, parts in pages.items()]


def _fetched(config: Config, document_ids: list[int]) -> dict[int, datetime]:
    if not document_ids:
        return {}
    with connect(config) as conn:
        return dict(conn.execute("SELECT id, fetched_at FROM brain.documents WHERE id = ANY(%s)",
                                 (document_ids,)).fetchall())
