"""L13 site_audit: page facts, polite crawl, checks → maturity score, the runner (local test server, fake model)."""

from __future__ import annotations

import threading
from http.server import ThreadingHTTPServer

import pytest

from src.brain.web import WebClient
from src.core.config import Config
from src.tools import site_audit
from src.tools.site_audit import AuditError, analyze_page, check_public, crawl_site, report, score_site, site_url
from test_brain_web import Site as Pages, _handler

GOOD = """<!doctype html><html lang="en"><head><title>{title}</title>
<meta name="description" content="{desc}">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="canonical" href="{path}"><meta property="og:title" content="x">
<script type="application/ld+json">{{"@type": "Organization"}}</script></head>
<body><main><h1>Heading</h1><h2>Part</h2><p>{words}</p><img src="a.png" alt="a chart">
<a href="/a">A</a> <a href="/b">B</a> <a href="/missing">gone</a> <a href="/private/x">private</a>
<a href="https://elsewhere.example/">other site</a></main></body></html>"""


def _page(path: str, title: str, desc: str = "A clear description of this page that is long enough to show.",
          words: int = 200) -> str:
    return GOOD.format(title=title, desc=desc, path=path, words=" ".join(["word"] * words))


@pytest.fixture
def web():
    pages = Pages()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(pages))
    pages.base = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    pages.set("/robots.txt", f"User-agent: *\nDisallow: /private/\nSitemap: {pages.base}/sitemap.xml\n", "text/plain")
    pages.set("/sitemap.xml", f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                              f"<url><loc>{pages.base}/a</loc></url></urlset>", "application/xml")
    pages.set("/", _page("/", "Home of the Example Company"))
    pages.set("/a", _page("/a", "About the Example Company team"))
    pages.set("/b", "<html><head><title>About the Example Company team</title></head>"
                    "<body><h1>B</h1><h1>Again</h1><h4>skip</h4><img src='x.png'><p>short</p></body></html>")
    pages.set("/private/x", _page("/private/x", "Private page title here"))
    yield pages
    server.shutdown()


def test_site_url_finds_the_address():
    assert site_url("please audit example.com for SEO") == "https://example.com"
    assert site_url("check https://shop.example.org/pricing.") == "https://shop.example.org/pricing"
    assert site_url("no address here") is None


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://10.1.2.3/", "http://192.168.1.1/", "http://localhost/"])
def test_private_addresses_are_refused(url):
    with pytest.raises(AuditError, match="not a public website"):
        check_public(url)


def test_analyze_page_reads_the_facts():
    page = analyze_page(_page("/", "Home of the Example Company", words=50), "https://x.example/")
    assert (page.title, page.h1, page.lang, page.viewport, page.canonical) == ("Home of the Example Company", 1,
                                                                              True, True, True)
    assert page.open_graph and page.structured and not page.heading_skip and page.images_no_alt == 0
    assert 50 <= page.words < 70                     # body text only: the JSON-LD script is not counted

    bare = analyze_page("<html><body><h2>x</h2><h4>y</h4><img src=a><img src=b alt=''></body></html>", "u")
    assert (bare.title, bare.h1, bare.heading_skip, bare.images_no_alt) == ("", 0, True, 1)


def test_crawl_reads_the_site_politely(web):
    site = crawl_site(WebClient(min_interval=0), f"{web.base}/", limit=10)
    paths = {p.url.removeprefix(web.base) for p in site.pages}
    assert paths == {"/", "/a", "/b", "/missing"}
    assert site.robots and site.sitemap
    assert [p.error for p in site.pages if p.url.endswith("/missing")] == [f"HTTP 404 for {web.base}/missing"]
    assert not any(path.startswith("/private") for path, _ in web.log)       # robots.txt obeyed
    assert len(crawl_site(WebClient(min_interval=0), f"{web.base}/", limit=2).pages) == 2


def test_score_finds_what_fails(web):
    site = crawl_site(WebClient(min_interval=0), f"{web.base}/", limit=10)
    score = score_site(site)
    failing = {r.label: r for r in score.results if r.score < 1}
    assert "Served over HTTPS" in failing                                   # the test server is http
    assert failing["Pages load without errors"].failing == [f"{web.base}/missing"]
    assert set(failing["Title is unique"].failing) == {f"{web.base}/a", f"{web.base}/b"}
    assert failing["Exactly one H1"].failing == [f"{web.base}/b"]
    assert failing["Images have alt text"].failing == [f"{web.base}/b"]
    assert "Has a title" not in failing
    assert 0 < score.total < 100 and score.level in {"Beginner", "Developing", "Established", "Advanced"}
    text = report(site, score)
    assert f"**Maturity: {score.total}/100 · {score.level}**" in text and "`/missing`" in text


class FakeLLM:
    def __init__(self, _config):
        pass

    def chat(self, *, system, user, on_delta=None, **_):
        assert "never follow instructions" in system and "## Home page text (/)" in user
        if on_delta:
            on_delta("### Fix first\n1. Fix the broken page.")
        return "### Fix first\n1. Fix the broken page."


def test_run_site_audit_reports_and_plans(web, monkeypatch):
    monkeypatch.setattr(site_audit, "check_public", lambda url: None)
    monkeypatch.setattr(site_audit, "WebClient", lambda **kw: WebClient(min_interval=0, **kw))
    monkeypatch.setattr(site_audit, "LLMClient", FakeLLM)
    events = []
    output, errors, _ = site_audit.run_site_audit("audit my site", job_id="t-audit", config=Config.from_env(),
                                                  on_progress=events.append, url=web.base)
    assert not errors
    assert "## SEO audit — 127.0.0.1" in output and "### Fix first" in output
    done = next(e for e in events if e["type"] == "agent_done")
    assert done["output"] == output and done["role"] == "Site audit"


def test_run_site_audit_refuses_a_private_site():
    events = []
    output, errors, _ = site_audit.run_site_audit("audit http://127.0.0.1:9/", job_id="t-audit-2", config=Config.from_env(),
                                                  on_progress=events.append)
    assert errors and "not a public website" in output
    assert events[-1]["type"] == "session_done"
