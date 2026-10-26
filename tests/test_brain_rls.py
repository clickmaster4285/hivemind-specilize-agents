"""B1 acceptance tests: brain schema + row-level security (doc/BRAIN_DESIGN.md §3.4).

Needs the brain database running (docker compose up -d db) and migrated
(python -m src.brain.migrate). Run:  python -m pytest tests/test_brain_rls.py -v
"""

from __future__ import annotations

import psycopg
import pytest
from pgvector import Vector

from src.brain import db as brain_db
from src.brain.migrate import migrate
from src.core.config import Config

PREFIX = "test-rls-"


@pytest.fixture(scope="module")
def config() -> Config:
    cfg = Config.from_env()
    try:
        migrate(cfg)
    except (brain_db.BrainDBError, psycopg.OperationalError) as exc:
        pytest.skip(f"brain database not available: {exc}")
    return cfg


@pytest.fixture(autouse=True)
def cleanup(config):
    yield
    # Owner login is not subject to RLS, so it can remove every test row.
    with psycopg.connect(brain_db.owner_url(config), autocommit=True) as conn:
        conn.execute("DELETE FROM brain.sources WHERE slug LIKE %s", (PREFIX + "%",))
        conn.execute("DELETE FROM brain.retrieval_log WHERE job_id LIKE %s", (PREFIX + "%",))


def _collection_id(conn, slug: str = "it") -> int:
    return conn.execute("SELECT id FROM brain.collections WHERE slug = %s", (slug,)).fetchone()[0]


def _add_source(conn, slug: str, *, scope: str = "public", owner: str | None = None) -> int:
    return conn.execute(
        """INSERT INTO brain.sources (slug, kind, title, collection_id, scope, owner_id)
           VALUES (%s, 'upload', %s, %s, %s, %s) RETURNING id""",
        (PREFIX + slug, slug, _collection_id(conn), scope, owner),
    ).fetchone()[0]


def _visible_slugs(config, **ctx) -> set[str]:
    with brain_db.connect(config, **ctx) as conn:
        rows = conn.execute("SELECT slug FROM brain.sources WHERE slug LIKE %s", (PREFIX + "%",))
        return {slug.removeprefix(PREFIX) for (slug,) in rows}


def test_migrations_are_idempotent(config):
    assert migrate(config) == []


def test_app_login_is_restricted(config):
    with brain_db.connect(config) as conn:
        flags = conn.execute(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
        ).fetchone()
    assert flags == (False, False)


def test_app_cannot_change_collections(config):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with brain_db.connect(config, system=True) as conn:
            conn.execute("INSERT INTO brain.collections (slug, name) VALUES ('x', 'x')")


def test_public_knowledge_is_visible_to_everyone(config):
    with brain_db.connect(config, system=True) as conn:
        _add_source(conn, "public-doc")
    assert "public-doc" in _visible_slugs(config)
    assert "public-doc" in _visible_slugs(config, account_id="alice")
    assert "public-doc" in _visible_slugs(config, account_id="bob")


def test_users_cannot_write_public_knowledge(config):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with brain_db.connect(config, account_id="alice") as conn:
            _add_source(conn, "sneaky-public")


def test_private_data_is_visible_only_to_its_owner(config):
    with brain_db.connect(config, account_id="alice") as conn:
        _add_source(conn, "alice-notes", scope="private", owner="alice")
    assert "alice-notes" in _visible_slugs(config, account_id="alice")
    assert "alice-notes" not in _visible_slugs(config, account_id="bob")
    assert "alice-notes" not in _visible_slugs(config)               # no account
    assert "alice-notes" not in _visible_slugs(config, system=True)  # ingestion service


def test_cannot_create_private_data_for_someone_else(config):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with brain_db.connect(config, account_id="bob") as conn:
            _add_source(conn, "fake-alice", scope="private", owner="alice")


def test_others_cannot_change_or_delete_private_data(config):
    with brain_db.connect(config, account_id="alice") as conn:
        _add_source(conn, "alice-keep", scope="private", owner="alice")
    with brain_db.connect(config, account_id="bob") as conn:
        updated = conn.execute(
            "UPDATE brain.sources SET title = 'hacked' WHERE slug = %s", (PREFIX + "alice-keep",)
        ).rowcount
        deleted = conn.execute(
            "DELETE FROM brain.sources WHERE slug = %s", (PREFIX + "alice-keep",)
        ).rowcount
    assert (updated, deleted) == (0, 0)
    assert "alice-keep" in _visible_slugs(config, account_id="alice")


def test_users_cannot_delete_public_knowledge(config):
    with brain_db.connect(config, system=True) as conn:
        _add_source(conn, "public-keep")
    with brain_db.connect(config, account_id="alice") as conn:
        deleted = conn.execute(
            "DELETE FROM brain.sources WHERE slug = %s", (PREFIX + "public-keep",)
        ).rowcount
    assert deleted == 0


def test_enabled_public_source_needs_a_licence(config):
    with pytest.raises(psycopg.errors.CheckViolation):
        with brain_db.connect(config, system=True) as conn:
            conn.execute(
                """INSERT INTO brain.sources (slug, kind, title, collection_id, enabled)
                   VALUES (%s, 'web_page', 'no licence', %s, true)""",
                (PREFIX + "no-licence", _collection_id(conn)),
            )


def test_private_chunks_are_hidden_from_vector_and_keyword_search(config):
    vec = Vector([0.01] * 1024)
    with brain_db.connect(config, account_id="alice") as conn:
        src = _add_source(conn, "alice-chunks", scope="private", owner="alice")
        cid = _collection_id(conn)
        doc = conn.execute(
            """INSERT INTO brain.documents (source_id, title, fetched_at, content_md, content_hash,
                                            collection_id, scope, owner_id)
               VALUES (%s, 'secret runbook', now(), 'x', 'h', %s, 'private', 'alice') RETURNING id""",
            (src, cid),
        ).fetchone()[0]
        conn.execute(
            """INSERT INTO brain.chunks (document_id, chunk_index, content, token_count, embedding,
                                         collection_id, scope, owner_id)
               VALUES (%s, 0, 'Patch CVE-2024-6387 on host db01', 8, %s, %s, 'private', 'alice')""",
            (doc, vec, cid),
        )

    search = """SELECT count(*) FROM brain.chunks
                WHERE tsv @@ websearch_to_tsquery('simple', 'CVE-2024-6387')
                   OR (embedding <=> %s) < 0.01"""
    with brain_db.connect(config, account_id="alice") as conn:
        assert conn.execute(search, (vec,)).fetchone()[0] == 1
    with brain_db.connect(config, account_id="bob") as conn:
        assert conn.execute(search, (vec,)).fetchone()[0] == 0


def test_retrieval_log_is_private_per_account(config):
    with brain_db.connect(config, account_id="alice") as conn:
        conn.execute(
            """INSERT INTO brain.retrieval_log (account_id, job_id, query, chunk_ids, scores, answered)
               VALUES ('alice', %s, 'my private question', '{}', '{}', false)""",
            (PREFIX + "job",),
        )
    query = "SELECT count(*) FROM brain.retrieval_log WHERE job_id = %s"
    with brain_db.connect(config, account_id="alice") as conn:
        assert conn.execute(query, (PREFIX + "job",)).fetchone()[0] == 1
    with brain_db.connect(config, account_id="bob") as conn:
        assert conn.execute(query, (PREFIX + "job",)).fetchone()[0] == 0
