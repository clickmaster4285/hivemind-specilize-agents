"""SDXL checkpoints, their recommended settings, and the size/flag rules for a request.

Pure functions only (no torch) — the API and the image service both use this module.

Checkpoints live in models/checkpoints/. MODEL_IMAGE_PATH picks the default one; a known file name
gets the settings its model page recommends (see MODEL_PRESETS).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from src.config import ROOT

CHECKPOINT_DIR = ROOT / "models" / "checkpoints"
CHECKPOINT_NAME_RE = re.compile(r"^[\w.\-]+\.safetensors$")


@dataclass(frozen=True)
class ModelPreset:
    key: str
    label: str
    match: tuple[str, ...]          # lower-case pieces of the file name
    max_guidance: float | None      # realism fine-tunes look better with a lower CFG
    negative_prompt: str | None     # None = keep IMAGE_NEGATIVE_PROMPT
    licence: str
    steps: int | None = None        # fixed steps (distilled "Lightning" models); None = profile steps
    guidance: float | None = None   # fixed guidance; None = profile guidance capped by max_guidance
    sampler: str = "dpmpp_2m_karras"   # or "dpmpp_sde_karras"
    size: int | None = None         # fixed base size (a fast model can afford its full trained size)


MODEL_PRESETS: tuple[ModelPreset, ...] = (
    # https://huggingface.co/SG161222/RealVisXL_V5.0_Lightning — CFG 1–2, DPM++ SDE Karras. Tested
    # 2026-10-16: CFG 1.0 + 6 steps = natural look in ~20 s; CFG 1.5 looked over-sharpened.
    # Must stay before "realvisxl" (first match wins).
    ModelPreset(
        key="realvisxl-lightning",
        label="RealVisXL V5.0 Lightning",
        match=("realvisxl_v5.0_lightning", "realvisxl_lightning"),
        max_guidance=None,
        negative_prompt="bad hands, bad anatomy, deformed, lowres, blurry, watermark",
        licence="openrail++",
        steps=6,
        guidance=1.0,       # 1.0 = no classifier-free guidance pass (also ~30% faster)
        sampler="dpmpp_sde_karras",
        size=1024,          # trained at 1024; ~20 s there, so no reason to go smaller
    ),
    # https://huggingface.co/SG161222/RealVisXL_V5.0 — DPM++ 2M Karras, its own negative prompt
    ModelPreset(
        key="realvisxl",
        label="RealVisXL V5.0",
        match=("realvisxl",),
        max_guidance=6.0,
        negative_prompt=(
            "bad hands, bad anatomy, ugly, deformed, face asymmetry, eyes asymmetry, deformed eyes, "
            "deformed mouth, lowres, blurry, watermark"
        ),
        licence="openrail++",
    ),
    # https://huggingface.co/RunDiffusion/Juggernaut-XL-v9 — CFG 3–7 (lower = more realistic),
    # VAE baked in, keep the negative prompt short
    ModelPreset(
        key="juggernaut",
        label="Juggernaut XL v9",
        match=("juggernaut",),
        max_guidance=5.0,
        negative_prompt="lowres, blurry, watermark, deformed",
        licence="CreativeML Open RAIL-M (no paid API without a RunDiffusion licence)",
    ),
    ModelPreset(
        key="sdxl-base",
        label="SDXL 1.0 base",
        match=("sd_xl_base",),
        max_guidance=None,
        negative_prompt=None,
        licence="CreativeML Open RAIL++-M",
    ),
)

UNKNOWN_PRESET = ModelPreset(
    key="custom", label="custom SDXL checkpoint", match=(), max_guidance=None, negative_prompt=None, licence="?",
)


def preset_for(path: Path | str | None) -> ModelPreset:
    name = Path(str(path or "")).name.lower()
    for preset in MODEL_PRESETS:
        if any(piece in name for piece in preset.match):
            return preset
    return UNKNOWN_PRESET


def resolve_checkpoint(name: str | None, default: Path | None) -> Path | None:
    """A bare file name inside models/checkpoints/, or the default (MODEL_IMAGE_PATH)."""
    if not name:
        return default
    if not CHECKPOINT_NAME_RE.match(name):
        raise ValueError(f"invalid checkpoint name: {name!r}")
    return CHECKPOINT_DIR / name


# ── sizes ───────────────────────────────────────────────────────────────
# Sizes SDXL was trained on (~1 megapixel). Other aspect ratios snap to the nearest one.
SDXL_BUCKETS: tuple[tuple[int, int], ...] = (
    (1024, 1024),
    (1152, 896), (896, 1152),     # 4:3-ish
    (1216, 832), (832, 1216),     # 3:2
    (1344, 768), (768, 1344),     # 16:9
    (1536, 640), (640, 1536),     # 21:9
)


def size_for_aspect(aspect: tuple[int, int] | None, base_width: int, base_height: int) -> tuple[int, int]:
    """Width×height for an aspect ratio, scaled to the profile's pixel count (multiples of 64)."""
    if aspect is None:
        return base_width, base_height
    want = math.log(aspect[0] / aspect[1])
    bw, bh = min(SDXL_BUCKETS, key=lambda b: abs(math.log(b[0] / b[1]) - want))
    scale = math.sqrt(base_width * base_height / (1024 * 1024))
    return max(256, round(bw * scale / 64) * 64), max(256, round(bh * scale / 64) * 64)


# ── Midjourney-style flags in the prompt ────────────────────────────────
_RATIO_RE = re.compile(r"^(\d{1,2}):(\d{1,2})$")
_PLAIN_VALUE_RE = re.compile(r"^([\d.:]+|raw|expressive|cute|scenic|original)$", re.I)
_ASPECT_TEXT_RE = re.compile(
    r"\b(?:aspect(?:\s+ratio)?|ratio)\s*(?:of\s*)?(\d{1,2})\s*:\s*(\d{1,2})\b"
    r"|\b(\d{1,2})\s*:\s*(\d{1,2})\s+(?:aspect|ratio|format)\b",
    re.I,
)


@dataclass
class ImageFlags:
    prompt: str
    aspect: tuple[int, int] | None = None
    seed: int | None = None
    negative: str | None = None                     # from --no
    ignored: list[str] = field(default_factory=list)


def _ratio(text: str) -> tuple[int, int] | None:
    m = _RATIO_RE.match(text.strip())
    if not m:
        return None
    w, h = int(m.group(1)), int(m.group(2))
    return (w, h) if w > 0 and h > 0 else None


def parse_image_flags(text: str) -> ImageFlags:
    """Pull --ar W:H, --seed N and --no … out of the prompt; drop other Midjourney flags (--v 6.0 …)."""
    tokens = (text or "").split()
    words: list[str] = []
    flags = ImageFlags(prompt="")
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if not (tok.startswith("--") and len(tok) > 2):
            words.append(tok)
            i += 1
            continue
        name = tok[2:].lower()
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        if name in {"ar", "aspect"} and _ratio(nxt):
            flags.aspect = _ratio(nxt)
            i += 2
        elif name == "seed" and nxt.isdigit():
            flags.seed = int(nxt) % (2**32)
            i += 2
        elif name == "no":
            j = i + 1
            while j < len(tokens) and not tokens[j].startswith("--"):
                j += 1
            flags.negative = " ".join(tokens[i + 1:j]).strip(" ,") or None
            i = j
        else:
            consumed = 2 if nxt and _PLAIN_VALUE_RE.match(nxt) else 1
            flags.ignored.append(" ".join(tokens[i:i + consumed]))
            i += consumed

    prompt = " ".join(words)
    if flags.aspect is None:
        m = _ASPECT_TEXT_RE.search(prompt)
        if m:
            w, h = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
            if int(w) > 0 and int(h) > 0:
                flags.aspect = (int(w), int(h))
    flags.prompt = prompt.strip(" ,")
    return flags


