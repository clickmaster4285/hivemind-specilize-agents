"""The events every tool runner sends to the chat, in one place (ISSUES_LOG O97: they were copied in 16 runners).

    run = ToolRun(on_progress, query=query, job_id=job_id, role="Ping", model="ping_host", accent="#3aa89a")
    run.status("Pinging…")
    …
    return run.finish(output, errors)

session_start + agent_start open the answer's bubble; status lines show progress; agent_done carries the answer and
session_done ends the job — the order the feed (frontend/src/lib/feedReducer.ts) expects.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any


class ToolRun:
    def __init__(self, on_progress: Callable[[dict], None] | None, *, query: str, job_id: str | None, role: str,
                 model: str, accent: str = ""):
        self.emit: Callable[[dict], None] = on_progress or (lambda event: None)
        self.agent: dict[str, Any] = {"agent_id": "assistant", "name": "Assistant", "role": role, "stage": "chat",
                                      **({"accent": accent} if accent else {}), "job_id": job_id}
        self.started = time.monotonic()
        self.emit({"type": "session_start", "mode": "chat", "query": query})
        self.emit({"type": "agent_start", **self.agent, "model": model})

    def status(self, message: str) -> None:
        self.emit({"type": "status", "agent_id": "assistant", "message": message})

    def finish(self, output: str, errors: list[str], **extra: Any) -> tuple[str, list[str], float]:
        """The answer and the end of the job → what a runner returns. extra: more fields on agent_done (a clarify
        form's questions)."""
        elapsed = time.monotonic() - self.started
        self.emit({"type": "agent_done", **self.agent, "elapsed_seconds": elapsed, "output": output, **extra})
        self.emit({"type": "session_done", "total_seconds": elapsed, "errors": errors, "mode": "chat"})
        return output, errors, elapsed
