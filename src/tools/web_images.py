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
MAX_TOPICS = 5                   # at most this many section searches (keeps it fast)
PER_TOPIC = 3                    # images kept per section
MAX_IMAGES = 12                  # overall cap across the answer
_HEADING_RE = re.compile(r"^\s{0,3}#{1,4}\s+(.+?)\s*#*\s*$", re.M)


def _norm_anchor(text: str) -> str:
    """A stable key tying a heading to its images — the frontend computes the exact same key to place them.
    NFKD + strip markup + drop non-alphanumerics folds emoji, accents and punctuation away consistently."""
    t = unicodedata.normalize("NFKD", text or "")
    t = re.sub(r"[*_`#]", "", t).lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def _topics(answer: str) -> list[tuple[str, str]]:
    """(anchor_key, search_term) for the answer's main section headings — deduped, cleaned, capped.
    The search term drops leading emoji / "Phase N:" / numbering and keeps the core entity to search for."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for m in _HEADING_RE.finditer(answer or ""):
        raw = m.group(1).strip()
        key = _norm_anchor(raw)
        if not key or key in seen:
            continue
        seen.add(key)
        term = re.sub(r"^[\W\d_]+", "", raw)                               # leading emoji / numbering
        term = re.sub(r"^(phase|step|part)\s*\d*\s*[:.\-]?\s*", "", term, flags=re.I)
        term = re.sub(r"[*_`#]", "", term)
        term = re.split(r"\s[–—-]\s|:\s", term, maxsplit=1)[0].strip()     # keep the entity before a dash/colon
        if len(term) >= 3:
            out.append((key, term[:60]))
        if len(out) >= MAX_TOPICS:
            break
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


def gather_web_images(query: str, answer: str, job_id: str, config: Config) -> list[dict[str, str]]:
    """Context-aware image gathering: search once per section heading of `answer` (falling back to the
    whole query when it has no headings), fetch the candidates, and tag each saved image with the
    `anchor` of the section it belongs to so the UI can place it there. → saved metadata."""
    plan = _topics(answer) or [("", query)]              # (anchor_key, search_term) per section
    with ThreadPoolExecutor(max_workers=min(MAX_TOPICS, len(plan))) as pool:
        found = list(pool.map(lambda t: (t[0], search_images(t[1], config, PER_TOPIC * 2)), plan))
    candidates = [(anchor, url) for anchor, urls in found for url in urls]
    if not candidates:
        return []
    # fetch all candidates concurrently — one slow or blocked image must not hold up the rest
    with ThreadPoolExecutor(max_workers=min(8, len(candidates))) as pool:
        fetched = list(pool.map(lambda c: (c[0], _fetch(c[1])), candidates))
    folder = Path(config.artifacts_dir) / job_id / "images"
    folder.mkdir(parents=True, exist_ok=True)
    saved: list[dict[str, str]] = []
    per_anchor: dict[str, int] = {}
    for anchor, got in fetched:                          # keep topic order; cap per section and overall
        if len(saved) >= MAX_IMAGES:
            break
        if not got or per_anchor.get(anchor, 0) >= PER_TOPIC:
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
