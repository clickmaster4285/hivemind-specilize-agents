"""B3 tests: hybrid retrieval building blocks (no DB) + search against the brain database.

DB tests use fake embeddings so they run without Ollama. Needs: docker compose up -d db.
Order-sensitive checks use keyword mode or a query vector equal to the target chunk's vector,
so other content in the database (e.g. the real DNS docs) cannot make them flaky.
"""

from __future__ import annotations

import hashlib

import psycopg
import pytest
from pgvector import Vector
from psycopg.types.json import Jsonb

from src.brain import db as brain_db
from src.brain.embed import EMBED_DIM
from src.brain.migrate import migrate
from src.brain.retrieve import detect_exact_terms, keyword_query, rrf_merge, search
from src.core.config import Config

PREFIX = "test-retrieve-"
FORWARDER_CHUNK = "zzqtoken Use Set-ZzqForwarder to change the forwarders list."
ALICE_CHUNK = "zzqtoken Alice private runbook"


# ── building blocks ──────────────────────────────────────────────────────

def test_exact_terms_strong_and_weak():
    strong, weak = detect_exact_terms("Why does Set-DnsServerForwarder fail with 0x8007232A and event 541 on 10.0.17763?")
    assert strong == ["0x8007232A", "Set-DnsServerForwarder"]
    assert "541" in weak and "10.0.17763" in weak


def test_exact_terms_cve_kb_and_flags():
    strong, _ = detect_exact_terms("Is CVE-2024-6387 fixed by KB5034441? run sshd --check-config")
    assert {"CVE-2024-6387", "KB5034441", "check-config"} <= set(strong)


def test_plain_question_has_no_strong_terms():
    strong, _ = detect_exact_terms("how do old records get deleted automatically")
    assert strong == []


def test_keyword_query_drops_english_and_hinglish_stopwords():
    assert keyword_query("DNS server pe forwarder kaise add karein?") == "dns OR server OR forwarder OR add"
    assert keyword_query("How do I enable DNS over HTTPS?") == "enable OR dns OR https"
    assert keyword_query("kya hai") == ""


def test_keyword_query_strips_leading_dash():
    # a leading '-' means NOT in websearch syntax; a flag must stay a positive term
    assert keyword_query("use --recurse") == "recurse"


def test_rrf_rewards_items_found_by_both_methods():
    merged = rrf_merge([1, 2, 3], [3, 4])
    assert merged[0][0] == 3               # in both lists → top
    assert {item for item, _ in merged} == {1, 2, 3, 4}
    assert merged[0][1] == pytest.approx(1 / 63 + 1 / 61)


# ── search against the database ──────────────────────────────────────────

def _vec(text: str) -> list[float]:
    seed = hashlib.sha256(text.encode()).digest()
    return [seed[i % len(seed)] / 255 - 0.5 for i in range(EMBED_DIM)]


def _aimed_at(chunk_text: str):
    """Fake embedder whose query vector equals the target chunk's vector (similarity 1.0)."""
    return lambda _question: _vec(chunk_text)


@pytest.fixture(scope="module")
def config() -> Config:
    cfg = Config.from_env()
    try:
        migrate(cfg)
    except (brain_db.BrainDBError, psycopg.OperationalError) as exc:
        pytest.skip(f"brain database not available: {exc}")
    return cfg


def _add_doc(conn, source_id, collection_id, url, chunks, *, scope="public", owner=None, status="active"):
    doc = conn.execute(
        """INSERT INTO brain.documents (source_id, url, title, fetched_at, content_md, content_hash,
                                        status, collection_id, scope, owner_id)
           VALUES (%s, %s, %s, now(), 'x', %s, %s, %s, %s, %s) RETURNING id""",
        (source_id, url, url, url + status, status, collection_id, scope, owner),
    ).fetchone()[0]
    for i, text in enumerate(chunks):
        conn.execute(
            """INSERT INTO brain.chunks (document_id, chunk_index, content, token_count, embedding,
                                         collection_id, scope, owner_id)
               VALUES (%s, %s, %s, 20, %s, %s, %s, %s)""",
            (doc, i, text, Vector(_vec(text)), collection_id, scope, owner),
        )


@pytest.fixture(scope="module")
def corpus(config):
    add_source = """INSERT INTO brain.sources (slug, kind, title, collection_id, scope, owner_id, config)
                    VALUES (%s, 'upload', 't', %s, %s, %s, %s) RETURNING id"""
    with brain_db.connect(config, system=True) as conn:
        cids = dict(conn.execute("SELECT slug, id FROM brain.collections").fetchall())
        src_it = conn.execute(add_source, (PREFIX + "it", cids["it"], "public", None, Jsonb({}))).fetchone()[0]
        src_mk = conn.execute(add_source, (PREFIX + "mk", cids["marketing"], "public", None, Jsonb({}))).fetchone()[0]
        _add_doc(conn, src_it, cids["it"], "zzq-forwarders", [
            FORWARDER_CHUNK,
            "zzqtoken Forwarders are tried in order.",
            "zzqtoken A third chunk about forwarders.",
        ])
        _add_doc(conn, src_it, cids["it"], "zzq-old", ["zzqtoken Old text Set-ZzqForwarder"], status="superseded")
        _add_doc(conn, src_mk, cids["marketing"], "zzq-ads", ["zzqtoken Ads budget planning."])
    with brain_db.connect(config, account_id="alice") as conn:
        src_priv = conn.execute(add_source, (PREFIX + "alice", cids["it"], "private", "alice", Jsonb({}))).fetchone()[0]
        _add_doc(conn, src_priv, cids["it"], "zzq-alice", [ALICE_CHUNK], scope="private", owner="alice")
    yield
    with psycopg.connect(brain_db.owner_url(config), autocommit=True) as conn:
        conn.execute("DELETE FROM brain.sources WHERE slug LIKE %s", (PREFIX + "%",))


def _urls(result) -> list[str]:
    return [p.url for p in result.passages]


def test_hybrid_puts_passage_found_by_both_methods_first(config, corpus):
    result = search("what does Set-ZzqForwarder do", config=config, embed=_aimed_at(FORWARDER_CHUNK))
    top = result.passages[0]
    assert top.content == FORWARDER_CHUNK
    assert top.vector_rank == 1 and top.keyword_rank == 1
    assert result.exact_match and result.enough_evidence


def test_superseded_versions_are_never_returned(config, corpus):
    result = search("Set-ZzqForwarder zzqtoken", config=config, mode="keyword", limit=20)
    assert "zzq-old" not in _urls(result)


def test_max_two_passages_per_document(config, corpus):
    result = search("zzqtoken forwarders", config=config, mode="keyword", limit=20)
    assert _urls(result).count("zzq-forwarders") == 2


def test_collection_filter(config, corpus):
    result = search("zzqtoken", config=config, mode="keyword", collections=["marketing"], limit=20)
    assert set(_urls(result)) == {"zzq-ads"}
    hybrid = search("zzqtoken", config=config, collections=["marketing"], limit=20, embed=_aimed_at(FORWARDER_CHUNK))
    assert all(p.source == PREFIX + "mk" for p in hybrid.passages)  # vector path filtered too


def test_private_passages_only_for_their_owner(config, corpus):
    aimed = _aimed_at(ALICE_CHUNK)  # vector search would rank Alice's chunk first — if it were visible
    assert _urls(search("runbook", config=config, embed=aimed, account_id="alice"))[0] == "zzq-alice"
    assert "zzq-alice" not in _urls(search("runbook", config=config, embed=aimed, account_id="bob", limit=20))
    assert "zzq-alice" not in _urls(search("runbook", config=config, embed=aimed, limit=20))


def test_keyword_mode_needs_no_embedding(config, corpus):
    def no_embed(_q):
        raise AssertionError("keyword mode must not call the embedder")

    result = search("Set-ZzqForwarder", config=config, mode="keyword", embed=no_embed)
    assert result.passages[0].url == "zzq-forwarders"
