"""What the API reports about itself: the model and whether Ollama answers, the job queue, the image service, the
chat-memory budget. Signed-in users get it from GET /api/health (web/routers/system.py); the start scripts, which run
on this machine, print it with `python -m src.core.status` — anonymous callers of /api/health get only {"ok": true}
(the model, the queue and the server's set-up are nobody's business before sign-in).
"""

from __future__ import annotations

import argparse
from typing import Any

from sqlalchemy.orm import Session

from src.core.config import Config


def details(config: Config, db: Session) -> dict[str, Any]:
    from src.images.image_client import ImageServiceClient
    from src.jobs.chat_memory import history_budget
    from src.jobs.jobs import estimate_wait_seconds, queue_depth
    from src.llm.llm_client import LLMClient

    return {
        "llm_reachable": LLMClient(config).ping(),
        "model": config.llm_model,
        "queue_depth": queue_depth(db),
        "estimated_wait_seconds": estimate_wait_seconds(db, config),
        "llm_slots": config.llm_slots,
        "image_slots": config.image_slots,
        "image_profile": config.image_profile,
        "image_pipeline": ImageServiceClient(config).status(),
        # chat memory (BACKLOG L12): the model's context window and the share of it a chat's history may use
        "context": {"window": config.llm_num_ctx, "history_budget": history_budget(config)},
    }


def summary_line(found: dict[str, Any]) -> str:
    """The one line start.sh / start.ps1 print after "API up"."""
    image = (found.get("image_pipeline") or {}).get("state")
    return (f"model {found.get('model')}, Ollama reachable: {found.get('llm_reachable')}, image model: {image}, "
            f"jobs waiting: {found.get('queue_depth')}")


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description="Print the API's status line (start.sh / start.ps1).").parse_args(argv)
    from src.core.db import session_scope

    config = Config.from_env()
    with session_scope(config) as db:
        print(summary_line(details(config, db)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
