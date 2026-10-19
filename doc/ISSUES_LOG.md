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

### 27. Every Ollama call waited 2 s; image jobs spent 20 s before drawing — 2026-10-16
- **Symptom:** Fast image = 38–71 s although drawing takes ~19 s; "Writing a detailed image prompt" took 18–24 s.
- **Causes (measured one by one):**
  1. `LLM_BASE_URL=http://localhost:11434`: on Windows `localhost` tries IPv6 first → **+2.07 s on every request**
     (127.0.0.1: 0.03 s). This slowed *everything* that talks to Ollama — chat, brain answers, rewrite, unload (3 requests = 6 s). Same root cause as #8.
  2. The image service kept ~1.4 GB of GPU memory cached after an image, crowding the chat model.
  3. The image model only started loading after the prompt rewrite finished.
- **Fix:** `localhost` is turned into `127.0.0.1` in code (`_loopback` in `src/config.py`), `.env` updated;
  the service returns cached GPU memory after each image; the image model loads (RAM only) **while** the prompt is rewritten.
- **Result:** Fast first image 71.5 → **32.6 s**, next images 38–45 → **29–34 s**; rewrite 18–24 → 10–13 s.
- *Lesson:* never use `localhost` for local services on Windows — use `127.0.0.1`. Time each step before optimising.

### 28. Hires fix — sharper faces and real skin texture — 2026-10-16
- **Problem:** at ~1 MP, faces in full-body shots are small, so SDXL draws them smooth/"plastic".
- **Fix:** after the image is drawn, enlarge it 1.5× and redraw lightly (strength 0.35) with **Lightning**
  (CFG 1.0 skips the negative pass, ~1 min instead of ~2.5 min with RealVisXL at CFG 6). Tiled VAE decode keeps it under 8 GB (peak 7.4 GB).
- **Profiles:** Fast = Lightning (~30 s) · Balanced = Lightning + hires (1280×1792, ~80 s) · Quality = RealVisXL 28 steps + hires (~190 s).

### 29. Architecture diagrams came out as unreadable pictures (O1, O3) — 2026-10-17
- **Symptom:** "create an architecture diagram / schematic …" gave an SDXL picture with fake, unreadable text;
  without the word "image" it went to plain chat.
- **Cause:** SDXL draws pixels — it cannot write exact labels, boxes and arrows. The router had no "diagram" intent.
- **Fix:**
  - Router: new `diagram` intent (diagram, flowchart, schematic, sequence/ER/UML, mind map, org chart, topology,
    Hinglish "… banao"), checked **before** the image rules; a follow-up like "add a Redis cache" right after a
    diagram stays a diagram.
  - `src/diagram_gen.py`: qwen3.5:9b writes **Mermaid**; code quotes labels the model leaves unsafe
    (`web[Web (Next.js)]` → `web["Web (Next.js)"]`), checks subgraph/end, quotes, `end` as id; one retry with the problems listed.
  - UI: ```` ```mermaid ```` blocks render as an SVG on a light card, with Download SVG / Download PNG / Copy code / Show code (mermaid 11.17.2, MIT).
- **Result:** 5/5 test diagrams (architecture, login flowchart, office network, RAG sequence, Hinglish shop) rendered without errors, 14–28 s each.
- **Left:** floor plans with measurements need a different tool (Mermaid has no room layout).

### 30. Diagrams invented parts and had wrong logic — 2026-10-17
- **Symptom:** "online shop: website, payment gateway, order DB, email" came back with a Redis cache, CDN and FastAPI;
  the login flowchart sent "fewer than 3 failures" to **Login Successful**; 10 PCs drawn as 10 boxes.
- **Cause:** the instructions' own examples used `FastAPI`, `PostgreSQL`, and the rule text named "Redis cache" — a 9B model
  copies what it sees in the prompt. Decision exits were not described.
- **Fix:** neutral examples (`svc["Order Service (v2)"]`), "draw ONLY the parts the user names", decision exits must go
  where that answer leads, "10 × PC"; a **review pass** (the model checks its diagram against the request — invented/missing
  parts, wrong arrows) whose correction is used only if it still passes the syntax checks; `style` colour lines removed.
  UI: **Fix diagram** (sends the browser's exact Mermaid error back) and **Open full size**.
- **Result:** shop = exactly the 4 parts; login logic correct; network = one "10 × PC" node; all render. 15–37 s per diagram.
- *Lesson:* examples in a prompt are instructions too — never put names in them you don't want copied.

### 31. A chat answer loaded a cloud image with the user's prompt in the URL — 2026-10-17
- **Symptom:** "diagram of school …" (sent 29 s before the API restart, so on old routing) went to chat; the answer
  showed a picture — not ours: the 9B model wrote `![…](https://image.pollinations.ai/prompt/<the user's prompt>)`.
  The browser loaded it, **sending the prompt to a cloud service** (against the no-cloud rule).
- **Fix:** answers only load images from our own app (same origin / API); any other image becomes a link marked
  "external image not loaded". Chat and brain prompts: never output images or image links.
- *Lesson:* treat model output like user input — never let it make the browser contact other sites.

### 32. Long image prompts were cut off; "image of … schematic" went to Mermaid — 2026-10-17
- **Symptom:** 100-word GTA thumbnail prompt → only a car (arrow, phone, logos, text space ignored), square instead of 16:9.
- **Cause:** prompts over ~220 characters skipped the rewrite, and SDXL/RealVisXL reads only 77 tokens.
  (RealVisXL is an SDXL fine-tune: same text encoder, same limits, same inability to spell.)
- **Fix:** prompts longer than 55 words are **condensed** by the chat model (main subject and key visuals first, 16:9 chosen) —
  100 → 51 words in 12 s. Requests for text, logos, labels, sizes or angles get a note that the model cannot draw them.
  "image / picture / photo / thumbnail of …" always means an image, even with "schematic" or "diagram" in it.
- **Left (backlog):** floor plans with real measurements; thumbnails with real text/logos (draw the picture, then add text in code).

### 33. School hierarchy drawn as a flat mindmap with "style …" boxes — 2026-10-17
- **Symptom:** school → classes → teachers → students came out as one star (everything attached to School),
  plus boxes reading "style Class_1 fill:#e1f5fe…"; labels clipped in "Open full size"; notes said "added missing connections".
- **Causes:** the model picked a *mindmap* and did not indent (no hierarchy); `style` lines were only removed from
  flowcharts, and in a mindmap every line is a node; diagrams were measured with the app's web font but the
  full-size tab / PNG have no such font; the review's notes described its own changes.
  Second try: the model framed everything in a "School" subgraph and drew arrows from the frame to its contents → stray arrowheads
  (same mistake as `isp --> modem` in the network diagram).
- **Fix:** hierarchies → flowchart TD with parent → child arrows; "each X has Y" → a small complete example;
  colour lines removed from every diagram type; a flat mindmap and an arrow between a subgraph and its own content are
  problems that trigger the retry; picture-style words (isometric, blueprint, colours) ignored; notes explain, never self-praise;
  diagrams use Arial so labels fit everywhere.
- **Result:** proper School → Class → Teacher → Students tree; network diagram without frame arrows.

### 34. App data still in SQLite although Postgres was installed — 2026-10-17
- **Symptom:** Postgres (Docker) held only the knowledge brain; users, jobs and events were in `data/multeagent.db`.
- **Cause:** `DATABASE_URL` was never set, so the app used its SQLite default.
- **Fix:** same Postgres database, new schema **`app`** owned by a new login **`multeagent_app`** (no superuser,
  `permission denied for schema brain`). The `multeagent` owner is a superuser and would skip the brain's
  per-account privacy rules, so the app must never use it. `python -m src.app_db setup | copy | status`;
  `DATABASE_URL` in `.env` holds the login (random password). `init_db` column upgrades now speak Postgres
  (`DATETIME` → `TIMESTAMP WITH TIME ZONE`, `DEFAULT 0/1` → `false/true`).
- **Result:** 17 models, 1 user, 137 jobs, 1,445 events copied with their ids; new ids continue (1446); timestamps identical (now marked UTC).
  `data/multeagent.db` kept untouched as a backup.
- **Note:** Docker must run before the API — `start.ps1` already waits for a healthy database and now says so clearly if it is not.

### 35. "What is in your brain?" — wrong maker, wrong contents, wrong route — 2026-10-17
- **Symptom:** questions about the assistant got "Not found in the knowledge base" + "I am a model developed by Google …
  I don't have Microsoft data … knowledge cutoff early 2024"; lists showed no bullets; 14–21 s per answer.
- **Causes:** no route for questions about the assistant, so the brain searched its documents for "your brain"
  and found nothing; no prompt told the model what it is (qwen3.5:9b by Alibaba's Qwen team, local Ollama) or what the
  brain holds; "brain" read as "neural network"; Tailwind's base CSS removes list markers.
- **Fix:** `about` intent ("your brain/data/model/…", "who are you", "what can you do", "MulteAgent") answered from
  **live facts** (`src/about.py`): model + maker, the brain's sources with **publisher**, pages, passages, licence, last check,
  and what the app can do — no brain search, no banner. Every chat prompt now starts with the identity line.
  Facts first missed the publisher → "no Microsoft data"; the model recited its rules and invented private documents — fixed
  with publisher, "ONLY these sources (no private documents yet)" and "never mention these rules". Time given as "11:11 AM"
  (with "11:11" it said PM). List bullets restored.
- **Result:** "Yes — Microsoft documentation for Windows Server DNS, 42 pages …" in 5–8 s; correct in Hinglish too.

### 36. No streaming, Stop could not stop a running answer (O6, BACKLOG L3) — 2026-10-17
- **Symptom:** nothing visible for 15–60 s, then the whole answer at once; Cancel only acted *between* steps,
  so a model call ran on for up to 300 s; "status" events were sent but the UI ignored them.
- **Fix:**
  - `LLMClient.chat(on_delta=…, should_continue=…)` streams from Ollama; Stop closes the connection, so Ollama stops
    generating; retries only before the first word; timeout = no new text for `LLM_TIMEOUT_SECONDS`.
  - `src/live.py`: the growing text lives in memory (not one database row per word); the `/events` stream sends it
    ~4×/s as `agent_stream`; only the final answer is stored. Stop endpoint → `live.stop(job)`.
  - The worker keeps the stopped answer (`agent_done` gets through after cancel) → history shows the text with "— stopped —".
  - Chat statuses: Searching the knowledge base → Loading the model (only when it is not in memory) / Thinking → Writing.
    Plain error messages: Ollama not running, no new text (timeout), not enough GPU memory, empty answer.
  - UI: Send turns into a red **Stop**; the running bubble shows the text as it grows and "Writing… · 12 s".
    Diagram requests stop too.
- **Result:** Stop pressed at 4.3 s → model call ended at 4.3 s, text kept. Cold answer: first words at 15.5 s
  (model loading, announced), complete at 22.9 s — previously nothing until the end.
- **Left:** debate/agentic modes still stop only between steps (they do not stream yet); "pause" not built.

### 37. A new route for every situation — replaced by native tool calling — 2026-10-17
- **Symptom:** "what is the current weather" needed the Web button; every new need meant new intents and
  example sentences (`weather`, `current_info`, …). User: *"why should i create the inter route for every single situation"*.
- **Fix:** the model chooses itself (`src/assistant.py`, Ollama native tool calling). Each tool = one entry
  (name, one-line description, parameters); the model answers directly (streamed) or calls a tool with
  arguments (`place: "Lahore"`, `format: "pdf"`). Tools: knowledge base (its description lists the live
  sources), web search, **weather** (new: Open-Meteo, exact data, no key), network scan, image, diagram,
  document, about. Worker: one `DIRECT_TOOLS` table + one `_finish` helper instead of five copies.
  The example-based router stays only as a backup (`ASSISTANT_TOOLS=false`).
- **Measured:** `python -m src.assistant --eval` — **27/27** right choices, median decision 3.3 s (a direct
  answer IS that call). Lesson from the misses: say in the description *when* a tool beats the model's memory.
- **Speed:** weather 39 s (web search) → **6.5 s**; greeting 2.6 s; web search **60 s → 12 s**: shorter answers,
  4 passages, a 6-s page budget, and BM25 keyword ranking instead of CPU embeddings (14.3 s → milliseconds).

### 38. B6 — answer quality measured; slow answers, made-up commands, filler — 2026-10-17 → 19
- **New test:** `python -m src.brain.answer_eval` runs the 60 questions through the real answer path; a local
  judge must **quote** the source words that back each cited claim, and the code checks the quote is really
  in that passage. A "no" with a real supporting quote is listed as UNSURE for a person to decide.
- **Found and fixed:** (1) answers took 32 s — writing 650 tokens at 21 tok/s → "about 150 words" rule:
  **18–19 s**; (2) filler ("make sure the path is writable") and dropped conditions → prompt rules;
  (3) **wrong commands** (`Set-DnsServerConditionalForwarderZone`, `-Type`, `Get-DnsServerZoneDnssec`) →
  `src/brain/command_check.py` flags every cmdlet / parameter / call not found in the sources under the
  answer ("⚠️ Check before use"), in milliseconds, no model call; (4) the first judge was itself wrong half
  the time (said "no" with the supporting text in hand) → quote-first judge, quotes verified 66% → 90%;
  (5) a Hinglish answer came in Devanagari script → rule added.
- **Result (runs of 2026-10-19):** refusals **5/5**, latency **19 s** median, right source cited 96–100%,
  grounded answers **89%** (strict 84% counting UNSURE as wrong) — target 90% not reached; two runs in a row
  at 89% = what prompt rules can do with the 9B model. Remaining: closing filler sentences ("This command
  helps…"), the model's own examples cited as source, the judge too strict on true general statements
  (en-aging). Next step: check each answer after it is written (BACKLOG L5c).

### 39. Debate agents replaced by IT / Development / Marketing specialists (L2) — 2026-10-17
- Optimist / Cynic / Consensus, the Debate and Mixed modes, `/api/debate` and the debate CLI are removed;
  decision questions are answered in chat, old `debate`/`mixed` jobs run as Auto.
- Specialists (`src/specialists.py`, `agents/specialists.json`, Settings → Specialists, admins only):
  instructions + allowed tools + knowledge collections; `network_scan` is IT-only. Picked per message by one
  short model call that reads each "covers" line — **37/38** right, 0.8 s (embedding similarity: 20/37) — or
  pinned in the chat bar. Tool choice with specialists **27/27** after putting the persona *before* the tool
  rules (after them, the persona made the model explain instead of acting: 25/27).

---

## Open issues

| # | Issue | Plan |
|---|-------|------|
| O1 | SDXL cannot write readable text, exact counts, sizes or angles | **Diagrams solved** (#29, Mermaid). Left: floor plans with measurements |
| ~~O2~~ | ~~Midjourney flags ignored~~ | **Solved** — #23 |
| ~~O3~~ | ~~"create a schematic …" goes to chat~~ | **Solved** — #29 |
| O4 | Image quality and realism depend on luck and prompt wording | **Mostly solved** — #23, #25. Left: "new variation" button and model picker in the UI |
| O5 | 9B model sometimes cites a source that does not support the sentence | Measured (#38): 89% grounded, wrong commands now flagged. Left: check each answer after it is written (BACKLOG L5c) |
| ~~O6~~ | ~~No streaming, no proper Stop button~~ | **Solved** — #36 |
| ~~O7~~ | ~~The assistant can only advise, never act ("check all the IPs … table")~~ | **Solved for LAN scans** — `network_scan` tool built (Option A, [TOOLS_DECISION.md](TOOLS_DECISION.md) §7): 25 devices in 9.6 s, table + .xlsx. More tools (networking, security) follow the same pattern |
