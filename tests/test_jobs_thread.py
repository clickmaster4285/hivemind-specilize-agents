"""job_to_dict surfaces the chat thread_id so history can regroup a conversation after a reload."""

from __future__ import annotations

import json

from src.jobs.jobs import job_to_dict
from src.core.models_db import Job


def _job(payload: dict) -> Job:
    return Job(id="j1", user_id=1, mode="chat", query="hi", payload_json=json.dumps(payload))


def test_job_to_dict_exposes_thread_id():
    out = job_to_dict(_job({"thread_id": "chat-123", "specialist": "auto"}))
    assert out["thread_id"] == "chat-123"
    # still returned inside the full payload too
    assert out["payload"]["thread_id"] == "chat-123"


def test_job_to_dict_thread_id_none_for_legacy_jobs():
    out = job_to_dict(_job({"specialist": "auto"}))
    assert out["thread_id"] is None
