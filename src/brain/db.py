"""Postgres connections for the knowledge brain (psycopg + pgvector).

Two logins:
- owner (BRAIN_DATABASE_URL): runs migrations; not subject to row-level security.
- brain_app (BRAIN_APP_PASSWORD): everything the app does; row-level security always applies.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import time
from urllib.parse import quote, urlsplit, urlunsplit

import psycopg
from pgvector.psycopg import register_vector

from src.core.config import Config

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


TEAM_CACHE_SECONDS = 60          # an account's team, looked up per search: a changed team counts within a minute
_teams: dict[str, tuple[float, str]] = {}


def team_of(account_id: str | None) -> str:
    """The team of a signed-in account (an app user id), for team knowledge (BACKLOG L30 T4); "" for none."""
    if not account_id or not account_id.isdigit():
        return ""
    now = time.monotonic()
    cached = _teams.get(account_id)
    if cached and now - cached[0] < TEAM_CACHE_SECONDS:
        return cached[1]
    try:
        from sqlalchemy import select

        from src.core.db import session_scope
        from src.core.models_db import User

        with session_scope() as session:
            team = session.scalar(select(User.team).where(User.id == int(account_id))) or ""
    except Exception:  # noqa: BLE001 — no app database (a script, a test): public and private rows only
        team = ""
    _teams[account_id] = (now, team)
    return team


@contextmanager
def connect(
    config: Config | None = None,
    *,
    account_id: str | None = None,
    system: bool = False,
    team: str | None = None,
) -> Iterator[psycopg.Connection]:
    """One transaction as brain_app, with the row-level-security context set.

    account_id: whose private rows may be read/written (None = public rows only); their team's rows are readable
                too (looked up, team_of).
    system:     True only for the ingestion service and the API's uploads, which write public and team knowledge.
    team:       the team whose rows this transaction may read (the upload of a team document); default: the
                account's own team.
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
                "SELECT set_config('brain.account_id', %s, true), set_config('brain.system', %s, true), "
                "set_config('brain.team', %s, true)",
                (account_id or "", "on" if system else "off", team if team is not None else team_of(account_id)),
            )
            yield conn
