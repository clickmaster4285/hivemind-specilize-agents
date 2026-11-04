"""Shared test setup: device logins (src/servers/server_vault.py) go to a fresh in-memory database per test,
encrypted with a throwaway key — no test ever touches the real database or the real DEVICE_KEY."""

from __future__ import annotations

import base64
import os
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.core.db import Base
from src.servers import server_vault


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
