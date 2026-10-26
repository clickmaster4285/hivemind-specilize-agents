"""B2 integration test: ingest a local folder into the brain database.

Uses a fake embedder (deterministic vectors) so it runs without Ollama. Needs the brain
database (docker compose up -d db). Run:  python -m pytest tests/test_brain_ingest.py -v
"""

from __future__ import annotations

import hashlib

import psycopg
import pytest
from psycopg.types.json import Jsonb

from src.brain import db as brain_db
from src.brain.embed import EMBED_DIM
from src.brain.ingest import ingest_source
from src.brain.migrate import migrate
from src.core.config import Config

SLUG = "test-ingest-local"

DOC_A = """---
title: DNS forwarders
ms.date: 01/31/2025
---

# Configure DNS forwarders

A forwarder is a DNS server that this server sends queries to when it cannot resolve them itself.

## Add a forwarder

Use PowerShell to add a forwarder:

```powershell
Add-DnsServerForwarder -IPAddress 10.0.0.1
```
"""

DOC_B = "# Zone types\n\nA primary zone holds the writable copy of the zone data. " * 3


def fake_embed(texts: list[str]) -> list[list[float]]:
    out = []
    for text in texts:
        seed = hashlib.sha256(text.encode()).digest()
        out.append([seed[i % len(seed)] / 255 for i in range(EMBED_DIM)])
    return out


@pytest.fixture(scope="module")
def config() -> Config:
    cfg = Config.from_env()
    try:
        migrate(cfg)
    except (brain_db.BrainDBError, psycopg.OperationalError) as exc:
        pytest.skip(f"brain database not available: {exc}")
    return cfg


@pytest.fixture
def folder(tmp_path, config):
    (tmp_path / "forwarders.md").write_text(DOC_A, encoding="utf-8")
    (tmp_path / "zones.md").write_text(DOC_B, encoding="utf-8")
    (tmp_path / "ignored.png").write_bytes(b"\x89PNG")
    with brain_db.connect(config, system=True) as conn:
        cid = conn.execute("SELECT id FROM brain.collections WHERE slug = 'it'").fetchone()[0]
        conn.execute(
            """INSERT INTO brain.sources (slug, kind, title, collection_id, licence, enabled, config)
               VALUES (%s, 'upload', 'Test folder', %s, 'test', true, %s)""",
            (SLUG, cid, Jsonb({"path": str(tmp_path)})),
        )
    yield tmp_path
    with psycopg.connect(brain_db.owner_url(config), autocommit=True) as conn:
        conn.execute("DELETE FROM brain.ingest_runs WHERE source_id IN (SELECT id FROM brain.sources WHERE slug = %s)", (SLUG,))
        conn.execute("DELETE FROM brain.sources WHERE slug = %s", (SLUG,))


def _ingest(config):
    return ingest_source(SLUG, config, embed=fake_embed, log=lambda _msg: None)


def _documents(config) -> list[tuple]:
    with brain_db.connect(config) as conn:
        return conn.execute(
            """SELECT d.url, d.version, d.status, d.title
               FROM brain.documents d JOIN brain.sources s ON s.id = d.source_id
               WHERE s.slug = %s ORDER BY d.url, d.version""",
            (SLUG,),
        ).fetchall()


def test_ingest_lifecycle(folder, config):
    # 1. first run: both Markdown files become documents with chunks; the PNG is ignored
    first = _ingest(config)
    assert (first.status, first.new, first.failed) == ("ok", 2, 0)
    assert first.chunks >= 2
    docs = _documents(config)
    assert [(url, ver, st) for url, ver, st, _ in docs] == [
        ("file:forwarders.md", 1, "active"),
        ("file:zones.md", 1, "active"),
    ]
    assert docs[0][3] == "DNS forwarders"

    with brain_db.connect(config) as conn:
        row = conn.execute(
            """SELECT c.heading_path, c.content FROM brain.chunks c
               JOIN brain.documents d ON d.id = c.document_id
               WHERE d.url = 'file:forwarders.md' AND c.content LIKE '%%Add-DnsServerForwarder%%'"""
        ).fetchone()
        hits = conn.execute(
            """SELECT count(*) FROM brain.chunks c JOIN brain.documents d ON d.id = c.document_id
               JOIN brain.sources s ON s.id = d.source_id
               WHERE s.slug = %s AND c.tsv @@ websearch_to_tsquery('simple', 'Add-DnsServerForwarder')""",
            (SLUG,),
        ).fetchone()[0]
    assert row is not None and "```powershell" in row[1]   # code block kept whole
    assert hits == 1                                        # keyword index works on exact tokens

    # 2. nothing changed: no new versions
    second = _ingest(config)
    assert (second.new, second.changed, second.same) == (0, 0, 2)

    # 3. one file edited: new version, old one kept as superseded
    (folder / "zones.md").write_text(DOC_B + "\n\nA secondary zone is a read-only copy.", encoding="utf-8")
    third = _ingest(config)
    assert (third.changed, third.same) == (1, 1)
    assert ("file:zones.md", 1, "superseded") in [(u, v, s) for u, v, s, _ in _documents(config)]
    assert ("file:zones.md", 2, "active") in [(u, v, s) for u, v, s, _ in _documents(config)]

    # 4. file deleted: missing once -> still active; missing twice -> removed (kept for audit)
    (folder / "forwarders.md").unlink()
    assert _ingest(config).removed == 0
    assert _ingest(config).removed == 1
    assert ("file:forwarders.md", 1, "removed") in [(u, v, s) for u, v, s, _ in _documents(config)]

    # 5. every run was recorded
    with brain_db.connect(config) as conn:
        runs = conn.execute(
            """SELECT r.status FROM brain.ingest_runs r JOIN brain.sources s ON s.id = r.source_id
               WHERE s.slug = %s""",
            (SLUG,),
        ).fetchall()
        status_row = conn.execute(
            "SELECT active_docs, last_run_status FROM brain.source_status WHERE slug = %s", (SLUG,)
        ).fetchone()
    assert len(runs) == 5 and all(status == "ok" for (status,) in runs)
    assert status_row == (1, "ok")


def test_older_chunker_output_is_rechunked_in_place(folder, config):
    assert _ingest(config).new == 2
    # pretend zones.md was chunked by an older chunker version
    with psycopg.connect(brain_db.owner_url(config), autocommit=True) as conn:
        conn.execute(
            "UPDATE brain.documents SET metadata = metadata || '{\"chunker\": \"0\"}' WHERE url = 'file:zones.md'"
        )
    stats = _ingest(config)
    assert (stats.rechunked, stats.same, stats.new, stats.changed) == (1, 1, 0, 0)
    docs = [(u, v, s) for u, v, s, _ in _documents(config)]
    assert ("file:zones.md", 1, "active") in docs and ("file:zones.md", 2, "active") not in docs  # no new version
    assert _ingest(config).same == 2  # now up to date


def test_disabled_source_is_refused(folder, config):
    with brain_db.connect(config, system=True) as conn:
        conn.execute("UPDATE brain.sources SET enabled = false WHERE slug = %s", (SLUG,))
    with pytest.raises(ValueError, match="disabled"):
        _ingest(config)
