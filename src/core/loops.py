"""A background thread that does one job again and again (ISSUES_LOG O97: four copies of this loop existed — the brain
refresh, the SEO tracker, scheduled tasks and the idle-SSH sweeper).

    start_loop("seo-tracker", run_due_and_log, every_seconds=3600, first_delay=120, log=log)

The first run waits `first_delay` (let the API finish starting); a run that raises is logged and the loop goes on, so
one bad run never stops the thread. Daemon threads: they end with the process.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable


def start_loop(name: str, work: Callable[[], object], *, every_seconds: float, first_delay: float = 0,
               log: logging.Logger) -> threading.Thread:
    def loop() -> None:
        time.sleep(first_delay)
        while True:
            try:
                work()
            except Exception:  # noqa: BLE001 — never let the thread die
                log.exception("%s crashed", name)
            time.sleep(every_seconds)

    thread = threading.Thread(target=loop, name=name, daemon=True)
    thread.start()
    return thread
