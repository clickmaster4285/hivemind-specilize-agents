"""Web images (R4): find real photos for an educational answer, fetch them SERVER-SIDE, and save them
into the job's attachment store so the browser loads them LOCALLY (no external request, no IP leak).

Safe by construction: we choose the URLs (SearXNG image results), never the model; every fetch blocks
private/internal hosts (SSRF), checks the content-type is an image, and caps size and time. Best-effort —
returns [] on any problem, so the answer is never blocked.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import socket
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

import requests

from src.core.config import Config

logger = logging.getLogger(__name__)

_ALLOWED = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}
_MAX_BYTES = 5 * 1024 * 1024
_SEARCH_TIMEOUT = 10
_FETCH_TIMEOUT = 8
# context-aware gathering: one image search per section heading, so a React diagram lands under "React"
# and Istanbul photos under "Istanbul" — not one bulk search for the whole answer.
MAX_TOPICS = 8                   # at most this many section/entity searches (keeps it fast)
PER_ANCHOR = 4                   # images kept per section
MAX_IMAGES = 12                  # overall cap across the answer
_HEADING_RE = re.compile(r"^\s{0,3}#{1,4}\s+(.+?)\s*#*\s*$", re.M)
# A heading like "Top Cities" / "Nature & Islands" groups entities rather than being one itself — so we
# search its bold sub-items (Seoul, Busan, …) instead of the generic heading, and anchor them to it.
_CATEGORY_RE = re.compile(
    r"\b(cities|places|sites|destinations?|attractions?|landmarks?|gems?|highlights?|essentials?|"
    r"nature|islands?|beaches?|mountains?|parks?|food|culture|things|must[- ]?sees?|overview|"
    r"top|best|recommended|picks|list)\b", re.I)
_BOLD_ITEM_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])?\s*\*\*(.+?)\*\*", re.M)


def _norm_anchor(text: str) -> str:
    """A stable key tying a heading to its images — the frontend computes the exact same key to place them.
    NFKD + strip markup + drop non-alphanumerics folds emoji, accents and punctuation away consistently."""
    t = unicodedata.normalize("NFKD", text or "")
    t = re.sub(r"[*_`#]", "", t).lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def _search_term(text: str) -> str:
    """A clean image search term from a heading or a bold label: drop emoji / numbering / 'Phase N:' and
    the '– subtitle' / ': …' tail / trailing punctuation, keeping the core entity."""
    t = re.sub(r"^[\W\d_]+", "", text or "")                          # leading emoji / numbering
    t = re.sub(r"^(phase|step|part)\s*\d*\s*[:.\-]?\s*", "", t, flags=re.I)
    t = re.sub(r"[*_`#]", "", t)
    t = re.split(r"\s[–—-]\s|:\s", t, maxsplit=1)[0]                  # keep the entity before a dash/colon
    return re.sub(r"[\s:–—-]+$", "", t).strip()[:60]                  # trim a trailing colon/dash


def _sections(answer: str) -> list[tuple[str, str]]:
    """[(heading, body)] split at Markdown headings; body is the text until the next heading. The intro
    before the first heading is dropped (no heading to anchor to)."""
    out: list[tuple[str, str]] = []
    heading, start, text = "", 0, answer or ""
    for m in _HEADING_RE.finditer(text):
        out.append((heading, text[start:m.start()]))
        heading, start = m.group(1).strip(), m.end()
    out.append((heading, text[start:]))
    return [(h, b) for h, b in out if h]


def _topics(answer: str) -> list[tuple[str, str]]:
    """(anchor_key, search_term) pairs for the answer. A specific heading (React.js, Istanbul) is its own
    entity; a category heading (Top Cities, Nature & Islands) is searched by its bold sub-items (Seoul,
    Busan, Jeju) instead — all anchored back to that section, so the UI groups them under it. Deduped, capped."""
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for heading, body in _sections(answer):
        hkey = _norm_anchor(heading)
        if not hkey:
            continue
        terms: list[str] = []
        if _CATEGORY_RE.search(heading):                             # a grouping heading → search its entities
            for bold in _BOLD_ITEM_RE.findall(body):
                term = _search_term(bold)
                if len(term) >= 3 and term[:1].isupper():            # a proper-noun-looking entity
                    terms.append(term)
        if not terms:                                                # specific heading, or no entities found
            term = _search_term(heading)
            if len(term) >= 3:
                terms = [term]
        for term in terms:
            dedup = (hkey, term.lower())
            if dedup in seen:
                continue
            seen.add(dedup)
            out.append((hkey, term))
            if len(out) >= MAX_TOPICS:
                return out
    return out


def _is_public(url: str) -> bool:
    """Reject anything that resolves to a private / loopback / link-local / reserved address (SSRF guard)."""
    try:
        host = urlsplit(url).hostname
        if not host:
            return False
        for info in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
        return True
    except Exception:  # noqa: BLE001 — unresolvable / malformed → treat as unsafe
        return False


def search_images(query: str, config: Config, n: int) -> list[str]:
    """Image-result URLs from SearXNG (public web only)."""
    if not config.web_search_url:
        return []
    try:
        resp = requests.get(
            f"{config.web_search_url}/search",
            params={"q": query, "format": "json", "categories": "images", "safesearch": 1},
            timeout=_SEARCH_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.info("image search failed: %s", exc)
        return []
    urls: list[str] = []
    for r in data.get("results") or []:
        u = str(r.get("img_src") or r.get("thumbnail_src") or "")
        if u.startswith(("http://", "https://")) and u not in urls:
            urls.append(u)
        if len(urls) >= n:
            break
    return urls


def _fetch(url: str) -> tuple[str, bytes] | None:
    """Download one image with all the limits. → (mime, bytes) or None."""
    if not _is_public(url):
        return None
    try:
        resp = requests.get(url, timeout=_FETCH_TIMEOUT, stream=True,
                            headers={"User-Agent": "Mozilla/5.0 (MulteAgent)"})
        resp.raise_for_status()
        ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype not in _ALLOWED:
            return None
        blob = b""
        for chunk in resp.iter_content(64 * 1024):
            blob += chunk
            if len(blob) > _MAX_BYTES:
                return None
        return (ctype, blob) if blob else None
    except Exception as exc:  # noqa: BLE001 — one bad image must not break the answer
        logger.info("image fetch failed (%s): %s", url, exc)
        return None


def wants_images(answer: str) -> bool:
    """Decide — from the ANSWER itself, not a fixed list of query keywords — whether images help.
    Pictures suit a structured answer about depictable things (several real section headings to anchor
    them under); they don't help a greeting, a one-liner, a refusal, or a mostly-code reply. This is how
    the tool self-selects, so it works for travel, roadmaps, products, history… without a hand-kept list."""
    if len(_topics(answer)) < 2:                         # no clear sections → nothing to illustrate per topic
        return False
    body = re.sub(r"```[\s\S]*?```", "", answer or "")   # drop fenced code
    return len(body) >= 0.6 * max(1, len(answer or ""))  # skip code-dominated answers (stock photos add noise)


def gather_web_images(query: str, answer: str, job_id: str, config: Config) -> list[dict[str, str]]:
    """Context-aware image gathering: if the answer is worth illustrating (wants_images), search once per
    section heading, fetch the candidates, and tag each saved image with the `anchor` of the section it
    belongs to so the UI can place it there. → saved metadata (or [] when images don't suit the answer).
    `query` is accepted for the caller's convenience but the decision is answer-driven, not query-driven."""
    if not wants_images(answer):
        return []
    plan = _topics(answer)                               # (anchor_key, search_term); several may share an anchor
    with ThreadPoolExecutor(max_workers=min(MAX_TOPICS, len(plan))) as pool:
        found = list(pool.map(lambda t: (t[0], search_images(t[1], config, PER_ANCHOR * 2)), plan))
    # round-robin the result URLs within each anchor, so a section with several entities (Seoul, Busan,
    # Jeju) contributes one of each before a second of any — a varied set, not 4 photos of one place.
    from collections import OrderedDict

    grouped: OrderedDict[str, list[list[str]]] = OrderedDict()
    for anchor, urls in found:
        grouped.setdefault(anchor, []).append(urls)
    candidates: list[tuple[str, str]] = []
    for anchor, url_lists in grouped.items():
        for i in range(max((len(u) for u in url_lists), default=0)):
            for urls in url_lists:
                if i < len(urls):
                    candidates.append((anchor, urls[i]))
    if not candidates:
        return []
    # fetch all candidates concurrently — one slow or blocked image must not hold up the rest
    with ThreadPoolExecutor(max_workers=min(8, len(candidates))) as pool:
        fetched = list(pool.map(lambda c: (c[0], _fetch(c[1])), candidates))
    folder = Path(config.artifacts_dir) / job_id / "images"
    folder.mkdir(parents=True, exist_ok=True)
    saved: list[dict[str, str]] = []
    per_anchor: dict[str, int] = {}
    for anchor, got in fetched:                          # keep the round-robin order; cap per section + overall
        if len(saved) >= MAX_IMAGES:
            break
        if not got or per_anchor.get(anchor, 0) >= PER_ANCHOR:
            continue
        mime, blob = got
        stored = f"web_{len(saved) + 1:02d}{_ALLOWED[mime]}"
        (folder / stored).write_bytes(blob)
        saved.append({"filename": stored, "mime": mime, "relpath": f"{job_id}/images/{stored}", "anchor": anchor})
        per_anchor[anchor] = per_anchor.get(anchor, 0) + 1
    if saved:
        _attach_to_job(config, job_id, saved)
    return saved


def _attach_to_job(config: Config, job_id: str, saved: list[dict[str, str]]) -> None:
    """Add the saved images to the job's payload so /api/jobs/{id}/attachments/{file} can serve them."""
    from src.core.db import session_scope
    from src.jobs.jobs import get_job

    try:
        with session_scope(config) as session:
            job = get_job(session, job_id)
            if job is None:
                return
            payload = json.loads(job.payload_json or "{}")
            existing = payload.get("attachments") if isinstance(payload.get("attachments"), list) else []
            payload["attachments"] = existing + [
                {"filename": s["filename"], "mime": s["mime"], "relpath": s["relpath"]} for s in saved
            ]
            job.payload_json = json.dumps(payload)
    except Exception as exc:  # noqa: BLE001 — serving the images is best-effort
        logger.info("attach web images failed: %s", exc)
