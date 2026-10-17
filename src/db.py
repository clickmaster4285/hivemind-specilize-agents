"""SQLAlchemy engine / session helpers (SQLite default, Postgres via DATABASE_URL)."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from src.config import Config, ROOT


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal = None


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

    _engine = create_engine(url, future=True, pool_pre_ping=True, connect_args=connect_args)

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


def _postgres_ddl(ddl: str) -> str:
    """The column DDL below is written for SQLite; Postgres has no DATETIME and wants true/false."""
    import re

    ddl = re.sub(r"\bDATETIME\b", "TIMESTAMP WITH TIME ZONE", ddl, flags=re.I)
    return re.sub(r"\b(BOOLEAN\s+DEFAULT\s+)([01])\b", lambda m: m.group(1) + ("true" if m.group(2) == "1" else "false"),
                  ddl, flags=re.I)


def init_db(config: Config | None = None) -> None:
    from src import models_db  # noqa: F401

    engine = get_engine(config)
    Base.metadata.create_all(bind=engine)
    _ensure_column(engine, "jobs", "deleted_at", "deleted_at DATETIME")
    _ensure_column(engine, "model_registry", "vision", "vision BOOLEAN DEFAULT 0")
    _ensure_column(engine, "model_registry", "image_gen", "image_gen BOOLEAN DEFAULT 0")
    _ensure_column(engine, "model_registry", "available", "available BOOLEAN DEFAULT 1")


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
