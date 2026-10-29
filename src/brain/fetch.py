"""Get a source's files onto disk and list the ones to ingest (doc/BRAIN_DESIGN.md §4 step 1).

Kinds:
- git_repo: shallow, sparse clone of only the folder we need, cached in data/brain_cache/<slug>;
            refreshed with fetch + reset (never builds or runs anything from the repo)
- upload:   a local folder (config.path)
- web_page: one or more pages (url + config.urls), optionally following links config.crawl_depth
            levels deep, same host + include pattern only
- sitemap:  every page listed in a sitemap (or sitemap index) that matches include
- feed:     every item of an RSS/Atom feed that matches include
Web pages are fetched lazily by SourceFile.load() with the stored ETag/Last-Modified, so an
unchanged page costs one HTTP 304 and no download.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from src.brain.web import (
    WebClient,
    WebError,
    WebResponse,
    crawl,
    feed_urls,
    matches,
    normalize_url,
    sitemap_urls,
)
from src.core.config import ROOT

CACHE_DIR = ROOT / "data" / "brain_cache"
SUPPORTED_SUFFIXES = (".md", ".markdown", ".txt", ".html", ".htm")
MAX_FILE_BYTES = 2_000_000
WEB_KINDS = {"web_page", "sitemap", "feed"}
DISCOVERY_LIMIT = 2000   # pages discovered (not ingested — max_pages limits that)
_GLOB_CHARS = set("*?[")


class FetchError(RuntimeError):
    """Source files could not be fetched or listed."""


@dataclass
class Loaded:
    name: str                       # decides the converter by extension: .md / .txt / .html
    text: str
    etag: str | None = None
    last_modified: str | None = None


# (stored etag, stored last_modified) -> Loaded, or None when the server says "not modified"
Loader = Callable[[str | None, str | None], "Loaded | None"]


@dataclass
class SourceFile:
    rel_path: str                   # path inside the source, or the page URL for web sources
    url: str                        # what answers cite
    path: Path | None = None        # local file (git_repo / upload)
    loader: Loader | None = None    # web page fetcher

    def load(self, etag: str | None = None, last_modified: str | None = None) -> Loaded | None:
        if self.loader is not None:
            return self.loader(etag, last_modified)
        if self.path is None:
            raise FetchError(f"nothing to load for {self.rel_path}")
        return Loaded(name=self.path.name, text=self.path.read_text(encoding="utf-8", errors="replace"))


@dataclass
class PreparedSource:
    files: list[SourceFile]
    revision: str | None   # git commit, when known


def _git(*args: str, cwd: Path | None = None, timeout: int = 900) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}  # never hang on a credential prompt
    try:
        result = subprocess.run(
            # Decode git's output as UTF-8 (not the Windows console's cp1252, which crashes the
            # subprocess reader thread on any non-cp1252 byte in a filename or contributor name).
            ["git", *args], cwd=cwd, env=env, capture_output=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise FetchError("git is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise FetchError(f"git {args[0]} timed out after {timeout}s") from exc
    if result.returncode != 0:
        raise FetchError(f"git {args[0]} failed: {(result.stderr or result.stdout).strip()[-500:]}")
    return result.stdout


def _static_prefix(include: str) -> str:
    """'A/b/**/*.md' -> 'A/b' (the part of the pattern without wildcards)."""
    parts: list[str] = []
    for part in include.split("/"):
        if _GLOB_CHARS & set(part):
            break
        parts.append(part)
    return "/".join(parts)


def checkout_git_repo(slug: str, url: str, include: str, branch: str | None = None) -> tuple[Path, str]:
    """Shallow sparse clone (or refresh) of just the folder `include` points at."""
    folder = _static_prefix(include)
    if not folder:
        raise FetchError(f"{slug}: 'include' must start with a folder, e.g. docs/**/*.md")
    dest = CACHE_DIR / slug
    if (dest / ".git").is_dir():
        _git("fetch", "--depth", "1", "--no-tags", "origin", branch or "HEAD", cwd=dest)
        _git("reset", "--hard", "FETCH_HEAD", cwd=dest)
    else:
        if dest.exists():
            shutil.rmtree(dest)  # leftover of an interrupted clone inside our own cache dir
        dest.parent.mkdir(parents=True, exist_ok=True)
        args = ["clone", "--depth", "1", "--filter=blob:none", "--sparse", "--no-tags"]
        if branch:
            args += ["--branch", branch]
        _git(*args, url, str(dest))
    _git("sparse-checkout", "set", folder, cwd=dest)
    return dest, _git("rev-parse", "HEAD", cwd=dest).strip()


def page_url_for(rel_path: str, source_url: str | None, config: dict[str, Any]) -> str:
    """Public URL for a file: the published page when a template is set, else the file itself."""
    template = config.get("page_url")
    if template:
        path = rel_path.rsplit(".", 1)[0]
        root = str(config.get("page_root") or "").strip("/")
        if root and path.startswith(root + "/"):
            path = path[len(root) + 1 :]
        if path == "index" or path.endswith("/index"):
            path = path[: -len("index")].rstrip("/")
        return template.format(path=path)
    if source_url and "github.com" in source_url:
        return f"{source_url.rstrip('/')}/blob/HEAD/{rel_path}"
    return f"file:{rel_path}"


def list_files(root: Path, include: str, source_url: str | None, config: dict[str, Any]) -> list[SourceFile]:
    root_resolved = root.resolve()
    files: list[SourceFile] = []
    for path in sorted(root.glob(include or "**/*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        resolved = path.resolve()
        if not resolved.is_relative_to(root_resolved):
            continue  # symlink pointing outside the source — never follow
        if resolved.stat().st_size > MAX_FILE_BYTES:
            continue
        rel = path.relative_to(root).as_posix()
        files.append(SourceFile(rel_path=rel, path=path, url=page_url_for(rel, source_url, config)))
    return files


def _web_name(resp: WebResponse) -> str:
    """A file name whose extension tells extract_file which converter to use."""
    if resp.content_type.startswith(("text/html", "application/xhtml")):
        ext = ".html"
    elif resp.content_type == "text/markdown" or resp.url.lower().endswith((".md", ".markdown")):
        ext = ".md"
    else:
        ext = ".txt"
    stem = urlsplit(resp.url).path.rstrip("/").rsplit("/", 1)[-1].rsplit(".", 1)[0] or "index"
    return stem + ext


def _web_loader(client: WebClient, url: str, prefetched: WebResponse | None) -> Loader:
    def load(etag: str | None, last_modified: str | None) -> Loaded | None:
        resp = prefetched
        if resp is None or resp.status != 200:
            try:
                resp = client.get(url, etag=etag, last_modified=last_modified)
            except WebError as exc:
                raise FetchError(str(exc)) from exc
        elif etag and resp.etag == etag:
            return None  # fetched during discovery and unchanged
        if resp.not_modified:
            return None
        return Loaded(name=_web_name(resp), text=resp.text or "", etag=resp.etag, last_modified=resp.last_modified)

    return load


def _web_files(source: dict[str, Any], client: WebClient | None = None) -> PreparedSource:
    client = client or WebClient()
    kind, base = source["kind"], source["url"]
    config = source.get("config") or {}
    include = source.get("include_pattern") or None
    fetched: dict[str, WebResponse] = {}
    try:
        if kind == "web_page":
            starts = list(dict.fromkeys(normalize_url(u) for u in [base, *(config.get("urls") or [])] if u))
            depth = int(config.get("crawl_depth") or 0)
            urls, fetched = crawl(client, starts, include=include, depth=depth, limit=DISCOVERY_LIMIT)
            if depth > 0 and starts[0] not in fetched:
                # never treat an outage as "all pages disappeared"
                raise FetchError(f"{source['slug']}: start page could not be loaded: {starts[0]}")
        elif kind == "sitemap":
            urls = [u for u in sitemap_urls(client, base) if matches(u, include, base) and client.allowed(u)]
        else:
            urls = [u for u in feed_urls(client, base) if matches(u, include, base) and client.allowed(u)]
    except WebError as exc:
        raise FetchError(f"{source['slug']}: {exc}") from exc
    files = [
        SourceFile(rel_path=url, url=url, loader=_web_loader(client, url, fetched.get(url)))
        for url in dict.fromkeys(normalize_url(u) for u in urls)
    ]
    return PreparedSource(files, None)


def prepare_source_files(source: dict[str, Any], *, web_client: WebClient | None = None) -> PreparedSource:
    """Fetch (if needed) and list every ingestible file of a source row."""
    kind = source["kind"]
    config = source.get("config") or {}
    include = source.get("include_pattern") or "**/*"
    if kind in WEB_KINDS:
        return _web_files(source, web_client)
    if kind == "git_repo":
        root, revision = checkout_git_repo(source["slug"], source["url"], include, config.get("branch"))
        return PreparedSource(list_files(root, include, source["url"], config), revision)
    if kind == "upload":
        root = Path(config.get("path") or "")
        if not root.is_absolute():
            root = ROOT / root
        if not root.is_dir():
            raise FetchError(f"{source['slug']}: folder not found: {root}")
        return PreparedSource(list_files(root, include, None, config), None)
    raise FetchError(f"{source['slug']}: kind '{kind}' is not supported")
