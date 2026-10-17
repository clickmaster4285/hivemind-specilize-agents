"""Fast intent router: decide chat vs agentic vs vision vs image gen vs diagram vs tools."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from src.config import Config

logger = logging.getLogger(__name__)

MANUAL_MODES = frozenset({"agentic", "chat"})
RESOLVED_MODES = frozenset({"chat", "agentic"})

GREETING_RE = re.compile(
    r"^(hi|hello|hey|howdy|yo|sup|hiya|good\s*(morning|afternoon|evening)|thanks|thank\s*you|"
    r"thx|ty|bye|goodbye|see\s*ya|ok|okay|k|cool|nice|great|awesome)[\s!.?]*$",
    re.I,
)
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
# Asking for prompt TEXT ("improve this prompt …") is chat, even when the prompt is full of image
# words like "highly detailed". Only "… and generate/draw it" means: also make the image.
PROMPT_TEXT_RE = re.compile(
    r"\b(improve|rewrite|refine|enhance|fix|polish|optimi[sz]e|correct|better|expand|shorten|translate|"
    r"write|give\s+me|suggest|make)\b.{0,40}\bprompts?\b|"
    r"\bprompts?\b.{0,30}\b(improve|better|rewrite|refine|enhance|ideas?|suggestions?)\b",
    re.I,
)
ALSO_GENERATE_RE = re.compile(
    r"\b(and|then|&)\s+(generate|create|draw|render|make|paint)\b(?!\s+(a\s+)?(better|new)\s+prompt)",
    re.I,
)
# Diagrams need exact labels, boxes and arrows: SDXL cannot do that, Mermaid can (src/diagram_gen.py).
_DIAGRAM_NOUN = (
    r"(diagram|flow\s*-?chart|schematic|mind\s*-?map|org(anization(al)?)?\s*chart|uml|"
    r"sequence\s+diagram|er\s+diagram|entity[- ]relationship|state\s+machine|gantt(\s+chart)?|"
    r"network\s+(topology|map|layout)|topology|data\s*flow|process\s+flow|workflow\s+chart)"
)
DIAGRAM_RE = re.compile(
    r"\b(create|generate|draw|make|design|build|sketch|show|give|prepare|render|visuali[sz]e|plot|map\s+out|"
    r"banao|bana\s+do|banaye)\b.{0,80}\b" + _DIAGRAM_NOUN + r"s?\b|"
    r"\b" + _DIAGRAM_NOUN + r"s?\s+(of|for|showing|banao|bana\s+do)\b|"
    r"^\s*" + _DIAGRAM_NOUN + r"s?\b",
    re.I,
)
# "create an image of … schematic": the user asked for a picture, so it stays an image
EXPLICIT_IMAGE_RE = re.compile(
    r"\b(image|picture|photo|photograph|rendering|render|illustration|artwork|wallpaper|thumbnail)\s+(of|for)\b|"
    r"\b(create|generate|make|draw|render|give\s+me)\s+(me\s+)?(an?\s+)?"
    r"(image|picture|photo|illustration|thumbnail|wallpaper)\b",
    re.I,
)
# Questions about the assistant itself: answered from live facts (src/about.py), not the knowledge
# base — "what is in your brain?" searched the documents, found nothing and then guessed "made by Google"
ABOUT_RE = re.compile(
    r"\b(you|your|yours|yourself|u|ur)\b.{0,50}\b(brain|knowledge(\s*base)?|data|database|sources?|documents?|"
    r"model|llm|trained|training|speciali[sz]\w*|capabilit\w*|features?|working|generat\w*\s+(the\s+)?(answer|response)s?|"
    r"using|made|built|created|developed|memory|remember|cutoff)\b|"
    r"\b(who|what)\s+(are|r)\s+(you|u)\b|\bwhat\s+can\s+(you|u)\s+do\b|\bhow\s+do\s+(you|u)\s+work\b|"
    r"\bmulte\s*-?\s*agent\b",
    re.I,
)
# Follow-up on a diagram the assistant just drew ("add a cache", "move the db to the right")
DIAGRAM_FOLLOWUP_RE = re.compile(
    r"\b(add|remove|delete|rename|change|move|connect|link|include|split|merge|replace|swap|update|fix|"
    r"redraw|instead|also|more\s+detail|simplify|label|arrow|box|node|layer|left|right|top|bottom)\b",
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
CREATIVE_RE = re.compile(
    r"\b(write|compose|draft|create|make|tell)\b.+\b(story|poem|joke|song|riddle|haiku|essay|"
    r"script|dialogue|fairy\s*tale|fable)\b|"
    r"\b(story|poem|joke|haiku)\b.+\b(about|on|with)\b|"
    r"\bwith\s+emojis?\b",
    re.I,
)
# Office/download asks — tolerate typos (formate, downlaoble) and "in a docx format".
DOC_GEN_RE = re.compile(
    r"\b(create|generate|make|write|export|draft|build|save|put|give|send|need|want)\b"
    r".{0,120}\b"
    r"(word|docx|pdf|powerpoint|pptx|slides?|deck|excel|xlsx|spreadsheet|"
    r"html|webpage|web\s*page|csv|zip\s+file|document|report)\b|"
    r"\b(as\s+a\s+|into\s+a\s+|to\s+a\s+|in\s+a\s+|in\s+)"
    r"(word|docx|pdf|pptx|powerpoint|excel|xlsx|html)\b|"
    r"\b(docx?|pdf|pptx?|xlsx?)\s*(formate?|file|doc)?\b|"
    r"\b(formate?|file)\s*(as\s+)?(docx?|pdf|pptx?|xlsx?|word)\b|"
    r"\b(downloadable|download|downloa?dable|downla[oa]?ble)\b.{0,60}\b"
    r"(pdf|docx?|pptx?|xlsx?|html|word|file|formate?)\b|"
    r"\b(microsoft\s+word|ms\s+word|word\s+doc(ument)?)\b",
    re.I,
)
AGENTIC_RE = re.compile(
    r"\b(research|browse|fetch|scrape|http|url|website|repo|codebase|file|artifact|"
    r"multi[- ]?step|use\s+tools?|look\s+up|investigate|crawl)\b|"
    r"https?://",
    re.I,
)
DECISION_RE = re.compile(
    r"\b(should\s+we|pros?\s+and\s+cons?|trade[- ]?offs?|risks?|feasib|decide|decision|"
    r"recommend(ation)?|strategy|invest|expand|launch|business|market|compliance|"
    r"consensus|evaluate|go\s*/\s*no[- ]?go|pilot\s+first)\b",
    re.I,
)

CLASSIFY_SYSTEM = """You classify user messages for a multi-agent system.
Reply with ONLY compact JSON, no markdown:
{"intent":"chat"|"creative"|"agentic"|"doc_gen"|"image_gen"|"vision","confidence":0.0-1.0,"image_mode":"txt2img"|"img2img"|null}

Rules:
- chat: greetings, thanks, chitchat, simple Q&A
- creative: stories, poems, jokes, fiction
- agentic: tools, research, browsing, multi-step repo/file work
- doc_gen: user wants a downloadable Word/PDF/PPT/Excel/HTML file (even with typos like formate/docx)
- image_gen: draw/generate/edit a picture (set image_mode=txt2img or img2img)
- vision: user attached/asks about an existing image to describe/read (not redraw)
Prefer chat when unsure and the message is short. Prefer heuristics-style certainty."""


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


def _heuristic(query: str) -> RouteDecision | None:
    text = query.strip()
    if not text:
        return RouteDecision("auto", "chat", "chat", "empty message -> chat")

    if not GREETING_RE.match(text) and len(text) <= 40 and re.search(r"\b(who|what)\s+(are|r)\s+(you|u)\b", text, re.I):
        return RouteDecision("auto", "chat", "about", "question about the assistant -> live facts")

    if GREETING_RE.match(text) or len(text) <= 12 and not DECISION_RE.search(text):
        return RouteDecision("auto", "chat", "chat", "greeting / short chitchat -> chat")

    if PROMPT_TEXT_RE.search(text) and not ALSO_GENERATE_RE.search(text):
        # creative (not "chat"): prompt writing should not go through the knowledge brain
        return RouteDecision("auto", "chat", "creative", "asks for prompt text, not an image -> chat")

    if DIAGRAM_RE.search(text) and not DOC_GEN_RE.search(text) and not EXPLICIT_IMAGE_RE.search(text):
        # before the image rules: "draw a flowchart" must not go to SDXL
        return _diagram_decision("auto", "diagram request -> Mermaid")

    if IMAGE_GEN_RE.search(text) or IMAGE_EDIT_RE.search(text) or EXPLICIT_IMAGE_RE.search(text):
        return _image_gen_decision(
            "auto",
            reason="image generation request -> txt2img",
            image_mode="txt2img",
        )

    if DOC_GEN_RE.search(text):
        return RouteDecision(
            "auto",
            "agentic",
            "doc_gen",
            "document/file generation -> agentic tools",
        )

    if ABOUT_RE.search(text) and len(text) < 200:
        return RouteDecision("auto", "chat", "about", "question about the assistant -> live facts")

    if CREATIVE_RE.search(text):
        return RouteDecision("auto", "chat", "creative", "creative writing -> chat")

    if AGENTIC_RE.search(text):
        return RouteDecision("auto", "agentic", "agentic", "tools / research signals -> agentic")

    if len(text) < 80 and not DECISION_RE.search(text) and not AGENTIC_RE.search(text):
        return RouteDecision("auto", "chat", "chat", "short question -> chat")

    return None


def _parse_llm_classify(raw: str) -> tuple[str, float, str | None] | None:
    text = raw.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    intent = str(data.get("intent") or "").strip().lower()
    try:
        conf = float(data.get("confidence", 0.5))
    except (TypeError, ValueError):
        conf = 0.5
    image_mode = data.get("image_mode")
    image_mode_s = str(image_mode).strip().lower() if image_mode else None
    if image_mode_s not in {"txt2img", "img2img"}:
        image_mode_s = None
    if intent == "creative":
        return "chat", conf, None
    if intent == "vision":
        return "vision", conf, None
    if intent in {"doc_gen", "document", "docs"}:
        return "doc_gen", conf, None
    if intent == "image_gen":
        return "image_gen", conf, image_mode_s or "txt2img"
    if intent in RESOLVED_MODES:
        return intent, conf, None
    return None


def _llm_classify(query: str, config: Config) -> RouteDecision | None:
    from src.llm_client import LLMClient, LLMError

    model = config.model_fast or config.llm_model
    try:
        raw = LLMClient(config).chat(
            system=CLASSIFY_SYSTEM,
            user=query[:1200],
            model=model,
            temperature=0.0,
            max_retries=0,
        )
    except (LLMError, Exception):
        return None
    parsed = _parse_llm_classify(raw)
    if not parsed:
        return None
    intent_mode, conf, image_mode = parsed
    if intent_mode == "image_gen":
        return _image_gen_decision(
            "auto",
            reason="fast-model classify -> image_gen",
            image_mode=image_mode or "txt2img",
            used_llm=True,
            confidence=conf,
        )
    if intent_mode == "vision":
        vision = (config.model_vision or "").strip() or None
        return RouteDecision(
            requested_mode="auto",
            resolved_mode="chat",
            intent="vision",
            reason="fast-model classify -> vision",
            used_llm=True,
            confidence=conf,
            model=vision,
        )
    if intent_mode == "doc_gen":
        return RouteDecision(
            requested_mode="auto",
            resolved_mode="agentic",
            intent="doc_gen",
            reason="fast-model classify -> doc_gen",
            used_llm=True,
            confidence=conf,
        )
    return RouteDecision(
        requested_mode="auto",
        resolved_mode=intent_mode,
        intent="creative" if "creative" in raw.lower() and intent_mode == "chat" else intent_mode,
        reason=f"fast-model classify -> {intent_mode}",
        used_llm=True,
        confidence=conf,
    )


def classify_query(
    query: str,
    *,
    requested_mode: str | None = "auto",
    config: Config | None = None,
    allow_llm: bool = True,
    has_images: bool = False,
    force_intent: str | None = None,
    after_diagram: bool = False,
) -> RouteDecision:
    """Resolve execution mode and optional image_mode (txt2img/img2img)."""
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

    if config.assistant_tools and requested in {"auto", "chat"}:
        # the assistant itself picks a tool or answers (src/assistant.py), in the worker
        return RouteDecision(requested, "chat", "auto", "the assistant chooses (tool calling)")

    decision = _semantic_decision(text, requested, config, after_diagram=after_diagram, allow_llm=allow_llm)
    if decision is None:
        # embedding model unreachable: the old word rules (frozen — extend data/router/intents.yaml instead)
        return _legacy_route(query, text, requested, config, after_diagram=after_diagram, allow_llm=allow_llm)
    if requested in MANUAL_MODES and requested != "chat":
        # a mode the user picked wins, except for asks that need their own tool
        bypass = {"image_gen", "diagram", "doc_gen", "web_search", "network_scan"}
        if decision.intent not in bypass:
            return RouteDecision(requested, requested, requested, "manual mode override", confidence=1.0)
    return decision


FLAG_RE = re.compile(r"(^|\s)--(ar|aspect|v|seed|no|style|stylize|chaos|niji)\b", re.I)


def _semantic_decision(
    text: str, requested: str, config: Config, *, after_diagram: bool, allow_llm: bool
) -> RouteDecision | None:
    """Intent by meaning (src/semantic_router.py, examples in data/router/intents.yaml); close calls
    are settled by the chat model. None only when the embedding model cannot be reached."""
    from src.semantic_router import get_router

    context = {"after_diagram"} if after_diagram else set()
    try:
        router = get_router(config)
        match = router.classify(text, context=context)
    except Exception:  # noqa: BLE001 — Ollama down / file problem: caller uses the offline rules
        logger.exception("semantic router unavailable")
        return None
    intent, used_llm = match.intent, False
    if intent is None and allow_llm:
        intent = router.ask_llm(text, match, context=context)
        used_llm = intent is not None
    if intent is None:
        intent = match.guess or "chat"       # the closest examples' intent, else a normal answer
    how = "LLM, close call" if used_llm else f"similarity {match.score:.2f}, lead {match.margin:.2f}"
    reason = f"semantic: {intent} ({how})"
    conf = match.score if not used_llm else 0.6
    if intent == "greeting":
        # "greeting" in the reason keeps the knowledge base out of small talk (should_use_brain)
        return RouteDecision(requested, "chat", "chat", f"greeting / small talk -> chat; {reason}", used_llm, conf)
    if intent == "web_search":
        # asked to search the internet: own path (src/web_search.py), never the knowledge base
        return RouteDecision(requested, "chat", "web_search", reason, used_llm, conf)
    if intent in {"diagram", "diagram_edit"}:
        return _diagram_decision(requested, reason)
    if intent == "network_scan":
        # a tool that acts (src/net_scan.py): runs directly, no LLM planning, exact table
        return RouteDecision(requested, "chat", "network_scan", reason, used_llm, conf)
    if intent == "image_gen":
        return _image_gen_decision(requested, reason=reason, image_mode="txt2img", used_llm=used_llm, confidence=conf)
    resolved = {"doc_gen": "agentic", "agentic": "agentic"}.get(intent, "chat")
    return RouteDecision(requested, resolved, intent, reason, used_llm, conf)


def _legacy_route(
    query: str, text: str, requested: str, config: Config, *, after_diagram: bool, allow_llm: bool
) -> RouteDecision:
    """The word rules used before the semantic router — only when embeddings are unavailable.
    Frozen: new phrasings and intents go into data/router/intents.yaml, not here."""
    if GREETING_RE.match(text) or (len(text) <= 8 and text.isalpha()):
        return RouteDecision(
            requested_mode=requested,
            resolved_mode="chat",
            intent="chat",
            reason="greeting always uses chat",
            used_llm=False,
            confidence=1.0,
        )

    if after_diagram and DIAGRAM_FOLLOWUP_RE.search(text) and not (
        IMAGE_GEN_RE.search(text) or DOC_GEN_RE.search(text)
    ):
        return _diagram_decision(requested, "change to the diagram just drawn -> Mermaid")

    if requested in MANUAL_MODES:
        if DIAGRAM_RE.search(text) and not DOC_GEN_RE.search(text) and not EXPLICIT_IMAGE_RE.search(text):
            return _diagram_decision(requested, "diagram request bypasses the chosen mode")
        if IMAGE_GEN_RE.search(text) or IMAGE_EDIT_RE.search(text):
            return _image_gen_decision(
                requested,
                reason="image generation bypasses the chosen mode",
                image_mode="txt2img",
            )
        if DOC_GEN_RE.search(text):
            return RouteDecision(
                requested_mode=requested,
                resolved_mode="agentic",
                intent="doc_gen",
                reason="document generation bypasses the chosen mode",
                confidence=1.0,
            )
        return RouteDecision(
            requested_mode=requested,
            resolved_mode=requested,
            intent=requested,
            reason="manual mode override",
            used_llm=False,
            confidence=1.0,
        )

    hit = _heuristic(query)
    if hit is not None:
        return hit

    # LLM classify only for long, decision/tool-shaped ambiguity — never for image cues.
    if (
        allow_llm
        and len(text) >= 80
        and (DECISION_RE.search(text) or AGENTIC_RE.search(text))
        and not IMAGE_GEN_RE.search(text)
        and not IMAGE_EDIT_RE.search(text)
    ):
        llm_hit = _llm_classify(query, config)
        if llm_hit is not None and llm_hit.confidence >= 0.6:
            return llm_hit

    return RouteDecision(
        "auto",
        "chat",
        "chat",
        "ambiguous query -> chat",
        used_llm=False,
        confidence=0.4,
    )
