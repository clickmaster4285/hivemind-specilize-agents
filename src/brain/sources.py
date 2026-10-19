"""Approved-source registry: data/brain/sources.yaml -> brain.sources (doc/BRAIN_DESIGN.md §4.1).

The YAML file (tracked in Git) is the single list of public sources the brain may learn from.
Sync rules: new entry -> insert · changed entry -> update · entry removed from the file ->
source disabled (never deleted; its documents stay for audit). Private sources are not managed here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

import yaml
from psycopg.types.json import Jsonb

from src.brain.db import connect
from src.config import ROOT, Config

REGISTRY_PATH = ROOT / "data" / "brain" / "sources.yaml"
KINDS = {"web_page", "sitemap", "feed", "git_repo", "api", "upload"}
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
_INTERVAL = re.compile(r"^\s*(\d+)\s*([dhm])\s*$", re.IGNORECASE)
# Registry keys stored in sources.config instead of their own column
CONFIG_KEYS = ("page_url", "page_root", "branch", "path", "language", "urls", "crawl_depth")
MAX_CRAWL_DEPTH = 3


class RegistryError(ValueError):
    """sources.yaml is invalid."""


@dataclass
class SourceSpec:
    slug: str
    title: str
    kind: str
    collection: str
    url: str | None = None
    include: str | None = None
    publisher: str | None = None
    licence: str | None = None
    reason: str | None = None
    added_by: str | None = None
    refresh_every: timedelta = timedelta(days=7)
    max_pages: int | None = None
    enabled: bool = False
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class SyncReport:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    disabled: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)


def parse_interval(value: Any) -> timedelta:
    """'7d' / '12h' / '30m' (or a plain number of days)."""
    if isinstance(value, int):
        return timedelta(days=value)
    match = _INTERVAL.match(str(value or ""))
    if not match:
        raise RegistryError(f"refresh_every must look like 7d, 12h or 30m (got {value!r})")
    amount, unit = int(match.group(1)), match.group(2).lower()
    return {"d": timedelta(days=amount), "h": timedelta(hours=amount), "m": timedelta(minutes=amount)}[unit]


def _spec(entry: dict[str, Any], position: int) -> SourceSpec:
    where = f"sources[{position}] ({entry.get('slug', '?')})"
    for key in ("slug", "title", "kind", "collection"):
        if not entry.get(key):
            raise RegistryError(f"{where}: '{key}' is required")
    slug = str(entry["slug"])
    if not _SLUG.match(slug):
        raise RegistryError(f"{where}: slug must be lowercase letters, digits and '-'")
    kind = str(entry["kind"])
    if kind not in KINDS:
        raise RegistryError(f"{where}: kind must be one of {sorted(KINDS)}")
    enabled = bool(entry.get("enabled", False))
    licence = entry.get("licence")
    if enabled and not licence:
        raise RegistryError(f"{where}: an enabled public source needs a verified 'licence'")
    if kind in {"git_repo", "web_page", "sitemap", "feed", "api"} and not entry.get("url"):
        raise RegistryError(f"{where}: kind '{kind}' needs a 'url'")
    if kind == "upload" and not entry.get("path"):
        raise RegistryError(f"{where}: kind 'upload' needs a local 'path'")
    max_pages = entry.get("max_pages")
    if max_pages is not None and (not isinstance(max_pages, int) or max_pages < 1):
        raise RegistryError(f"{where}: max_pages must be a positive whole number")
    depth = entry.get("crawl_depth", 0)
    if not isinstance(depth, int) or not 0 <= depth <= MAX_CRAWL_DEPTH:
        raise RegistryError(f"{where}: crawl_depth must be a whole number from 0 to {MAX_CRAWL_DEPTH}")
    urls = entry.get("urls") or []
    if not isinstance(urls, list) or not all(isinstance(u, str) and u.startswith(("http://", "https://")) for u in urls):
        raise RegistryError(f"{where}: urls must be a list of http(s) addresses")
    if kind in {"web_page", "sitemap", "feed"} and not str(entry.get("url", "")).startswith(("http://", "https://")):
        raise RegistryError(f"{where}: url must start with http:// or https://")
    return SourceSpec(
        slug=slug,
        title=str(entry["title"]),
        kind=kind,
        collection=str(entry["collection"]),
        url=entry.get("url"),
        include=entry.get("include"),
        publisher=entry.get("publisher"),
        licence=str(licence) if licence else None,
        reason=entry.get("reason"),
        added_by=entry.get("added_by"),
        refresh_every=parse_interval(entry.get("refresh_every", "7d")),
        max_pages=max_pages,
        enabled=enabled,
        config={k: entry[k] for k in CONFIG_KEYS if entry.get(k) is not None},
    )


def load_registry(path: Path | None = None) -> list[SourceSpec]:
    path = path or REGISTRY_PATH
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as exc:
        raise RegistryError(f"Source registry not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise RegistryError(f"{path.name} is not valid YAML: {exc}") from exc
    entries = data.get("sources") or []
    if not isinstance(entries, list):
        raise RegistryError("'sources' must be a list")
    specs = [_spec(entry or {}, i) for i, entry in enumerate(entries)]
    seen: set[str] = set()
    for spec in specs:
        if spec.slug in seen:
            raise RegistryError(f"duplicate slug: {spec.slug}")
        seen.add(spec.slug)
    return specs


_UPSERT = """
INSERT INTO brain.sources AS s
  (slug, kind, url, title, publisher, licence, reason, added_by, collection_id, scope,
   include_pattern, max_pages, refresh_every, enabled, config)
VALUES (%(slug)s, %(kind)s, %(url)s, %(title)s, %(publisher)s, %(licence)s, %(reason)s,
        %(added_by)s, %(collection_id)s, 'public', %(include)s, %(max_pages)s,
        %(refresh_every)s, %(enabled)s, %(config)s)
ON CONFLICT (slug) DO UPDATE SET
  kind = EXCLUDED.kind, url = EXCLUDED.url, title = EXCLUDED.title,
  publisher = EXCLUDED.publisher, licence = EXCLUDED.licence, reason = EXCLUDED.reason,
  added_by = EXCLUDED.added_by, collection_id = EXCLUDED.collection_id,
  include_pattern = EXCLUDED.include_pattern, max_pages = EXCLUDED.max_pages,
  refresh_every = EXCLUDED.refresh_every, enabled = EXCLUDED.enabled,
  config = EXCLUDED.config, updated_at = now()
WHERE (s.kind, s.url, s.title, s.publisher, s.licence, s.reason, s.added_by, s.collection_id,
       s.include_pattern, s.max_pages, s.refresh_every, s.enabled, s.config)
      IS DISTINCT FROM
      (EXCLUDED.kind, EXCLUDED.url, EXCLUDED.title, EXCLUDED.publisher, EXCLUDED.licence,
       EXCLUDED.reason, EXCLUDED.added_by, EXCLUDED.collection_id, EXCLUDED.include_pattern,
       EXCLUDED.max_pages, EXCLUDED.refresh_every, EXCLUDED.enabled, EXCLUDED.config)
RETURNING (xmax = 0) AS inserted
"""


def sync_sources(config: Config | None = None, specs: list[SourceSpec] | None = None) -> SyncReport:
    """Bring brain.sources in line with the registry file."""
    config = config or Config.from_env()
    specs = load_registry() if specs is None else specs
    report = SyncReport()
    with connect(config, system=True) as conn:
        collections = dict(conn.execute("SELECT slug, id FROM brain.collections").fetchall())
        for spec in specs:
            if spec.collection not in collections:
                raise RegistryError(
                    f"{spec.slug}: unknown collection '{spec.collection}' (known: {sorted(collections)})"
                )
            row = conn.execute(
                _UPSERT,
                {
                    "slug": spec.slug, "kind": spec.kind, "url": spec.url, "title": spec.title,
                    "publisher": spec.publisher, "licence": spec.licence, "reason": spec.reason,
                    "added_by": spec.added_by, "collection_id": collections[spec.collection],
                    "include": spec.include, "max_pages": spec.max_pages,
                    "refresh_every": spec.refresh_every, "enabled": spec.enabled,
                    "config": Jsonb(spec.config),
                },
            ).fetchone()
            if row is None:
                report.unchanged.append(spec.slug)
            elif row[0]:
                report.added.append(spec.slug)
            else:
                report.updated.append(spec.slug)

        listed = [spec.slug for spec in specs]
        gone = conn.execute(
            """UPDATE brain.sources SET enabled = false, updated_at = now()
               WHERE scope = 'public' AND enabled AND kind <> 'upload' AND NOT (slug = ANY(%s))
               RETURNING slug""",
            (listed,),
        ).fetchall()
        report.disabled = [slug for (slug,) in gone]
    return report
