"""Search results as SEO data (BACKLOG L15 P3/P4): who ranks for a keyword, keyword ideas, what top pages cover.

Everything comes from our own SearXNG, so it is an APPROXIMATION (user's choice, doc/SEO_TOOLS.md): SearXNG merges
several engines, positions are its merged order, and there is no search volume. Searches run in one market (a country
or worldwide, src/tools/market.py) — without one SearXNG would search from its own region.
Top pages are read with the audit's polite client (robots.txt, public addresses only) and are treated as data.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from urllib.parse import urlsplit

import requests

from src.brain.web import WebClient, WebError
from src.core.config import Config
from src.tools.market import Market
from src.tools.site_audit import AUDIT_AGENT, AuditError, Page, analyze_page, check_public
from src.tools.web_search import SEARCH_TIMEOUT, WebSearchError

SERP_PAGES = 2                     # two result pages ≈ the top 20
TOP = 10                           # "the first page" that difficulty and overviews look at
IDEA_EXPAND = 3                    # the first suggestions are asked for their own suggestions too
MAX_IDEAS = 30
BENCHMARK_PAGES = 5                # top pages read to see what ranking content looks like
READ_SECONDS = 25                  # all top pages together; slower ones are left out
COMMON_SHARE = 0.5                 # a phrase "the top pages cover" appears on at least half of them
MAX_PHRASES = 15
DIFFICULTY = ((2 / 3, "High"), (1 / 3, "Medium"), (0, "Low"))


@dataclass
class Ranked:
    position: int
    url: str
    title: str
    snippet: str
    engines: list[str]

    @property
    def host(self) -> str:
        return bare_host(self.url)


def bare_host(url: str) -> str:
    host = (urlsplit(url if "://" in url else f"https://{url}").hostname or "").lower()
    return host.removeprefix("www.")


def _get(config: Config, path: str, params: dict) -> object:
    if not config.web_search_url:
        raise WebSearchError("web search is not set up (WEB_SEARCH_URL in .env)")
    try:
        resp = requests.get(f"{config.web_search_url}{path}", params=params, timeout=SEARCH_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.ConnectionError as exc:
        raise WebSearchError("the web search service is not running (docker compose up -d searxng)") from exc
    except (requests.RequestException, ValueError) as exc:
        raise WebSearchError(f"web search failed: {exc}") from exc


def _in(market: Market | None) -> dict:
    """SearXNG's language-region parameter for the market (none: SearXNG's own default)."""
    return {"language": market.search_language} if market else {}


def serp(keyword: str, config: Config, *, pages: int = SERP_PAGES,
         market: Market | None = None) -> tuple[list[Ranked], list[str]]:
    """(results in SearXNG's merged order, related searches the engines suggested)."""
    results: list[Ranked] = []
    related: list[str] = []
    seen: set[str] = set()
    for pageno in range(1, pages + 1):
        data = _get(config, "/search", {"q": keyword, "format": "json", "pageno": pageno, "safesearch": 1,
                                             **_in(market)})
        related += [str(s) for s in (data.get("suggestions") or []) if str(s).strip()]
        for r in data.get("results") or []:
            url = str(r.get("url") or "")
            if not url.startswith(("http://", "https://")) or url in seen:
                continue
            seen.add(url)
            results.append(Ranked(len(results) + 1, url, str(r.get("title") or url), str(r.get("content") or ""),
                                  [str(e) for e in r.get("engines") or [r.get("engine")] if e]))
    return results, list(dict.fromkeys(related))


def autocomplete(text: str, config: Config, market: Market | None = None) -> list[str]:
    """What people type after `text` (the engines' suggestions via SearXNG's autocompleter)."""
    data = _get(config, "/autocompleter", {"q": text, **_in(market)})
    items = data[1] if isinstance(data, list) and len(data) > 1 and isinstance(data[1], list) else data
    return [str(s).strip() for s in items or [] if isinstance(s, str) and s.strip()]


def keyword_ideas(keyword: str, config: Config, *, related: list[str] = (),
                  market: Market | None = None) -> list[str]:
    """Suggestions for the keyword, then for its first few suggestions, plus related searches."""
    first = autocomplete(keyword, config, market)
    ideas = list(first) + list(related)
    for idea in first[:IDEA_EXPAND]:
        try:
            ideas += autocomplete(idea, config, market)
        except WebSearchError:
            break
    own = keyword.strip().lower()
    return [i for i in dict.fromkeys(x.lower() for x in ideas) if i != own][:MAX_IDEAS]


def read_top(results: list[Ranked], count: int) -> list[Page]:
    """The top `count` result pages, read in parallel (each from its own host), in rank order."""
    def read(url: str) -> Page | None:
        try:
            check_public(url)
            resp = WebClient(user_agent=AUDIT_AGENT).get(url)
        except (AuditError, WebError):
            return None
        if not (resp.text and resp.content_type.startswith(("text/html", "application/xhtml"))):
            return None
        return analyze_page(resp.text, resp.url)

    urls = [r.url for r in results[:count]]
    if not urls:
        return []
    pool = ThreadPoolExecutor(max_workers=len(urls))
    futures = [pool.submit(read, url) for url in urls]
    wait(futures, timeout=READ_SECONDS)
    pool.shutdown(wait=False, cancel_futures=True)
    return [f.result() for f in futures if f.done() and not f.exception() and f.result() is not None]


def phrases(text: str) -> Counter:
    """Two-word phrases of real words (both 4+ letters, so 'of the' never counts) → how often they occur."""
    words = re.findall(r"[^\W\d_]+", text.lower())
    return Counter(f"{a} {b}" for a, b in zip(words, words[1:]) if len(a) >= 4 and len(b) >= 4)


def common_phrases(texts: list[str], *, share: float = COMMON_SHARE, limit: int = MAX_PHRASES) -> list[str]:
    """Phrases on at least `share` of the texts (and at least two), most widespread first."""
    counts = [phrases(t) for t in texts]
    spread = Counter(p for c in counts for p in c)
    total = sum(counts, Counter())
    need = max(2, share * len(texts))
    return [p for p, n in sorted(spread.items(), key=lambda kv: (-kv[1], -total[kv[0]])) if n >= need][:limit]


@dataclass
class Difficulty:
    label: str
    targeted: float                 # share of the top results whose title contains every word of the keyword
    home_pages: float               # share of the top results that are a site's home page (strong, broad sites)


def difficulty(keyword: str, results: list[Ranked]) -> Difficulty:
    """An estimate without backlink data: the more top results target the keyword in their title, and the more of
    them are whole sites' home pages, the harder it is to break in."""
    top = results[:TOP]
    words = set(re.findall(r"\w+", keyword.lower()))
    targeted = statistics.mean(all(w in r.title.lower() for w in words) for r in top) if top else 0.0
    homes = statistics.mean(urlsplit(r.url).path in ("", "/") for r in top) if top else 0.0
    value = (targeted + homes) / 2
    return Difficulty(next(label for floor, label in DIFFICULTY if value >= floor), targeted, homes)


@dataclass
class Benchmark:
    """What the pages ranking for a keyword look like — the bar new content has to clear."""
    keyword: str
    pages: int                      # top pages actually read
    words: int                      # their median length
    phrases: list[str]              # phrases most of them use
    titles: list[str]
    market: str = "Worldwide"      # where they rank (Market.label)


def benchmark(keyword: str, config: Config, *, count: int = BENCHMARK_PAGES,
              market: Market | None = None) -> Benchmark | None:
    """Read the top pages for a keyword; None when search is not set up or nothing could be read."""
    try:
        results, _ = serp(keyword, config, pages=1, market=market)
    except WebSearchError:
        return None
    pages = read_top(results, count)
    if not pages:
        return None
    return Benchmark(keyword, len(pages), round(statistics.median(p.words for p in pages)),
                     common_phrases([p.text for p in pages]), [r.title for r in results[:count]],
                     market.label if market else Benchmark.market)


def rank_of(domain: str, results: list[Ranked]) -> Ranked | None:
    """The first result from this site (subdomains count: blog.example.com ranks for example.com)."""
    host = bare_host(domain)
    return next((r for r in results if r.host == host or r.host.endswith(f".{host}")), None)
