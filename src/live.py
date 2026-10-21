"""Live, in-memory state of running jobs: the answer text as it is being written, and Stop requests.

Why not the database: a streamed answer arrives in dozens of small pieces per second. Only the
finished answer is stored (agent_done event); the growing text lives here and the SSE endpoint
sends it to the browser a few times per second. Works when the worker runs inside the API
process (the default); a standalone worker still delivers the final answer, just not live.
"""

from __future__ import annotations

import threading
import time

from src.job_events import notify_job

NOTIFY_EVERY_SECONDS = 0.25          # wake the SSE stream at most ~4×/s per job

_lock = threading.Lock()
_text: dict[str, dict[str, str]] = {}           # job_id → agent_id → text so far
_last_notify: dict[str, float] = {}
_stopped: set[str] = set()
# command confirmations: a running job asks "run this?" and blocks until the browser answers or it times out
_confirm: dict[tuple[str, str], dict] = {}      # (job_id, command_id) → {"event", "approved"}


def set_text(job_id: str | None, agent_id: str, text: str) -> None:
    if not job_id:
        return
    now = time.monotonic()
    with _lock:
        _text.setdefault(job_id, {})[agent_id] = text
        due = now - _last_notify.get(job_id, 0.0) >= NOTIFY_EVERY_SECONDS
        if due:
            _last_notify[job_id] = now
    if due:
        notify_job(job_id)


def snapshot(job_id: str) -> dict[str, str]:
    with _lock:
        return dict(_text.get(job_id, {}))


def finish_agent(job_id: str | None, agent_id: str) -> None:
    """Call right before the agent's final event: the stream must not show old text after it."""
    if not job_id:
        return
    with _lock:
        _text.get(job_id, {}).pop(agent_id, None)


def stop(job_id: str) -> None:
    """Stop button: running model calls of this job end at their next piece of text."""
    with _lock:
        _stopped.add(job_id)
    notify_job(job_id)


def is_stopped(job_id: str | None) -> bool:
    if not job_id:
        return False
    with _lock:
        return job_id in _stopped


def clear(job_id: str) -> None:
    with _lock:
        _text.pop(job_id, None)
        _last_notify.pop(job_id, None)
        _stopped.discard(job_id)
        for key in [k for k in _confirm if k[0] == job_id]:
            _confirm[key]["event"].set()            # release any waiter so the worker thread can exit
            del _confirm[key]


# ── command confirmation (operate_server: a change needs the user's click) ──
def wait_confirm(job_id: str | None, command_id: str, timeout: float) -> bool | None:
    """Block until the browser approves/rejects this command, or timeout. True=run, False=cancel,
    None=timed out or the job was stopped. Only works with the in-process worker (shared memory)."""
    if not job_id:
        return None
    event = threading.Event()
    with _lock:
        _confirm[(job_id, command_id)] = {"event": event, "approved": None}
    answered = event.wait(timeout)
    with _lock:
        entry = _confirm.pop((job_id, command_id), None)
    if not answered or entry is None or is_stopped(job_id):
        return None
    return bool(entry["approved"])


def answer_confirm(job_id: str, command_id: str, approved: bool) -> bool:
    """From the confirm endpoint. True if a waiter was found and released."""
    with _lock:
        entry = _confirm.get((job_id, command_id))
        if entry is None:
            return False
        entry["approved"] = approved
        entry["event"].set()
    return True
