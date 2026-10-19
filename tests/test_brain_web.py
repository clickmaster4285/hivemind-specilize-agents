"""B5 tests: web fetching, HTML extraction, web ingestion lifecycle, scheduler.

Everything runs against a local test web server (127.0.0.1, random port) — no internet needed.
DB tests need: docker compose up -d db. Embeddings are faked (no Ollama needed).
"""

from __future__ import annotations

import hashlib
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg
import pytest
from psycopg.types.json import Jsonb

from src.brain import db as brain_db
from src.brain import web as brain_web
from src.brain.embed import EMBED_DIM
from src.brain.extract import extract_html
from src.brain.ingest import ingest_source
from src.brain.migrate import migrate
from src.brain.scheduler import LOCK_KEY, due_sources, in_window, run_due
from src.brain.web import WebClient, WebError, crawl, feed_urls, sitemap_urls
from src.config import Config

PREFIX = "test-web-"


# ── local test web server ────────────────────────────────────────────────

class Site:
    """Mutable pages served by the test server; records how each request was answered."""

    def __init__(self) -> None:
        self.pages: dict[str, tuple[str, bytes]] = {}   # path -> (content-type, body)
        self.redirects: dict[str, str] = {}
        self.log: list[tuple[str, int]] = []            # (path, status)
        self.down = False
        self.base = ""

    def set(self, path: str, body: str | bytes, ctype: str = "text/html; charset=utf-8") -> None:
        self.pages[path] = (ctype, body.encode("utf-8") if isinstance(body, str) else body)


def _handler(site: Site):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):  # keep test output quiet
            pass

        def _send(self, status: int, ctype: str = "text/plain", body: bytes = b"", headers=None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if body:
                self.wfile.write(body)
            site.log.append((self.path, status))

        def do_GET(self):  # noqa: N802 — http.server API
            if site.down and self.path != "/robots.txt":
                return self._send(503)
            if self.path in site.redirects:
                return self._send(302, headers={"Location": site.redirects[self.path]})
            if self.path not in site.pages:
                return self._send(404)
            ctype, body = site.pages[self.path]
            etag = '"' + hashlib.md5(body).hexdigest() + '"'
            if self.headers.get("If-None-Match") == etag:
                return self._send(304, headers={"ETag": etag})
            self._send(200, ctype, body, {"ETag": etag, "Last-Modified": "Thu, 15 Oct 2026 07:10:47 GMT"})

    return Handler


@pytest.fixture
def site():
    s = Site()
    s.set("/robots.txt", "User-agent: *\nDisallow: /docs/private/\n", "text/plain")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(s))
    s.base = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield s
    server.shutdown()


@pytest.fixture
def client() -> WebClient:
    return WebClient(min_interval=0)


DOC_A = """<!doctype html><html><head><title>Guide A — Example Docs</title>
<meta name="description" content="How to do A">
<meta property="article:modified_time" content="2026-03-01T10:00:00Z"></head>
<body><nav>Home | Docs | Blog</nav><script>track()</script>
<main><h1>Guide A<a class="headerlink" href="#a">¶</a></h1>
<p>Call <code>do_a()</code> to start. See <a href="b.html">guide B</a>.</p>
<h2>Example</h2><pre class="highlight-python">print("a")
print("done")</pre>
<table><tr><th>Option</th><th>Meaning</th></tr><tr><td>-v</td><td>verbose</td></tr></table>
</main><footer>Copyright Example</footer></body></html>"""


# ── fetching ─────────────────────────────────────────────────────────────

def test_conditional_get_returns_304_when_unchanged(site, client):
    site.set("/docs/a.html", DOC_A)
    first = client.get(site.base + "/docs/a.html")
    assert first.status == 200 and first.etag and "Guide A" in first.text
    second = client.get(site.base + "/docs/a.html", etag=first.etag)
    assert second.not_modified and second.text is None


def test_robots_txt_is_obeyed(site, client):
    site.set("/docs/private/secret.html", "<p>secret</p>")
    with pytest.raises(WebError, match="robots"):
        client.get(site.base + "/docs/private/secret.html")


def test_redirect_to_another_host_is_refused(site, client):
    site.redirects["/go"] = "http://127.0.0.2:1/elsewhere"
    with pytest.raises(WebError, match="another host"):
        client.get(site.base + "/go")


def test_same_host_redirect_is_followed(site, client):
    site.set("/docs/new.html", "<h1>New</h1>")
    site.redirects["/docs/old.html"] = "/docs/new.html"
    resp = client.get(site.base + "/docs/old.html")
    assert resp.url.endswith("/docs/new.html") and "New" in resp.text


def test_size_limit(site, client, monkeypatch):
    monkeypatch.setattr(brain_web, "MAX_BYTES", 1000)
    site.set("/big.html", "x" * 5000)
    with pytest.raises(WebError, match="larger than"):
        client.get(site.base + "/big.html")


def test_binary_content_is_refused(site, client):
    site.set("/file.zip", b"PK\x03\x04", "application/zip")
    with pytest.raises(WebError, match="not a text page"):
        client.get(site.base + "/file.zip")


def test_declared_and_detected_charsets(site, client):
    site.set("/latin.html", "<p>café</p>".encode("latin-1"), "text/html; charset=iso-8859-1")
    site.set("/meta.html", '<meta charset="utf-8"><p>naïve – ok</p>'.encode("utf-8"), "text/html")
    assert "café" in client.get(site.base + "/latin.html").text
    assert "naïve – ok" in client.get(site.base + "/meta.html").text


def test_crawl_stays_inside_include_pattern_and_robots(site, client):
    site.set("/docs/index.html", '<a href="a.html">A</a> <a href="b.html#part">B</a> <a href="/blog/x.html">X</a>'
             ' <a href="private/p.html">P</a> <a href="https://other.example/y.html">Y</a>')
    urls, fetched = crawl(client, [site.base + "/docs/index.html"],
                          include=site.base + "/docs/*.html", depth=1, limit=100)
    assert urls == [site.base + "/docs/index.html", site.base + "/docs/a.html", site.base + "/docs/b.html"]
    assert site.base + "/docs/index.html" in fetched


def test_sitemap_index_and_feeds(site, client):
    site.set("/sitemap.xml", f"""<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
        <sitemap><loc>{site.base}/sm1.xml</loc></sitemap></sitemapindex>""", "application/xml")
    site.set("/sm1.xml", f"""<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
        <url><loc>{site.base}/docs/a.html</loc></url><url><loc>{site.base}/docs/b.html</loc></url></urlset>""",
             "application/xml")
    site.set("/feed.rss", f"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
        <item><title>one</title><link>{site.base}/news/1.html</link></item>
        <item><title>two</title><link>/news/2.html</link></item></channel></rss>""", "application/rss+xml")
    site.set("/feed.atom", """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
        <entry><title>e</title><link rel="alternate" href="/news/3.html"/></entry></feed>""", "application/atom+xml")
    assert sitemap_urls(client, site.base + "/sitemap.xml") == [site.base + "/docs/a.html", site.base + "/docs/b.html"]
    assert feed_urls(client, site.base + "/feed.rss") == [site.base + "/news/1.html", site.base + "/news/2.html"]
    assert feed_urls(client, site.base + "/feed.atom") == [site.base + "/news/3.html"]


# ── HTML -> Markdown ────────────────────────────────────────────────────

def test_extract_html_keeps_content_and_drops_page_furniture():
    doc = extract_html(DOC_A, fallback_title="fallback")
    md = doc.content_md
    assert doc.title == "Guide A"
    assert doc.description == "How to do A"
    assert doc.updated_at and doc.updated_at.isoformat().startswith("2026-03-01")
    assert md.startswith("# Guide A")
    assert "Call `do_a()` to start. See guide B." in md            # inline code kept, link target dropped
    assert '```python\nprint("a")\nprint("done")\n```' in md         # code block with language
    assert "| Option | Meaning |" in md and "| -v | verbose |" in md  # table
    for junk in ("Home | Docs", "track()", "Copyright Example", "¶"):
        assert junk not in md


# ── web ingestion lifecycle (database) ──────────────────────────────────

def fake_embed(texts: list[str]) -> list[list[float]]:
    return [[(hashlib.sha256(t.encode()).digest()[i % 32]) / 255 for i in range(EMBED_DIM)] for t in texts]


@pytest.fixture
def config() -> Config:
    cfg = Config.from_env()
    try:
        migrate(cfg)
    except (brain_db.BrainDBError, psycopg.OperationalError) as exc:
        pytest.skip(f"brain database not available: {exc}")
    return cfg


@pytest.fixture
def web_source(site, config):
    slug = PREFIX + "docs"
    with brain_db.connect(config, system=True) as conn:
        cid = conn.execute("SELECT id FROM brain.collections WHERE slug = 'development'").fetchone()[0]
        conn.execute(
            """INSERT INTO brain.sources (slug, kind, url, title, collection_id, licence, enabled,
                                          include_pattern, config)
               VALUES (%s, 'web_page', %s, 'Test docs', %s, 'test', true, %s, %s)""",
            (slug, site.base + "/docs/index.html", cid, site.base + "/docs/*.html", Jsonb({"crawl_depth": 1})),
        )
    yield slug
    with psycopg.connect(brain_db.owner_url(config), autocommit=True) as conn:
        conn.execute("DELETE FROM brain.ingest_runs WHERE source_id IN (SELECT id FROM brain.sources WHERE slug LIKE %s)", (PREFIX + "%",))
        conn.execute("DELETE FROM brain.sources WHERE slug LIKE %s", (PREFIX + "%",))


def _ingest(slug, config, client):
    return ingest_source(slug, config, embed=fake_embed, log=lambda _m: None, web_client=client)


def _docs(slug, config):
    with brain_db.connect(config) as conn:
        return conn.execute(
            """SELECT regexp_replace(d.url, '^.*/', ''), d.version, d.status, d.etag IS NOT NULL
               FROM brain.documents d JOIN brain.sources s ON s.id = d.source_id
               WHERE s.slug = %s ORDER BY 1, 2""",
            (slug,),
        ).fetchall()


def test_web_ingest_lifecycle(site, client, config, web_source):
    site.set("/docs/index.html", '<main><h1>Docs</h1><p>Start here.</p><a href="a.html">A</a> <a href="b.html">B</a></main>')
    site.set("/docs/a.html", DOC_A)
    site.set("/docs/b.html", "<main><h1>Guide B</h1><p>Version one of B.</p></main>")

    # 1. first run: three pages become documents, with ETags stored
    first = _ingest(web_source, config, client)
    assert (first.status, first.new) == ("ok", 3)
    assert _docs(web_source, config) == [("a.html", 1, "active", True), ("b.html", 1, "active", True),
                                         ("index.html", 1, "active", True)]

    # 2. nothing changed: the server answers 304 and no page is downloaded again
    site.log.clear()
    second = _ingest(web_source, config, client)
    assert (second.same, second.new, second.changed) == (3, 0, 0)
    assert ("/docs/a.html", 304) in site.log and ("/docs/b.html", 304) in site.log

    # 3. page B changes: version 2, version 1 kept as superseded
    site.set("/docs/b.html", "<main><h1>Guide B</h1><p>Version two of B, with a new step.</p></main>")
    third = _ingest(web_source, config, client)
    assert (third.changed, third.same) == (1, 2)
    assert ("b.html", 1, "superseded", True) in _docs(web_source, config)
    assert ("b.html", 2, "active", True) in _docs(web_source, config)

    # 4. outage: the run fails, and NO page is counted as missing
    site.down = True
    outage = _ingest(web_source, config, client)
    assert outage.status == "failed" and outage.removed == 0
    site.down = False

    # 5. page A unlinked and gone: missing once -> kept; twice -> removed (kept for audit)
    site.set("/docs/index.html", '<main><h1>Docs</h1><p>Start here.</p><a href="b.html">B</a></main>')
    del site.pages["/docs/a.html"]
    assert _ingest(web_source, config, client).removed == 0
    assert _ingest(web_source, config, client).removed == 1
    assert ("a.html", 1, "removed", True) in _docs(web_source, config)


# ── scheduler ────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "window, hour, expected",
    [("", 14, True), ("01-06", 3, True), ("01-06", 6, False), ("01-06", 14, False),
     ("22-06", 23, True), ("22-06", 2, True), ("22-06", 12, False), ("05-05", 9, True)],
)
def test_refresh_window(window, hour, expected):
    assert in_window(window, datetime(2026, 10, 15, hour, 30)) is expected


def test_bad_refresh_window_is_rejected():
    with pytest.raises(ValueError):
        in_window("nightly")


def test_due_sources(config, web_source):
    with brain_db.connect(config, system=True) as conn:
        conn.execute("UPDATE brain.sources SET last_checked_at = now() - interval '8 days' WHERE slug = %s", (web_source,))
    assert web_source in due_sources(config)
    with brain_db.connect(config, system=True) as conn:
        conn.execute("UPDATE brain.sources SET last_checked_at = now() WHERE slug = %s", (web_source,))
    assert web_source not in due_sources(config)


def test_only_one_refresh_runs_at_a_time(config):
    with psycopg.connect(brain_db.owner_url(config), autocommit=True) as holder:
        holder.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
        try:
            assert run_due(config, log_fn=lambda _m: None) == []
        finally:
            holder.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
