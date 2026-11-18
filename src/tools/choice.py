"""A choice card in the chat: a question with options the user clicks (one, or several with Continue), while the job
waits — deploy_project's steps, schedule_task's "Save this schedule?". The browser answers through
POST /api/jobs/{id}/choose (live.wait_choice); the card and its answer are saved with the job's events, so a reloaded
chat shows it answered (as the confirm cards, O66). Rendered by frontend/src/components/chat/ChoiceCard.tsx.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# how long a card waits for a click before the job goes on without an answer (as a confirm card)
WAIT_SECONDS = 300


@dataclass
class Option:
    id: str
    label: str
    detail: str = ""
    checked: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label, "detail": self.detail, "checked": self.checked}


# ask(question, options, multi) → the picked option ids, or None (no answer in time / the job was stopped)
Ask = Callable[[str, list[Option], bool], "list[str] | None"]


def card_asker(emit: Callable[[dict], None], job_id: str, *, status: Callable[[str], None],
               server: str = "") -> Ask:
    """An `ask` that shows the card in this job's chat and waits for the click."""
    from src.jobs import live

    def ask(question: str, options: list[Option], multi: bool) -> list[str] | None:
        choice_id = uuid.uuid4().hex[:12]
        emit({"type": "choice_ask", "agent_id": "assistant", "choice_id": choice_id, "question": question,
              "options": [o.as_dict() for o in options], "multi": multi, "server": server})
        status("Waiting for your choice…")
        picked = live.wait_choice(job_id, choice_id, WAIT_SECONDS)
        emit({"type": "choice_resolved", "agent_id": "assistant", "choice_id": choice_id,
              "answer": "timeout" if picked is None else "picked", "picked": picked or []})
        return picked

    return ask
