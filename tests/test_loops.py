"""One background-loop helper (src/core/loops.py, ISSUES_LOG O97): a failing run is logged and the loop goes on."""

import logging
import threading

from src.core import loops


def test_a_failing_run_never_stops_the_loop(monkeypatch, caplog):
    runs, done, parked = [], threading.Event(), threading.Event()
    monkeypatch.setattr(loops.time, "sleep", lambda seconds: None)

    def work():
        runs.append(1)
        if len(runs) == 1:
            raise RuntimeError("boom")
        if len(runs) == 3:
            done.set()
            parked.wait()             # the daemon thread rests here until the test process ends

    with caplog.at_level(logging.ERROR):
        thread = loops.start_loop("test-loop", work, every_seconds=60, first_delay=5, log=logging.getLogger("t"))
        assert done.wait(2)
    assert len(runs) == 3 and thread.daemon and thread.name == "test-loop"
    assert "test-loop crashed" in caplog.text
