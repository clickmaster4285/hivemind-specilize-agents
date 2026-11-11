"""Shared test setup — no test ever touches the real data or the real DEVICE_KEY:

- device logins (src/servers/server_vault.py) go to a fresh in-memory database per test, encrypted with a
  throwaway key;
- the knowledge-brain tests run on their own Postgres database (`<live name>_test`, created here when missing),
  not on the one the API uses: no test rows in live tables, and the API's refresh lock (advisory locks are
  per database) can never make the suite wait (O33).
"""

from __future__ import annotations

import base64
import os
from contextlib import contextmanager
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from psycopg import sql
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.core.db import Base
from src.servers import server_vault

TEST_DB_SUFFIX = "_test"
# What deploy/postgres-init/01-extensions.sql gives the live database on its first start
TEST_DB_EXTENSIONS = ("vector", "pg_trgm")


def _test_database_url(url: str) -> str:
    parts = urlsplit(url)
    name = parts.path.lstrip("/")
    return url if name.endswith(TEST_DB_SUFFIX) else urlunsplit(parts._replace(path=f"/{name}{TEST_DB_SUFFIX}"))


def _create_test_database(live_url: str, url: str) -> None:
    """Create the test database once (as the owner, connected to the live one). No database server: the
    brain tests skip themselves, as before."""
    from src.brain.db import CONNECT_TIMEOUT_SECONDS, _libpq_url

    name = urlsplit(url).path.lstrip("/")
    try:
        with psycopg.connect(_libpq_url(live_url), autocommit=True, connect_timeout=CONNECT_TIMEOUT_SECONDS) as conn:
            if conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone() is None:
                conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        with psycopg.connect(_libpq_url(url), autocommit=True, connect_timeout=CONNECT_TIMEOUT_SECONDS) as conn:
            for ext in TEST_DB_EXTENSIONS:
                conn.execute(sql.SQL("CREATE EXTENSION IF NOT EXISTS {}").format(sql.Identifier(ext)))
    except psycopg.OperationalError:
        pass


def pytest_sessionstart(session):
    # before any test module builds a Config: Config.from_env() reads BRAIN_DATABASE_URL at call time
    url = os.environ.get("BRAIN_DATABASE_URL")
    if url:
        os.environ["BRAIN_DATABASE_URL"] = _test_database_url(url)
        _create_test_database(url, os.environ["BRAIN_DATABASE_URL"])


@pytest.fixture(autouse=True)
def device_store(monkeypatch):
    monkeypatch.setenv("DEVICE_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)

    @contextmanager
    def scope():
        session = Session(bind=engine, expire_on_commit=False)
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    monkeypatch.setattr(server_vault, "_scope", scope)
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def request_counts():
    """Each test starts with no API calls counted (L16 S2 rate limits live in memory for the whole run)."""
    from web import limits

    limits.reset()
    yield
    limits.reset()


TEST_BCRYPT_ROUNDS = 4                    # bcrypt's minimum cost


@pytest.fixture
def cheap_hashing(monkeypatch):
    """bcrypt at its lowest cost, for tests that sign in or create accounts many times."""
    from passlib.context import CryptContext

    from src.core import auth

    monkeypatch.setattr(auth, "pwd_context", CryptContext(schemes=["bcrypt"], bcrypt__rounds=TEST_BCRYPT_ROUNDS))
    auth._no_account_hash.cache_clear()
    yield
    auth._no_account_hash.cache_clear()
