"""SQLAlchemy engine / session helpers (SQLite default, Postgres via DATABASE_URL)."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from src.core.config import Config, ROOT


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal = None


DB_POOL_SIZE = 20           # connections kept open
DB_POOL_OVERFLOW = 20       # extra ones for a busy moment
DB_POOL_TIMEOUT = 10        # seconds a request waits for a free connection

def get_engine(config: Config | None = None):
    global _engine, _SessionLocal
    if _engine is not None:
        return _engine

    config = config or Config.from_env()
    url = config.database_url
    connect_args = {}
    if url.startswith("sqlite"):
        (ROOT / "data").mkdir(parents=True, exist_ok=True)
        connect_args["check_same_thread"] = False

    elif url.startswith("postgresql"):
        # fail fast when Docker/Postgres is down instead of hanging (see doc/ISSUES_LOG.md #8)
        connect_args["connect_timeout"] = 10

    pool: dict = {}
    if url.startswith("postgresql"):
        # 10+ users: requests, chat streams' short reads and the worker share these (ISSUES_LOG O121). A request that
        # cannot get one fails after 10 s instead of hanging 30 s. Postgres allows 100 connections by default.
        pool = {"pool_size": DB_POOL_SIZE, "max_overflow": DB_POOL_OVERFLOW, "pool_timeout": DB_POOL_TIMEOUT}
    _engine = create_engine(url, future=True, pool_pre_ping=True, connect_args=connect_args, **pool)

    if url.startswith("sqlite"):

        @event.listens_for(_engine, "connect")
        def _sqlite_pragma(dbapi_connection, _connection_record):  # noqa: ANN001
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False, future=True)
    return _engine


def _ensure_column(engine, table: str, column: str, ddl: str) -> None:
    """Add a missing column on existing DBs (create_all does not alter)."""
    from sqlalchemy import text

    with engine.begin() as conn:
        dialect = engine.dialect.name
        if dialect == "sqlite":
            rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
            names = {row[1] for row in rows}
            if column not in names:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))
        else:
            # Postgres: look only in our own schema (the brain lives in the same database)
            exists = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema = current_schema() AND table_name = :table AND column_name = :column"
                ),
                {"table": table, "column": column},
            ).fetchone()
            if not exists:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {_postgres_ddl(ddl)}"))


def _ensure_nullable(engine, table: str, column: str) -> None:
    """Let an existing NOT NULL column hold NULL (create_all does not alter). SQLite tables are made fresh by
    create_all from the model, so only Postgres needs it; running it again changes nothing."""
    from sqlalchemy import text

    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE {table} ALTER COLUMN {column} DROP NOT NULL"))


def _postgres_ddl(ddl: str) -> str:
    """The column DDL below is written for SQLite; Postgres has no DATETIME and wants true/false."""
    import re

    ddl = re.sub(r"\bDATETIME\b", "TIMESTAMP WITH TIME ZONE", ddl, flags=re.I)
    return re.sub(r"\b(BOOLEAN\s+DEFAULT\s+)([01])\b", lambda m: m.group(1) + ("true" if m.group(2) == "1" else "false"),
                  ddl, flags=re.I)


def init_db(config: Config | None = None) -> None:
    from src.core import models_db

    engine = get_engine(config)
    Base.metadata.create_all(bind=engine)
    _ensure_column(engine, "jobs", "deleted_at", "deleted_at DATETIME")
    _ensure_column(engine, "users", "team", "team VARCHAR(40)")
    _ensure_column(engine, "users", "email", "email VARCHAR(255)")
    _ensure_column(engine, "users", "phone", "phone VARCHAR(40)")
    _ensure_column(engine, "users", "status", "status VARCHAR(20) DEFAULT 'active'")
    _ensure_column(engine, "users", "updated_at", "updated_at DATETIME")
    _ensure_column(engine, "users", "last_login_at", "last_login_at DATETIME")
    _ensure_column(engine, "users", "signup_address", f"signup_address VARCHAR({models_db.ADDRESS_CHARS})")
    _ensure_column(engine, "users", "invited_by", "invited_by INTEGER REFERENCES users(id)")
    from src.core.modules import DEFAULT_MODULES_JSON

    _ensure_column(engine, "users", "modules_json", f"modules_json TEXT DEFAULT '{DEFAULT_MODULES_JSON}'")
    _ensure_column(engine, "model_registry", "vision", "vision BOOLEAN DEFAULT 0")
    _ensure_column(engine, "model_registry", "image_gen", "image_gen BOOLEAN DEFAULT 0")
    _ensure_column(engine, "model_registry", "available", "available BOOLEAN DEFAULT 1")
    _ensure_column(engine, "chat_threads", "summary", "summary TEXT")
    _ensure_column(engine, "chat_threads", "summary_through", "summary_through DATETIME")
    _ensure_column(engine, "chat_threads", "continued_from", "continued_from VARCHAR(36)")
    _ensure_column(engine, "schedules", "last_status", "last_status VARCHAR(10) DEFAULT ''")
    _ensure_column(engine, "chat_threads", "unread", "unread BOOLEAN DEFAULT 0")
    _ensure_column(engine, "chat_threads", "work", "work TEXT")
    # the command log outlives a deleted account (ISSUES_LOG O123)
    _ensure_column(engine, "command_log", "username", "username VARCHAR(120) DEFAULT ''")
    _ensure_nullable(engine, "command_log", "user_id")


@contextmanager
def session_scope(config: Config | None = None) -> Generator[Session, None, None]:
    get_engine(config)
    assert _SessionLocal is not None
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Session:
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal()
