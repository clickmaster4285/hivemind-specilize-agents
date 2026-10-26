"""Apply deploy/brain-migrations/*.sql in order and keep the brain_app login in sync.

Usage (from the repo root):
    python -m src.brain.migrate            apply pending migrations
    python -m src.brain.migrate --status   list applied / pending migrations

Rules: files run in name order, each in its own transaction, and are recorded in
brain.schema_migrations with a checksum. Never edit an applied file — add 002_….
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import psycopg
from psycopg import sql

from src.brain.db import APP_ROLE, CONNECT_TIMEOUT_SECONDS, BrainDBError, owner_url
from src.core.config import ROOT, Config

MIGRATIONS_DIR = ROOT / "deploy" / "brain-migrations"

_BOOTSTRAP = """
CREATE SCHEMA IF NOT EXISTS brain;
CREATE TABLE IF NOT EXISTS brain.schema_migrations (
  filename   text PRIMARY KEY,
  checksum   text NOT NULL,
  applied_at timestamptz NOT NULL DEFAULT now()
);
"""

# Tables the app may read but never change
_READ_ONLY_TABLES = {"collections", "schema_migrations"}


class MigrationError(RuntimeError):
    """A migration failed or an applied migration file was edited."""


def _migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _applied(conn: psycopg.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT filename, checksum FROM brain.schema_migrations").fetchall()
    return {name: checksum for name, checksum in rows}


def ensure_app_role(conn: psycopg.Connection, password: str) -> None:
    """Create/refresh the brain_app login: no superuser, no RLS bypass, minimal grants."""
    if not password:
        raise MigrationError("BRAIN_APP_PASSWORD is not set in .env")
    exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (APP_ROLE,)).fetchone()
    verb = sql.SQL("ALTER ROLE") if exists else sql.SQL("CREATE ROLE")
    conn.execute(
        sql.SQL(
            "{} {} WITH LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD {}"
        ).format(verb, sql.Identifier(APP_ROLE), sql.Literal(password))
    )

    db_name = conn.execute("SELECT current_database()").fetchone()[0]
    role = sql.Identifier(APP_ROLE)
    conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(db_name), role))
    conn.execute(sql.SQL("GRANT USAGE ON SCHEMA brain TO {}").format(role))
    conn.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA brain TO {}").format(role))
    conn.execute(sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA brain TO {}").format(role))

    tables = [
        name
        for (name,) in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'brain'"
        ).fetchall()
        if name not in _READ_ONLY_TABLES
    ]
    for name in tables:
        conn.execute(
            sql.SQL("GRANT INSERT, UPDATE, DELETE ON {} TO {}").format(
                sql.Identifier("brain", name), role
            )
        )


def migrate(config: Config | None = None) -> list[str]:
    """Apply pending migrations; return the names of the files applied in this run."""
    config = config or Config.from_env()
    applied_now: list[str] = []
    with psycopg.connect(
        owner_url(config), autocommit=True, connect_timeout=CONNECT_TIMEOUT_SECONDS
    ) as conn:
        conn.execute(_BOOTSTRAP)
        # Only one migrator at a time (e.g. API and worker starting together)
        conn.execute("SELECT pg_advisory_lock(hashtext('brain.migrate'))")
        try:
            done = _applied(conn)
            for path in _migration_files():
                checksum = _checksum(path)
                if path.name in done:
                    if done[path.name] != checksum:
                        raise MigrationError(
                            f"{path.name} was edited after it was applied. "
                            "Restore it and put the change in a new migration file."
                        )
                    continue
                try:
                    with conn.transaction():
                        conn.execute(path.read_text(encoding="utf-8"))
                        conn.execute(
                            "INSERT INTO brain.schema_migrations (filename, checksum) VALUES (%s, %s)",
                            (path.name, checksum),
                        )
                except psycopg.Error as exc:
                    raise MigrationError(f"{path.name} failed and was rolled back: {exc}") from exc
                applied_now.append(path.name)
            with conn.transaction():
                ensure_app_role(conn, config.brain_app_password)
        finally:
            conn.execute("SELECT pg_advisory_unlock(hashtext('brain.migrate'))")
    return applied_now


def status(config: Config | None = None) -> list[tuple[str, str]]:
    """[(filename, 'applied' | 'pending' | 'EDITED')] for every migration file."""
    config = config or Config.from_env()
    with psycopg.connect(
        owner_url(config), autocommit=True, connect_timeout=CONNECT_TIMEOUT_SECONDS
    ) as conn:
        conn.execute(_BOOTSTRAP)
        done = _applied(conn)
    out = []
    for path in _migration_files():
        if path.name not in done:
            out.append((path.name, "pending"))
        elif done[path.name] != _checksum(path):
            out.append((path.name, "EDITED"))
        else:
            out.append((path.name, "applied"))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--status", action="store_true", help="list applied / pending migrations")
    args = parser.parse_args(argv)
    try:
        if args.status:
            for name, state in status():
                print(f"{state:<8} {name}")
            return 0
        applied = migrate()
    except (MigrationError, BrainDBError, psycopg.OperationalError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print("Applied: " + ", ".join(applied) if applied else "Brain schema is up to date.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
