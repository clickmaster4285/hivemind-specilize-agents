"""Get a source's files onto disk and list the ones to ingest (doc/BRAIN_DESIGN.md §4 step 1).

Phase 1 kinds:
- git_repo: shallow, sparse clone of only the folder we need, cached in data/brain_cache/<slug>;
            refreshed with fetch + reset (never builds or runs anything from the repo)
- upload:   a local folder (config.path)
Web pages / sitemaps / feeds arrive in B5.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.config import ROOT

CACHE_DIR = ROOT / "data" / "brain_cache"
SUPPORTED_SUFFIXES = (".md", ".markdown", ".txt")
MAX_FILE_BYTES = 2_000_000
_GLOB_CHARS = set("*?[")


class FetchError(RuntimeError):
    """Source files could not be fetched or listed."""


@dataclass
class SourceFile:
    rel_path: str          # posix path inside the source, e.g. WindowsServerDocs/networking/dns/zone-types.md
    path: Path             # file on disk
    url: str               # what answers cite


@dataclass
class PreparedSource:
    files: list[SourceFile]
    revision: str | None   # git commit, when known


def _git(*args: str, cwd: Path | None = None, timeout: int = 900) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}  # never hang on a credential prompt
    try:
        result = subprocess.run(
            ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout
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


def prepare_source_files(source: dict[str, Any]) -> PreparedSource:
    """Fetch (if needed) and list every ingestible file of a source row."""
    kind = source["kind"]
    config = source.get("config") or {}
    include = source.get("include_pattern") or "**/*"
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
    raise FetchError(f"{source['slug']}: kind '{kind}' is not supported yet (comes with web sources, B5)")
