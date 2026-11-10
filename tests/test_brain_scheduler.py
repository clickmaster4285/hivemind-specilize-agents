"""Brain refresh progress in the log (O34) — no database: lock, registry and ingest are faked."""

from __future__ import annotations

import pytest

from src.brain import scheduler
from src.brain.ingest import IngestStats


class _LockConn:
    """The advisory-lock connection: the lock is always free."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=None):
        return type("Row", (), {"fetchone": lambda _self: (True,)})()


@pytest.fixture
def refresh(monkeypatch):
    def fake_ingest(slug, config, log):
        log(f"[{slug}] fetching…")
        log(f"  [1/1] new     {slug}/index.html  (3 chunks)")
        return IngestStats(slug=slug, new=1, status="ok", seconds=1.0)

    monkeypatch.setattr(scheduler, "app_url", lambda _cfg: "postgresql://brain_app@127.0.0.1/multeagent")
    monkeypatch.setattr(scheduler.psycopg, "connect", lambda *a, **k: _LockConn())
    monkeypatch.setattr(scheduler, "migrate", lambda _cfg: [])
    monkeypatch.setattr(scheduler, "sync_sources", lambda _cfg: None)
    monkeypatch.setattr(scheduler, "due_sources", lambda _cfg: ["docs-a", "docs-b"])
    monkeypatch.setattr(scheduler, "ingest_source", fake_ingest)
    lines: list[str] = []
    stats = scheduler.run_due(object(), log_fn=lines.append)
    return stats, lines


def test_refresh_logs_each_file_with_its_place_in_the_run(refresh):
    stats, lines = refresh
    assert [s.slug for s in stats] == ["docs-a", "docs-b"]
    assert lines[0] == "brain refresh: 2 sources due"
    assert "brain refresh 1/2: [docs-a] fetching…" in lines
    assert "brain refresh 2/2: [1/1] new     docs-b/index.html  (3 chunks)" in lines
    assert lines[-1].startswith("brain refresh 2/2: docs-b ok — new 1")
