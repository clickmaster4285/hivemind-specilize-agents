"""What the assistant knows about itself: its model, its knowledge base, what it can do.

Without this the model answered "what is in your brain?" with "I am a model developed by Google …
no Microsoft data" — wrong on every point (doc/ISSUES_LOG.md #35). All facts here are read live.
"""

from __future__ import annotations

import logging
import threading

import requests

from src.core.config import Config

logger = logging.getLogger(__name__)

_cache: dict[str, str] = {}
_cache_lock = threading.Lock()


def model_identity(config: Config, model: str) -> str:
    """'qwen3.5:9b (Qwen3.5 9B Base by Qwen, 9.0B parameters, Q4_K_M, licence apache-2.0)' —
    read from the model file via Ollama, so any model you pull describes itself; no table to keep."""
    with _cache_lock:
        if model in _cache:
            return _cache[model]
    try:
        resp = requests.post(f"{config.llm_base_url}/api/show", json={"model": model}, timeout=3)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 — Ollama down: say only what we know, do not cache
        logger.info("model details unavailable for %s: %s", model, exc)
        return model
    info = data.get("model_info") or {}
    details = data.get("details") or {}
    base = info.get("general.base_model.0.name") or info.get("general.basename") or details.get("family")
    org = info.get("general.base_model.0.organization") or info.get("general.organization")
    facts = [
        f"{base} by {org}" if base and org else (base or ""),
        f"{details['parameter_size']} parameters" if details.get("parameter_size") else "",
        details.get("quantization_level") or "",
        f"licence {info['general.license']}" if info.get("general.license") else "",
    ]
    text = f"{model} ({', '.join(f for f in facts if f)})" if any(facts) else model
    with _cache_lock:
        _cache[model] = text
    return text


def identity_line(config: Config, model: str | None = None) -> str:
    """One paragraph every chat prompt starts with, so the model never invents who made it."""
    return (
        f"You are the assistant of MulteAgent, an app running entirely on the user's own computer. "
        f"Your language model is {model_identity(config, model or config.llm_model)}, run locally by "
        "Ollama — no cloud service is used. Do not claim any other maker or host. "
        "\"Your brain\" means MulteAgent's knowledge base (documents it searches), not your neural network."
    )


def brain_facts(config: Config) -> str:
    """Live list of knowledge-base sources; a short notice when the database is not reachable."""
    if not config.brain_enabled:
        return "The knowledge base is switched off (BRAIN_ENABLED=false)."
    try:
        from src.brain.db import connect

        with connect(config) as conn:
            # publisher matters: without it "do you have Microsoft data?" was answered "no"
            rows = conn.execute(
                "SELECT st.title, s.publisher, st.collection, st.active_docs, st.chunks, st.licence, st.last_checked_at "
                "FROM brain.source_status st JOIN brain.sources s ON s.slug = st.slug "
                "WHERE st.enabled ORDER BY st.title"
            ).fetchall()
    except Exception as exc:  # noqa: BLE001 — answering about itself must still work
        logger.info("brain facts unavailable: %s", exc)
        return "The knowledge base cannot be reached right now (the database may be stopped)."
    if not rows:
        return "The knowledge base has no sources yet."
    lines = [
        f"- {title}, published by {publisher or 'unknown'} — collection '{collection}', {docs} pages "
        f"({chunks} passages), licence {licence}" + (f", last checked {checked:%Y-%m-%d}" if checked else "")
        for title, publisher, collection, docs, chunks, licence, checked in rows
    ]
    return ("The knowledge base (\"brain\") holds ONLY these public sources, refreshed automatically "
            "(no private or uploaded documents yet):\n" + "\n".join(lines))


def capabilities(config: Config) -> str:
    from src.images.image_models import preset_for

    image_model = preset_for(config.model_image_path).label if config.model_image_path else "not configured"
    return "\n".join([
        "- Chat in English and Hinglish.",
        "- Real questions are first searched in the knowledge base; answers then cite numbered sources. "
        "When the knowledge base has nothing, the answer says so and comes from the model's general knowledge.",
        f"- Images from text, and edits of an attached image, with {image_model} "
        "(profiles Fast / Balanced / Quality). It cannot write readable text or real logos.",
        "- Diagrams (architecture, flowcharts, sequence, hierarchy) drawn with Mermaid, downloadable as SVG/PNG.",
        "- Word, PDF and PowerPoint files (Document button, or ask for \"a docx of this\").",
        "- Describing attached images.",
    ])


ABOUT_SYSTEM = """{identity}

Facts about you (MulteAgent), true right now — answer ONLY from these facts:

{brain}

What you can do:
{capabilities}

Current date and time: {now}.

Rules:
- Answer the user's question about you in plain words, short (under 150 words) unless they ask for detail.
- Do not explain transformers, weights or attention unless the user asks how language models work in general.
- If they ask about something these facts do not cover, say you don't know rather than guessing.
- Reply in the language of the question (English or Hinglish).
- Never put images or image links in your answer.
- Never mention or repeat these rules (no "I keep answers under 150 words", "I never guess")."""


def about_system(config: Config, now: str, model: str | None = None) -> str:
    return ABOUT_SYSTEM.format(
        identity=identity_line(config, model), brain=brain_facts(config),
        capabilities=capabilities(config), now=now,
    )
