"""Prompt preparation for SDXL.

polish_prompt  — fast local cleanup (no LLM), used for edits and as the fallback.
rewrite_prompt — the chat model turns a request into a concrete, visual SDXL prompt and picks the
                 aspect ratio. Vague wording was the main cause of bad images (doc/ISSUES_LOG.md #21).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from src.core.config import Config

logger = logging.getLogger(__name__)

_PREFIX_RE = re.compile(
    r"^(please\s+)?("
    r"create|generate|draw|paint|render|make|design|imagine|regenerate"
    r")(\s+(me|an?|the))?\s+"
    r"(an?\s+)?(image|picture|photo|illustration|artwork|drawing)\s+(of|with|for)?\s*",
    re.I,
)
_THIS_RE = re.compile(
    r"\b(of\s+this|from\s+this|based\s+on\s+this|using\s+this)\b"
    r"(\s+(image|picture|photo|screenshot))?",
    re.I,
)
_NOISE_RE = re.compile(
    r"\b(then\s+regenerate(\s+image)?|don'?t\s+change\s+anything\s+else|"
    r"and\s+then\s+regenerate(\s+the)?\s*(image)?)\b",
    re.I,
)
_RICH_TAGS = (
    "photorealistic",
    "cinematic",
    "8k",
    "4k",
    "highly detailed",
    "masterpiece",
    "octane",
    "unreal engine",
    "volumetric",
    "sharp focus",
)


def should_polish(query: str, *, img2img: bool = False) -> bool:
    """Skip enrichment when the user already wrote a rich SDXL-style prompt."""
    text = (query or "").strip()
    if not text:
        return False
    if img2img:
        return True  # always add keep-layout hints for edits
    lower = text.lower()
    rich_hits = sum(1 for t in _RICH_TAGS if t in lower)
    if rich_hits >= 2 and len(text) >= 80:
        return False
    if len(text) >= 220:
        return False
    return True


def polish_prompt(query: str, *, img2img: bool = False) -> str:
    """Turn chat phrasing into a tighter SDXL prompt without calling an LLM."""
    text = (query or "").strip()
    if not text:
        return "high quality detailed image"
    text = _PREFIX_RE.sub("", text).strip(" ,.-")
    text = _THIS_RE.sub("", text)
    text = _NOISE_RE.sub("", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" ,.-")
    if not text:
        text = "high quality detailed image"

    lower = text.lower()
    if img2img:
        if "same" not in lower and "keep" not in lower:
            text = (
                f"{text}, same composition and layout as the reference image, "
                "only apply the requested changes"
            )
    else:
        if len(text) < 120 and not any(t in lower for t in _RICH_TAGS):
            text = f"{text}, highly detailed, sharp focus, natural lighting"

    return text[:800]


# ── LLM rewrite ─────────────────────────────────────────────────────────
REWRITE_SYSTEM = """You write prompts for Stable Diffusion XL, a photo-realistic text-to-image model.
Rewrite the user's request as ONE image prompt.

Rules:
- Keep every subject, detail and constraint the user asked for. Do not add new main subjects.
- A long request will not fit: keep the main subject and the most important visible elements first,
  drop minor ones, and drop words about text, logos, labels, dimensions and angles (the model cannot
  draw them; leave empty space instead if the user wants room for text).
- Be concrete and visual: subject and appearance, action or pose, setting, lighting, camera and
  framing, style. Describe what is VISIBLE, not names of ideas or meanings.
- Replace vague or ambiguous references with what they look like (a character's "iconic look",
  a word with two meanings, "make it beautiful").
- The user may write in Hinglish or another language: always write the prompt in English.
- Default style is a realistic photograph, unless the user asks for an illustration, painting,
  3D render, anime, logo, icon, etc.
- SDXL cannot render readable text: only keep text the user explicitly asked for, max 3 words in quotes.
- Real-world scale: "small house" is a small real house. Never write "tiny", "miniature" or
  "diorama" unless the user asks for a miniature.
- 25-50 words (SDXL reads only the first ~75 words-pieces), comma-separated phrases, most important
  first. No filler like "8k, masterpiece, best quality". Do not put the aspect ratio in the prompt.
- Pick the aspect ratio: one of 1:1, 4:3, 3:4, 3:2, 2:3, 16:9, 9:16 (a person's portrait 2:3,
  landscapes and wide scenes 3:2 or 16:9, products and icons 1:1, phone wallpaper 9:16).

Reply with JSON only: {"prompt": "...", "aspect": "W:H"}"""

ASPECTS = {"1:1", "4:3", "3:4", "3:2", "2:3", "16:9", "9:16"}
_JSON_RE = re.compile(r"\{.*\}", re.S)
_TRAILING_RATIO_RE = re.compile(
    r"[,\s]*\b(?:aspect(?:\s+ratio)?\s*)?\d{1,2}\s*:\s*\d{1,2}(?:\s+aspect(?:\s+ratio)?)?\s*$", re.I,
)
MAX_PROMPT_WORDS = 55   # SDXL's text encoder reads 77 tokens; ~55 English words fit

# Things SDXL-family models (RealVisXL included) cannot draw: readable text, real logos, exact sizes
TEXT_REQUEST_RE = re.compile(
    r"\b(text|logos?|labels?|captions?|titles?|written|lettering|font|words?|dimensions?|measurements?|"
    r"\d+\s*(ft|feet|foot|m|cm|mm|sq\s*ft)\b|\d+\s*degrees?|angled\s+at)\b|'[^']{3,}'|\"[^\"]{3,}\"",
    re.I,
)


def needs_rewrite(prompt: str) -> bool:
    """Short or plain prompts get detail; long ones get condensed (else SDXL silently cuts them)."""
    return should_polish(prompt) or len(prompt.split()) > MAX_PROMPT_WORDS


def fit_prompt(prompt: str, max_words: int = MAX_PROMPT_WORDS) -> str:
    """Keep whole comma-separated phrases from the start until the word budget is used."""
    if len(prompt.split()) <= max_words:
        return prompt
    kept: list[str] = []
    used = 0
    for phrase in (p.strip() for p in prompt.split(",")):
        n = len(phrase.split())
        if used + n > max_words:
            break
        kept.append(phrase)
        used += n
    return ", ".join(kept) or " ".join(prompt.split()[:max_words])


@dataclass(frozen=True)
class PromptRewrite:
    prompt: str
    aspect: tuple[int, int] | None


def parse_rewrite(raw: str) -> PromptRewrite | None:
    """Read the model's JSON reply; None when it is unusable."""
    m = _JSON_RE.search(raw or "")
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    prompt = str(data.get("prompt") or "").strip().strip('"').strip()
    prompt = fit_prompt(_TRAILING_RATIO_RE.sub("", prompt).strip(" ,"))   # drop "…, 2:3", cap length
    if len(prompt) < 10:
        return None
    aspect_s = str(data.get("aspect") or "").strip()
    aspect = tuple(int(x) for x in aspect_s.split(":")) if aspect_s in ASPECTS else None
    return PromptRewrite(prompt=prompt[:800], aspect=aspect)  # type: ignore[arg-type]


def rewrite_prompt(query: str, config: Config) -> PromptRewrite | None:
    """Ask the chat model for a better SDXL prompt. None on any failure (caller falls back)."""
    from src.llm.llm_client import LLMClient

    text = (query or "").strip()
    if not text:
        return None
    try:
        raw = LLMClient(config).chat(
            system=REWRITE_SYSTEM, user=text[:1500], temperature=0.3, max_retries=0,
        )
    except Exception as exc:  # noqa: BLE001 — a failed rewrite must never block the image
        logger.info("prompt rewrite skipped: %s", exc)
        return None
    return parse_rewrite(raw)
