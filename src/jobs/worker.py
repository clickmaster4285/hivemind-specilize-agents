"""Background job worker (in-process or standalone)."""

from __future__ import annotations

import json
import threading
from typing import Any

from src.agentic.runtime import run_agentic
from src.brain.answer import should_use_brain
from src.core.conversation import normalize_conversation
from src.jobs.chat_runner import run_chat
from src.core.clock import set_time_zone
from src.tools.diagram_gen import run_diagram
from src.servers.net_scan import run_network_scan
from src.servers.security_scan import run_security_check
from src.servers.linux_audit import run_server_audit
from src.servers.server_ops import run_server_diagnose, run_server_operate
from src.servers.host_check import run_host_check
from src.llm.assistant import decide
from src.llm.specialists import resolve as resolve_specialist
from src.tools.weather import run_weather
from src.jobs import live
from src.images.image_gen import run_image_gen
from src.tools.attachments import load_b64_images
from src.core.config import Config
from src.core.db import session_scope
from src.jobs.jobs import append_event, claim_next_job, get_job, set_job_status

_worker_thread: threading.Thread | None = None
_stop = threading.Event()


# tools that run on their own: (query, job_id, config, on_progress, conversation, **tool_args) -> (output, errors, s)
DIRECT_TOOLS = {"network_scan": run_network_scan, "security_check": run_security_check,
                "server_audit": run_server_audit, "server_diagnose": run_server_diagnose,
                "server_operate": run_server_operate, "host_check": run_host_check,
                "weather": run_weather, "diagram": run_diagram}
# tools only admins may run (doc/SECURITY_TOOLS_DECISION.md §2): they probe the local network in depth.
# server_audit / server_diagnose need a stored server login, so they are already gated by who holds the
# credentials (SERVER_ACCESS_DECISION.md §8) — not listed here.
ADMIN_TOOLS = frozenset({"security_check"})


def _is_admin(config: Config, user_id: int | None) -> bool:
    from src.core.models_db import User

    if user_id is None:
        return False
    with session_scope(config) as session:
        user = session.get(User, user_id)
        return bool(user and user.role == "admin")


def _refuse(config: Config, job_id: str, query: str, on_progress, tool: str) -> None:
    """A non-admin asked for an admin-only tool: say so in the chat, run nothing."""
    text = (f"The **{tool.replace('_', ' ')}** tool can only be run by an administrator, because it probes "
            "other machines in depth. Ask an admin to run it, or use the network scan to list devices.")
    agent = {"agent_id": "assistant", "name": "Assistant", "role": "Not allowed", "stage": "chat", "accent": "#c4a35a"}
    on_progress({"type": "session_start", "mode": "chat", "query": query})
    on_progress({"type": "agent_start", **agent})
    on_progress({"type": "agent_done", **agent, "elapsed_seconds": 0, "output": text})
    on_progress({"type": "session_done", "total_seconds": 0, "errors": [], "mode": "chat"})
    _finish(config, job_id, [])


def _finish(config: Config, job_id: str, errors: list[str], *, failed: bool | None = None) -> None:
    """Final job status (unless the user stopped it): failed when there are errors, else succeeded."""
    with session_scope(config) as session:
        current = get_job(session, job_id)
        if current is None or current.status == "cancelled":
            return
        bad = bool(errors) if failed is None else failed
        set_job_status(session, job_id, "failed" if bad else "succeeded", error="; ".join(errors) if errors else None)


def _is_cancelled(job_id: str) -> bool:
    with session_scope() as session:
        job = get_job(session, job_id)
        return bool(job and job.status == "cancelled")


def process_job(job_id: str, config: Config | None = None) -> None:
    config = config or Config.from_env()
    with session_scope(config) as session:
        job = get_job(session, job_id)
        if job is None:
            return
        mode = job.mode
        query = job.query
        user_id = job.user_id
        payload = json.loads(job.payload_json or "{}")
        model_plan = json.loads(job.model_plan_json or "{}")

    # After Stop the job is "cancelled", but the partly written answer must still be saved
    # (chat history), so the closing events get through; everything else is dropped.
    keep_after_stop = {"agent_done", "session_done"}

    def on_progress(event: dict[str, Any]) -> None:
        closing = event.get("type") in keep_after_stop
        if not closing and _is_cancelled(job_id):
            return
        with session_scope(config) as session:
            current = get_job(session, job_id)
            if current is None or (current.status == "cancelled" and not closing):
                return
            if event.get("type") == "artifact_ready":
                payload_now = json.loads(current.payload_json or "{}")
                arts = payload_now.get("attachments")
                if not isinstance(arts, list):
                    arts = []
                entry = {
                    "filename": event.get("filename"),
                    "mime": event.get("mime"),
                    "relpath": event.get("relpath"),
                    "bytes": event.get("bytes"),
                    "kind": event.get("kind") or "document",
                }
                arts = [a for a in arts if a.get("filename") != entry.get("filename")]
                arts.append(entry)
                payload_now["attachments"] = arts
                current.payload_json = json.dumps(payload_now)
            append_event(session, job_id, event)

    try:
        if _is_cancelled(job_id):
            return

        route = payload.get("route") if isinstance(payload.get("route"), dict) else None
        if route:
            on_progress({"type": "route_decided", **route})

        attachments = payload.get("attachments") if isinstance(payload.get("attachments"), list) else []
        has_images = bool(payload.get("has_images") or attachments)
        images = load_b64_images(config, attachments) if attachments else []
        conversation = normalize_conversation(payload.get("conversation"))
        intent = (route or {}).get("intent")
        # every prompt of this job tells the time in the user's own time zone (sent by the browser)
        set_time_zone(payload.get("client_time_zone"))

        # Direct answer: picking an approach card answers from the model, skipping tool/web routing.
        if bool(payload.get("direct")) and not (has_images or images):
            specialist, how = resolve_specialist(payload.get("specialist"), query, config=config,
                                                 conversation=conversation)
            if specialist is not None:
                on_progress({"type": "specialist_chosen", "id": specialist.id, "name": specialist.name,
                             "accent": specialist.accent, "how": how})
            result = run_chat(
                query, config=config, model_plan=model_plan, on_progress=on_progress, images=[],
                conversation=conversation, account_id=str(user_id) if user_id is not None else None,
                job_id=job_id, specialist=specialist,
            )
            _finish(config, job_id, result.errors, failed=bool(result.errors and not result.output))
            return

        # Deep research toggle: skip tool routing and research the topic across many sources (text-only).
        if str(payload.get("depth") or "").strip().lower() == "deep" and not (has_images or images):
            specialist, how = resolve_specialist(payload.get("specialist"), query, config=config,
                                                 conversation=conversation)
            if specialist is not None:
                on_progress({"type": "specialist_chosen", "id": specialist.id, "name": specialist.name,
                             "accent": specialist.accent, "how": how})
            result = run_chat(
                query, config=config, model_plan=model_plan, on_progress=on_progress, images=[],
                conversation=conversation, account_id=str(user_id) if user_id is not None else None,
                job_id=job_id, deep=True, specialist=specialist,
            )
            _finish(config, job_id, result.errors, failed=bool(result.errors and not result.output))
            return

        # Options toggle: offer a few candidate approaches to pick from, instead of one answer (text-only).
        if bool(payload.get("options")) and not (has_images or images):
            specialist, how = resolve_specialist(payload.get("specialist"), query, config=config,
                                                 conversation=conversation)
            if specialist is not None:
                on_progress({"type": "specialist_chosen", "id": specialist.id, "name": specialist.name,
                             "accent": specialist.accent, "how": how})
            result = run_chat(
                query, config=config, model_plan=model_plan, on_progress=on_progress, images=[],
                conversation=conversation, account_id=str(user_id) if user_id is not None else None,
                job_id=job_id, options=True, specialist=specialist,
            )
            _finish(config, job_id, result.errors, failed=bool(result.errors and not result.output))
            return

        tool_args: dict[str, Any] = {}
        specialist = None
        if intent == "auto":
            # which specialist answers (src/specialists.py): pinned in the chat bar, or picked by the model
            specialist, how = resolve_specialist(payload.get("specialist"), query, config=config,
                                                 conversation=conversation)
            if specialist is not None:
                on_progress({"type": "specialist_chosen", "id": specialist.id, "name": specialist.name,
                             "accent": specialist.accent, "how": how})
            # the model answers directly (streamed, done) or picks a tool with its arguments
            decision = decide(query, job_id=job_id, config=config, conversation=conversation,
                              on_progress=on_progress, specialist=specialist)
            if decision.answered:
                _finish(config, job_id, decision.errors)
                return
            intent, tool_args = decision.intent, decision.args
            on_progress({"type": "tool_chosen", "tool": decision.tool, "args": tool_args})
            if intent == "doc_gen":
                mode = "agentic"

        runner = DIRECT_TOOLS.get(intent)
        if runner is not None and intent in ADMIN_TOOLS and not _is_admin(config, user_id):
            _refuse(config, job_id, query, on_progress, intent)
            return
        if runner is not None:
            # user_id: tools that use the session's server logins (src/server_vault.py) find this user's only
            tool_args = {k: v for k, v in tool_args.items() if k != "user_id"}     # never from the model
            _output, errors, _elapsed = runner(query, job_id=job_id, config=config, on_progress=on_progress,
                                               conversation=conversation, user_id=user_id, **tool_args)
            _finish(config, job_id, errors)
            return

        if intent == "image_gen":
            img_cfg = config
            try:
                img_cfg = config.with_image_overrides(
                    profile=str(payload.get("image_profile") or "") or None,
                    strength=(
                        float(payload["image_strength"])
                        if payload.get("image_strength") is not None
                        else None
                    ),
                    steps=(
                        int(payload["image_steps"])
                        if payload.get("image_steps") is not None
                        else None
                    ),
                    guidance=(
                        float(payload["image_guidance"])
                        if payload.get("image_guidance") is not None
                        else None
                    ),
                )
            except (TypeError, ValueError):
                img_cfg = config

            def _still_running() -> bool:
                return not _is_cancelled(job_id)

            _output, saved, errors, _elapsed = run_image_gen(
                query,
                job_id=job_id,
                config=img_cfg,
                model_plan=model_plan,
                on_progress=on_progress,
                attachments=attachments,
                image_mode=(route or {}).get("image_mode"),
                should_continue=_still_running,
                checkpoint=str(payload.get("image_checkpoint") or "") or None,
            )
            with session_scope(config) as session:
                current = get_job(session, job_id)
                if current is None or current.status == "cancelled":
                    return
                payload_now = json.loads(current.payload_json or "{}")
                if saved:
                    existing = payload_now.get("attachments") if isinstance(payload_now.get("attachments"), list) else []
                    payload_now["attachments"] = existing + saved
                    payload_now["generated"] = saved
                    current.payload_json = json.dumps(payload_now)
                status = "failed" if errors and not saved else "succeeded"
                set_job_status(
                    session,
                    job_id,
                    status,
                    error="; ".join(errors) if errors else None,
                )
            return

        if mode == "chat":
            use_brain = config.brain_enabled and (intent == "kb" or should_use_brain(
                query,
                intent=intent,
                reason=(route or {}).get("reason"),
                has_images=bool(has_images or images),
            ))
            result = run_chat(
                query,
                config=config,
                model_plan=model_plan,
                on_progress=on_progress,
                images=images if (has_images or images) else [],
                conversation=conversation,
                use_brain=use_brain,
                account_id=str(user_id) if user_id is not None else None,
                job_id=job_id,
                about=intent == "about",
                web_search=intent == "web_search",
                specialist=specialist,
            )
            _finish(config, job_id, result.errors, failed=bool(result.errors and not result.output))
            return

        if mode == "agentic":
            result = run_agentic(
                query,
                job_id=job_id,
                config=config,
                model_plan=model_plan,
                conversation=conversation,
                on_progress=on_progress,
            )
            with session_scope(config) as session:
                if get_job(session, job_id) and get_job(session, job_id).status == "cancelled":
                    return
                # Emit completion event before terminal status so SSE clients drain it.
                append_event(
                    session,
                    job_id,
                    {
                        "type": "session_done",
                        "total_seconds": result.total_seconds,
                        "log_path": "",
                        "errors": result.errors,
                    },
                )
                set_job_status(session, job_id, "succeeded")
            return

        # jobs queued before the debate was removed (2026-10-17) — they are not run
        _finish(config, job_id, [f"mode '{mode}' no longer exists — ask again"], failed=True)
    except Exception as exc:  # noqa: BLE001
        with session_scope(config) as session:
            current = get_job(session, job_id)
            if current and current.status != "cancelled":
                set_job_status(session, job_id, "failed", error=str(exc))
                append_event(session, job_id, {"type": "fatal", "error": str(exc)})
    finally:
        live.clear(job_id)


def worker_loop(config: Config | None = None, poll_seconds: float = 1.0) -> None:
    config = config or Config.from_env()
    while not _stop.is_set():
        job_id = None
        try:
            with session_scope(config) as session:
                job = claim_next_job(session)
                if job is not None:
                    job_id = job.id
        except Exception:
            job_id = None

        if job_id:
            process_job(job_id, config)
        else:
            _stop.wait(poll_seconds)


def start_inprocess_worker(config: Config | None = None) -> None:
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        return
    _stop.clear()
    _worker_thread = threading.Thread(
        target=worker_loop,
        kwargs={"config": config or Config.from_env()},
        name="multeagent-worker",
        daemon=True,
    )
    _worker_thread.start()


