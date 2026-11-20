"""Live notices (BACKLOG L29, src/core/live_events.py, web/routers/events.py): who hears what, one notice per topic,
sent only once the change is committed, and the stream that carries them."""

from __future__ import annotations

import asyncio
import json
import threading

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.core import live_events
from src.core.db import Base
from src.core.models_db import User
from web.routers import events


async def _drain(sub, timeout: float = 0.05) -> list[str]:
    got = []
    while (topic := await live_events.next_topic(sub, timeout)) is not None:
        got.append(topic)
    return got


def test_a_notice_reaches_its_user_and_the_admins_only():
    async def run():
        ana = live_events.subscribe(1, admin=False)
        bo = live_events.subscribe(2, admin=False)
        boss = live_events.subscribe(3, admin=True)
        try:
            live_events.publish(live_events.DEVICES, user_id=1)
            live_events.publish(live_events.SIGNUP, admins=True)
            return await _drain(ana), await _drain(bo), await _drain(boss)
        finally:
            for s in (ana, bo, boss):
                live_events.unsubscribe(s)

    ana, bo, boss = asyncio.run(run())
    assert ana == ["devices"] and bo == [] and boss == ["signup"]
    assert live_events.count() == 0


def test_the_same_topic_waiting_twice_is_sent_once():
    async def run():
        sub = live_events.subscribe(1, admin=False)
        try:
            for _ in range(5):
                live_events.publish(live_events.CHATS, user_id=1)
            live_events.publish(live_events.PROJECTS, user_id=1)
            return await _drain(sub)
        finally:
            live_events.unsubscribe(sub)

    assert asyncio.run(run()) == ["chats", "projects"]


def test_a_notice_from_another_thread_arrives():
    async def run():
        sub = live_events.subscribe(7, admin=False)
        try:
            worker = threading.Thread(target=live_events.publish, args=(live_events.CHATS,), kwargs={"user_id": 7})
            worker.start()
            worker.join()
            return await live_events.next_topic(sub, 1.0)
        finally:
            live_events.unsubscribe(sub)

    assert asyncio.run(run()) == "chats"


def test_after_commit_waits_for_the_commit_and_a_rollback_sends_nothing():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    async def run():
        sub = live_events.subscribe(1, admin=True)
        try:
            with Session(engine) as db:
                live_events.after_commit(db, live_events.USERS, admins=True)
                before = await _drain(sub)
                db.add(User(username="x", password_hash="x", role="member"))
                db.commit()
                after = await _drain(sub)
            with Session(engine) as db:
                live_events.after_commit(db, live_events.SECURITY, admins=True)
                db.add(User(username="y", password_hash="x", role="member"))
                db.rollback()
                rolled = await _drain(sub)
            return before, after, rolled
        finally:
            live_events.unsubscribe(sub)

    before, after, rolled = asyncio.run(run())
    assert before == [] and after == ["users"] and rolled == []


class _Request:
    """Enough of a Starlette request for the stream: never disconnected."""
    cookies: dict = {}

    async def is_disconnected(self) -> bool:
        return False


def test_the_stream_says_hello_then_sends_topics_and_keep_alives(monkeypatch):
    monkeypatch.setattr(events, "HEARTBEAT_SECONDS", 0.05)

    async def run():
        user = User(id=11, username="ana", password_hash="x", role="member")
        response = await events.live_events_stream(_Request(), user)
        body = response.body_iterator
        hello = await anext(body)
        live_events.publish(live_events.DEVICES, user_id=11)
        topic = await anext(body)
        quiet = await anext(body)
        await body.aclose()                     # the tab closed: the subscriber goes
        return hello, topic, quiet

    hello, topic, quiet = asyncio.run(run())
    assert json.loads(hello.removeprefix("data: ")) == {"type": "hello"}
    assert json.loads(topic.removeprefix("data: ")) == {"type": "devices"}
    assert quiet == ": keep-alive\n\n"
    assert live_events.count() == 0


def test_a_signed_out_user_is_told_and_the_stream_ends(monkeypatch):
    monkeypatch.setattr(events, "HEARTBEAT_SECONDS", 0.01)
    monkeypatch.setattr(events, "RECHECK_SECONDS", 0)
    monkeypatch.setattr(events, "_still_signed_in", lambda request: False)

    async def run():
        user = User(id=12, username="bo", password_hash="x", role="member")
        response = await events.live_events_stream(_Request(), user)
        return [chunk async for chunk in response.body_iterator]

    chunks = asyncio.run(run())
    assert json.loads(chunks[-1].removeprefix("data: ")) == {"type": "signed_out"}
    assert live_events.count() == 0


def test_saving_a_device_tells_its_owner(device_store):
    from src.servers import server_vault

    async def run():
        sub = live_events.subscribe(5150, admin=False)
        try:
            login = server_vault.add(5150, label="pk", host="10.0.0.2", port=22, username="root", password="pw")
            server_vault.set_mode(5150, login.id, "confirm")
            first = await _drain(sub)
            server_vault.remove(5150, login.id)
            return first, await _drain(sub)
        finally:
            live_events.unsubscribe(sub)

    first, second = asyncio.run(run())
    assert first == ["devices"] and second == ["devices"]
