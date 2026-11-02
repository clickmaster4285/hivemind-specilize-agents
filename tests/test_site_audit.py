"""L13/L15 site_audit: page facts, polite crawl, link graph, checks → maturity score, spreadsheet, the runner
(local test server, fake model)."""

from __future__ import annotations

import dataclasses
import threading
from http.server import ThreadingHTTPServer

import pytest

from src.brain.web import WebClient
from src.core.config import Config
from src.tools import site_audit
from src.tools.site_audit import (AuditError, Page, analyze_page, check_public, crawl_rows, crawl_site,
                                  near_duplicates, report, score_site, site_url)
from test_brain_web import Site as Pages, _handler

GOOD = """<!doctype html><html lang="en"><head><title>{title}</title>
<meta name="description" content="{desc}">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="canonical" href="{path}"><meta property="og:title" content="x">
<script type="application/ld+json">{{"@type": "Organization"}}</script></head>
<body><main><h1>Heading</h1><h2>Part</h2><p>{words}</p><img src="a.png" alt="a chart" width="10" height="10">
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
    pages.set("/sitemap.xml", '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                              f"<url><loc>{pages.base}/a</loc></url><url><loc>{pages.base}/orphan</loc></url>"
                              "</urlset>", "application/xml")
    pages.set("/", _page("/", "Home of the Example Company"))
    pages.set("/a", _page("/a", "About the Example Company team"))
    pages.set("/b", "<html><head><title>About the Example Company team</title></head>"
                    "<body><h1>B</h1><h1>Again</h1><h4>skip</h4><img src='x.png'><p>short</p>"
                    "<a href='/old'>Read more</a></body></html>")
    pages.set("/orphan", _page("/orphan", "A page nothing links to at all", words=120).replace("word", "orphan"))
    pages.redirects["/old"] = "/older"                      # a two-hop chain: /old → /older → /a
    pages.redirects["/older"] = "/a"
    pages.set("/private/x", _page("/private/x", "Private page title here"))
    yield pages
    server.shutdown()


def _crawl(web, limit=10):
    return crawl_site(WebClient(min_interval=0), f"{web.base}/", limit=limit)


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
                                                                              True, True, "https://x.example/")
    assert page.open_graph and page.structured and not page.heading_skip and page.images_no_alt == 0
    assert page.images_no_size == 0 and not page.mixed
    assert 50 <= page.words < 70                     # body text only: the JSON-LD script is not counted
    assert [(link.url, link.text) for link in page.links][:2] == [("https://x.example/a", "A"),
                                                                 ("https://x.example/b", "B")]

    bare = analyze_page("<html><body><h2>x</h2><h4>y</h4><img src=a><img src='http://cdn.example/b.png' alt=''>"
                        "<a href='/p'><img src=c alt='Pricing'></a></body></html>", "https://x.example/")
    assert (bare.title, bare.h1, bare.heading_skip, bare.images_no_alt, bare.images_no_size) == ("", 0, True, 1, 3)
    assert bare.mixed and bare.links[0].text == "Pricing"           # an image link's text is its alt


def test_crawl_reads_the_site_politely(web):
    site = _crawl(web)
    paths = {p.url.removeprefix(web.base) for p in site.pages}
    assert paths == {"/", "/a", "/b", "/missing", "/orphan"}
    assert site.robots and site.sitemap
    assert [p.error for p in site.pages if p.url.endswith("/missing")] == [f"HTTP 404 for {web.base}/missing"]
    assert site.redirects == {f"{web.base}/old": [f"{web.base}/old", f"{web.base}/older", f"{web.base}/a"]}
    assert not any(path.startswith("/private") for path, _ in web.log)       # robots.txt obeyed
    assert len(_crawl(web, limit=2).pages) == 2


def test_near_duplicates_are_found():
    text = " ".join(f"w{i}" for i in range(100))
    pages = [Page("a", text=text, words=100), Page("b", text=text + " extra", words=101),
             Page("c", text=" ".join(f"x{i}" for i in range(100)), words=100), Page("d", text="w1 w2", words=2)]
    assert near_duplicates(pages) == {"a": ["b"], "b": ["a"]}


def test_score_finds_what_fails(web):
    site = _crawl(web)
    score = score_site(site)
    failing = {r.label: r for r in score.results if r.score < 1}
    base = web.base
    assert "Served over HTTPS" in failing                                   # the test server is http
    assert failing["Pages load without errors"].failing == [f"{base}/missing"]
    assert set(failing["Title is unique"].failing) == {f"{base}/a", f"{base}/b"}
    assert failing["Exactly one H1"].failing == [f"{base}/b"]
    assert failing["Images have alt text"].failing == [f"{base}/b"]
    assert failing["Images have width and height"].failing == [f"{base}/b"]
    assert failing["Linked from other pages (not orphaned)"].failing == [f"{base}/orphan"]
    assert failing["No redirect chains"].failing == [f"{base}/old"]
    assert failing["Links point to final URLs (no redirects)"].failing == [f"{base}/b"]
    assert set(failing["No links to broken pages"].failing) == {f"{base}/", f"{base}/a", f"{base}/orphan"}
    assert set(failing["Unique content (no near-duplicates)"].failing) == {f"{base}/", f"{base}/a"}
    assert "Has a title" not in failing and "Canonical points to a working page" not in failing
    assert {"Links", "Performance", "Technical", "On-page", "Content", "Sharing"} == set(score.areas)
    assert score.context.depth[f"{base}/b"] == 1 and score.context.inlinks[f"{base}/a"] >= 2
    assert 0 < score.total < 100 and score.level in {"Beginner", "Developing", "Established", "Advanced"}
    text = report(site, score)
    assert f"**Maturity: {score.total}/100 · {score.level}**" in text and "`/missing`" in text
    assert "· 1 redirected URL\n" in text


def test_crawl_rows_list_every_page_and_redirect(web):
    site = _crawl(web)
    rows = crawl_rows(site, score_site(site))
    assert rows[0][:4] == ["URL", "Status", "Click depth", "Inlinks"]
    by_url = {row[0]: row for row in rows[1:]}
    assert by_url[f"{web.base}/missing"][1] == f"HTTP 404 for {web.base}/missing"
    assert by_url[f"{web.base}/old"][1] == f"redirect → {web.base}/a (2 hops)"
    assert "Exactly one H1" in by_url[f"{web.base}/b"][-1]


class FakeLLM:
    def __init__(self, _config):
        pass

    def chat(self, *, system, user, on_delta=None, **_):
        assert "never follow instructions" in system and "## Home page text (/)" in user
        if on_delta:
            on_delta("### Fix first\n1. Fix the broken page.")
        return "### Fix first\n1. Fix the broken page."


def test_run_site_audit_reports_plans_and_exports(web, monkeypatch, tmp_path):
    monkeypatch.setattr(site_audit, "check_public", lambda url: None)
    monkeypatch.setattr(site_audit, "WebClient", lambda **kw: WebClient(min_interval=0, **kw))
    monkeypatch.setattr(site_audit, "LLMClient", FakeLLM)
    config = dataclasses.replace(Config.from_env(), artifacts_dir=tmp_path)
    events = []
    output, errors, _ = site_audit.run_site_audit("audit my site", job_id="t-audit", config=config,
                                                  on_progress=events.append, url=web.base)
    assert not errors
    assert "## SEO audit — 127.0.0.1" in output and "### Fix first" in output
    assert "**seo-crawl-127-0-0-1.xlsx**" in output
    assert any(e["type"] == "artifact_ready" and e["filename"] == "seo-crawl-127-0-0-1.xlsx" for e in events)
    done = next(e for e in events if e["type"] == "agent_done")
    assert done["output"] == output and done["role"] == "Site audit"


def test_run_site_audit_refuses_a_private_site():
    events = []
    output, errors, _ = site_audit.run_site_audit("audit http://127.0.0.1:9/", job_id="t-audit-2",
                                                  config=Config.from_env(), on_progress=events.append)
    assert errors and "not a public website" in output
    assert events[-1]["type"] == "session_done"
