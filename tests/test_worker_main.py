"""The standalone worker entry point (O26) — no database: setup and the job loop are faked."""

from __future__ import annotations

import dataclasses

import worker_main
from src.core.config import Config

SECRET = "s3cret-pw"


def test_worker_starts_on_the_current_modules_and_hides_the_password(monkeypatch, tmp_path, capsys):
    config = dataclasses.replace(
        Config.from_env(), database_url=f"postgresql+psycopg://multeagent_app:{SECRET}@127.0.0.1:5432/multeagent",
        log_dir=tmp_path / "logs", artifacts_dir=tmp_path / "artifacts")
    calls = []
    monkeypatch.setattr(worker_main.Config, "from_env", classmethod(lambda _cls: config))
    monkeypatch.setattr(worker_main, "check_key", lambda cfg: calls.append("check_key"))
    monkeypatch.setattr(worker_main, "init_db", lambda cfg: calls.append("init_db"))
    monkeypatch.setattr(worker_main, "worker_loop", lambda cfg: calls.append("worker_loop"))

    worker_main.main()

    assert calls == ["check_key", "init_db", "worker_loop"]
    assert (tmp_path / "logs").is_dir() and (tmp_path / "artifacts").is_dir()
    out = capsys.readouterr().out
    assert SECRET not in out
    assert "multeagent_app:***@127.0.0.1:5432/multeagent" in out
