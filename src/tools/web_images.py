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
from typing import NamedTuple
from urllib.parse import urlsplit

import requests

from src.core.config import Config

logger = logging.getLogger(__name__)

_ALLOWED = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}
_MAX_BYTES = 5 * 1024 * 1024
_SEARCH_TIMEOUT = 10
_FETCH_TIMEOUT = 8
# context-aware gathering: one image search per named thing, so a React diagram lands under "React"
# and Istanbul photos under "Istanbul" — not one bulk search for the whole answer.
MAX_TOPICS = 8                   # at most this many searches (keeps it fast)
PER_ANCHOR = 4                   # images kept under a heading that names one thing
PER_ITEM = 1                     # images kept per named list item: one each keeps a list readable (O75)
MAX_IMAGES = 12                  # overall cap across the answer
MIN_TOPICS = 2                   # fewer named things than this → nothing to illustrate per topic
LEAD_KEEP = 1                    # pictures of a learning answer's one subject ("Who is Isaac Newton?")
CANDIDATES_PER_KEPT = 2          # search results fetched per picture kept: some fail to download or aren't images
_JOINING_WORD_LEN = 3            # "of" / "the" / "and" may stay lowercase inside a name
_HEADING_RE = re.compile(r"^\s{0,3}#{1,4}\s+(.+?)\s*#*\s*$", re.M)
_BOLD_ITEM_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])?\s*\*\*(.+?)\*\*", re.M)
_NAME_WORD_RE = re.compile(r"[A-Za-z0-9][\w.+#-]*")


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
    t = re.sub(r"\s*\([^)]*\)", "", t)                               # "(Capital City)" notes add noise
    t = re.split(r"\s[–—-]\s|:\s", t, maxsplit=1)[0]                  # keep the entity before a dash/colon
    return re.sub(r"[\s:–—-]+$", "", t).strip()[:60]                  # trim a trailing colon/dash


def _sections(answer: str) -> list[tuple[str, str]]:
    """[(heading, body)] split at Markdown headings; body is the text until the next heading. The intro
    before the first heading has heading "" (its bold items can still be anchors, O75)."""
    out: list[tuple[str, str]] = []
    heading, start, text = "", 0, answer or ""
    for m in _HEADING_RE.finditer(text):
        out.append((heading, text[start:m.start()]))
        heading, start = m.group(1).strip(), m.end()
    out.append((heading, text[start:]))
    return [(h, b) for h, b in out if h or b.strip()]


def _mentions(word: str, text: str) -> int:
    return len(re.findall(rf"(?<!\w){re.escape(word)}(?!\w)", text))


def _name_words(text: str, answer: str) -> list[str]:
    """The words of `text` the answer treats as names: capitalised, and never written in lowercase anywhere in
    the answer. "Jeju", "MongoDB", "MERN" qualify; "The", "Island", "Stack" don't — the answer also writes
    "the", "island", "stack" — so sentence-initial capitals and common nouns drop out without a word list."""
    out: list[str] = []
    for w in _NAME_WORD_RE.findall(text or ""):
        if not w.islower() and w not in out and not _mentions(w.lower(), answer):
            out.append(w)
    return out


def _names_its_section(term: str, body: str, others: list[str], answer: str) -> bool:
    """Is the heading ONE named thing, judged from the answer itself (no word list)? Its section talks about it
    — "Jeju" in "Jeju Olle Trail", "MongoDB is a NoSQL…" — while most other sections don't. A heading that
    groups things ("Top Cities", "The Foundation") fails: it has no name word, or its section never uses it."""
    words = _name_words(term, answer)
    return any(
        _mentions(w, body) and sum(1 for other in others if _mentions(w, other)) < len(others) / 2
        for w in words
    )


def _is_name(term: str, answer: str) -> bool:
    """Is a bold item's label a named thing (Istanbul, Hagia Sophia, Temple of Artemis), judged from the answer
    itself? Its first word is a name word (_name_words), and it has no lowercase word longer than a joining
    word ("of", "the") — so "Best time to visit" or "Why it matters" stay labels, not things to picture."""
    words = _NAME_WORD_RE.findall(term)
    return (bool(words) and words[0] in _name_words(words[0], answer)
            and not any(w.islower() and len(w) > _JOINING_WORD_LEN for w in words))



def _members(body: str, answer: str) -> list[tuple[str, str]]:
    """(anchor_key, search_term) for each bold item of `body` that names a thing (Seoul, Busan, Ephesus). The
    key is the bold label's, so the UI places the item's picture right after that list item."""
    out: list[tuple[str, str]] = []
    for bold in _BOLD_ITEM_RE.findall(body):
        term = _search_term(bold)
        if len(term) >= 3 and _is_name(term, answer):
            out.append((_norm_anchor(bold), term))
    return out


def _subject(query: str, answer: str) -> str:
    """The answer's subject in the answer's own spelling: the question's words that the answer writes as a
    name ("places to visit in korea" → "Korea", "mern stack roadmap" → "MERN Stack"). Gives a vague search
    term its context ("Top Cities" → "Top Cities Korea") without any list of topics."""
    found: list[str] = []
    for word in _NAME_WORD_RE.findall(query or ""):
        if len(word) < 3:
            continue
        for m in re.finditer(rf"(?<!\w){re.escape(word)}(?!\w)", answer or "", re.I):
            if m.group(0) in _name_words(m.group(0), answer) and m.group(0) not in found:
                found.append(m.group(0))
                break
    return " ".join(found)


def _with_subject(term: str, subject: str) -> str:
    extra = [w for w in subject.split() if w.lower() not in term.lower()]
    return " ".join([term, *extra]).strip()


class Topic(NamedTuple):
    anchor: str                  # _norm_anchor of the heading or bold label the pictures go under
    term: str                    # the image search
    keep: int                    # pictures kept for it


def _topics(answer: str, query: str = "") -> list[Topic]:
    """What to illustrate, each anchored where the UI places its pictures. Only named things get pictures
    (ISSUES_LOG O53): a heading that names one thing (Seoul, Jeju Island, MongoDB) is searched as written. A
    heading that doesn't ("The Math", "Who Controls the Ban?", "Top Cities") gets none itself; its bold items
    that name things (Seoul, Busan) get one picture each, searched with the answer's subject ("Busan Korea") —
    as do the bold items of an answer with no headings at all (Turkey: Istanbul, Cappadocia, Ephesus, O75).
    One search per term across the answer, in answer order, capped."""
    sections = _sections(answer)
    subject = _subject(query, answer)
    candidates: list[Topic] = []
    for i, (heading, body) in enumerate(sections):
        term = _search_term(heading)
        others = [b for j, (_h, b) in enumerate(sections) if j != i]
        if heading and len(term) >= 3 and _names_its_section(term, body, others, answer):
            candidates.append(Topic(_norm_anchor(heading), term, PER_ANCHOR))
        else:
            candidates += [Topic(key, _with_subject(item, subject), PER_ITEM) for key, item in _members(body, answer)]
    out: list[Topic] = []
    searched: set[str] = set()
    for topic in candidates:
        if topic.anchor and topic.term.lower() not in searched:
            searched.add(topic.term.lower())
            out.append(topic)
    return out[:MAX_TOPICS]


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


def _plan(answer: str, query: str = "", lead: str = "") -> list[Topic]:
    """What to search. Pictures suit an answer about several named things (headings or bold list items to anchor
    them under). An answer about ONE subject — prose with no such structure, "Who is Isaac Newton?" — gets
    LEAD_KEEP pictures of `lead` (the web search's query, given only when the user is learning a topic) placed
    with the answer's opening; its name in the answer's spelling when it has one ("Isaac Newton"). Nothing for
    an answer with commands or code: a how-to, where a server's PM2 reply got news thumbnails (ISSUES_LOG O71)."""
    if "```" in (answer or ""):
        return []
    topics = _topics(answer, query)
    if len(topics) >= MIN_TOPICS:
        return topics
    lead = lead.strip()
    return [Topic("", _subject(lead, answer) or lead, LEAD_KEEP)] if lead else []


def wants_images(answer: str, lead: str = "") -> bool:
    """Decide — from the ANSWER itself, not a fixed list of query keywords — whether images help (_plan). They
    don't help a greeting, a one-liner, a refusal, or a mostly-code reply, so the tool self-selects for travel,
    roadmaps, people, history… without a hand-kept list."""
    return bool(_plan(answer, lead=lead))


def _round_robin[T](lists: list[list[T]]) -> list[T]:
    """[[a1, a2], [b1], [c1, c2]] → [a1, b1, c1, a2, c2]: the first of each before the second of any."""
    return [lst[i] for i in range(max((len(lst) for lst in lists), default=0)) for lst in lists if i < len(lst)]


def image_anchors(answer: str, lead: str = "") -> list[str]:
    """The sections that will get images (in answer order), known before any search runs — the UI shows
    loading placeholders under exactly these headings ("" = the answer's opening). [] when the answer isn't
    worth illustrating."""
    return list(dict.fromkeys(topic.anchor for topic in _plan(answer, lead=lead)))


def gather_web_images(query: str, answer: str, job_id: str, config: Config, lead: str = "") -> list[dict[str, str]]:
    """Context-aware image gathering: if the answer is worth illustrating (wants_images), search once per
    named thing (_topics), fetch the candidates, and tag each saved image with the `anchor` of the heading or
    list item it belongs to so the UI can place it there. → saved metadata (or [] when images don't suit the
    answer). Whether to illustrate is decided from the answer alone; `query` only lends a list item its subject,
    and `lead` is the subject of a learning answer about one thing (_plan)."""
    plan = _plan(answer, query, lead)
    if not plan:
        return []
    keep = {topic.anchor: topic.keep for topic in plan}
    with ThreadPoolExecutor(max_workers=min(MAX_TOPICS, len(plan))) as pool:
        found = list(pool.map(
            lambda t: [(t.anchor, url) for url in search_images(t.term, config, t.keep * CANDIDATES_PER_KEPT)],
            plan))
    # round-robin across topics, so every topic gets its first photo before any gets a second — the overall
    # cap must not be used up by the first few
    candidates = _round_robin(found)
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
        if not got or per_anchor.get(anchor, 0) >= keep[anchor]:
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
            # kind="web": the answer's illustrations, not images the user attached to the question
            payload["attachments"] = existing + [
                {"filename": s["filename"], "mime": s["mime"], "relpath": s["relpath"], "kind": "web"}
                for s in saved
            ]
            job.payload_json = json.dumps(payload)
    except Exception as exc:  # noqa: BLE001 — serving the images is best-effort
        logger.info("attach web images failed: %s", exc)
