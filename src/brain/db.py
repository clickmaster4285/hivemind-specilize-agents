"""Postgres connections for the knowledge brain (psycopg + pgvector).

Two logins:
- owner (BRAIN_DATABASE_URL): runs migrations; not subject to row-level security.
- brain_app (BRAIN_APP_PASSWORD): everything the app does; row-level security always applies.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from urllib.parse import quote, urlsplit, urlunsplit

import psycopg
from pgvector.psycopg import register_vector

from src.config import Config

APP_ROLE = "brain_app"
# Fail fast instead of hanging (e.g. "localhost" resolving to IPv6 ::1 while Docker listens on 127.0.0.1)
CONNECT_TIMEOUT_SECONDS = 10


class BrainDBError(RuntimeError):
    """Brain database is not configured or not reachable."""


def _libpq_url(url: str) -> str:
    # .env uses the SQLAlchemy form; psycopg wants plain postgresql://
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def owner_url(config: Config) -> str:
    if not config.brain_database_url:
        raise BrainDBError("BRAIN_DATABASE_URL is not set in .env")
    return _libpq_url(config.brain_database_url)


def app_url(config: Config) -> str:
    """Same host/port/database as the owner URL, but logged in as brain_app."""
    if not config.brain_app_password:
        raise BrainDBError("BRAIN_APP_PASSWORD is not set in .env")
    parts = urlsplit(owner_url(config))
    host = parts.hostname or "localhost"
    netloc = f"{APP_ROLE}:{quote(config.brain_app_password, safe='')}@{host}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit(parts._replace(netloc=netloc))


@contextmanager
def connect(
    config: Config | None = None,
    *,
    account_id: str | None = None,
    system: bool = False,
) -> Iterator[psycopg.Connection]:
    """One transaction as brain_app, with the row-level-security context set.

    account_id: whose private rows may be read/written (None = public rows only).
    system:     True only for the ingestion service, which writes public knowledge.
    Commits when the block finishes, rolls back if it raises.
    """
    config = config or Config.from_env()
    try:
        conn = psycopg.connect(app_url(config), connect_timeout=CONNECT_TIMEOUT_SECONDS)
    except psycopg.OperationalError as exc:
        raise BrainDBError(f"Cannot reach the brain database: {exc}") from exc
    with conn:
        register_vector(conn)
        with conn.transaction():
            # is_local=true: settings end with the transaction, never leak to another request
            conn.execute(
                "SELECT set_config('brain.account_id', %s, true), set_config('brain.system', %s, true)",
                (account_id or "", "on" if system else "off"),
            )
            yield conn
