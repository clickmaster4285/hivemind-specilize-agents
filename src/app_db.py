"""The app's own data (users, jobs, job events, model registry) in Postgres instead of SQLite.

Same Postgres database as the knowledge brain, but its own schema `app` and its own login
(the user in DATABASE_URL, e.g. multeagent_app): no superuser, no rights on the `brain` schema —
so a bug in the app can never read other accounts' private brain rows.

Usage (from the repo root, Docker database running):
    python -m src.app_db setup     create the login + schema `app` (uses the owner from BRAIN_DATABASE_URL)
    python -m src.app_db copy      copy data/multeagent.db (SQLite) into Postgres, keeping every id
    python -m src.app_db status    row counts in SQLite and Postgres

The SQLite file is only read, never changed: it stays as a backup.
"""

from __future__ import annotations

import argparse
import sys
from datetime import timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

import psycopg
from psycopg import sql
from sqlalchemy import create_engine, func, select, text

from src.config import ROOT, Config

APP_SCHEMA = "app"
SQLITE_FILE = ROOT / "data" / "multeagent.db"
BATCH = 500


class AppDBError(RuntimeError):
    """Postgres app database is not configured, not reachable, or not empty."""


def _app_login(config: Config) -> tuple[str, str]:
    """(role, password) from DATABASE_URL — the one place they are written down."""
    if not config.database_url.startswith("postgresql"):
        raise AppDBError("DATABASE_URL does not point to Postgres (it is still SQLite)")
    parts = urlsplit(config.database_url)
    if not parts.username or not parts.password:
        raise AppDBError("DATABASE_URL needs a user and password: postgresql+psycopg://USER:PASSWORD@host:5432/db")
    return unquote(parts.username), unquote(parts.password)


def _owner_url(config: Config) -> str:
    from src.brain.db import owner_url

    return owner_url(config)


def setup(config: Config | None = None) -> None:
    """Create/refresh the app login and its schema. Safe to run again."""
    config = config or Config.from_env()
    role_name, password = _app_login(config)
    from src.brain.db import APP_ROLE as BRAIN_ROLE

    if role_name in {BRAIN_ROLE, urlsplit(_owner_url(config)).username}:
        raise AppDBError(f"DATABASE_URL must use its own login, not {role_name!r}")
    role = sql.Identifier(role_name)
    with psycopg.connect(_owner_url(config), autocommit=True, connect_timeout=10) as conn:
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role_name,)).fetchone()
        verb = sql.SQL("ALTER ROLE") if exists else sql.SQL("CREATE ROLE")
        conn.execute(
            sql.SQL("{} {} WITH LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD {}").format(
                verb, role, sql.Literal(password)
            )
        )
        db_name = conn.execute("SELECT current_database()").fetchone()[0]
        conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(db_name), role))
        conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {} AUTHORIZATION {}").format(sql.Identifier(APP_SCHEMA), role))
        conn.execute(sql.SQL("ALTER SCHEMA {} OWNER TO {}").format(sql.Identifier(APP_SCHEMA), role))
        # unqualified table names (users, jobs, …) resolve to app.*
        conn.execute(sql.SQL("ALTER ROLE {} SET search_path = {}").format(role, sql.Identifier(APP_SCHEMA)))


def _engines(config: Config, sqlite_file: Path):
    if not sqlite_file.is_file():
        raise AppDBError(f"SQLite file not found: {sqlite_file}")
    src = create_engine(f"sqlite:///{sqlite_file.as_posix()}", future=True)
    dst = create_engine(config.database_url, future=True, pool_pre_ping=True)
    return src, dst


def counts(config: Config | None = None, sqlite_file: Path = SQLITE_FILE) -> dict[str, tuple[int, int | None]]:
    """{table: (rows in SQLite, rows in Postgres or None if the table does not exist yet)}."""
    from src import models_db  # noqa: F401 — registers the tables
    from src.db import Base

    config = config or Config.from_env()
    src, dst = _engines(config, sqlite_file)
    out = {}
    with src.connect() as s, dst.connect() as d:
        for table in Base.metadata.sorted_tables:
            n_src = s.execute(select(func.count()).select_from(table)).scalar_one()
            try:
                n_dst = d.execute(select(func.count()).select_from(table)).scalar_one()
            except Exception:  # noqa: BLE001 — table not created yet
                d.rollback()
                n_dst = None
            out[table.name] = (n_src, n_dst)
    return out


def copy(config: Config | None = None, sqlite_file: Path = SQLITE_FILE) -> dict[str, int]:
    """Copy every row, keeping ids; refuses when Postgres already has app data. → {table: rows}."""
    from src import models_db  # noqa: F401
    from src.db import Base

    config = config or Config.from_env()
    src, dst = _engines(config, sqlite_file)
    Base.metadata.create_all(bind=dst)
    copied: dict[str, int] = {}
    with src.connect() as s, dst.begin() as d:
        for table in Base.metadata.sorted_tables:
            if d.execute(select(func.count()).select_from(table)).scalar_one():
                raise AppDBError(f"Postgres table {APP_SCHEMA}.{table.name} already has rows — copy refused "
                                 "(it would mix two histories). Empty it first if you really want to copy again.")
        for table in Base.metadata.sorted_tables:              # parents first (users → jobs → job_events)
            rows = [dict(r) for r in s.execute(select(table)).mappings()]
            for row in rows:
                for key, value in row.items():
                    # SQLite gives back timestamps without a time zone; they were written as UTC
                    if hasattr(value, "tzinfo") and value.tzinfo is None and hasattr(value, "hour"):
                        row[key] = value.replace(tzinfo=timezone.utc)
            for i in range(0, len(rows), BATCH):
                d.execute(table.insert(), rows[i:i + BATCH])
            copied[table.name] = len(rows)
            # keep new ids after the copied ones
            pk = list(table.primary_key.columns)
            if len(pk) == 1 and pk[0].autoincrement is not False and pk[0].type.python_type is int:
                d.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('{APP_SCHEMA}.{table.name}', '{pk[0].name}'), "
                    f"COALESCE((SELECT MAX({pk[0].name}) FROM {APP_SCHEMA}.{table.name}), 1), "
                    f"(SELECT MAX({pk[0].name}) FROM {APP_SCHEMA}.{table.name}) IS NOT NULL)"
                ))
    return copied


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["setup", "copy", "status"])
    parser.add_argument("--sqlite", type=Path, default=SQLITE_FILE, help="SQLite file to copy from")
    args = parser.parse_args(argv)
    try:
        if args.command == "setup":
            setup()
            print(f"Login and schema '{APP_SCHEMA}' are ready.")
        elif args.command == "copy":
            for name, n in copy(sqlite_file=args.sqlite).items():
                print(f"copied {n:>6} rows  {name}")
        else:
            for name, (n_src, n_dst) in counts(sqlite_file=args.sqlite).items():
                print(f"{name:<16} SQLite {n_src:>6}   Postgres {'-' if n_dst is None else n_dst:>6}")
    except (AppDBError, psycopg.OperationalError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
