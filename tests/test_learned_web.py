"""Knowledge learned from web searches (BACKLOG L31, src/brain/learned.py): the pages a web answer cited are kept
with an expiry; a similar question is answered from them without searching; expired pages are never used.

The database tests run on the test brain database with fake embeddings (no Ollama); they skip without it.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from src.brain import db as brain_db
from src.brain import learned
from src.brain.embed import EMBED_DIM, passage_text
from src.brain.migrate import migrate
from src.brain.retrieve import _collection_filter
from src.brain.retrieve import search as kb_search
from src.core.config import Config
from src.tools.web_search import Passage as WebPassage

URL = "https://learned.test/{}"


def _page(n: int, words: str = "") -> WebPassage:
    text = (words or f"Page {n} explains how the proxmox backup server stores deduplicated chunks. ") * 6
    return WebPassage(f"Proxmox page {n}", URL.format(n), "Backups", text.strip(), "2026-11-01")


# ── no database ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("question, news, kind", [
    ("who won the match yesterday", True, "news"),
    ("what is the latest proxmox version", False, "changing"),
    ("price of a quadro p4000", False, "changing"),
    ("how does proxmox backup deduplicate chunks", False, "lasting"),
])
def test_freshness_by_kind_of_question(question, news, kind):
    assert learned.freshness(question, news=news).name == kind


def test_the_collection_filter_puts_learned_pages_in_or_out():
    assert _collection_filter(["it"], "with")[1] == [["it", "web"]]           # a specialist still finds them
    assert "NOT IN" in _collection_filter(None, "without")[0]                  # deep research leaves them out
    assert _collection_filter(["it"], "only")[1] == ["web"]


# ── the database ───────────────────────────────────────────────────────────

def _vec(text: str) -> list[float]:
    seed = hashlib.sha256(text.encode()).digest()
    return [seed[i % len(seed)] / 255 - 0.5 for i in range(EMBED_DIM)]


def _fake_embed(texts):
    return [_vec(t) for t in texts]


def _aimed_at(page: WebPassage):
    """A query vector equal to the page's (single) chunk vector: similarity 1.0 with it."""
    from src.brain.chunking import chunk_markdown

    chunk = chunk_markdown(page.text)[0]
    target = _vec(passage_text(page.title, chunk.heading_path or page.heading, chunk.content))
    return lambda q, **kw: kb_search(q, embed=lambda _q: target, **kw)


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
    if "config" not in request.fixturenames:
        return
    cfg = request.getfixturevalue("config")
    with psycopg.connect(brain_db.owner_url(cfg), autocommit=True) as conn:
        conn.execute("DELETE FROM brain.documents WHERE url LIKE 'https://learned.test/%'")


def _docs(config) -> list[tuple]:
    with psycopg.connect(brain_db.owner_url(config)) as conn:
        return conn.execute(
            """SELECT url, status, version, metadata->>'freshness', (metadata->>'expires_at')::timestamptz
               FROM brain.documents WHERE url LIKE 'https://learned.test/%' ORDER BY url, version""").fetchall()


def test_cited_pages_are_kept_with_their_expiry(config):
    now = datetime.now(timezone.utc)
    assert learned.learn(config, [_page(1), _page(2)], question="how does proxmox backup work", news=False,
                         embed=_fake_embed, now=now) == 2
    rows = _docs(config)
    assert [(r[0], r[1], r[3]) for r in rows] == [(URL.format(1), "active", "lasting"), (URL.format(2), "active", "lasting")]
    assert abs(rows[0][4] - (now + timedelta(days=90))) < timedelta(seconds=1)


def test_a_snippet_is_not_worth_keeping(config):
    short = WebPassage("Snippet", URL.format(9), "", "Only a few words here.", None)
    assert learned.learn(config, [short], question="q", news=False, embed=_fake_embed) == 0


def test_the_same_page_again_gets_a_new_expiry_and_changed_text_a_new_version(config):
    t0 = datetime.now(timezone.utc)
    learned.learn(config, [_page(1)], question="how does it work", news=False, embed=_fake_embed, now=t0)
    learned.learn(config, [_page(1)], question="latest version of it", news=False, embed=_fake_embed, now=t0)
    rows = _docs(config)
    assert len(rows) == 1 and rows[0][3] == "changing"                # same text: refreshed in place
    learned.learn(config, [_page(1, "The page was rewritten with other words about proxmox storage. ")],
                  question="how does it work", news=False, embed=_fake_embed, now=t0)
    assert [(r[1], r[2]) for r in _docs(config)] == [("active", 2)]          # the old version is purged at once


def test_a_similar_question_is_answered_from_the_kept_pages(config):
    learned.learn(config, [_page(1), _page(2)], question="how does proxmox backup work", news=False,
                  embed=_fake_embed)
    kept = learned.recall("how does proxmox backup deduplicate", config, news=False, search=_aimed_at(_page(1)))
    assert kept is not None and {p.url for p in kept} >= {URL.format(1)}
    assert all(isinstance(p, WebPassage) and p.text for p in kept)


def test_one_page_is_too_thin_to_replace_a_search(config):
    learned.learn(config, [_page(1)], question="how does proxmox backup work", news=False, embed=_fake_embed)
    assert learned.recall("how does proxmox backup work", config, news=False, search=_aimed_at(_page(1))) is None


def test_a_news_question_does_not_take_pages_saved_days_ago(config):
    old = datetime.now(timezone.utc) - timedelta(days=3)
    learned.learn(config, [_page(1), _page(2)], question="how does proxmox backup work", news=False,
                  embed=_fake_embed, now=old)
    search = _aimed_at(_page(1))
    assert learned.recall("how does proxmox backup work", config, news=False, search=search) is not None
    assert learned.recall("proxmox backup news", config, news=True, search=search) is None


def test_expired_pages_are_never_found_and_are_purged_later(config):
    long_ago = datetime.now(timezone.utc) - timedelta(days=95)            # 90-day pages, expired 5 days ago
    learned.learn(config, [_page(1), _page(2)], question="how does proxmox backup work", news=False,
                  embed=_fake_embed, now=long_ago)
    found = _aimed_at(_page(1))("how does proxmox backup work", config=config, learned="only")
    assert not any(p.url and p.url.startswith("https://learned.test/") for p in found.passages)
    assert learned.purge(config) == 0                                     # within the 30 days after expiry
    assert learned.purge(config, now=datetime.now(timezone.utc) + timedelta(days=30)) == 2
    assert _docs(config) == []


# ── the chat: the web path tries kept pages first, and keeps what a fresh answer cited ─────────────────────────

class _Client:
    def __init__(self, _config):
        pass

    def chat(self, **_kwargs):
        return "unused"


def _run(monkeypatch, *, kept):
    from src.jobs import chat_runner

    calls: dict = {"web": 0, "learned": []}

    def fake_web(*_a, read=None, **_k):
        calls["web"] += 1
        if read is not None:
            read[:] = [_page(1), _page(2)]
        return "Fresh [1].", [{"n": 1, "title": "Proxmox page 1", "url": URL.format(1), "source": "web", "cited": True},
                              {"n": 2, "title": "Proxmox page 2", "url": URL.format(2), "source": "web", "cited": False}]

    monkeypatch.setattr(chat_runner, "LLMClient", _Client)
    monkeypatch.setattr(chat_runner, "answer_from_web", fake_web)
    monkeypatch.setattr(chat_runner, "_recall_learned", lambda q, c, news: kept)
    monkeypatch.setattr(chat_runner, "answer_from_pages",
                        lambda q, pages, **k: ("From kept pages [1].",
                                               [{"n": 1, "title": pages[0].title, "url": pages[0].url,
                                                 "source": "web", "cited": True}]))
    monkeypatch.setattr("src.brain.learned.learn",
                        lambda config, pages, **kw: calls["learned"].append([p.url for p in pages]) or len(pages))
    cfg = replace(Config.from_env(), web_search_url="http://127.0.0.1:8888", suggestions_enabled=False,
                  quiz_enabled=False, web_images_enabled=False, verify_web_answers=False, brain_enabled=True)
    events: list[dict] = []
    chat_runner.run_chat("how does proxmox backup work", config=cfg, web_search=True, on_progress=events.append)
    import time

    for _ in range(50):                      # learning runs in a background thread
        if calls["learned"] or kept:
            break
        time.sleep(0.02)
    return calls, events, next(e for e in events if e["type"] == "agent_done")


def test_kept_pages_answer_without_a_web_search(monkeypatch):
    calls, events, done = _run(monkeypatch, kept=[_page(1), _page(2)])
    assert calls["web"] == 0 and done["output"] == "From kept pages [1]." and done["learned"] is True
    assert "Answering from pages saved from an earlier web search…" in [e.get("message") for e in events]


def test_a_fresh_web_answer_keeps_only_the_pages_it_cited(monkeypatch):
    calls, _events, done = _run(monkeypatch, kept=None)
    assert calls["web"] == 1 and done["output"] == "Fresh [1]." and "learned" not in done
    assert calls["learned"] == [[URL.format(1)]]
