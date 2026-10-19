"""Polite web fetching for knowledge sources (doc/BRAIN_DESIGN.md §4 step 1, B5).

- Identifies itself (User-Agent) and obeys robots.txt
- At most one request per MIN_INTERVAL seconds per host
- Stays on the source's own host: redirects to other hosts are refused
- Size and time limits; only http(s); only text content (HTML, Markdown, plain text, XML)
- Conditional requests (ETag / Last-Modified) so unchanged pages cost a 304 and no download
- URL discovery: explicit pages, bounded link-following, sitemaps (incl. sitemap indexes), RSS/Atom feeds
"""

from __future__ import annotations

import fnmatch
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import urldefrag, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup
from bs4.dammit import UnicodeDammit

USER_AGENT = "MulteAgentBrain/0.1 (local knowledge indexer; respects robots.txt)"
TIMEOUT_SECONDS = 30
MAX_BYTES = 5_000_000
MAX_REDIRECTS = 5
MIN_INTERVAL_SECONDS = 1.0
TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/markdown", "text/plain",
              "application/xml", "text/xml", "application/rss+xml", "application/atom+xml")


class WebError(RuntimeError):
    """A page could not be fetched (blocked by robots.txt, wrong host, too big, HTTP error, …)."""


@dataclass
class WebResponse:
    url: str                    # final URL after same-host redirects
    status: int                 # 200, or 304 when unchanged
    text: str | None            # decoded body (None on 304)
    content_type: str
    etag: str | None
    last_modified: str | None

    @property
    def not_modified(self) -> bool:
        return self.status == 304


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def normalize_url(url: str) -> str:
    """Drop #fragments; keep everything else exactly (query strings can matter)."""
    return urldefrag(url.strip())[0]


class WebClient:
    def __init__(self, *, min_interval: float = MIN_INTERVAL_SECONDS, user_agent: str = USER_AGENT) -> None:
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self.user_agent = user_agent
        self.min_interval = min_interval
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}

    # ── politeness ───────────────────────────────────────────────────
    def _wait_turn(self, host: str) -> None:
        last = self._last_request.get(host)
        if last is not None:
            delay = self.min_interval - (time.monotonic() - last)
            if delay > 0:
                time.sleep(delay)
        self._last_request[host] = time.monotonic()

    def allowed(self, url: str) -> bool:
        """robots.txt check (cached per host). No robots.txt or unreadable → allowed."""
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            parser: RobotFileParser | None = RobotFileParser()
            try:
                self._wait_turn(_host(url))
                resp = self.session.get(f"{origin}/robots.txt", timeout=TIMEOUT_SECONDS)
                if resp.status_code == 200:
                    parser.parse(resp.text.splitlines())
                elif resp.status_code in (401, 403):
                    parser.disallow_all = True
                else:
                    parser = None
            except requests.RequestException:
                parser = None
            self._robots[origin] = parser
        parser = self._robots[origin]
        return True if parser is None else parser.can_fetch(self.user_agent, url)

    # ── fetching ─────────────────────────────────────────────────────
    def get(self, url: str, *, etag: str | None = None, last_modified: str | None = None) -> WebResponse:
        url = normalize_url(url)
        if urlsplit(url).scheme not in ("http", "https"):
            raise WebError(f"only http(s) URLs are allowed: {url}")
        if not self.allowed(url):
            raise WebError(f"blocked by robots.txt: {url}")
        start_host = _host(url)
        headers = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        current = url
        for _ in range(MAX_REDIRECTS + 1):
            self._wait_turn(start_host)
            try:
                resp = self.session.get(current, headers=headers, timeout=TIMEOUT_SECONDS,
                                        allow_redirects=False, stream=True)
            except requests.RequestException as exc:
                raise WebError(f"request failed for {current}: {exc}") from exc
            if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
                target = normalize_url(urljoin(current, resp.headers.get("Location", "")))
                resp.close()
                if _host(target) != start_host:
                    raise WebError(f"redirect to another host refused: {current} -> {target}")
                if not self.allowed(target):
                    raise WebError(f"blocked by robots.txt: {target}")
                current = target
                continue
            break
        else:
            raise WebError(f"too many redirects: {url}")

        with resp:
            if resp.status_code == 304:
                return WebResponse(current, 304, None, "", etag, last_modified)
            if resp.status_code != 200:
                raise WebError(f"HTTP {resp.status_code} for {current}")
            content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if content_type and not content_type.startswith(TEXT_TYPES):
                raise WebError(f"not a text page ({content_type}): {current}")
            body = bytearray()
            for block in resp.iter_content(64 * 1024):
                body.extend(block)
                if len(body) > MAX_BYTES:
                    raise WebError(f"page larger than {MAX_BYTES // 1_000_000} MB: {current}")
            return WebResponse(
                url=current,
                status=200,
                text=_decode(bytes(body), resp.headers.get("Content-Type", "")),
                content_type=content_type,
                etag=resp.headers.get("ETag"),
                last_modified=resp.headers.get("Last-Modified"),
            )


def _decode(body: bytes, content_type_header: str) -> str:
    """Use the declared charset; otherwise detect (HTML <meta charset>, BOM, UTF-8 first)."""
    match = re.search(r"charset=([\w-]+)", content_type_header, re.IGNORECASE)
    if match:
        try:
            return body.decode(match.group(1), errors="replace")
        except LookupError:
            pass
    return UnicodeDammit(body, ["utf-8"], is_html=True).unicode_markup or body.decode("utf-8", errors="replace")


# ── URL discovery ────────────────────────────────────────────────────

def matches(url: str, include: str | None, base_url: str) -> bool:
    """Same host as the source, and matching the include glob when one is given."""
    if _host(url) != _host(base_url):
        return False
    return fnmatch.fnmatch(url, include) if include else True


def page_links(html: str, page_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, None] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "javascript:", "tel:")):
            continue
        out.setdefault(normalize_url(urljoin(page_url, href)))
    return list(out)


def _xml_root(text: str) -> ET.Element:
    try:
        return ET.fromstring(text.encode("utf-8"))
    except ET.ParseError as exc:
        raise WebError(f"invalid XML: {exc}") from exc


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def sitemap_urls(client: WebClient, sitemap_url: str, *, max_sitemaps: int = 20) -> list[str]:
    """All <loc> page URLs of a sitemap; follows sitemap indexes (bounded)."""
    pending, seen_maps, pages = [sitemap_url], set(), []
    while pending and len(seen_maps) < max_sitemaps:
        current = pending.pop(0)
        if current in seen_maps:
            continue
        seen_maps.add(current)
        root = _xml_root(client.get(current).text or "")
        locs = [el.text.strip() for el in root.iter() if _local(el.tag) == "loc" and el.text]
        if _local(root.tag) == "sitemapindex":
            pending.extend(locs)
        else:
            pages.extend(locs)
    return pages


def feed_urls(client: WebClient, feed_url: str) -> list[str]:
    """Item links of an RSS 2.0 or Atom feed."""
    root = _xml_root(client.get(feed_url).text or "")
    links: list[str] = []
    for el in root.iter():
        name = _local(el.tag)
        if name == "item":  # RSS
            link = next((c.text for c in el if _local(c.tag) == "link" and c.text), None)
            if link:
                links.append(link.strip())
        elif name == "entry":  # Atom
            for c in el:
                if _local(c.tag) == "link" and c.get("href") and c.get("rel", "alternate") == "alternate":
                    links.append(c.get("href").strip())
                    break
    return [normalize_url(urljoin(feed_url, link)) for link in links]


def crawl(
    client: WebClient, start_urls: list[str], *, include: str | None, depth: int, limit: int
) -> tuple[list[str], dict[str, WebResponse]]:
    """Breadth-first link following from start pages, same host + include pattern only.

    Returns (discovered page URLs in order, responses already fetched along the way).
    Start pages are always kept; index pages that only list links are kept too (they hold text).
    """
    base = start_urls[0]
    found: list[str] = []
    fetched: dict[str, WebResponse] = {}
    frontier = [normalize_url(u) for u in start_urls]
    seen = set(frontier)
    for level in range(depth + 1):
        next_frontier: list[str] = []
        for url in frontier:
            if len(found) >= limit:
                return found, fetched
            found.append(url)
            if level == depth:
                continue
            try:
                resp = client.get(url)
            except WebError:
                continue
            fetched[url] = resp
            if resp.text and resp.content_type.startswith(("text/html", "application/xhtml")):
                for link in page_links(resp.text, resp.url):
                    if link not in seen and matches(link, include, base) and client.allowed(link):
                        seen.add(link)
                        next_frontier.append(link)
        frontier = next_frontier
    return found, fetched
