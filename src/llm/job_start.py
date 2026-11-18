"""How a job starts, before the assistant sees it (src/jobs/jobs.py create_job).

A composer button (Describe, Generate, Edit, Inpaint, Diagram, Web, Document) decides by itself; an attached picture is
described or edited; Midjourney-style flags (--ar 16:9) are an image prompt; the agentic mode is the user's choice.
Everything else is intent "auto": the assistant picks a tool or answers (src/llm/assistant.py, src/llm/router.py).
The older word rules and the embedding ("semantic") router were removed 2026-11-18 (ISSUES_LOG O97): they only ran
with ASSISTANT_TOOLS=false.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from src.core.config import Config

MANUAL_MODES = frozenset({"agentic", "chat"})

IMAGE_GEN_RE = re.compile(
    r"\b(create|generate|draw|paint|render|make|design|imagine)\b.{0,60}\b"
    r"(image|picture|photo|illustration|artwork|drawing|logo|icon|poster)\b|"
    r"\b(image|picture|photo|illustration)\s+of\b|"
    r"\b(draw|sketch|paint|illustrate|visualize)\s+(me\s+)?(a|an|the)\b|"
    r"\b(show\s+me\s+a|generate\s+a)\b|"
    r"\btext[- ]to[- ]image\b|"
    r"\b(ui\s*/?\s*ux|ux\s*/?\s*ui|dashboard|mockup|wireframe|landing\s+page|"
    r"app\s+screen|mobile\s+ui|web\s+ui)\b|"
    r"\b(design|make|create|generate|render|build)\b.{0,80}\b"
    r"(dashboard|mockup|wireframe|landing\s+page|interface|layout|ui|ux)\b|"
    r"\b(photorealistic|photo[- ]realistic|cinematic|8k|4k|ultra[- ]detailed|"
    r"highly detailed|masterpiece|octane render|unreal engine|concept art|"
    r"portrait of|landscape of|anime style|pixel art)\b",
    re.I,
)

IMAGE_EDIT_RE = re.compile(
    r"\b(regenerate|remix|restyle|img2img|image[- ]to[- ]image|inpaint)\b|"
    r"\b(edit|modify|change|replace|swap|update)\b.{0,80}\b"
    r"(image|picture|photo|screenshot|this|it|text|label|title|logo)\b|"
    r"\b(only\s+change|change\s+.+\s+to\b|don'?t\s+change\s+anything\s+else|"
    r"keep\s+(the\s+)?(same|layout|rest)|based\s+on\s+this|"
    r"using\s+this\s+(image|picture|photo|screenshot))\b",
    re.I,
)

VISION_ASK_RE = re.compile(
    r"\b(what('s|\s+is|\s+are)|whats|describe|explain|summarize|read|ocr|"
    r"transcribe|analyze|identify|caption|look\s+at|tell\s+me\s+about)\b|"
    r"\b(in|on)\s+this\s+(image|picture|photo|screenshot)\b|"
    r"\b(this\s+image|this\s+picture|this\s+screenshot)\b",
    re.I,
)

FLAG_RE = re.compile(r"(^|\s)--(ar|aspect|v|seed|no|style|stylize|chaos|niji)\b", re.I)


@dataclass(frozen=True)
class RouteDecision:
    requested_mode: str
    resolved_mode: str
    intent: str
    reason: str
    used_llm: bool = False
    confidence: float = 1.0
    model: str | None = None
    image_mode: str | None = None  # txt2img | img2img | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "requested_mode": self.requested_mode,
            "resolved_mode": self.resolved_mode,
            "intent": self.intent,
            "reason": self.reason,
            "used_llm": self.used_llm,
            "confidence": self.confidence,
            "model": self.model,
            "image_mode": self.image_mode,
        }

    def with_model(self, model: str) -> RouteDecision:
        return RouteDecision(
            requested_mode=self.requested_mode,
            resolved_mode=self.resolved_mode,
            intent=self.intent,
            reason=f"{self.reason} ({model})",
            used_llm=self.used_llm,
            confidence=self.confidence,
            model=model,
            image_mode=self.image_mode,
        )


def _normalize_mode(mode: str | None) -> str:
    m = (mode or "auto").strip().lower()
    if m in MANUAL_MODES or m == "auto":
        return m
    return "auto"


def _image_gen_decision(
    requested: str,
    *,
    reason: str,
    image_mode: str,
    used_llm: bool = False,
    confidence: float = 1.0,
) -> RouteDecision:
    return RouteDecision(
        requested_mode=requested,
        resolved_mode="chat",
        intent="image_gen",
        reason=reason,
        used_llm=used_llm,
        confidence=confidence,
        image_mode=image_mode,
    )


def _route_with_images(text: str, requested: str, config: Config) -> RouteDecision:
    """Decide vision vs img2img vs txt2img when the user attached files."""
    wants_edit = bool(IMAGE_EDIT_RE.search(text) or IMAGE_GEN_RE.search(text))
    wants_vision = bool(VISION_ASK_RE.search(text))

    if wants_edit and not wants_vision:
        return _image_gen_decision(
            requested,
            reason="attachment + draw/edit -> img2img",
            image_mode="img2img",
        )
    if wants_edit and wants_vision:
        # "describe then regenerate" / mixed → prefer edit when regenerate/change present
        if IMAGE_EDIT_RE.search(text):
            return _image_gen_decision(
                requested,
                reason="attachment + mixed ask, edit wins -> img2img",
                image_mode="img2img",
            )
    if wants_vision or not text or text.lower() in {"what's in this image?", "whats in this image?"}:
        vision = (config.model_vision or "").strip() or None
        return RouteDecision(
            requested_mode=requested,
            resolved_mode="chat",
            intent="vision",
            reason="attachment + describe/read ask -> vision",
            used_llm=False,
            confidence=1.0,
            model=vision,
        )
    # Attached image + short vague edit like "make it darker"
    if len(text) < 160 and re.search(
        r"\b(make|change|turn|convert|update|fix|improve|brighter|darker|blue|red)\b",
        text,
        re.I,
    ):
        return _image_gen_decision(
            requested,
            reason="attachment + transform phrasing -> img2img",
            image_mode="img2img",
            confidence=0.85,
        )
    vision = (config.model_vision or "").strip() or None
    return RouteDecision(
        requested_mode=requested,
        resolved_mode="chat",
        intent="vision",
        reason="attachment default -> vision",
        used_llm=False,
        confidence=0.7,
        model=vision,
    )


def _diagram_decision(requested: str, reason: str) -> RouteDecision:
    return RouteDecision(requested, "chat", "diagram", reason)


def classify_query(
    query: str,
    *,
    requested_mode: str | None = "auto",
    config: Config | None = None,
    has_images: bool = False,
    force_intent: str | None = None,
) -> RouteDecision:
    """What starts the job: a composer button (force_intent), an attached picture, image-prompt flags or the
    agentic mode; anything else goes to the assistant, which picks a tool or answers (intent "auto")."""
    config = config or Config.from_env()
    requested = _normalize_mode(requested_mode)
    text = query.strip()
    forced = (force_intent or "").strip().lower()

    # Explicit UI actions bypass Auto guessing entirely.
    if forced in {"vision", "describe"}:
        vision = (config.model_vision or "").strip() or None
        return RouteDecision(
            requested_mode=requested,
            resolved_mode="chat",
            intent="vision",
            reason="forced describe/vision action",
            confidence=1.0,
            model=vision,
        )
    if forced in {"image_gen", "generate", "txt2img"}:
        return _image_gen_decision(
            requested,
            reason="forced generate/txt2img action",
            image_mode="txt2img",
        )
    if forced in {"edit", "img2img", "image_edit"}:
        return _image_gen_decision(
            requested,
            reason="forced edit/img2img action",
            image_mode="img2img",
        )
    if forced in {"inpaint", "image_inpaint"}:
        return _image_gen_decision(
            requested,
            reason="forced inpaint action",
            image_mode="inpaint",
        )
    if forced in {"diagram", "mermaid"}:
        return _diagram_decision(requested, "forced diagram action")
    if forced in {"web_search", "web", "search_web"}:
        return RouteDecision(
            requested_mode=requested,
            resolved_mode="chat",
            intent="web_search",
            reason="forced web search action",
            confidence=1.0,
        )
    if forced in {"doc_gen", "document", "docs"}:
        return RouteDecision(
            requested_mode=requested,
            resolved_mode="agentic",
            intent="doc_gen",
            reason="forced document generation action",
            confidence=1.0,
        )

    if has_images:
        return _route_with_images(text, requested, config)

    # Midjourney-style flags (--ar 16:9, --v 6) are a sign of an image prompt, not a meaning
    if FLAG_RE.search(text):
        return _image_gen_decision(requested, reason="image prompt flags -> txt2img", image_mode="txt2img")

    if requested == "agentic":
        return RouteDecision(requested, "agentic", "agentic", "agentic mode, chosen by you")
    # the assistant itself picks a tool or answers (src/llm/assistant.py decide), in the worker
    return RouteDecision(requested, "chat", "auto", "the assistant chooses (tool calling)")
