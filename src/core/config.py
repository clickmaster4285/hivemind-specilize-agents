"""Runtime configuration loaded from environment / .env."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(ROOT / ".env")


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _loopback(url: str) -> str:
    """Use 127.0.0.1 instead of localhost: on Windows "localhost" tries IPv6 (::1) first and every
    request waits ~2 s before falling back to IPv4 (doc/ISSUES_LOG.md #27)."""
    return re.sub(r"^(\w+://)localhost(?=[:/]|$)", r"\g<1>127.0.0.1", url.strip())


def _optional_bool(name: str) -> bool | None:
    """Unset or blank -> None (leave the backend default); otherwise parse like _bool."""
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return None
    return raw.lower() in {"1", "true", "yes", "on"}


# size = side of a square image; other aspect ratios keep the same pixel count (src/image_models.py)
# hires = hires-fix scale (0 = off). fast and balanced use IMAGE_FAST_CHECKPOINT when it is set.
_PROFILE_DEFAULTS = {
    "fast": {"steps": 16, "guidance": 5.5, "strength": 0.42, "size": 768, "hires": 0.0},
    "balanced": {"steps": 20, "guidance": 6.5, "strength": 0.4, "size": 896, "hires": 1.5},
    "quality": {"steps": 28, "guidance": 7.0, "strength": 0.35, "size": 1024, "hires": 1.5},
}
FAST_CHECKPOINT_PROFILES = {"fast", "balanced"}


@dataclass(frozen=True)
class Config:
    llm_base_url: str
    llm_model: str
    temperature: float
    timeout_seconds: int
    llm_num_ctx: int
    log_dir: Path
    database_url: str
    jwt_secret: str
    jwt_expire_minutes: int
    llm_slots: int
    image_slots: int
    max_active_jobs_per_user: int
    queue_depth_limit: int
    overflow_wait_seconds: float
    model_fast: str
    model_strong: str
    model_cloud: str
    model_vision: str
    model_image: str
    model_image_path: Path | None
    image_width: int
    image_height: int
    image_steps: int
    image_guidance: float
    image_strength: float
    image_negative_prompt: str
    image_prompt_polish: bool
    image_profile: str
    image_warm_on_start: bool
    image_unload_ollama: bool
    image_tiny_vae: bool
    remote_llm_base_url: str
    remote_llm_api_key: str
    http_allowlist: str
    artifacts_dir: Path
    sso_enabled: bool
    default_admin_user: str
    default_admin_password: str
    api_host: str
    api_port: int
    # Ollama "think" flag: None = model default, False = answer directly, True = reason first
    llm_think: bool | None = None
    # Keep the chat model warm between messages (Ollama keep_alive, e.g. "30m" / "-1" / "0"); "" = Ollama
    # default (~5 min). Image jobs still unload it (IMAGE_UNLOAD_OLLAMA) to free VRAM for SDXL.
    llm_keep_alive: str = "30m"
    # Preload the chat model when the API starts, so the first message isn't a cold load (~10 s).
    llm_warm_on_start: bool = True
    # Knowledge brain (Postgres + pgvector). Owner URL runs migrations; the app connects as brain_app.
    brain_database_url: str = ""
    brain_app_password: str = ""
    model_embed: str = "qwen3-embedding:0.6b"
    # Chat answers real questions from the knowledge brain (with citations)
    brain_enabled: bool = True
    # No relevant passages: "general" = say so, then answer from the model's own knowledge;
    #                       "refuse"  = only say the knowledge base does not cover it
    brain_no_evidence: str = "general"
    # Automatic refresh of sources whose refresh_every has passed (runs inside the API process)
    brain_scheduler: bool = True
    brain_refresh_window: str = ""      # local hours "01-06" (night only); empty = any time
    brain_scheduler_minutes: int = 30   # how often to look for due sources
    # on demand: a source used by an answer and not checked for this many hours is refreshed right away
    brain_fresh_hours: float = 24.0
    # after a knowledge-base answer, check each cited sentence against its source (src/brain/verify.py)
    verify_answers: bool = True
    # security_check tool (src/security_scan.py): ports, services, certificates, this PC's firewall — admins only
    security_scan_enabled: bool = True
    # SDXL lives in system RAM (CPU offload): free it after this many idle minutes (0 = keep loaded)
    image_idle_unload_minutes: int = 10
    # SDXL runs in its own process (python -m src.images.image_service) so the API never holds the model
    image_service_port: int = 8790
    image_service_token: str = ""
    image_service_autostart: bool = True   # API starts the service on the first image request
    # Refuse to run SDXL on the CPU (≈4 min/image, float32 = double RAM) unless explicitly allowed
    image_allow_cpu: bool = False
    # Replacement VAE (Hugging Face id or local folder); "" = the checkpoint's own VAE.
    # The fp16-fix VAE decodes in float16 instead of upcasting to float32 (faster, less VRAM).
    image_vae: str = ""
    # Let the chat model rewrite image requests into concrete SDXL prompts (falls back to polish)
    image_prompt_rewrite: bool = True
    # Checkpoint (file name in models/checkpoints) the "fast" profile uses; "" = MODEL_IMAGE_PATH
    image_fast_checkpoint: str = ""
    # Hires-fix scale for text-to-image (set by the profile; 0 = off)
    image_hires: float = 0.0
    # Web search on request: SearXNG in Docker (127.0.0.1:8888). "" = not configured.
    web_search_url: str = ""
    # network_scan tool (src/net_scan.py): private networks only; off switch + size limit
    network_scan_enabled: bool = True
    network_scan_max_hosts: int = 1024
    # The model picks tools itself (native tool calling, src/assistant.py); false = example-based router
    assistant_tools: bool = True
    # Deep research mode (the Deep toggle): plan N focused sub-queries, search each, pool the best
    # passages, then write a thorough sourced report. Bounded so a deep answer stays ~1–2 min on local.
    deep_subqueries: int = 4
    deep_max_passages: int = 12
    # Options toggle: how many candidate approaches to offer before writing the full answer
    options_count: int = 3
    # Follow-up suggestions: clickable "ask this next" chips under each substantive answer
    suggestions_enabled: bool = True
    suggestions_count: int = 3
    # Quiz: a short multiple-choice self-check under educational answers
    quiz_enabled: bool = True
    quiz_count: int = 4
    # Web images: a small gallery of real photos (searched + proxied locally) atop educational answers
    web_images_enabled: bool = True
    web_images_count: int = 4

    @classmethod
    def from_env(cls) -> Config:
        data_dir = ROOT / "data"
        default_db = f"sqlite:///{(data_dir / 'multeagent.db').as_posix()}"
        default_ckpt = ROOT / "models" / "checkpoints" / "sd_xl_base_1.0.safetensors"
        raw_image_path = (os.getenv("MODEL_IMAGE_PATH") or "").strip()
        if raw_image_path:
            image_path = Path(raw_image_path)
        elif default_ckpt.is_file():
            image_path = default_ckpt
        else:
            image_path = None

        profile = (os.getenv("IMAGE_PROFILE") or "fast").strip().lower()
        if profile not in _PROFILE_DEFAULTS:
            profile = "fast"
        pdata = _PROFILE_DEFAULTS[profile]

        steps_raw = os.getenv("IMAGE_STEPS")
        guidance_raw = os.getenv("IMAGE_GUIDANCE")
        strength_raw = os.getenv("IMAGE_STRENGTH")

        return cls(
            llm_base_url=_loopback(os.getenv("LLM_BASE_URL", "http://127.0.0.1:11434")).rstrip("/"),
            llm_model=os.getenv("LLM_MODEL", "qwen3.5:9b"),
            temperature=float(os.getenv("LLM_TEMPERATURE", "0.4")),
            timeout_seconds=int(os.getenv("LLM_TIMEOUT_SECONDS", "300")),
            llm_num_ctx=max(512, int(os.getenv("LLM_NUM_CTX", "8192"))),
            log_dir=Path(os.getenv("LOG_DIR", str(ROOT / "logs"))),
            database_url=os.getenv("DATABASE_URL", default_db),
            jwt_secret=os.getenv(
                "JWT_SECRET",
                "change-me-multeagent-dev-secret-32b+",
            ),
            jwt_expire_minutes=int(os.getenv("JWT_EXPIRE_MINUTES", "720")),
            llm_slots=max(1, int(os.getenv("LLM_SLOTS", "1"))),
            image_slots=max(1, int(os.getenv("IMAGE_SLOTS", "1"))),
            max_active_jobs_per_user=max(1, int(os.getenv("MAX_ACTIVE_JOBS_PER_USER", "1"))),
            queue_depth_limit=max(1, int(os.getenv("QUEUE_DEPTH_LIMIT", "40"))),
            overflow_wait_seconds=float(os.getenv("OVERFLOW_WAIT_SECONDS", "45")),
            model_fast=os.getenv("MODEL_FAST", "qwen3.5:9b"),
            model_strong=os.getenv("MODEL_STRONG", "qwen3.5:9b"),
            model_cloud=os.getenv("MODEL_CLOUD", ""),
            model_vision=os.getenv("MODEL_VISION", "qwen3.5:9b"),
            model_image=os.getenv("MODEL_IMAGE", "sdxl-local"),
            model_image_path=image_path,
            image_width=max(256, int(os.getenv("IMAGE_WIDTH", "768"))),
            image_height=max(256, int(os.getenv("IMAGE_HEIGHT", "768"))),
            image_steps=max(
                1, int(steps_raw) if steps_raw else int(pdata["steps"])
            ),
            image_guidance=float(
                guidance_raw if guidance_raw else pdata["guidance"]
            ),
            image_strength=max(
                0.05,
                min(
                    1.0,
                    float(strength_raw if strength_raw else pdata["strength"]),
                ),
            ),
            image_negative_prompt=os.getenv(
                "IMAGE_NEGATIVE_PROMPT",
                "lowres, blurry, distorted, watermark, text, logo, deformed, ugly",
            ),
            image_prompt_polish=_bool("IMAGE_PROMPT_POLISH", True),
            image_profile=profile,
            # Off by default: warming holds the whole SDXL model in RAM before anyone asks for an image
            image_warm_on_start=_bool("IMAGE_WARM_ON_START", False),
            image_unload_ollama=_bool("IMAGE_UNLOAD_OLLAMA", True),
            image_tiny_vae=_bool("IMAGE_TINY_VAE", False),
            remote_llm_base_url=os.getenv("REMOTE_LLM_BASE_URL", "").rstrip("/"),
            remote_llm_api_key=os.getenv("REMOTE_LLM_API_KEY", ""),
            http_allowlist=os.getenv(
                "HTTP_ALLOWLIST",
                "localhost,127.0.0.1,ollama.com,raw.githubusercontent.com",
            ),
            artifacts_dir=Path(os.getenv("ARTIFACTS_DIR", str(ROOT / "artifacts"))),
            sso_enabled=_bool("SSO_ENABLED", False),
            default_admin_user=os.getenv("DEFAULT_ADMIN_USER", "admin"),
            default_admin_password=os.getenv("DEFAULT_ADMIN_PASSWORD", "admin123"),
            api_host=os.getenv("API_HOST", "0.0.0.0"),
            api_port=int(os.getenv("API_PORT", "8787")),
            llm_think=_optional_bool("LLM_THINK"),
            llm_keep_alive=(os.getenv("LLM_KEEP_ALIVE", "30m")).strip(),
            llm_warm_on_start=_bool("LLM_WARM_ON_START", True),
            brain_database_url=os.getenv("BRAIN_DATABASE_URL", ""),
            brain_app_password=os.getenv("BRAIN_APP_PASSWORD", ""),
            model_embed=os.getenv("MODEL_EMBED", "qwen3-embedding:0.6b"),
            brain_enabled=_bool("BRAIN_ENABLED", True),
            brain_no_evidence=(
                "refuse" if (os.getenv("BRAIN_NO_EVIDENCE") or "").strip().lower() == "refuse" else "general"
            ),
            brain_scheduler=_bool("BRAIN_SCHEDULER", True),
            brain_refresh_window=(os.getenv("BRAIN_REFRESH_WINDOW") or "").strip(),
            brain_scheduler_minutes=max(5, int(os.getenv("BRAIN_SCHEDULER_MINUTES", "30"))),
            brain_fresh_hours=max(0.1, float(os.getenv("BRAIN_FRESH_HOURS", "24"))),
            verify_answers=os.getenv("VERIFY_ANSWERS", "true").strip().lower() not in {"0", "false", "no", "off"},
            security_scan_enabled=os.getenv("SECURITY_SCAN_ENABLED", "true").strip().lower()
            not in {"0", "false", "no", "off"},
            image_idle_unload_minutes=max(0, int(os.getenv("IMAGE_IDLE_UNLOAD_MINUTES", "10"))),
            image_service_port=int(os.getenv("IMAGE_SERVICE_PORT", "8790")),
            image_service_token=os.getenv("IMAGE_SERVICE_TOKEN", ""),
            image_service_autostart=_bool("IMAGE_SERVICE_AUTOSTART", True),
            image_allow_cpu=_bool("IMAGE_ALLOW_CPU", False),
            image_vae=(os.getenv("IMAGE_VAE") or "").strip(),
            image_prompt_rewrite=_bool("IMAGE_PROMPT_REWRITE", True),
            image_fast_checkpoint=(os.getenv("IMAGE_FAST_CHECKPOINT") or "").strip(),
            image_hires=float(pdata["hires"]),
            web_search_url=_loopback((os.getenv("WEB_SEARCH_URL") or "").strip()).rstrip("/"),
            network_scan_enabled=_bool("NETWORK_SCAN_ENABLED", True),
            # hard ceiling 4096 (a /20) even if .env asks for more
            network_scan_max_hosts=min(4096, max(1, int(os.getenv("NETWORK_SCAN_MAX_HOSTS", "1024")))),
            assistant_tools=_bool("ASSISTANT_TOOLS", True),
            deep_subqueries=max(1, min(8, int(os.getenv("DEEP_SUBQUERIES", "4")))),
            deep_max_passages=max(4, min(24, int(os.getenv("DEEP_MAX_PASSAGES", "12")))),
            options_count=max(2, min(5, int(os.getenv("OPTIONS_COUNT", "3")))),
            suggestions_enabled=_bool("SUGGESTIONS_ENABLED", True),
            suggestions_count=max(2, min(5, int(os.getenv("SUGGESTIONS_COUNT", "3")))),
            quiz_enabled=_bool("QUIZ_ENABLED", True),
            quiz_count=max(2, min(6, int(os.getenv("QUIZ_COUNT", "4")))),
            web_images_enabled=_bool("WEB_IMAGES_ENABLED", True),
            web_images_count=max(1, min(8, int(os.getenv("WEB_IMAGES_COUNT", "4")))),
        )

    def with_model(self, model: str | None) -> Config:
        if not model:
            return self
        return replace(self, llm_model=model)

    def with_image_overrides(
        self,
        *,
        steps: int | None = None,
        guidance: float | None = None,
        strength: float | None = None,
        profile: str | None = None,
    ) -> Config:
        """Apply per-job image overrides (UI presets / profiles)."""
        prof = (profile or "").strip().lower() or None
        pdata = _PROFILE_DEFAULTS.get(prof) if prof else None
        size = pdata["size"] if pdata else None
        return replace(
            self,
            image_profile=prof or self.image_profile,
            image_width=size or self.image_width,
            image_height=size or self.image_height,
            image_hires=float(pdata["hires"]) if pdata else self.image_hires,
            image_steps=max(
                1,
                int(
                    steps
                    if steps is not None
                    else (pdata["steps"] if pdata else self.image_steps)
                ),
            ),
            image_guidance=float(
                guidance
                if guidance is not None
                else (pdata["guidance"] if pdata else self.image_guidance)
            ),
            image_strength=max(
                0.05,
                min(
                    1.0,
                    float(
                        strength
                        if strength is not None
                        else (pdata["strength"] if pdata else self.image_strength)
                    ),
                ),
            ),
        )
