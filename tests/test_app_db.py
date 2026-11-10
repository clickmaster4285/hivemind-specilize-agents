"""App data on Postgres: config guards and DDL translation (no database needed)."""

from __future__ import annotations

import dataclasses

import pytest

from src.core.app_db import AppDBError, _app_login
from src.core.config import Config
from src.core.db import _postgres_ddl


@pytest.mark.parametrize("ddl, expected", [
    ("deleted_at DATETIME", "deleted_at TIMESTAMP WITH TIME ZONE"),
    ("vision BOOLEAN DEFAULT 0", "vision BOOLEAN DEFAULT false"),
    ("available BOOLEAN DEFAULT 1", "available BOOLEAN DEFAULT true"),
    ("note TEXT", "note TEXT"),
])
def test_sqlite_column_ddl_becomes_postgres(ddl, expected):
    assert _postgres_ddl(ddl) == expected


def _cfg(url: str) -> Config:
    return dataclasses.replace(Config.from_env(), database_url=url)


def test_app_login_comes_from_database_url():
    assert _app_login(_cfg("postgresql+psycopg://multeagent_app:s3cr%40t@127.0.0.1:5432/multeagent")) == (
        "multeagent_app", "s3cr@t")


@pytest.mark.parametrize("url, message", [
    ("sqlite:///data/multeagent.db", "still SQLite"),
    ("postgresql+psycopg://127.0.0.1:5432/multeagent", "needs a user and password"),
])
def test_bad_database_url_is_explained(url, message):
    with pytest.raises(AppDBError, match=message):
        _app_login(_cfg(url))


class _Catalog:
    """Stands in for the owner connection: answers the role / schema lookups from a set of names."""

    def __init__(self, existing: set[str]):
        self.existing = existing

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params):
        found = params[0] in self.existing
        return type("Row", (), {"fetchone": lambda _self: (1,) if found else None})()


APP_URL = "postgresql+psycopg://multeagent_app:pw@127.0.0.1:5432/multeagent"
ALL_OBJECTS = {"brain_app", "multeagent_app", "app"}


@pytest.fixture
def prepare_with(monkeypatch):
    """prepare() against a fake catalog and migration state; returns (done, calls)."""
    from src.brain import migrate as brain_migrate
    from src.core import app_db

    def run(existing: set[str], migration_states: list[str]):
        calls = []
        monkeypatch.setattr(app_db, "_owner_url", lambda _cfg: "postgresql://owner@127.0.0.1/multeagent")
        monkeypatch.setattr(app_db.psycopg, "connect", lambda *a, **k: _Catalog(existing))
        monkeypatch.setattr(brain_migrate, "status",
                            lambda _cfg: [(f"{i:03}.sql", s) for i, s in enumerate(migration_states, 1)])
        monkeypatch.setattr(brain_migrate, "migrate",
                            lambda _cfg: calls.append("migrate") or [n for n, s in brain_migrate.status(_cfg)
                                                                     if s == "pending"])
        monkeypatch.setattr(app_db, "setup", lambda _cfg: calls.append("setup"))
        return app_db.prepare(_cfg(APP_URL)), calls

    return run


def test_prepare_sets_up_a_fresh_database(prepare_with):
    done, calls = prepare_with(set(), ["pending", "pending"])
    assert calls == ["migrate", "setup"]
    assert done == ["brain migration applied: 001.sql", "brain migration applied: 002.sql",
                    "login brain_app created", "login multeagent_app and schema 'app' created"]


def test_prepare_leaves_a_ready_database_alone(prepare_with):
    assert prepare_with(ALL_OBJECTS, ["applied", "applied"]) == ([], [])


def test_prepare_recreates_logins_after_a_restore(prepare_with):
    # a pg_dump brings tables, the app schema and the migration record, but no logins
    done, calls = prepare_with({"app"}, ["applied"])
    assert calls == ["migrate", "setup"]
    assert done == ["login brain_app created", "login multeagent_app and schema 'app' created"]


def test_prepare_applies_only_new_migrations(prepare_with):
    done, calls = prepare_with(ALL_OBJECTS, ["applied", "pending"])
    assert (done, calls) == (["brain migration applied: 002.sql"], ["migrate"])


class _RecordingOwnerConn(_Catalog):
    """Owner connection for setup(): the app login exists, two restored tables belong to someone else."""

    def __init__(self, foreign_tables: list[str]):
        super().__init__({"multeagent_app"})
        self.foreign_tables = foreign_tables
        self.statements: list[str] = []

    def execute(self, query, params=None):
        self.statements.append(query if isinstance(query, str) else query.as_string())
        if isinstance(query, str) and "FROM pg_tables" in query:
            return type("Rows", (), {"fetchall": lambda _self: [(t,) for t in self.foreign_tables]})()
        if isinstance(query, str) and "current_database" in query:
            return type("Row", (), {"fetchone": lambda _self: ("multeagent",)})()
        if params:  # a role / schema lookup
            return super().execute(query, params)
        return None  # DDL: only recorded


def test_setup_hands_restored_tables_back_to_the_app_login(monkeypatch):
    from src.core import app_db

    conn = _RecordingOwnerConn(["users", "jobs"])
    monkeypatch.setattr(app_db, "_owner_url", lambda _cfg: "postgresql://multeagent@127.0.0.1/multeagent")
    monkeypatch.setattr(app_db.psycopg, "connect", lambda *a, **k: conn)
    app_db.setup(_cfg(APP_URL))
    assert [s for s in conn.statements if s.startswith("ALTER TABLE")] == [
        'ALTER TABLE "app"."users" OWNER TO "multeagent_app"',
        'ALTER TABLE "app"."jobs" OWNER TO "multeagent_app"',
    ]
