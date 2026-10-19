"""App data on Postgres: config guards and DDL translation (no database needed)."""

from __future__ import annotations

import dataclasses

import pytest

from src.app_db import AppDBError, _app_login
from src.config import Config
from src.db import _postgres_ddl


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
