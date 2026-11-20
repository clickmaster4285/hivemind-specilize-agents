"""The queue with a team of 10 (BACKLOG L30 T3): people's questions before scheduled runs, a run never blocking its
owner, an honest place-and-wait from measured answer times, several workers, and the VRAM unload inside the GPU
lock."""

from __future__ import annotations

import dataclasses
import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.core.config import Config
from src.core.db import Base
from src.core.models_db import Job, User
from src.jobs import jobs, worker
from src.jobs.jobs import LIVE_PRIORITY, SCHEDULED_PRIORITY, claim_next_job, count_active_for_user, queue_state

T0 = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)
CFG = dataclasses.replace(Config.from_env(), llm_slots=1)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add_all([User(id=1, username="ana", password_hash="x"), User(id=2, username="bo", password_hash="x")])
        s.commit()
        yield s


def _job(db, jid: str, *, user=1, status="queued", priority=LIVE_PRIORITY, created=T0, started=None, finished=None):
    db.add(Job(id=jid, user_id=user, mode="chat", status=status, priority=priority, query="q", created_at=created,
               started_at=started, finished_at=finished))
    db.flush()
    return db.get(Job, jid)


def test_a_scheduled_run_never_blocks_its_owner_from_asking(db):
    _job(db, "run", priority=SCHEDULED_PRIORITY)
    assert count_active_for_user(db, 1) == 0                                   # live questions: none
    assert count_active_for_user(db, 1, scheduled=True) == 1                   # its own kind: one


def test_a_question_goes_before_an_older_scheduled_run(db):
    _job(db, "run", priority=SCHEDULED_PRIORITY, created=T0)
    _job(db, "ask", user=2, created=T0 + timedelta(minutes=1))
    assert claim_next_job(db).id == "ask"


def test_the_wait_is_measured_from_recent_answers(db):
    for n, seconds in enumerate([20, 30, 40, 400]):                            # one deep research: the median ignores it
        _job(db, f"done{n}", status="succeeded", started=T0, finished=T0 + timedelta(seconds=seconds))
    assert jobs.typical_job_seconds(db) == 40


def test_place_and_wait_count_what_is_ahead_and_what_still_runs(db, monkeypatch):
    for n, seconds in enumerate([30, 30, 30]):
        _job(db, f"done{n}", status="succeeded", started=T0, finished=T0 + timedelta(seconds=seconds))
    now = T0 + timedelta(hours=1)
    monkeypatch.setattr(jobs, "_utcnow", lambda: now)
    _job(db, "running", status="running", started=now - timedelta(seconds=10))         # needs ~20 s more
    _job(db, "first", created=now - timedelta(seconds=5))
    run = _job(db, "run", priority=SCHEDULED_PRIORITY, created=now - timedelta(seconds=9))
    mine = _job(db, "mine", user=2, created=now)
    assert queue_state(db, CFG, mine) == {"position": 2, "running": 1, "wait_seconds": 50}   # 30 ahead + 20 running
    assert queue_state(db, CFG, run)["position"] == 3                         # the scheduled run waits for both
    slower_gpu_share = queue_state(db, dataclasses.replace(CFG, llm_slots=2), mine)["wait_seconds"]
    assert slower_gpu_share == 25


def test_a_job_past_the_usual_length_still_counts_some_time(db, monkeypatch):
    now = T0 + timedelta(hours=1)
    monkeypatch.setattr(jobs, "_utcnow", lambda: now)
    _job(db, "slow", status="running", started=now - timedelta(minutes=10))
    assert queue_state(db, CFG)["wait_seconds"] == jobs.MIN_REMAINING_SECONDS


def test_several_workers_start_and_stop(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(worker, "reclaim_orphaned_jobs", lambda session: 0)
    monkeypatch.setattr(worker, "session_scope", _no_db)
    monkeypatch.setattr(worker, "worker_loop", lambda config: (seen.append(threading.current_thread().name),
                                                               worker._stop.wait(5)))
    monkeypatch.setattr(worker, "_worker_threads", [])
    worker.start_inprocess_worker(dataclasses.replace(CFG, worker_threads=3))
    try:
        for _ in range(100):
            if len(seen) == 3:
                break
            threading.Event().wait(0.01)
        assert sorted(seen) == ["multeagent-worker-1", "multeagent-worker-2", "multeagent-worker-3"]
    finally:
        worker._stop.set()
        for t in worker._worker_threads:
            t.join(2)
        worker._stop.clear()


class _no_db:
    def __init__(self, *_a, **_k):
        pass

    def __enter__(self):
        return None

    def __exit__(self, *_a):
        return False


def test_the_vram_unload_happens_inside_the_gpu_lock(monkeypatch, tmp_path):
    """With several workers, a chat call between the unload and the picture would load the chat model back."""
    from src.images import image_gen
    from src.images.image_prompt import PromptRewrite
    from src.llm import llm_lock
    from tests.test_image_models import FakeClient

    held: list[bool] = []

    def unload(config):
        sem = llm_lock.get_llm_semaphore(config)
        held.append(not sem.acquire(blocking=False))      # the slot is taken = we are inside image_slot
        return []

    ckpt = tmp_path / "model.safetensors"
    ckpt.write_bytes(b"x")
    cfg = dataclasses.replace(Config.from_env(), model_image_path=ckpt, image_unload_ollama=True, llm_slots=1)
    monkeypatch.setattr(image_gen, "ImageServiceClient", FakeClient)          # the image tests' fake service
    monkeypatch.setattr(image_gen, "unload_ollama_models", unload)
    monkeypatch.setattr(image_gen, "save_images", lambda config, job_id, rows: [{"filename": "x.png"}])
    monkeypatch.setattr(image_gen, "rewrite_prompt", lambda q, c, previous="": PromptRewrite("a cat", None))
    image_gen.run_image_gen("a cat", job_id="j1", config=cfg)
    assert held == [True]
