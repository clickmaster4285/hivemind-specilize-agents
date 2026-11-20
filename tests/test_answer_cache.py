"""Whole answers reused for repeat questions (BACKLOG L31 step 2, src/brain/answer_cache.py, migration 007): kept
grounded answers come back for the same first question, at once, exactly as they were — never for another version,
a stale news question, an unsupported statement or a private source. Test brain database, fake embeddings."""

from __future__ import annotations

import hashlib

import psycopg
import pytest

from src.brain import answer_cache
from src.brain import db as brain_db
from src.brain.embed import EMBED_DIM
from src.brain.migrate import migrate
from src.core.config import Config

Q = "how do I restart nginx on ubuntu"
SOURCES = [{"n": 1, "title": "nginx docs", "url": "https://nginx.org/x", "source": "web", "cited": True}]
EXTRA = {"role": "Web search", "grounded": True, "sources": SOURCES,
         "verification": {"checked": 2, "supported": 2, "unsupported": [], "unclear": []}}


def _vec(text: str) -> list[float]:
    seed = hashlib.sha256(text.encode()).digest()
    return [seed[i % len(seed)] / 255 - 0.5 for i in range(EMBED_DIM)]


def _same_meaning(*texts: str):
    """An embedder that puts these phrasings on one point (similarity 1.0) — every other text elsewhere."""
    return lambda q: _vec(texts[0] if q in texts else q)


@pytest.fixture(scope="module")
def config() -> Config:
    cfg = Config.from_env()
    try:
        migrate(cfg)
    except (brain_db.BrainDBError, psycopg.OperationalError) as exc:
        pytest.skip(f"brain database not available: {exc}")
    return cfg


@pytest.fixture(autouse=True)
def clean(request):
    yield
    if "config" in request.fixturenames:
        with psycopg.connect(brain_db.owner_url(request.getfixturevalue("config")), autocommit=True) as conn:
            conn.execute("DELETE FROM brain.answer_cache")


def _count(config) -> int:
    with psycopg.connect(brain_db.owner_url(config)) as conn:
        return conn.execute("SELECT count(*) FROM brain.answer_cache").fetchone()[0]


def test_the_same_question_in_other_words_gets_the_kept_answer(config):
    embed = _same_meaning(Q, "how to restart nginx in ubuntu")
    assert answer_cache.keep(Q, "Run `sudo systemctl restart nginx` [1].", EXTRA, config, news=False, job_id="j1",
                             embed=embed)
    found = answer_cache.find("how to restart nginx in ubuntu", config, embed=embed)
    assert found["answer"] == "Run `sudo systemctl restart nginx` [1]." and found["similarity"] >= 0.99
    assert found["extra"]["sources"] == SOURCES and found["extra"]["verification"]["supported"] == 2
    assert answer_cache.find("how do I install docker", config, embed=embed) is None        # another question


def test_another_version_never_gets_the_answer(config):
    embed = _same_meaning("upgrade nginx 1.24 on debian", "upgrade nginx 1.26 on debian")
    answer_cache.keep("upgrade nginx 1.24 on debian", "For 1.24 …", EXTRA, config, news=False, job_id="j", embed=embed)
    assert answer_cache.find("upgrade nginx 1.26 on debian", config, embed=embed) is None
    assert answer_cache.find("upgrade nginx 1.24 on debian", config, embed=embed) is not None


def test_a_news_question_takes_only_a_fresh_answer(config):
    embed = _same_meaning(Q)
    answer_cache.keep(Q, "answer [1]", EXTRA, config, news=False, job_id="j", embed=embed)
    with psycopg.connect(brain_db.owner_url(config), autocommit=True) as conn:
        conn.execute("UPDATE brain.answer_cache SET created_at = now() - interval '3 days'")
    assert answer_cache.find(Q, config, embed=embed) is not None                 # a how-to: 90 days
    assert answer_cache.find(Q, config, news=True, embed=embed) is None          # news: 1 day


@pytest.mark.parametrize("extra, why", [
    ({**EXTRA, "grounded": False}, "not from sources"),
    ({**EXTRA, "sources": []}, "no sources"),
    ({**EXTRA, "verification": {"unsupported": [{"text": "x"}]}}, "a statement its sources do not back"),
    ({**EXTRA, "sources": [{"n": 1, "title": "x", "source": "someones-private-upload"}]}, "not a public source"),
])
def test_what_is_never_kept(config, extra, why):
    assert not answer_cache.keep(Q, "answer [1]", extra, config, news=False, job_id="j", embed=_same_meaning(Q)), why
    assert _count(config) == 0


def test_keeping_the_same_question_again_replaces_it(config):
    embed = _same_meaning(Q)
    answer_cache.keep(Q, "old [1]", EXTRA, config, news=False, job_id="j1", embed=embed)
    answer_cache.keep(Q, "new [1]", EXTRA, config, news=False, job_id="j2", embed=embed)
    assert _count(config) == 1 and answer_cache.find(Q, config, embed=embed)["answer"] == "new [1]"


def test_a_session_cannot_write_the_kept_answers_itself(config):
    answer_cache.keep(Q, "answer [1]", EXTRA, config, news=False, job_id="j", embed=_same_meaning(Q))
    with brain_db.connect(config, account_id="7") as conn:                       # a user's own connection
        conn.execute("UPDATE brain.answer_cache SET answer = 'changed'")
    with brain_db.connect(config) as conn:
        assert conn.execute("SELECT answer FROM brain.answer_cache").fetchone()[0] == "answer [1]"


# ── the chat: sent before routing, labelled; kept after a grounded first answer ──────────────────────────────

def test_a_reused_answer_is_sent_labelled_with_its_sources(config, monkeypatch):
    from dataclasses import replace

    from src.jobs import chat_runner

    monkeypatch.setattr("src.brain.answer_cache.find", lambda q, c, **k: {
        "answer": "Run it [1].", "extra": dict(EXTRA), "asked_at": "2026-11-18T09:00:00+00:00", "similarity": 0.97})
    events: list[dict] = []
    cfg = replace(config, brain_enabled=True, reuse_answers=True)
    assert chat_runner.reuse_answer(Q, job_id="j9", config=cfg, on_progress=events.append)
    done = next(e for e in events if e["type"] == "agent_done")
    assert done["output"] == "Run it [1]." and done["role"] == "Web search" and done["sources"] == SOURCES
    assert done["reused"] == {"asked_at": "2026-11-18T09:00:00+00:00", "similarity": 0.97}
    assert events[-1]["type"] == "answer_written"
    assert not chat_runner.reuse_answer(Q, job_id="j9", config=replace(cfg, reuse_answers=False), on_progress=print)


def test_a_general_question_does_not_get_an_answer_written_for_one_version(config):
    embed = _same_meaning("upgrade nginx 1.24 on debian", "upgrade nginx on debian")
    answer_cache.keep("upgrade nginx 1.24 on debian", "For 1.24 …", EXTRA, config, news=False, job_id="j", embed=embed)
    assert answer_cache.find("upgrade nginx on debian", config, embed=embed) is None


@pytest.mark.parametrize("question, ids", [
    ("upgrade nginx 1.26 on debian", {"1.26"}),
    ("what is on 192.168.10.45?", {"192.168.10.45"}),
    ("error 0x80070005 after KB5034441", {"0x80070005", "kb5034441"}),
    ("how do I restart nginx", set()),
])
def test_the_identifiers_two_questions_must_share(question, ids):
    assert answer_cache.identifiers(question) == ids
