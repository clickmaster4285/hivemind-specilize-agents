"""Live notices to the open pages (BACKLOG L29, ISSUES_LOG O120): "users changed", "your devices changed"…

Each signed-in tab holds one Server-Sent Events stream, `GET /api/events` (web/routers/events.py). Code that
changes something calls `publish` — or `after_commit` with its database session, so the notice leaves only
once the change is saved and a page that reloads at once sees it. A notice carries only its topic, never the
data: the page asks the API again with its own rights.

Who hears a notice: the user it is about (`user_id`), and/or every admin (`admins=True`).

In memory, in this process: the API runs its worker in the same process (main.py --gui). A worker in another
process (`main.py --worker`) would need Postgres LISTEN / NOTIFY behind `publish`.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# what a page can follow; the browser reloads the list each one names
USERS = "users"            # admins: an account was added, changed, deactivated or deleted
SIGNUP = "signup"          # admins: someone signed up and waits for approval
SECURITY = "security"      # admins: a new security-log entry
DEVICES = "devices"        # one user: their saved devices
CHATS = "chats"            # one user: their chat list (a chat started, finished, renamed, moved, deleted)
PROJECTS = "projects"      # one user: their projects
SCHEDULES = "schedules"    # one user: their scheduled questions
TOPICS = (USERS, SIGNUP, SECURITY, DEVICES, CHATS, PROJECTS, SCHEDULES)

QUEUE_LIMIT = 64           # notices waiting for one slow tab; the same topic is never queued twice


@dataclass(eq=False)
class Subscriber:
    user_id: int
    admin: bool
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(QUEUE_LIMIT))
    pending: set[str] = field(default_factory=set)       # topics queued and not yet sent


_subscribers: set[Subscriber] = set()
_lock = threading.Lock()


def subscribe(user_id: int, admin: bool) -> Subscriber:
    """A tab's stream opens (call from the stream's own event loop)."""
    sub = Subscriber(user_id=user_id, admin=admin, loop=asyncio.get_running_loop())
    with _lock:
        _subscribers.add(sub)
    return sub


def unsubscribe(sub: Subscriber) -> None:
    with _lock:
        _subscribers.discard(sub)


def count() -> int:
    with _lock:
        return len(_subscribers)


def _deliver(sub: Subscriber, topic: str) -> None:
    """On the subscriber's loop: queue the topic once (ten device edits in a row = one reload)."""
    if topic in sub.pending:
        return
    try:
        sub.queue.put_nowait(topic)
        sub.pending.add(topic)
    except asyncio.QueueFull:
        pass                    # the tab is not reading; it reloads everything when it reconnects


def publish(topic: str, *, user_id: int | None = None, admins: bool = False) -> None:
    """Tell the tabs that may see it that `topic` changed. Safe from any thread (the worker, a router)."""
    with _lock:
        targets = [s for s in _subscribers
                   if (user_id is not None and s.user_id == user_id) or (admins and s.admin)]
    for sub in targets:
        try:
            sub.loop.call_soon_threadsafe(_deliver, sub, topic)
        except RuntimeError:    # its loop is closed: the stream is ending
            unsubscribe(sub)


def after_commit(db: Any, topic: str, *, user_id: int | None = None, admins: bool = False) -> None:
    """publish() once `db` (a SQLAlchemy session) commits — a page that reloads at once then sees the change."""
    from sqlalchemy import event

    event.listen(db, "after_commit", lambda _session: publish(topic, user_id=user_id, admins=admins), once=True)


async def next_topic(sub: Subscriber, timeout: float) -> str | None:
    """The next topic for this tab, or None after `timeout` seconds of quiet (time for a keep-alive)."""
    try:
        topic = await asyncio.wait_for(sub.queue.get(), timeout)
    except asyncio.TimeoutError:
        return None
    sub.pending.discard(topic)
    return topic
