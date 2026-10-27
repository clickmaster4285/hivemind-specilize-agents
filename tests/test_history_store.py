"""Per-user chat-thread / project metadata store (history_store) — CRUD, isolation, import."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.jobs import history_store as hs
from src.core.db import Base
from src.core.models_db import ChatThreadRow  # noqa: F401 — ensures tables are registered on Base


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = Session(bind=engine)
    try:
        yield s
    finally:
        s.close()


def test_upsert_creates_then_updates_only_given_fields(session):
    hs.upsert_thread(session, 1, "t1", title="First chat")
    hs.upsert_thread(session, 1, "t1", pinned=True)  # title must survive
    (row,) = hs.list_history(session, 1)["threads"]
    assert row["id"] == "t1"
    assert row["title"] == "First chat"
    assert row["pinned"] is True
    assert row["project_id"] is None


def test_move_to_project_and_back(session):
    hs.upsert_thread(session, 1, "t1", title="c")
    hs.upsert_thread(session, 1, "t1", project_id="p1")
    assert hs.list_history(session, 1)["threads"][0]["project_id"] == "p1"
    hs.upsert_thread(session, 1, "t1", project_id=None)  # explicit null detaches
    assert hs.list_history(session, 1)["threads"][0]["project_id"] is None


def test_history_is_scoped_per_user(session):
    hs.upsert_thread(session, 1, "t1", title="mine")
    hs.upsert_thread(session, 2, "t2", title="theirs")
    assert [t["id"] for t in hs.list_history(session, 1)["threads"]] == ["t1"]
    assert [t["id"] for t in hs.list_history(session, 2)["threads"]] == ["t2"]


def test_cannot_touch_another_users_thread(session):
    hs.upsert_thread(session, 1, "t1", title="mine")
    with pytest.raises(PermissionError):
        hs.upsert_thread(session, 2, "t1", title="hijack")


def test_delete_thread_hides_it(session):
    hs.upsert_thread(session, 1, "t1", title="c")
    assert hs.delete_thread(session, 1, "t1") is True
    assert hs.list_history(session, 1)["threads"] == []
    # deleting someone else's / unknown is a no-op, not an error
    assert hs.delete_thread(session, 2, "t1") is False


def test_delete_project_detaches_its_threads(session):
    proj = hs.create_project(session, 1, "Work")
    hs.upsert_thread(session, 1, "t1", title="c", project_id=proj.id)
    assert hs.delete_project(session, 1, proj.id) is True
    assert hs.list_history(session, 1)["projects"] == []
    assert hs.list_history(session, 1)["threads"][0]["project_id"] is None


def test_rename_project(session):
    proj = hs.create_project(session, 1, "Old")
    assert hs.rename_project(session, 1, proj.id, "New").name == "New"
    assert hs.rename_project(session, 2, proj.id, "x") is None  # not the owner


def test_set_feedback_upserts_and_normalizes(session):
    from src.core.models_db import Feedback

    assert hs.set_feedback(session, 1, "job1", "up") == "up"
    assert hs.set_feedback(session, 1, "job1", "down", note="wrong parts") == "down"   # re-rate updates
    rows = session.query(Feedback).all()
    assert len(rows) == 1                                   # one row per (user, job)
    assert rows[0].rating == "down" and rows[0].note == "wrong parts"
    # a different user's rating on the same job is its own row
    hs.set_feedback(session, 2, "job1", "like")
    assert session.query(Feedback).count() == 2


def test_import_is_one_time(session):
    projects = [{"id": "p1", "name": "Imported"}]
    threads = [
        {"id": "t1", "title": "Kept chat", "pinned": True, "project_id": "p1"},
        {"id": "t2", "title": "Deleted chat", "deleted_at": "2026-10-01T00:00:00Z"},
    ]
    first = hs.import_history(session, 1, threads=threads, projects=projects)
    assert first["imported"] is True

    hist = hs.list_history(session, 1)
    assert [p["name"] for p in hist["projects"]] == ["Imported"]
    kept = {t["id"]: t for t in hist["threads"]}
    assert "t1" in kept and kept["t1"]["pinned"] is True and kept["t1"]["project_id"] == "p1"
    assert "t2" not in kept  # imported already soft-deleted

    # re-posting does nothing once the user has history
    second = hs.import_history(session, 1, threads=threads, projects=projects)
    assert second["imported"] is False
