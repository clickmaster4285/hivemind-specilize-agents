"""Every job logs one `job_timing` line: wall time and model time per step (src/jobs/worker.py, ISSUES_LOG O48)."""

import logging
from contextlib import contextmanager
from types import SimpleNamespace

from src.jobs import worker


@contextmanager
def _no_db(config=None):
    yield None


def test_the_line_has_the_total_and_each_model_step(monkeypatch, caplog):
    monkeypatch.setattr(worker, "session_scope", _no_db)
    monkeypatch.setattr(worker, "job_totals", lambda session, job_id: {"steps": [
        {"step": "assistant", "seconds": 3.1}, {"step": "chat_runner", "seconds": 25.2}]})
    with caplog.at_level(logging.INFO, logger=worker.__name__):
        worker._log_timing(SimpleNamespace(), "job-1", "chat", 41.25)
    assert "job_timing job=job-1 mode=chat total=41.2s model steps: assistant 3.1s, chat_runner 25.2s" in caplog.text


def test_a_job_without_model_calls_still_logs_its_time(monkeypatch, caplog):
    monkeypatch.setattr(worker, "session_scope", _no_db)
    monkeypatch.setattr(worker, "job_totals", lambda session, job_id: None)
    with caplog.at_level(logging.INFO, logger=worker.__name__):
        worker._log_timing(SimpleNamespace(), "job-2", "chat", 2.0)
    assert "job=job-2 mode=chat total=2.0s model steps: none" in caplog.text


def test_a_database_problem_never_breaks_the_job(monkeypatch, caplog):
    @contextmanager
    def broken(config=None):
        raise RuntimeError("database down")
        yield

    monkeypatch.setattr(worker, "session_scope", broken)
    with caplog.at_level(logging.INFO, logger=worker.__name__):
        worker._log_timing(SimpleNamespace(), "job-3", "chat", 1.0)
    assert "job=job-3" in caplog.text
