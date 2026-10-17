"""Single-call assistant path for greetings, creative writing, and simple Q&A."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from src.brain.answer import answer_question, current_time_text
from src.brain.db import BrainDBError
from src.brain.embed import EmbedError
from src.config import Config
from src.conversation import format_conversation_block, normalize_conversation
from src.llm_client import LLMClient

BRAIN_DOWN_NOTICE = (
    "> **Knowledge base unavailable right now** — this answer is from the model's general knowledge, "
    "not verified against sources."
)

ProgressCallback = Callable[[dict[str, Any]], None]

CHAT_SYSTEM = """You are MulteAgent Assistant — a helpful, concise local AI.

Rules:
- Match the user's intent directly (greetings → warm short reply; stories → write the story; questions → answer).
- If the user attached image(s), look at them and answer about what you see. Be specific.
- Do NOT frame creative, casual, or vision requests as business strategy, risks, or feasibility.
- Prefer clear markdown when useful. Keep greetings to 1–3 sentences.
- Stay helpful and concrete. Do not mention being a multi-agent board unless asked.
- Never claim you cannot create Word/PDF/PPT files, and never give pip/python-docx scripts.
- Never put images or image links in your answer (no markdown ![..](..), no image-generator URLs):
  you cannot create pictures here. For a picture the user asks "create an image of …"; for a
  diagram, "create a diagram of …" — say that instead.
- Never output TOOL lines, XML tool tags, or fake function calls — this chat path has no tools.
  If the user clearly wants a .docx/.pdf/.pptx download, tell them to click **Document** or say
  "create a docx of this" so routing can run the real file tools — then briefly answer content only if needed."""


VISION_SYSTEM = """You are MulteAgent Assistant with vision.

Look at every attached image. Describe what is actually in the picture, then answer the user's question.
Be specific (objects, text, layout, people, charts). If text is in the image, transcribe the important parts.
Do not invent details you cannot see. Do not treat the image as a business proposal unless asked."""


@dataclass
class ChatResult:
    output: str = ""
    errors: list[str] = field(default_factory=list)
    total_seconds: float = 0.0


def _emit(cb: ProgressCallback | None, event: dict[str, Any]) -> None:
    if cb:
        cb(event)


def run_chat(
    query: str,
    *,
    config: Config | None = None,
    model_plan: dict[str, str] | None = None,
    on_progress: ProgressCallback | None = None,
    images: list[str] | None = None,
    conversation: list[dict[str, str]] | None = None,
    use_brain: bool = False,
    account_id: str | None = None,
    job_id: str | None = None,
) -> ChatResult:
    config = config or Config.from_env()
    model_plan = model_plan or {}
    pics = [img for img in (images or []) if img]
    use_brain = use_brain and not pics
    if pics:
        model = model_plan.get("vision") or config.model_vision or config.llm_model
        system = VISION_SYSTEM
        role = "Vision"
    else:
        model = model_plan.get("worker") or model_plan.get("planner") or config.model_fast or config.llm_model
        system = CHAT_SYSTEM
        role = "Searching knowledge base" if use_brain else "Direct reply"
    # The model has no clock: always tell it the current date and time.
    system = f"{system}\n\nCurrent date and time: {current_time_text()}."
    client = LLMClient(config)
    result = ChatResult()
    started = time.perf_counter()

    _emit(
        on_progress,
        {
            "type": "session_start",
            "mode": "chat",
            "query": query,
        },
    )
    _emit(
        on_progress,
        {
            "type": "agent_start",
            "agent_id": "assistant",
            "name": "Assistant",
            "role": role,
            "stage": "chat",
            "accent": "#c4a35a",
            "model": model,
        },
    )

    try:
        history = normalize_conversation(conversation)
        prior = format_conversation_block(history)
        user_msg = f"{prior}\n\n## Current message\n{query}" if prior else query
        extra: dict[str, Any] = {}
        if use_brain:
            try:
                answer = answer_question(
                    query, config=config, account_id=account_id, job_id=job_id, model=model,
                    conversation=history, conversation_block=prior, chat=client.chat,
                )
                output = answer.text
                role = "Knowledge base" if answer.grounded else "General knowledge"
                extra = {"grounded": answer.grounded, "sources": answer.sources_payload()}
            except (BrainDBError, EmbedError) as exc:
                result.errors.append(f"knowledge base unavailable: {exc}")
                output = f"{BRAIN_DOWN_NOTICE}\n\n" + client.chat(system=system, user=user_msg, model=model)
                role = "General knowledge"
                extra = {"grounded": False, "sources": []}
        else:
            output = client.chat(system=system, user=user_msg, model=model, images=pics or None)
        result.output = output
        elapsed = time.perf_counter() - started
        _emit(
            on_progress,
            {
                "type": "agent_done",
                "agent_id": "assistant",
                "name": "Assistant",
                "role": role,
                "stage": "chat",
                "accent": "#c4a35a",
                "elapsed_seconds": elapsed,
                "output": output,
                **extra,
            },
        )
    except Exception as exc:  # noqa: BLE001
        result.errors.append(str(exc))
        _emit(
            on_progress,
            {
                "type": "agent_error",
                "agent_id": "assistant",
                "name": "Assistant",
                "error": str(exc),
            },
        )

    result.total_seconds = time.perf_counter() - started
    _emit(
        on_progress,
        {
            "type": "session_done",
            "total_seconds": result.total_seconds,
            "errors": result.errors,
            "mode": "chat",
        },
    )
    return result
