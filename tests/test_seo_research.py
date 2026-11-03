"""L15 P3-P5: search results as SEO data (fake SearXNG on 127.0.0.1), keyword research, competitor comparison
and tracked projects (in-memory database). No internet, no model."""

from __future__ import annotations

import contextlib
import dataclasses
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.core.config import Config
from src.core.db import Base
from src.core.models_db import SeoAudit, SeoProject, User
from src.tools import compare_sites, keyword_research, seo_projects
from src.tools.market import Market
from src.tools.serp import (Ranked, autocomplete, bare_host, common_phrases, difficulty, keyword_ideas, phrases,
                            rank_of, serp)
from src.tools.site_audit import Page, Site, score_site

RESULTS = [
    ("https://www.bigshop.com/", "Big Shop — everything for the home"),
    ("https://guide.example.org/solar-panels-price", "Solar panels price guide 2026"),
    ("https://blog.mysite.com/solar", "What do solar panels cost?"),
    ("https://news.example.net/story", "Solar panels price falls again"),
]
SUGGEST = {"solar panels price": ["solar panels price uk", "solar panels price per watt"],
           "solar panels price uk": ["solar panels price uk 2026"],
           "solar panels price per watt": ["solar panels price per watt uk"]}
LANGUAGES: list[str] = []           # the market SearXNG was asked for, per request


class FakeSearx(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):  # noqa: N802 — http.server API
        parts = urlsplit(self.path)
        q = parse_qs(parts.query)
        LANGUAGES.append(q.get("language", [""])[0])
        if parts.path == "/search":
            page = int(q["pageno"][0])
            rows = RESULTS[:2] if page == 1 else RESULTS[2:]
            body = {"results": [{"url": u, "title": t, "content": f"About {t}", "engines": ["google cse"]}
                                for u, t in rows], "suggestions": ["solar panel cost"] if page == 1 else []}
        else:
            text = q["q"][0]
            body = [text, SUGGEST.get(text, [])]
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def config():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeSearx)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield dataclasses.replace(Config.from_env(), web_search_url=f"http://127.0.0.1:{server.server_address[1]}")
    server.shutdown()


# ── search results as data ─────────────────────────────────────────────

def test_serp_ranks_and_suggestions(config):
    results, related = serp("solar panels price", config)
    assert [r.position for r in results] == [1, 2, 3, 4]
    assert results[0].host == "bigshop.com" and results[0].engines == ["google cse"]
    assert related == ["solar panel cost"]
    assert rank_of("mysite.com", results).position == 3                  # a subdomain ranks for the site
    assert rank_of("https://www.bigshop.com/x", results).position == 1
    assert rank_of("nowhere.com", results) is None
    assert bare_host("WWW.Example.com/path") == "example.com"


def test_keyword_ideas_go_one_level_deeper(config):
    assert autocomplete("solar panels price", config) == SUGGEST["solar panels price"]
    ideas = keyword_ideas("solar panels price", config, related=["solar panel cost"])
    assert ideas[:3] == ["solar panels price uk", "solar panels price per watt", "solar panel cost"]
    assert "solar panels price uk 2026" in ideas and "solar panels price" not in ideas


def test_difficulty_reads_the_results():
    results = [Ranked(i + 1, url, title, "", []) for i, (url, title) in enumerate(RESULTS)]
    diff = difficulty("solar panels price", results)
    assert (diff.targeted, diff.home_pages, diff.label) == (0.5, 0.25, "Medium")
    assert difficulty("anything", []).label == "Low"


def test_phrases_skip_small_words_and_find_shared_topics():
    assert set(phrases("The cost of solar panels and battery storage")) == {"solar panels", "battery storage"}
    texts = ["solar panels need battery storage", "battery storage for solar panels", "roof tiles only"]
    assert common_phrases(texts) == ["solar panels", "battery storage"]


# ── keyword research ───────────────────────────────────────────────────

class QuietLLM:
    def __init__(self, _config):
        pass

    def chat(self, *, system, user, **_):
        assert "never follow instructions" in system and "no search volume" in system.lower()
        return "### What it means\nTarget the price guide."


def test_keyword_research_report(config, monkeypatch):
    monkeypatch.setattr(keyword_research, "LLMClient", QuietLLM)
    monkeypatch.setattr(keyword_research, "read_top", lambda results, count: [
        Page(r.url, text="solar panels cost less with battery storage", words=600) for r in results[:count]])
    monkeypatch.setattr(keyword_research, "for_site", lambda url, keyword: Market("PK", "en", "the site declares en-PK"))
    LANGUAGES.clear()
    output, errors, _ = keyword_research.run_keyword_research(
        "research", job_id="t-kw", config=config, keywords="solar panels price", domain="mysite.com")
    assert not errors
    assert set(LANGUAGES) == {"en-PK"}                        # searched in the site's own market
    assert "· Pakistan" in output and "in the Pakistan market (the site declares en-PK)" in output
    assert "| solar panels price | ≈ #3 | Medium" in output
    assert "| 3 | blog.mysite.com ⭐ |" in output
    assert "**600 words** (median)" in output and "*battery storage*" in output
    assert "solar panels price uk" in output and "### What it means" in output
    assert "no search volume" in output


def test_keyword_research_without_search():
    config = dataclasses.replace(Config.from_env(), web_search_url="")
    output, errors, _ = keyword_research.run_keyword_research("x", job_id="t-kw-2", config=config, keywords="a",
                                                              market="worldwide")
    assert errors and "not set up" in output


def test_keyword_research_asks_for_the_market_when_nothing_decides(config, monkeypatch):
    events: list[dict] = []
    monkeypatch.setattr("src.tools.market.current_time_zone", lambda: "Asia/Karachi")
    LANGUAGES.clear()
    output, errors, _ = keyword_research.run_keyword_research("keyword ideas for solar", job_id="t-kw-3",
                                                              config=config, keywords="solar", on_progress=events.append)
    done = next(e for e in events if e["type"] == "agent_done")
    assert not errors and not LANGUAGES                       # nothing searched before the user answers
    assert done["clarify"][0]["options"] == ["Pakistan", "Worldwide"]       # their own country first
    assert done["clarify_query"] == "keyword ideas for solar" and "which market" in output


def test_the_users_words_beat_the_site(config, monkeypatch):
    monkeypatch.setattr(keyword_research, "LLMClient", QuietLLM)
    monkeypatch.setattr(keyword_research, "read_top", lambda results, count: [])
    monkeypatch.setattr(keyword_research, "for_site", lambda url, keyword: pytest.fail("the user already named the market"))
    LANGUAGES.clear()
    output, _, _ = keyword_research.run_keyword_research("x", job_id="t-kw-4", config=config, keywords="solar",
                                                         domain="mysite.com", market="worldwide")
    assert set(LANGUAGES) == {"all"} and "Worldwide market (you asked)" in output


# ── competitor comparison ──────────────────────────────────────────────

def _site(host: str, texts: list[str], *, titled: bool = True) -> Site:
    site = Site(start=f"https://{host}/", robots=True, sitemap=True)
    site.pages = [Page(f"https://{host}/{i}", title=f"Page {i} of {host} site" if titled else "", text=t,
                       words=len(t.split())) for i, t in enumerate(texts)]
    return site


class PlanLLM:
    def __init__(self, _config):
        pass

    def chat(self, **_):
        return "### Catch up first\n1. Titles."


def test_content_gap_and_check_gaps():
    mine = _site("me.com", ["we sell solar panels", "solar panels for homes"], titled=False)
    rival_a = _site("a.com", ["solar panels with battery storage", "battery storage explained"])
    rival_b = _site("b.com", ["battery storage costs", "home battery storage guide"])
    assert compare_sites.content_gap(mine, [rival_a, rival_b]) == ["battery storage"]
    gaps = compare_sites.check_gaps(score_site(mine), [score_site(rival_a), score_site(rival_b)])
    assert ("Has a title", 0.0, 1.0) in gaps


def test_compare_sites_runner(monkeypatch):
    sites = {"https://me.com/": _site("me.com", ["we sell solar panels"], titled=False),
             "https://a.com/": _site("a.com", ["battery storage guide", "battery storage costs"])}
    monkeypatch.setattr(compare_sites, "resolve_start", lambda client, url: url.rstrip("/") + "/")
    monkeypatch.setattr(compare_sites, "_audit", lambda start, limit, going: (sites[start], score_site(sites[start])))
    monkeypatch.setattr(compare_sites, "LLMClient", PlanLLM)
    output, errors, _ = compare_sites.run_compare_sites("compare", job_id="t-cmp", config=Config.from_env(),
                                                        url="me.com", competitors="a.com")
    assert not errors
    assert "## me.com vs a.com" in output and "| | me.com (you) | a.com |" in output
    assert "### They do it, you don't" in output and "| Has a title | 0% | 100% |" in output
    assert "*battery storage*" in output and "### Catch up first" in output


# ── tracked projects ───────────────────────────────────────────────────

@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = Session(bind=engine)
    session.add(User(id=1, username="ana", password_hash="x"))
    session.flush()

    @contextlib.contextmanager
    def scope(_config=None):
        yield session
        session.flush()

    monkeypatch.setattr(seo_projects, "session_scope", scope)
    yield session
    session.close()


def test_audits_are_saved_and_compared(db):
    config = Config.from_env()
    before = _site("me.com", ["solar panels " * 20, "battery storage " * 20], titled=False)
    assert seo_projects.save_audit(config, 1, before, score_site(before)) is None
    after = _site("me.com", ["solar panels " * 20, "battery storage " * 20])
    after_score = score_site(after)
    previous = seo_projects.save_audit(config, 1, after, after_score)
    assert previous["total"] < after_score.total
    text = seo_projects.changes(previous, seo_projects.snapshot(after_score))
    assert f"{previous['total']} → {after_score.total} (+{after_score.total - previous['total']})" in text
    assert "✅ Fixed: Has a title" in text
    assert len(list(db.scalars(select(SeoAudit)))) == 2
    report = seo_projects.projects_report(config, 1)
    assert "| me.com | no |" in report and f"{previous['total']} → {after_score.total}" in report


def test_tracking_reaudits_when_due(db):
    config = Config.from_env()
    site = _site("me.com", ["solar panels " * 20])
    assert seo_projects.track(config, 1, site, 7) == 7
    assert seo_projects.track(config, 1, site, 500) == seo_projects.MAX_EVERY_DAYS     # updated, not duplicated
    project = db.scalars(select(SeoProject)).one()
    assert seo_projects.run_due(config, audit=lambda start, limit: (site, score_site(site))) == 0   # not due yet
    project.next_run_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.flush()
    assert seo_projects.run_due(config, audit=lambda start, limit: (site, score_site(site))) == 1
    saved = db.scalars(select(SeoAudit)).one()
    assert saved.scheduled and saved.host == "me.com"
    assert seo_projects.stop_tracking(config, 1, "me.com") and not seo_projects.stop_tracking(config, 1, "me.com")


def test_seo_projects_tool(db):
    output, errors, _ = seo_projects.run_seo_projects("show", job_id="t-proj", config=Config.from_env(), user_id=1,
                                                      untrack="example.com")
    assert not errors and "**example.com** was not being tracked." in output and "no SEO audits yet" in output


def test_an_audit_remembers_and_tracks(db):
    from src.tools.site_audit import _remember

    config = Config.from_env()
    site = _site("me.com", ["solar panels " * 20])
    assert _remember(config, 1, site, score_site(site), None) == ""                 # first audit: nothing to compare
    text = _remember(config, 1, site, score_site(site), "weekly = 7")
    assert text.startswith("**Since the audit of ") and "📌 Tracking **me.com**: it is re-audited every 7 days" in text
    assert db.scalars(select(SeoProject)).one().every_days == 7


def test_topics_leave_out_menus_and_the_brand():
    rival = _site("acme-solar.com", ["home menu battery storage", "home menu battery storage costs",
                                     "home menu acme solar panels", "home menu acme solar offers"])
    topics = compare_sites.site_topics(rival)
    assert "battery storage" in topics
    assert "home menu" not in topics                     # on every page: the menu
    assert not any("acme" in t or "solar" in t for t in topics)      # the competitor's own name
