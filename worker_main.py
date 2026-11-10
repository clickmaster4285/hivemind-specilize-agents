#!/usr/bin/env python3
"""Standalone worker process: claims jobs from the same queue as the API's in-process worker.

Several workers may run at once (jobs are claimed with SKIP LOCKED on Postgres). Unlike the API it does
not free jobs left 'running' at start: a running job may belong to the API or another worker.
"""

from __future__ import annotations

from sqlalchemy.engine import make_url

from src.core.config import Config
from src.core.crypto import check_key
from src.core.db import init_db
from src.jobs.worker import worker_loop


def main() -> None:
    config = Config.from_env()
    check_key(config)          # jobs may use saved device logins, encrypted with DEVICE_KEY
    config.log_dir.mkdir(parents=True, exist_ok=True)
    config.artifacts_dir.mkdir(parents=True, exist_ok=True)
    init_db(config)
    print(f"MulteAgent worker started (llm_slots={config.llm_slots})")
    print(f"Database: {make_url(config.database_url).render_as_string(hide_password=True)}")
    try:
        worker_loop(config)
    except KeyboardInterrupt:
        print("MulteAgent worker stopped")


if __name__ == "__main__":
    main()
