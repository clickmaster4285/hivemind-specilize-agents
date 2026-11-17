"""The offline CVE list: which known vulnerabilities name a software version (doc/SECURITY_TOOLS_DECISION.md §10, V1).

Built from the NVD's public JSON 2.0 feeds (public domain) into one SQLite file (`CVE_DB`, default
data/cve/nvd.sqlite), so a security check never sends anything about the network out: the list is downloaded by an
admin command, and the check only reads the file.

    python -m src.servers.cve_db --update    # first time: every year's feed (~400 MB download); then only changes
    python -m src.servers.cve_db --status

Matching (`lookup`): nmap names software as a CPE (`cpe:/a:openbsd:openssh:8.9p1`); a CVE lists the products it
affects as CPE criteria with an exact version or a version range. A hit means "this version is in a range a CVE
names" — *possible*, not proven: an OS vendor may have patched the package without changing its version number.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sqlite3
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

FEED_URL = "https://nvd.nist.gov/feeds/json/cve/2.0/nvdcve-2.0-{name}.json.gz"
# the NVD's feeds start with this year's file (older CVEs are filed in it)
FIRST_FEED_YEAR = 2002
# the "modified" feed holds the CVEs changed in the last 8 days (NVD); an update older than that re-reads every year
MODIFIED_FEED_DAYS = 8
DOWNLOAD_TIMEOUT_SECONDS = 120
# a generic client name: nothing about this installation goes to the NVD
USER_AGENT = "MulteAgent-cve-update"
# how much of a CVE's English description is kept for the report
SUMMARY_CHARS = 300
# CPE "any version" and "not applicable"
ANY, NOT_APPLICABLE = "*", "-"
# the CVSS versions read for a score, best first
CVSS_METRICS = ("cvssMetricV31", "cvssMetricV30", "cvssMetricV40", "cvssMetricV2")
REJECTED = "Rejected"

SCHEMA = """
CREATE TABLE IF NOT EXISTS cves (id TEXT PRIMARY KEY, score REAL, severity TEXT, published TEXT, summary TEXT);
CREATE TABLE IF NOT EXISTS matches (cve_id TEXT, vendor TEXT, product TEXT, version TEXT,
    start_incl TEXT, start_excl TEXT, end_incl TEXT, end_excl TEXT);
CREATE INDEX IF NOT EXISTS matches_product ON matches (vendor, product);
CREATE INDEX IF NOT EXISTS matches_cve ON matches (cve_id);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

_CPE_SPLIT = re.compile(r"(?<!\\):")
_VERSION_PART = re.compile(r"\d+|[a-z]+")


@dataclass(frozen=True)
class CveHit:
    id: str
    score: float | None
    severity: str
    summary: str


# ── versions ──────────────────────────────────────────────────────────────
def version_key(version: str) -> tuple:
    """"8.9p1" → ((1, 8), (1, 9), (0, 'p'), (1, 1)): numbers compare as numbers, letters as text."""
    return tuple((1, int(p)) if p.isdigit() else (0, p) for p in _VERSION_PART.findall(version.lower()))


def in_range(version: str, row: sqlite3.Row | dict[str, Any]) -> bool:
    """Whether `version` is the exact version a match row names, or inside its range."""
    key = version_key(version)
    if row["version"]:
        return key == version_key(row["version"])
    bounds = ((row["start_incl"], lambda k, b: k >= b), (row["start_excl"], lambda k, b: k > b),
              (row["end_incl"], lambda k, b: k <= b), (row["end_excl"], lambda k, b: k < b))
    return all(test(key, version_key(bound)) for bound, test in bounds if bound)


# ── CPE names ─────────────────────────────────────────────────────────────
def parse_cpe(cpe: str) -> tuple[str, str, str, str] | None:
    """(part, vendor, product, version) from a CPE 2.2 (`cpe:/a:v:p:1.2`) or 2.3 (`cpe:2.3:a:v:p:1.2:upd:…`) name.
    A 2.3 name's update field joins the version when it is set ("8.9" + "p1" → "8.9p1"), as nmap writes it."""
    if cpe.startswith("cpe:2.3:"):
        f = _CPE_SPLIT.split(cpe)
        if len(f) < 7:
            return None
        version, update = f[5], f[6]
        if version not in (ANY, NOT_APPLICABLE) and update not in (ANY, NOT_APPLICABLE):
            version += update
        return f[2], f[3].replace("\\", ""), f[4].replace("\\", ""), version
    if cpe.startswith("cpe:/"):
        f = cpe[len("cpe:/"):].split(":")
        if len(f) < 3:
            return None
        return f[0], f[1], f[2], f[3] if len(f) > 3 else ""
    return None


# ── building the list ─────────────────────────────────────────────────────
def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _score(metrics: dict[str, Any]) -> tuple[float | None, str]:
    for name in CVSS_METRICS:
        for metric in metrics.get(name) or []:
            data = metric.get("cvssData") or {}
            if "baseScore" in data:
                return float(data["baseScore"]), str(data.get("baseSeverity") or metric.get("baseSeverity") or "")
    return None, ""


def _match_rows(cve: dict[str, Any]) -> Iterable[tuple]:
    for config in cve.get("configurations") or []:
        for node in config.get("nodes") or []:
            for m in node.get("cpeMatch") or []:
                parsed = parse_cpe(str(m.get("criteria") or ""))
                if not m.get("vulnerable") or parsed is None or parsed[0] != "a":
                    continue
                _part, vendor, product, version = parsed
                if version == NOT_APPLICABLE:
                    continue
                yield (cve["id"], vendor, product, None if version == ANY else version,
                       m.get("versionStartIncluding"), m.get("versionStartExcluding"),
                       m.get("versionEndIncluding"), m.get("versionEndExcluding"))


def import_feed(conn: sqlite3.Connection, feed: dict[str, Any]) -> int:
    """Insert or replace every CVE of one feed (a rejected CVE is removed). Returns the number read."""
    count = 0
    for item in feed.get("vulnerabilities") or []:
        cve = item.get("cve") or {}
        cve_id = cve.get("id")
        if not cve_id:
            continue
        count += 1
        conn.execute("DELETE FROM matches WHERE cve_id = ?", (cve_id,))
        if cve.get("vulnStatus") == REJECTED:
            conn.execute("DELETE FROM cves WHERE id = ?", (cve_id,))
            continue
        score, severity = _score(cve.get("metrics") or {})
        summary = next((d.get("value", "") for d in cve.get("descriptions") or [] if d.get("lang") == "en"), "")
        conn.execute("INSERT OR REPLACE INTO cves VALUES (?, ?, ?, ?, ?)",
                     (cve_id, score, severity, str(cve.get("published") or "")[:10], summary[:SUMMARY_CHARS]))
        conn.executemany("INSERT INTO matches VALUES (?, ?, ?, ?, ?, ?, ?, ?)", list(_match_rows(cve)))
    conn.commit()
    return count


def download(name: str) -> dict[str, Any]:
    request = urllib.request.Request(FEED_URL.format(name=name), headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:   # noqa: S310 — fixed https URL
        return json.loads(gzip.decompress(response.read()))


def _meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def update(path: Path, *, fetch: Callable[[str], dict[str, Any]] = download,
           now: datetime | None = None, say: Callable[[str], None] = print) -> None:
    """Bring the list up to date: every year's feed the first time (or when the last update is older than the
    modified feed reaches back), then the modified feed."""
    now = now or datetime.now(timezone.utc)
    conn = connect(path)
    try:
        last = _meta(conn, "updated_at")
        fresh = last and now - datetime.fromisoformat(last) < timedelta(days=MODIFIED_FEED_DAYS)
        names = ["modified"] if fresh else [*map(str, range(FIRST_FEED_YEAR, now.year + 1)), "modified"]
        for name in names:
            say(f"CVE list: reading the {name} feed…")
            say(f"  {import_feed(conn, fetch(name))} CVEs")
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('updated_at', ?)", (now.isoformat(),))
        conn.commit()
    finally:
        conn.close()


# ── reading it ────────────────────────────────────────────────────────────
def status(path: Path) -> dict[str, Any] | None:
    """When the list was updated and how many CVEs it holds; None when it was never built."""
    if not path.is_file():
        return None
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        updated = _meta(conn, "updated_at")
        count = conn.execute("SELECT COUNT(*) FROM cves").fetchone()[0]
    return {"updated_at": updated, "cves": count} if updated else None


def lookup(path: Path, cpe: str) -> list[CveHit]:
    """The CVEs whose criteria name this software version, worst first; [] without a version or a list."""
    parsed = parse_cpe(cpe)
    if parsed is None or not parsed[3] or not path.is_file():
        return []
    _part, vendor, product, version = parsed
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT m.*, c.score, c.severity, c.summary FROM matches m JOIN cves c ON c.id = m.cve_id "
            "WHERE m.vendor = ? AND m.product = ?", (vendor, product)).fetchall()
    hits = {r["cve_id"]: CveHit(r["cve_id"], r["score"], r["severity"] or "", r["summary"] or "")
            for r in rows if in_range(version, r)}
    return sorted(hits.values(), key=lambda h: (-(h.score or 0), h.id))


def main() -> None:
    from src.core.config import Config

    parser = argparse.ArgumentParser(description="The offline CVE list for security_check (NVD JSON 2.0 feeds).")
    parser.add_argument("--update", action="store_true", help="download the feeds into the list")
    parser.add_argument("--status", action="store_true", help="show when the list was updated")
    args = parser.parse_args()
    path = Config.from_env().cve_db_path
    if args.update:
        update(path)
    info = status(path)
    print(f"{path}: " + (f"{info['cves']} CVEs, updated {info['updated_at']}" if info else "not built yet"))


if __name__ == "__main__":
    main()
