"""Single-call assistant path for greetings, creative writing, and simple Q&A."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from src.tools.about import about_system, identity_line
from src.llm.assistant import speaker
from src.brain.answer import answer_question, current_time_text
from src.brain.db import BrainDBError
from src.brain.embed import EmbedError
from src.core.config import Config
from src.jobs import live
from src.core.conversation import format_conversation_block, normalize_conversation
from src.llm.llm_client import LLMClient, LLMStopped
from src.llm.ollama_mgmt import model_loaded
from src.tools.quiz import generate_quiz
from src.tools.suggestions import generate_suggestions
from src.tools.web_search import WebSearchError, answer_from_web

# "parts of / explain / how X works / difference between …" — answers that benefit from a quiz and images.
_EDUCATIONAL = re.compile(
    r"\b(parts?\s+of|components?\s+of|types?\s+of|features?\s+of|overview\s+of|architecture\s+of|"
    r"explain|explained|what\s+(is|are)|how\s+\w+.*\bworks?\b|how\s+to|guide\s+to|teach|learn|"
    r"difference\s+between|examples?\s+of|introduction\s+to)\b",
    re.I,
)


def _is_educational(query: str) -> bool:
    return bool(_EDUCATIONAL.search(query or ""))

log = logging.getLogger(__name__)

BRAIN_DOWN_NOTICE = (
    "> **Knowledge base unavailable right now** — this answer is from the model's general knowledge, "
    "not verified against sources."
)

if TYPE_CHECKING:
    from src.llm.specialists import Specialist

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
    stopped: bool = False


def friendly_error(exc: BaseException, config: Config) -> str:
    """What went wrong, in words a user can act on (the raw error stays in the log)."""
    text = str(exc).lower()
    if "not reachable" in text or "connection refused" in text or "failed to establish" in text:
        return "The language model is not running (Ollama is not reachable). Start Ollama or run start.ps1, then retry."
    if "timeout" in text or "timed out" in text:
        return (f"The model stopped answering (no new text for {config.timeout_seconds} s). "
                "It may be overloaded: retry, or ask a shorter question.")
    if "out of memory" in text or "cuda" in text or "insufficient" in text:
        return "Not enough GPU memory for the model right now (an image job may be running). Retry in a moment."
    if "empty response" in text:
        return "The model returned an empty answer. Please retry."
    return f"The answer failed: {exc}"


def _emit(cb: ProgressCallback | None, event: dict[str, Any]) -> None:
    if cb:
        cb(event)


def _verify(answer: Any, *, client: LLMClient, model: str, job_id: str | None,
            status: Callable[[str], None]) -> dict[str, Any] | None:
    """Check a knowledge-base answer against its sources (src/brain/verify.py) after it is written.

    Never breaks the answer: a failed or stopped check just shows no verification line."""
    from src.brain.verify import verify_answer

    passages = answer.search.passages if answer.search else []
    if not passages or live.is_stopped(job_id):
        return None
    status("Checking the answer against its sources…")
    try:
        checked = verify_answer(answer.text, passages,
                                chat=lambda **kw: client.chat(model=model, max_retries=0, **kw))
    except Exception as exc:  # noqa: BLE001 — the answer is already written; only the badge is lost
        log.warning("answer check failed: %s", exc)
        return None
    if checked is None or live.is_stopped(job_id):
        return None
    checked.pop("claims", None)             # the chat needs counts + the unconfirmed sentences only
    return checked


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
    about: bool = False,
    web_search: bool = False,
    deep: bool = False,
    options: bool = False,
    should_continue: Callable[[], bool] | None = None,
    specialist: Specialist | None = None,
) -> ChatResult:
    """about=True: a question about MulteAgent itself, answered from live facts with no brain search.
    web_search=True: the user asked to search the internet — SearXNG + the top pages (src/web_search.py).

    The answer streams into src/live.py (shown in the browser as it is written); the Stop button
    (live.stop / should_continue) ends the model call mid-answer and keeps what was written so far.
    """
    config = config or Config.from_env()
    model_plan = model_plan or {}
    pics = [img for img in (images or []) if img]
    deep = deep and not pics                       # Deep research is a web-only, text-only path
    options = options and not pics and not deep     # Options offers approach cards before the answer
    use_web = web_search and not pics and not deep and not options
    use_brain = use_brain and not pics and not about and not use_web and not deep and not options
    if pics:
        model = model_plan.get("vision") or config.model_vision or config.llm_model
        system = VISION_SYSTEM
        role = "Vision"
    else:
        model = model_plan.get("worker") or model_plan.get("planner") or config.model_fast or config.llm_model
        system = CHAT_SYSTEM
        role = (
            "Deep research" if deep
            else "Options" if options
            else "Web search" if use_web
            else "Searching knowledge base" if use_brain
            else "Direct reply"
        )
    # The model has no clock: always tell it the current date and time.
    if about and not pics:
        system = about_system(config, current_time_text(), model)
        role = "About MulteAgent"
    else:
        # Who it is (else it says "made by Google"), and the date: the model has no clock.
        system = f"{identity_line(config, model)}\n\n{system}\n\nCurrent date and time: {current_time_text()}."
        if specialist is not None and not pics:
            system += f"\n\n{specialist.prompt_block()}"
    who = speaker(specialist)
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
            **who,
            "role": role,
            "stage": "chat",
            "model": model,
        },
    )

    keep_going = should_continue or (lambda: not live.is_stopped(job_id))
    first_piece = {"seen": False, "at": 0.0}
    # P0 speed instrumentation (doc/RESPONSE_QUALITY.md): was the model cold when we started, and how
    # long until the first visible token. model_warm is captured on the first chat() call only.
    timing: dict[str, Any] = {"model_warm": None}

    def on_delta(text: str) -> None:
        if not first_piece["seen"]:
            first_piece["seen"] = True
            first_piece["at"] = time.perf_counter()
            _emit(on_progress, {"type": "status", "agent_id": "assistant", "message": "Writing…"})
        live.set_text(job_id, "assistant", text)

    def chat(**kwargs: Any) -> str:
        """client.chat, streamed, with a status that says whether the model must load first."""
        target = kwargs.get("model") or model
        warm = model_loaded(config, target)
        if timing["model_warm"] is None:
            timing["model_warm"] = warm
        _emit(on_progress, {"type": "status", "agent_id": "assistant",
                            "message": "Thinking…" if warm
                            else f"Loading {target} into memory (about 10 s)…"})
        return client.chat(on_delta=on_delta, should_continue=keep_going, **kwargs)

    def ttft() -> float | None:
        """Seconds from start to the first visible token (None if nothing streamed)."""
        return round(first_piece["at"] - started, 3) if first_piece["seen"] else None

    try:
        history = normalize_conversation(conversation)
        prior = format_conversation_block(history)
        user_msg = f"{prior}\n\n## Current message\n{query}" if prior else query
        extra: dict[str, Any] = {}
        if deep:
            from src.tools.deep_research import deep_research

            text, sources = deep_research(
                query,
                config,
                client_chat=lambda **kw: client.chat(**kw),
                chat=chat,
                model=model,
                now=current_time_text(),
                status=lambda message: _emit(
                    on_progress, {"type": "status", "agent_id": "assistant", "message": message}
                ),
                conversation_block=prior,
                should_continue=keep_going,
            )
            output = text
            role = "Deep research"
            extra = {"grounded": bool(sources), "sources": sources}
        elif options:
            from src.tools.answer_options import generate_options

            _emit(on_progress, {"type": "status", "agent_id": "assistant", "message": "Thinking of a few approaches…"})
            cards = generate_options(query, client_chat=lambda **kw: client.chat(**kw),
                                     model=model, n=config.options_count)
            if cards:
                output = "Here are a few ways I can answer this — pick one:"
                role = "Options"
                extra = {"options": cards, "options_query": query}
            else:
                output = chat(system=system, user=user_msg, model=model)   # fall back to a normal answer
                role = "Direct reply"
        elif use_web:
            text, sources = answer_from_web(
                query,
                config,
                chat=chat,
                model=model,
                now=current_time_text(),
                status=lambda message: _emit(
                    on_progress, {"type": "status", "agent_id": "assistant", "message": message}
                ),
                conversation_block=prior,
            )
            output = text
            role = "Web search"
            extra = {"grounded": bool(sources), "sources": sources}
        elif use_brain:
            _emit(on_progress, {"type": "status", "agent_id": "assistant", "message": "Searching the knowledge base…"})
            try:
                answer = answer_question(
                    query, config=config, account_id=account_id, job_id=job_id, model=model,
                    conversation=history, conversation_block=prior, chat=chat,
                    collections=specialist.collections if specialist else None,
                    specialist_block=specialist.prompt_block() if specialist else "",
                )
                output = answer.text
                role = "Knowledge base" if answer.grounded else "General knowledge"
                extra = {"grounded": answer.grounded, "sources": answer.sources_payload()}
                if answer.grounded and config.verify_answers:
                    live.set_text(job_id, "assistant", output)          # the answer stays readable meanwhile
                    checked = _verify(answer, client=client, model=model, job_id=job_id,
                                      status=lambda m: _emit(on_progress, {"type": "status",
                                                                           "agent_id": "assistant", "message": m}))
                    if checked is not None:
                        extra["verification"] = checked
            except (BrainDBError, EmbedError) as exc:
                result.errors.append(f"knowledge base unavailable: {exc}")
                output = f"{BRAIN_DOWN_NOTICE}\n\n" + chat(system=system, user=user_msg, model=model)
                role = "General knowledge"
                extra = {"grounded": False, "sources": []}
        else:
            output = chat(system=system, user=user_msg, model=model, images=pics or None)
        result.output = output
        elapsed = time.perf_counter() - started
        live.finish_agent(job_id, "assistant")       # the final text replaces the streamed one
        _emit(
            on_progress,
            {
                "type": "agent_done",
                "agent_id": "assistant",
                **who,
                "role": role,
                "stage": "chat",
                "elapsed_seconds": elapsed,
                "first_token_seconds": ttft(),
                "model_warm": timing["model_warm"],
                "output": output,
                **extra,
            },
        )
        # Post-answer extras (suggestions, quiz, images): computed AFTER the answer is finalised and sent
        # as a SEPARATE event, so the message completes at once and the extras pop in a few seconds later.
        try:
            if "options" not in extra and len(output.strip()) > 120 and not live.is_stopped(job_id):
                post: dict[str, Any] = {}
                if config.suggestions_enabled:
                    sugg = generate_suggestions(query, output, client_chat=lambda **kw: client.chat(**kw),
                                                model=model, n=config.suggestions_count)
                    if sugg:
                        post["suggestions"] = sugg
                if _is_educational(query):
                    if config.quiz_enabled:
                        quiz = generate_quiz(query, output, client_chat=lambda **kw: client.chat(**kw),
                                             model=model, n=config.quiz_count)
                        if quiz:
                            post["quiz"] = quiz
                    if config.web_images_enabled:
                        from src.tools.web_images import gather_web_images

                        imgs = gather_web_images(query, job_id, config, n=config.web_images_count)
                        if imgs:
                            # a distinct key so the UI shows these as a small thumbnail strip, not the big gallery
                            post["web_images"] = [{"filename": i["filename"], "mime": i["mime"]} for i in imgs]
                if post:
                    _emit(on_progress, {"type": "answer_extras", "agent_id": "assistant", **post})
        except Exception as exc:  # noqa: BLE001 — extras are optional; never affect the emitted answer
            log.warning("post-answer extras failed: %s", exc)
    except WebSearchError as exc:
        # the message already says what to do (start the container, set WEB_SEARCH_URL)
        result.errors.append(str(exc))
        live.finish_agent(job_id, "assistant")
        _emit(on_progress, {"type": "agent_error", "agent_id": "assistant", "name": who["name"], "error": str(exc)})
    except LLMStopped as exc:
        # keep what was already written; the job itself is marked cancelled by the Stop endpoint
        result.stopped = True
        result.output = (exc.partial + "\n\n_— stopped —_").strip() if exc.partial else "_Stopped before the answer started._"
        live.finish_agent(job_id, "assistant")
        _emit(on_progress, {"type": "agent_done", "agent_id": "assistant", **who, "role": "Stopped",
                            "stage": "chat",
                            "elapsed_seconds": time.perf_counter() - started, "output": result.output})
    except Exception as exc:  # noqa: BLE001
        result.errors.append(str(exc))
        live.finish_agent(job_id, "assistant")
        _emit(
            on_progress,
            {
                "type": "agent_error",
                "agent_id": "assistant",
                "name": who["name"],
                "error": friendly_error(exc, config),
            },
        )

    result.total_seconds = time.perf_counter() - started
    # P0: one timing line per job, so speed work is driven by data, not guesses (doc/RESPONSE_QUALITY.md §5).
    log.info(
        "timing job=%s role=%s warm=%s ttft=%s total=%.2fs",
        job_id, role, timing["model_warm"], ttft(), round(result.total_seconds, 2),
    )
    _emit(
        on_progress,
        {
            "type": "session_done",
            "total_seconds": result.total_seconds,
            "first_token_seconds": ttft(),
            "model_warm": timing["model_warm"],
            "errors": result.errors,
            "mode": "chat",
        },
    )
    return result
