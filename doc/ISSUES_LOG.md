# MulteAgent — Issues Log

Every real problem we hit: what we saw, why it happened, how we fixed it.
Open items are at the end.
Related: [BACKLOG.md](BACKLOG.md) (plans) · [BRAIN_DESIGN.md](BRAIN_DESIGN.md) (knowledge brain).

Format: **Symptom** → **Cause** → **Fix** · *Lesson*

---

## Models and the LLM

### 1. `.env` pointed to models that were not installed — 2026-10-13
- **Symptom:** chat/debate would fail with "model not found".
- **Cause:** `.env` still named `qwen2.5:3b`, `llama3.1`, `llava:7b`; Ollama had different models.
- **Fix:** `.env` / `.env.example` updated to the installed models.
- *Lesson:* run `ollama list` and compare with `.env` after changing models.

### 2. `qwen3.5:9b` took ~65 s for a one-line answer — 2026-10-13
- **Symptom:** every call very slow.
- **Cause:** Ollama enables "thinking" by default for Qwen3.x; the model wrote ~5,000 hidden reasoning characters first.
- **Fix:** `LLM_THINK=false` (sent as `think` in `src/llm_client.py`) → **0.9 s**.

### 3. Small router models were inaccurate and evicted the main model — 2026-10-14
- **Symptom:** `qwen3:0.6b` routed 6/10 test prompts correctly, `qwen3.5:2b` 7/10; running a small model on the GPU unloaded the 9b (26 s first reply).
- **Cause:** too small for reliable classification; 8 GB VRAM cannot hold two models.
- **Fix:** one model, `qwen3.5:9b`, for every role (10/10 routing). Small models removed.
- *Lesson:* on 8 GB, one model that stays loaded beats several that swap.

### 4. The model did not know today's date — 2026-10-14
- **Symptom:** "Today's date is October 16, 2023" / "I don't have real-time information".
- **Cause:** a model has no clock; it only knows its training data.
- **Fix:** current date/time is added to every chat prompt (B4, `src/brain/answer.py`).

## Database and tools

### 5. A native PostgreSQL 18 appeared that we "never installed" — 2026-10-14
- **Symptom:** port 5432 taken by service `postgresql-x64-18`.
- **Cause:** the EnterpriseDB installer was run while looking up Postgres (Stack Builder opened at its end).
- **Fix:** uninstalled; Postgres runs in Docker (`pgvector/pgvector:0.8.0-pg17`) because pgvector is hard to install on native Windows.

### 6. pgAdmin: random containers and a locked account — 2026-10-15
- **Symptom:** containers like `dazzling_dhawan` exiting with "define PGADMIN_DEFAULT_EMAIL"; login locked after 3 tries.
- **Cause:** clicking **Run** on the image in Docker Desktop creates a new, unconfigured container; the password was copied from `.env.example` (placeholder `change-me`) instead of `.env`.
- **Fix:** removed the stray containers, reset the pgAdmin volume. Use Containers → Start/Stop, never Images → Run; passwords live only in `.env`.

### 7. pgAdmin "The CSRF token is invalid" — 2026-10-15
- **Cause:** browser kept the login page of the deleted pgAdmin.
- **Fix:** hard reload (Ctrl+Shift+R) or clear `localhost` cookies.

### 8. Brain migration hung forever — 2026-10-15
- **Cause:** on Windows `localhost` tries IPv6 first; Docker listened on IPv4 only, so the connection waited.
- **Fix:** `BRAIN_DATABASE_URL` uses `127.0.0.1`; brain connections time out after 10 s with a clear error.

### 9. `sources.yaml` was not tracked by Git — 2026-10-15
- **Cause:** `.gitignore` ignored all of `data/` (it holds the SQLite database).
- **Fix:** `data/*` plus exceptions for `data/brain/` and `data/brain_eval/`.

## Knowledge brain (RAG)

### 10. Chunk overlap silently missing for normal paragraphs — 2026-10-15
- **Found by:** a unit test. Overlap only worked when a whole paragraph fit the overlap budget.
- **Fix:** carry the last sentences of a long paragraph into the next chunk.

### 11. One chunk was 2,684 tokens (a big table) — 2026-10-15
- **Cause:** tables were never split, so one table exceeded the answer budget.
- **Fix:** chunker v2 splits large tables by rows and repeats the header; stored pages were re-chunked in place (no new versions). Max chunk now 591 tokens.

### 12. Hybrid search ranked worse than meaning-only search — 2026-10-15
- **Symptom:** equal-weight RRF: MRR 0.81 vs 0.88 meaning-only.
- **Cause:** broad keyword matches on common words ("dns", "server") out-voted correct meaning matches.
- **Fix:** weighted RRF — meaning + an extra vote for passages containing the exact identifier (cmdlet, CVE, error code); broad keywords no longer vote. MRR **0.93–0.95**, exact-identifier questions 1.00.
- *Lesson:* measure fusion on an eval set; textbook defaults are not always best.

### 13. Hinglish answer translated "scavenging" as "pakhana" — 2026-10-15
- **Fix:** grounding prompt rule: keep technical terms in English exactly as the sources write them.

### 14. HTML extractor mangled pages — 2026-10-16
- **Symptom:** `trafilatura` broke inline code into fake code blocks, flattened tables, produced mojibake (`Â¶`).
- **Fix:** main-content selection + `markdownify` + charset from bytes; `trafilatura` removed.

### 15. Python docs import lost the internet halfway — 2026-10-16
- **Symptom:** last 3 of 27 pages failed (connection reset / DNS failure).
- **Fix:** run recorded as `partial`, nothing marked missing (outage protection); re-run fetched only the 3 pages, the other 24 answered HTTP 304.

## Running the app

### 16. Ollama not running after every restart
- **Fix:** `start.ps1` starts Docker + database, Ollama, API and UI and runs a health check.

### 17. API start added ~13 GB of RAM — 2026-10-16
- **Symptom:** RAM 14.7 → 28.0 GB when `python main.py --gui` started; GPU unchanged.
- **Cause:** SDXL was warm-loaded inside the API; with CPU offload the whole model lives in system RAM.
- **Fix:** SDXL moved to its **own process** (`src/image_service.py`), started on the first image request, exits after 10 idle minutes (frees 100% of its memory). API start now **+0.7 GB**. (A 15-second health poll that was commented out was unrelated.)

### 18. Images on the "GPU" showed GPU 0% and took ~4 minutes — 2026-10-16
- **Symptom:** CPU 56%, GPU 0%, 215–228 s per image, image service 17.4 GB.
- **Cause:** the venv had **`torch 2.14.1+cpu`** (pulled in by `accelerate`), so SDXL silently ran on the CPU in float32.
- **Fix:** installed `torch==2.7.1+cu118` + `torchvision==0.22.1+cu118` → **41 s**, 10.4 GB, GPU 100%. The service now **refuses** CPU mode with an "install the GPU build" message (`IMAGE_ALLOW_CPU=true` to override).
- *Lesson:* after `pip install -r requirements.txt`, always run `pip install -r requirements-image-cu118.txt`. Task Manager's GPU "3D" graph does not show CUDA work reliably — check with `nvidia-smi`.

### 19. One request failed with "torch 2.14.1+cpu" right after the fix
- **Cause:** an image service started while the new torch was still installing.
- **Fix:** none needed — the guard reported it; the next service start used the GPU build.

### 20. "improve this prompt …" generated an image — 2026-10-16
- **Cause:** image words in the prompt ("highly detailed") matched the image router.
- **Fix:** requests to improve/rewrite/suggest a *prompt* go to chat; "… and generate it" still draws.

### 21. "GPU images are worse than CPU images" — 2026-10-16
- **Investigation:** same prompt + same seed: GPU float16 vs CPU float32 vs GPU 1024×1024.
- **Result:** GPU float16 quality was equal or better (the CPU image even dropped the person). The bad images came from **ambiguous prompts** ("rose" → rose flowers; "her iconic look" means nothing to SDXL) and **random seeds**. 1024×1024 gave the best image (274 s).
- **Follow-up:** O4 below.

### 22. "1024×1024 takes 274 s" — measured again — 2026-10-16
- **Investigation:** timed every diffusion step and the final decode separately, alone on the GPU.
- **Result:** 1024×1024, 25 steps = **103 s** (3.7 s/step, decode 6 s, 7.2 GB VRAM). The 274 s run was not alone on the GPU.
  The fp16-fix VAE (`madebyollin/sdxl-vae-fp16-fix`) gave **no** speed gain here (decode 6.0 vs 6.2 s), so it stays off (`IMAGE_VAE` empty).
- *Lesson:* benchmark with nothing else on the GPU, and time the parts (steps vs decode) before "fixing" one.

### 23. Image quality and realism (O2, O4) — 2026-10-16
- **Done:**
  - Chat model rewrites short requests into concrete SDXL prompts (`IMAGE_PROMPT_REWRITE`) and picks the aspect ratio; falls back to the local polish if it fails.
  - `--ar 16:9`, `--seed 42`, `--no …` work; other Midjourney flags (`--v 6.0`, `--style raw`) are removed and listed as ignored.
  - Aspect ratios snap to SDXL's trained sizes (16:9 → 1344×768 at Quality, 1024×576 at Fast).
  - Seed shown in every reply; `--seed N` repeats an image; prompt/seed/model stored inside the PNG.
  - Sampler DPM++ 2M Karras (was UniPC).
  - Quality profile = 1024-class size, 28 steps; Balanced 896; Fast 768.
  - Realism checkpoints **RealVisXL V5.0** and **Juggernaut XL v9** added with their model pages' settings (`src/image_models.py`); `MODEL_IMAGE_PATH` picks the default, a job can pass `image_checkpoint`.
- **Comparison** (4 requests incl. Hinglish, same seeds, Quality profile, ~2 min/image for all three):
  both realism models beat SDXL base (more natural skin, light and detail; base looks more "painted").
  RealVisXL: most natural scale and scenes. Juggernaut: excellent faces, but bent the laptop in the desk scene.

### 24. LLM-written prompts were cut off or looked like miniatures — 2026-10-16
- **Symptom:** log "CLIP can only handle sequences up to 77 tokens … truncated: 35mm lens"; a stray "2:3" at the end of a prompt;
  "chhota sa ghar" became "tiny hut", and two models drew a toy-like miniature.
- **Cause:** SDXL's text encoder reads only 77 tokens; the 9B model ignores word limits sometimes; "tiny" pulls SDXL towards miniature/tilt-shift photos.
- **Fix:** rewrite rules (25–50 words, real-world scale, no "tiny/miniature", no aspect in the prompt) **plus** code that enforces it:
  `fit_prompt` keeps whole phrases up to 55 words, ratio text is stripped.
- *Lesson:* never trust an LLM to obey a length rule — enforce it in code.

### 25. RealVisXL Lightning for the Fast profile — 2026-10-16
- **Test:** same prompts and seeds as #23, full 1024-class size.
- **Result:** model page settings (5 steps, CFG 1.5) = 27 s but over-sharpened, "processed" faces.
  **CFG 1.0 + 6 steps = 20 s and natural** (CFG 1.0 also skips the negative-prompt pass, so it is faster).
- **Fix:** Fast profile → `IMAGE_FAST_CHECKPOINT=RealVisXL_V5.0_Lightning_fp16.safetensors`, always at 1024-class size.
  Quality profile → RealVisXL V5.0 (~2 min). Edits scale a distilled model's steps by 1/strength so they still run ~6 real steps.
- *Lesson:* a model page's numbers are a starting point; test 2–3 settings on your own prompts.

### 26. Z-Image Turbo does not fit the Quadro P4000 — 2026-10-16
- **Test:** 6B model, GGUF Q6_K transformer on GPU (5.9 GB), Qwen3 text encoder on CPU.
- **Result:** float16 → **black image** (values overflow; the model is made for bfloat16) and 95 s/step.
  bfloat16 (emulated on Pascal) at 768×768 → **best realism of all models, but 600 s per image** (65 s/step):
  it needs ~9 GB VRAM, so Windows spills into system RAM.
- **Decision:** not used. Revisit with a GPU that has ≥12 GB and native bfloat16 (RTX 30xx or newer).
  Files deleted 2026-10-16 (with Juggernaut XL v9) — re-download from `Tongyi-MAI/Z-Image-Turbo` + `jayn7/Z-Image-Turbo-GGUF`.
  Models kept: **RealVisXL V5.0** (Quality) and **RealVisXL V5.0 Lightning** (Fast).
- *Lesson:* "runs on 8 GB" in guides means 8 GB **on a modern GPU** (FP8/bf16 support); Pascal is a different story.

---

## Open issues

| # | Issue | Plan |
|---|-------|------|
| O1 | SDXL cannot write readable text, exact counts, sizes or angles (schematics, floor plans) | Diagram tool: LLM writes SVG/draw.io with exact labels and measurements |
| ~~O2~~ | ~~Midjourney flags ignored~~ | **Solved** — #23 |
| O3 | "create a schematic …" (without "image") goes to chat | Route diagram/schematic/floor-plan requests to the diagram tool |
| O4 | Image quality and realism depend on luck and prompt wording | **Mostly solved** — #23, #25. Left: "new variation" button and model picker in the UI |
| O5 | 9B model sometimes cites a source that does not support the sentence | Citation checker (BACKLOG L5c) |
| O6 | No streaming, no proper Stop button | BACKLOG L3 |
