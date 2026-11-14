# MulteAgent — Issues Log

Every real problem we hit: what we saw, why it happened, how we fixed it.

- **#1–#44** (below, by topic): the early problems, each written out in full.
- **O-numbers** (at the end): every problem since, one table row each, in two tables — **Open** and
  **Solved**, both in number order. A new problem gets the next O-number in Open; when it is fixed, its row
  moves to Solved with the number struck through (`~~O41~~`) and the fix.
- The plan for each fix: [BACKLOG.md](BACKLOG.md). What got done, by date: [PROGRESS_LOG.md](PROGRESS_LOG.md).
  All docs: [README.md](README.md).

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
- **Measured:** `python -m src.llm.assistant --eval` — **27/27** right choices, median decision 3.3 s (a direct
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

### 40. IT specialist gets real tools: security check, ping, server audit/diagnose/operate — 2026-10-19 → 21
- **security_check** (`src/security_scan.py`): ports + service versions + HTTPS certs + this PC's firewall/RDP/SMBv1,
  risk-flagged; admin-only. **ping_host** (`src/host_check.py`): is one device up — fixes "the model answered
  'I cannot ping'"; catches public-IP typos (192.16.→192.168). Both private-range only, read-only.
- **server_audit / diagnose_server / operate_server** (`src/linux_audit.py`, `src/server_ops.py`): log in to a
  Linux server over SSH. Logins are typed in Settings → Servers and kept **in the API's memory only**
  (`src/server_vault.py`) — never in `.env`, the chat, the DB or the model; gone at logout / restart / 8 h.
  Access is gated by who holds the credentials, not an admin role (SERVER_ACCESS_DECISION.md §8).
- **Safety for changes (operate):** a deterministic allow-list classifier decides read-only vs change (not the
  model); a per-server trust mode (audit / confirm / trusted); a Run/Cancel confirm card for each change
  (`live.wait_confirm` + `/api/jobs/{id}/confirm`); a destructive-command deny-list that holds even in trusted
  mode; an audit log (`app.command_log`). Completes P0–P2 of `doc/requirment.md`. **Live-test pending** (the user
  runs the first real server session). ~130 new tests.

### 41. One chat split into many single-prompt chats after refresh / login — 2026-10-26 → 27
- **Symptom:** start a chat, send ~10 prompts, then refresh or log out/in → that one chat became 10
  separate single-prompt chats in the sidebar.
- **Cause:** the chat→message grouping lived **only** in the browser's `localStorage`. The open
  conversation is held in memory so it *looked* like one chat during the session, but on reload
  `ensureThreadsForJobs` had no durable grouping to rebuild from and made one thread per job.
- **Fix (two parts):** (1) every job now carries a **`thread_id`** stored in its payload and surfaced
  by `job_to_dict`; the client groups jobs by it (`buildThreads`). (2) Chat **threads + projects moved
  server-side** — `chat_threads` / `projects` tables, `src/history_store.py`, and `/api/history`,
  `/api/threads/{id}`, `/api/projects` — so titles, pins and project assignment sync across devices,
  with a one-time localStorage→server migration on first login. New tests: `test_jobs_thread.py`,
  `test_history_store.py`.
- *Lesson:* client state that must survive reload / re-login / another device belongs on the server
  keyed by user; `localStorage` is only for per-device conveniences. See [CHAT_HISTORY_DECISION.md](CHAT_HISTORY_DECISION.md).

### 42. `src/` reorganised into domain packages — the import-rewrite traps — 2026-10-27
- **What:** 42 flat `src/*.py` modules grouped into `core/ llm/ jobs/ servers/ images/ tools/`
  (`brain/`, `agentic/` unchanged); 41 `git mv` renames + imports rewritten across `src/ web/ tests/`.
- **Trap 1 — `from src import X` missed by a dotted find-replace.** *Symptom:* `test_about.py` →
  `ImportError: cannot import name 'about' from 'src'`. *Cause:* `from src import about` isn't a
  `src.about` dotted ref, so a sed over `src\.X` skipped it. *Fix:* a Python migration script that also
  rewrites `from src import X[, Y as Z]` → `from src.PKG import …`, plus **string** refs
  (`python -m src.image_service`, test `monkeypatch("src.artifacts…")`).
- **Trap 2 — `__file__`-relative path broke.** *Symptom:* `config.ROOT` would point at `src/` instead
  of the project root. *Cause:* `ROOT = Path(__file__).resolve().parent.parent` with `config.py` moved
  one folder deeper. *Fix:* added one more `.parent`; verified `ROOT = D:\multeagent` and `data/` resolves.
- *Lesson:* moving Python modules means rewriting **all three** reference forms (dotted,
  `from src import X`, string literals) and every `__file__`-relative path; the 526-test suite proved it.

### 43. Couldn't see where response latency went (P0, response-quality) — 2026-10-27
- **Symptom:** no way to say which part of a slow answer was slow — only end-to-end `total_seconds`
  was recorded.
- **Cause:** the chat path (`src/jobs/chat_runner.py`) logged `elapsed`/`total` but not
  **time-to-first-token** nor whether the model was **cold** (and the chat model is deliberately
  unloaded to give VRAM to SDXL — see IMPROVEMENT_ROADMAP Tier 1, so cold loads are likely).
- **Fix:** `chat_runner` now records TTFT + cold/warm, logs one line per job
  (`timing job=… role=… warm=… ttft=…s total=…s`) and emits the fields on `agent_done`/`session_done`.
  Additive; 526 tests still pass. First step of the Fast/Deep program — [RESPONSE_QUALITY.md](RESPONSE_QUALITY.md).
- *Lesson:* measure before optimizing; TTFT (not total) is the perceived speed, and cold-load is the
  prime suspect to confirm before changing the unload policy.

### 44. Chat UI polish + source presentation — 2026-10-26 → 27
- **Layout / loading / flow** (commit `4929bb4`): fixed the overall chat layout and loading states and
  smoothed the source-selection step so a web-backed answer flows cleanly (search → cited answer) with
  runtime web search.
- **Citation chips redesigned** (commit `d53d0a9` + this session): inline `[n]` markers now collapse
  into one **source pill** — favicon + site name, `+N` for the rest — with a hover card listing every
  source in the group, plus an end-of-answer sources table, replacing the tiny number-only chips.
- **CSS → inline Tailwind:** the chat components' styles moved out of `globals.css` into inline
  Tailwind (citation chip/hover card, sources table, live-status, tool drawer, answer-check, thumbs),
  shrinking `globals.css`. *Not a bug — a maintainability refactor, logged for the record.*
- *Status note:* the device-persistence (`thread_id`) item batched with these is **done, not
  in-progress** — see #41 (server-side threads/projects + cross-device sync). Tool live-testing is
  still ongoing (as with #40).

---

## Open issues

| # | Issue | Plan |
|---|-------|------|
| O1 | SDXL cannot write readable text, exact counts, sizes or angles | **Diagrams solved** (#29, Mermaid). Left: floor plans with measurements |
| O4 | Image quality and realism depend on luck and prompt wording | **Mostly solved** — #23, #25. Left: "new variation" button and model picker in the UI |
| O5 | 9B model sometimes cites a source that does not support the sentence | Measured (#38): 89% grounded, wrong commands now flagged. Left: check each answer after it is written (BACKLOG L5c) |
| O12 | The site audit covers ~20 checks; Screaming Frog / Semrush also find redirects, slow pages, orphan and deep pages, duplicate content, security headers, and offer Core Web Vitals, keyword/SERP research, competitor gaps and tracked projects — _2026-11-02_ | **Mostly solved** (BACKLOG L15): P1 crawler depth, P3 keyword research, P4 competitors + SERP-briefed writing, P5 tracked projects done; **P2 page speed on hold** (options in SEO_TOOLS.md, user to decide) |
| O13 | No way to save a repeatable task ("write an incident report like this", "do my monthly SEO check") — the user re-types the same instructions every time; specialists only set the domain — _2026-11-03_ | **In progress** (BACKLOG L14): S1–S3 done 2026-11-03 — skills (SKILL.md format), built-in catalog, team + personal skills, `/api/skills`, `/name` in chat with a `/` menu, Settings → Skills (Yours / Discover). Left: assistant eval run by the user |
| O16 | Security gaps: (1) the login token is a 12-hour JWT kept in `localStorage` (any XSS can read it), with no refresh token, no logout revocation, and a dev default secret used if `JWT_SECRET` is unset; (2) **a disabled user's existing token keeps working** until it expires (`get_current_user` never checks `status`); (3) CORS `*` and the API on `0.0.0.0`; (4) no rate limit on login (password guessing) or the API (floods), no request size cap; (5) image uploads trust the declared MIME type — no file-type sniff, no decode check (decompression bomb), no virus scan; (6) no security headers (CSP, nosniff, frame-ancestors) — _2026-11-04_ | **In progress** (BACKLOG L16): (1), (2) and CORS solved 2026-11-04 by S1 (cookie sessions, refresh rotation, revocation, same-origin API); API on 127.0.0.1, reached only through the UI (S1b; HTTPS dropped by the user — plain http on the LAN, risk accepted), (4) S2, (5) S3, (6) S4 next. Already sound: Mermaid `securityLevel: strict`, Markdown without raw HTML, attachment paths checked against traversal, bcrypt passwords, admin-only accounts |
| O18 | User wants "an RDP window" and pointed at vercel-labs/agent-desktop — that project lets an AI operate apps on the machine it runs on through the accessibility tree (CLI/MCP, Apache-2.0); it is not a remote-desktop viewer — _2026-11-04_ | **Open** (BACKLOG L18): which need is meant decides the tool — a remote desktop in the browser (Apache Guacamole, SERVER_ACCESS_DECISION.md §6 C2) or the AI operating desktop apps (agent-desktop) |
| O19 | Settings → Servers is only for servers (and now recorders); routers, switches and other network devices cannot be added, and the agent cannot read them — _2026-11-04_ | **In progress** (BACKLOG L19): one **Devices** section with a type per device; read-only first (user); brands MikroTik, Cisco, TP-Link / D-Link / Netgear |
| O21 | The NVR answer lists channel, name, IP, model only; asked for MAC, serial, firmware etc. the assistant says ISAPI cannot give them. The user's reference report (nvr-report-NVR_001…xlsx: NVRs 31 cols, Cameras 100 cols — device, network, system, main/sub stream, image — and Storage per HDD) shows it can — _2026-11-04_ | **Open** (BACKLOG L17 N5): NVR-side ISAPI (status, network, HDDs, recording, stream proxy) + each camera's own ISAPI |
| O22 | The user form, Agents (a modal) and Devices are panels inside Settings, not proper full pages within the layout — _2026-11-04_ | **Open** (BACKLOG L19 D5) |
| O32 | The database owner password (`POSTGRES_PASSWORD`, login `multeagent`, full rights) is only 5 characters — _2026-11-10_ | **Open** (BACKLOG L20): user changes it — `ALTER ROLE multeagent PASSWORD …` in the database, then `POSTGRES_PASSWORD` and the password in `BRAIN_DATABASE_URL` in `.env`, then restart the API (Docker reads `POSTGRES_PASSWORD` only when the volume is first created) |
| O44 | Assistant eval (34 cases, first run on the rebuilt set): **24/34 right**, median 7.6 s. Misses: no tool where one was wanted (`about_multeagent` ×2, `draw_diagram`, `write_content`, the MikroTik VLAN steps, `ask_clarification` ×2 for "Make me a report" / "Set it up for me"); a near tool (latest PostgreSQL → `search_knowledge_base` not `web_search`; firewall audit → `security_check` not `server_audit`; table into Excel → `web_search` not `create_document` xlsx) — _2026-11-11_ | **Open** (BACKLOG L20 O44): look at each miss — the tool's description, the router, or the case itself. 2026-11-12: the eval skipped `decide()`'s pre-routing (web nudge, clarify check), so 2 misses (PostgreSQL, MikroTik) never happen in real chats — both now share `pre_route()`; SYSTEM told the model to answer "flow, process" with its own Mermaid block and to keep "creative writing" direct (flowchart, blog post) — reworded; `web_search` pulled in the Excel table ("prices"), `security_check` the server audit — descriptions now point to `create_document` / `server_audit`. Waiting: the user reruns the eval; a miss now shows the start of the model's reply, needed for `about_multeagent` ×2 and `ask_clarification` ×2 2026-11-13 eval (38 cases): **31/38**. 2 misses came from the routing call (weather and the Excel table sent to web search), fixed: the router sends requests its own tools do to the tool choice (router alone: 38/38 live). Left, all the tool choice answering directly: `about_multeagent` ("which model are you running"), the MikroTik VLAN steps (from memory), `nvr_cameras`, `write_content` (the blog post written directly); one miss not seen (the paste showed 6) |
| O46 | Real chat "how much time it will take to download gta 6, on a 100mb/s pkg": answered from memory ("expected in late 2025 or 2026" on 2026-11-12), the formulas came out as raw LaTeX (`$ \frac{…} $`), the arithmetic was wrong (800 Gb ÷ 100 Mbps = 8000 s ≈ 2.2 h, said 8 h) and the web images showed a size (210 GB) the text never used. Causes: the web nudge's phrase list has no "how much"; the model does not see that a game or product may be newer than its training; the answer renderer has no math support and the prompt never forbade LaTeX — _2026-11-12_ | **Open** (BACKLOG L20 O46): eval case + a prompt rule (unknown or recent things → web_search) and a no-LaTeX rule; arithmetic (a calculate tool?) waits for the user's decision |
| O47 | Real chat "tell me about the controvery of jensen huang and chiness spy ?" (web search, 27 s): the right tool, a weak answer. (1) It talks about its own mechanics ("Based on the provided web passages", a "Note on Sources"); (2) thin and repetitive, the key facts missing (who the person is, what is alleged); (3) 2 of 4 sources are Facebook and YouTube, one bullet only describes the sources; (4) allegations about a real person stated as fact, not attributed ("reportedly", "the NYT reports"); (5) every source dated 2026-11-12: the fetch date shown as the page's date (`updated_at = fetched`); (6) a source title shown twice (section heading = title); (7) the chat stays named "hi" after the topic changes. Causes: WEB_SYSTEM's wording ("the numbered web passages provided"); only 4 pages read within 6 s together, so paywalled or slow news pages fall back to a 1–2 sentence snippet; every question goes to SearXNG's general results, none to its news search — _2026-11-12_ | **Open** (BACKLOG L20 O47): fixes per problem in BACKLOG. Done: news search (3), 2026-11-12; wording (1), attribution (4), honest dates (5), title once (6), 2026-11-13 ([HOW_QUERY_WORKS.md](HOW_QUERY_WORKS.md) B). Left: substance (2): user decided 2026-11-13 — depth to suit the question (a quick fact fast, an explanation or news story reads more). Chat title (7): dropped by the user (not worth the extra model call) |
| O48 | A web search took ~40 s and its timing line (`web_timing search= read= synth=`) was not in `logs/api.log`, so where the time went could not be seen; other paths (tools, documents, diagrams) have no timing line at all. Cause: `start.sh` starts each part with `>logs/<name>.log`, which empties the log on every start: the API restarted at 18:00 and the earlier chats' lines were gone — _2026-11-12_ | **Open** (BACKLOG L20 O48), built 2026-11-12: `start.sh` appends (a log over 20 MB becomes `.log.1` at a start); every job logs `job_timing` (total + model time per step, [HOW_QUERY_WORKS.md](HOW_QUERY_WORKS.md) "Where the time went"). Waiting: the first lines after a restart. Found while measuring: ~1500 of the web answer's ~1900 instruction tokens are the diagram rules, sent even for a plain fact |
| O52 | Every chat takes 57–82 s in all although the answer label says 20–42 s. `job_timing` (2026-11-13): the tool-choice call reads ~8,000 prompt tokens and takes ~20 s even when it writes 3 tokens; quiz (9–17 s) and suggestions (2–22 s) run after each answer and keep the GPU busy, so the next question waits; a web answer's synth took 16–32 s — _2026-11-13_ | **Open** (BACKLOG L20 O52): measure whether the prompt prefix is reused (the date/time near the top changes every call); diagram rules only when a diagram could help. Quiz: user decided 2026-11-13 — only when the user is learning a topic. 2026-11-14: measured, Ollama does reuse a prompt's start, but the date/time near the top changed every minute and broke it: the tool choice (6,867 tokens) read in full, 17.1 s. The date now goes last (`date_line()`, tool choice, router, web, deep research, knowledge-base answers): 1.5 s, router 4.5 → 1.35 s ([HOW_QUERY_WORKS.md](HOW_QUERY_WORKS.md) "The date goes last"). Eval after it: 35/38 (FA + q8_0 on), 34/38 (Ollama defaults): 2 new misses, both from the router. (1) The date and time at the end of the router prompt sent the Excel table to web search: the router now has the date alone at the top (changes once a day, still cached). (2) The blog post: the router chose `write_content` but wrote a long invented `request` and ran out of its 300 output tokens, so the cut-off JSON read as "model"; the router now copies arguments in the user's words, never adding details. Router alone on the eval texts (live): 26 right, 0 wrong, 7 handed on (was 25 / 0 / 8). User's eval after the fixes: **37/38, median 4.0 s** (the best yet; left: "Make me a report", the clarify case that varies run to run). Left: suggestions, quiz, summaries; diagram rules (~930 tokens) only when a diagram could help |
| O53 | Pictures under the wrong sections: "The Math" got GTA 3 / meme images, "Estimated File Size" a DaVinci Resolve screenshot, "Who Controls the Ban?" Ray-Ban glasses. Cause: `web_images` searches every section heading; a heading that is not one named thing is searched with the answer's subject added, which still matches unrelated pages — _2026-11-13_ | **Open** (BACKLOG L20 O53): only headings that name one thing get pictures |
| O62 | Mermaid 12 is out (12.3.0, 2026-11-03; VS Code 1.146 moved to it) and the app pins 11.17.2. 12.0.0 changes every diagram by default: ELK becomes the layout for flowchart, class, state, ER, requirement and use-case diagrams, `neo` replaces the classic look, `defaultRenderer` is gone; it needs ES2024 browsers (Safari 17.4+) and Node 22.12+. New: use-case diagrams, per-diagram `layout` / `look` / `theme` in front matter — _2026-11-14_ | **In progress** (BACKLOG L20 O62): pinned `mermaid` 12.3.0 with `layout: "dagre"`, `look: "classic"` (`components/answer/MermaidDiagram.tsx`) so diagrams look as before; tsc, eslint clean; flowchart, architecture (icons), sequence, mindmap and ER parse (`scripts/validate_mermaid.mjs`). Waiting: the user checks diagrams in the app and the PNG / SVG downloads (O45). Then try ELK and `neo` and keep what reads better; use-case diagrams in `diagram_rules.py` |
| O63 | A long chat forgets details. Every message is stored word for word (`app.jobs`, `app.job_events`, ~1 MB in all), but past the history budget (`CONTEXT_HISTORY_SHARE` 0.35 × `LLM_NUM_CTX` 12288 ≈ 4,300 tokens) the oldest turns are folded into `chat_threads.summary` (`src/jobs/chat_memory.py`), and the model never sees them again: an IP, a command or a number the summary dropped is gone for the rest of the chat. Raised from a pasted "lossless memory engine" proposal (Zstd archive, SQLite, Qdrant, A/B/C layers): storage is already lossless (Postgres compresses large text), so the archive layer is not needed; what is missing is fetching the original turns back — _2026-11-14_ | **Open** (BACKLOG L20 O63), built 2026-11-14 (user: build steps 1–3, nothing for the old chats). Waiting: the API restart (it applies migration 003, `brain.chat_passages`) and the user's `python -m src.llm.bench_recall`. Threshold measured with CPU embeddings: the right piece 0.68–0.80, the next best 0.40–0.56, unrelated ≤ 0.35 → `MIN_SIMILARITY` 0.60. Plan as built: (1) index each finished exchange in pgvector, embedded on the CPU in the background; (2) when a chat has a summary, put the few earlier original passages that best match the new message (vector + exact words, so IPs and names match) next to the summary, within the history budget; (3) a memory eval: facts said early in a long chat, asked after they are folded, summary only vs summary + recall. Later: recall across the user's own chats |

## Solved issues

| # | Issue | Fix |
|---|-------|-----|
| ~~O2~~ | ~~Midjourney flags ignored~~ | **Solved** — #23 |
| ~~O3~~ | ~~"create a schematic …" goes to chat~~ | **Solved** — #29 |
| ~~O6~~ | ~~No streaming, no proper Stop button~~ | **Solved** — #36 |
| ~~O7~~ | ~~The assistant can only advise, never act ("check all the IPs … table")~~ | **Solved for LAN scans** — `network_scan` tool built (Option A, [TOOLS_DECISION.md](TOOLS_DECISION.md) §7): 25 devices in 9.6 s, table + .xlsx. More tools (networking, security) follow the same pattern |
| ~~O8~~ | ~~"Make this a PDF/DOCX/PPT" loses or cuts the earlier answer; often no file at all~~ — _2026-11-02_ | **Cause:** (1) prior turns are cut to 6,000 chars each / 24,000 total by character count ([conversation.py](../src/core/conversation.py)) inside an 8K-token context, so a long answer arrives truncated; (2) the agentic worker must re-type the whole answer as one JSON tool call, which the 9B model truncates → invalid JSON → no file. **Solved 2026-11-02** (BACKLOG L10): an existing answer is converted directly (no LLM, read in full from its job events), "Download as" menu under every answer, tables/code kept; new documents are written section by section with a check per section (`doc_writer.py`); file names follow the topic |
| ~~O9~~ | ~~Chat history is trimmed by characters, not tokens; nothing tells the user the chat is getting long~~ — _2026-11-02_ | **Solved** (BACKLOG L11 + L12): memory rebuilt from the chat's jobs and budgeted in tokens, oldest turns folded into a saved running summary, memory meter, 80% banner with "Summarize & continue in a new chat" / "Save summary" |
| ~~O10~~ | ~~Answers were hard to read (dense text)~~ — _2026-11-02_ | **Solved** — typography pass (0.97rem, line-height 1.7, heading font) + light/dark/system theme |
| ~~O11~~ | ~~Marketing specialist cannot crawl/audit another website or run a structured writing flow; no way to judge a site's "maturity" or improve copy over drafts~~ — _2026-11-02_ | **Solved** (BACKLOG L13): `site_audit` (polite crawl → 20 checks → maturity score + level → fix plan) and `write_content` (brief → draft → checks + editor → revisions, best draft wins; can start from an existing page). Assistant eval run pending |
| ~~O14~~ | Keyword/SERP results come from SearXNG's own region (UK) while the user and many target sites are in Pakistan; content-gap topics still list some menu phrases — _2026-11-03_ | **Cause:** no language/region sent to SearXNG; the menu filter only drops phrases found on *every* page, so a menu that differs between pages slips through. **Solved 2026-11-03** (BACKLOG L15 P6): market from the user's words, else the site (declared locale, country domain), else asked once; sent to SearXNG as `language=en-PK`; `nav`/`aside`/site header and footer removed from page text |
| ~~O15~~ | Typing `/` in the chat shows no skills (the menu lists only skills added to Yours, and none were added — the built-in ones were not usable until copied); typing in the message box lags in long chats — _2026-11-03_ | **Cause:** the menu listed only Yours; the whole conversation (Markdown) re-rendered on every key. **Solved 2026-11-03** (BACKLOG L14 S2 fix): built-in skills usable straight away; message feed memoised |
| ~~O17~~ | "Access the NVR's web interface at http://192.168.190.248 and tell me all the cameras" → the assistant can only ping/scan, so it tells the user to log in themselves; no tool reads a recorder's channel list — _2026-11-04_ | **Solved 2026-11-04** (BACKLOG L17; live: 27 cameras on the DS-7732NI-M4): read-only `nvr_cameras` tool over the vendors' own APIs (Hikvision ISAPI first — the 192.168.190.248 NVR answers `/ISAPI` with a Digest login — then Dahua, then ONVIF), NVR login kept in memory like server logins |
| ~~O20~~ | Settings → Users mixes the create form, the list and an inline Devices tick; access is one flag per feature, edited in a different place (a modal) than creation — _2026-11-04_ | **Solved 2026-11-04** (BACKLOG L19 D1c): one full-page user form for create and edit, with per-module access (Devices, Skills, Usage) enforced by the API |
| ~~O23~~ | Device logins live in memory only (gone at logout/restart); the user wants them saved, each user's own, encrypted — _2026-11-04_ | **Solved 2026-11-04** (BACKLOG L19b D4 / L16 S5): AES-256-GCM in the database; replaces the memory-only decision (SECURITY_TOOLS_DECISION option B) |
| ~~O24~~ | Three requirements files (`requirements.txt`, `-dev`, `-image-cu118`); the user wants one. After the move to Ubuntu (Python 3.14) the cu118 file cannot install at all (no cu118 wheels past Python 3.13), and the default torch (cu132) has no kernels for the Quadro P4000 (sm_61) — _2026-11-07_ | **Solved 2026-11-07** (BACKLOG L20): one `requirements.txt` with pytest and `torch==2.14.1+cu126` from the PyTorch index (the newest torch with a cu126 build for Python 3.14; cu126 still carries Pascal kernels) |
| ~~O25~~ | The API does not start on Ubuntu: `src/tools/market.py` imports `babel`, but `Babel` is not in `requirements.txt` (it only arrived on Windows by chance) → `import web.server` fails with `ModuleNotFoundError: No module named 'babel'` — _2026-11-07_ | **Solved 2026-11-07** (BACKLOG L20): `Babel` added; an import scan of `src/`, `web/`, `tests/` found four more modules imported directly but only present through other packages (`cryptography`, `numpy`, `psutil`, `urllib3`) — now listed too. `import web.server` OK; 738 passed, 27 skipped (25 = Postgres not running yet) |
| ~~O26~~ | The separate worker cannot start: `worker_main.py` imports `src.config`, `src.db`, `src.worker`, which no longer exist (now `src.core.config`, `src.jobs.worker`, …). The API's in-process worker is unaffected — _2026-11-07_ | **Solved 2026-11-10** (BACKLOG L20): `worker_main.py` on `src.core.config`, `src.core.db`, `src.jobs.worker`; also checks `DEVICE_KEY` and creates the log/artifact folders like the API, no longer prints the database password, does not free 'running' jobs at start (they may belong to the API), Ctrl+C stops it cleanly. +1 offline test; not run against the live queue |
| ~~O27~~ | All data lost moving from Windows to Ubuntu: Postgres lived in the Docker volume `pgdata` inside Docker Desktop's virtual disk, not in the project folder; `.env`, `artifacts/`, `agents/*.json` are git-ignored. There is no backup or restore procedure — _2026-11-07_ | **Solved 2026-11-10** (BACKLOG L20): `backup.sh` — `pg_dump -Fc` + `.env` + `artifacts/` + `agents/` + eval results + MANIFEST in one dated `.tar.gz` in `BACKUP_DIR` (default `backups/`, git-ignored, mode 600; not encrypted — user's choice for now). `restore.sh --verify` restores into a scratch database and shows row counts; `restore.sh` (API stopped, asks first) saves the current state, puts files back, rebuilds the database and re-creates logins/grants (`migrate` + `app_db setup`, which now hands restored `app` tables back to the app login). Daily schedule: cron line in `--help`. Checked live: backup 67 MB, verify OK (21 tables); a full restore over the live database not run yet |
| ~~O28~~ | `doc/DOCKER_FULL_STACK_PLAN.md` (draft, untracked) predates L16 S1/S1b and D4: it keeps and extends Caddy (HTTPS dropped), publishes the API on the LAN (`8787:8787`, `API_HOST 0.0.0.0`; should only be reached through Next.js), hardcodes `JWT_SECRET: change-me-in-production` (now refused) and passes no `DEVICE_KEY` (now required); the single `requirements.txt` puts GPU torch (~3 GB) + pytest into the API image; a containerised `next start` loses hot reload. Frontend still not in compose; Caddyfile serves a stub — _2026-11-07_ | **Solved 2026-11-10** (BACKLOG L20): user decided — **hybrid stays**, broken services removed. `docker-compose.yml` keeps `db`, `searxng`, `pgadmin`; `api`, `worker`, `caddy` (LAN ports 8787/80/443, owner login, hardcoded `JWT_SECRET`; a plain `docker compose up -d` or Portainer's Start would have started them) removed with `Dockerfile`, `deploy/Caddyfile`, volume `caddy_data`. README: no `--scale worker`, no owner login in `DATABASE_URL`. `DOCKER_FULL_STACK_PLAN.md`: what runs where + what a full stack would need |
| ~~O29~~ | First full `./start.sh` run fails: `~/.docker/config.json` still has `"credsStore": "desktop"`, left by Docker Desktop for Linux (installed 2026-11-06, then removed — `dpkg` state `rc`; Docker Engine `docker-ce` runs instead) → image pull fails with `docker-credential-desktop: executable file not found`, so Postgres never starts. `start.sh` then still starts the API (which exits: connection refused on 5432), gives no fix for the credential error, and its health loop prints a curl error every second for 120 s — _2026-11-10_ | **Solved 2026-11-10** (BACKLOG L20): `start.sh` names the missing credential helper and says to remove `credsStore` from `~/.docker/config.json` (other compose failures: last lines of its output); the API is not started and not waited for while the database is down; health polling is quiet. Checked with a copy of the bad config; the healthy path waits for the user's run |
| ~~O30~~ | `start.sh` health step crashes once the API is up: the inline Python (inside bash single quotes) wrote `\"` in f-string expressions; bash passes the backslashes through, and Python reports `SyntaxError: unexpected character after line continuation character` — _2026-11-10_ | **Solved 2026-11-10** (BACKLOG L20): plain double quotes (PEP 701 f-strings, Python 3.12+), checked against the running API's `/api/health` |
| ~~O31~~ | A fresh database (new machine, lost volume, restore) makes the API exit with `password authentication failed for user "multeagent_app"`: Docker only creates the owner login, and the two one-time steps (`python -m src.brain.migrate`, `python -m src.core.app_db setup`) must be remembered and run by hand. A `pg_dump` restore brings the same problem back, because a dump carries no logins — _2026-11-10_ | **Solved 2026-11-10** (BACKLOG L20): `python -m src.core.app_db prepare` runs each step only when needed (pending migrations, missing `brain_app` / app login or schema; a normal start changes nothing); `start.sh` calls it once the database is healthy. 4 offline tests (fresh, ready, restored dump, new migration); live: "Database already set up."; 766 passed, 2 skipped |
| ~~O33~~ | The database tests run against the live `multeagent` database the API uses. `test_only_one_refresh_runs_at_a_time` takes the brain refresh lock with a blocking `pg_advisory_lock`, so while the API's scheduler refreshes (hours on a fresh database: 34 sources) the suite hangs; test rows are also written into live tables (cleaned up afterwards) — _2026-11-10_ | **Solved 2026-11-10** (BACKLOG L20): `tests/conftest.py` points `BRAIN_DATABASE_URL` at `<name>_test` before any test builds a Config and creates that database (extensions `vector`, `pg_trgm`) when missing; migrations run there as before. Advisory locks are per database, so the API's refresh lock can no longer block the suite; the lock test also sets `lock_timeout` (fails instead of hanging). Checked during a live API: 770 passed, 2 skipped, nothing deselected, 68 s (was 254 s); live `multeagent` untouched |
| ~~O34~~ | After `./start.sh` the user cannot see what runs or what it does: the API and UI write to `logs/*.log`, the containers and Ollama to their own logs, and the brain refresh logs one line per finished source (a source can run 20 min in silence; its per-file progress is thrown away) — _2026-11-10_ | **Solved 2026-11-10** (BACKLOG L20): the refresh logs `brain refresh n/total:` with every file; `./start.sh --status` (containers + Portainer, Ollama models and where they run, GPU memory, API health/queue, UI, brain progress and whether a refresh runs, newest backup) and `./start.sh --follow [parts]` (API, UI, db, SearXNG, pgAdmin, Ollama; tagged; Ctrl+C ends only the watching). +1 offline test |
| ~~O35~~ | The embedding model runs on the CPU during the brain refresh: Ollama loaded `qwen3.5:9b` fully on the 8 GB P4000 first (≈4.8 GB + context), then had no room left for `qwen3-embedding:0.6b` (1.2 GB) and started it on the CPU; the refresh keeps it busy, so it never reloads on the GPU — sources take 5–20 min each. Seen via `./start.sh --status` — _2026-11-10_ | **Solved 2026-11-10 — not a fault** (BACKLOG L20): CPU is the design — `src/brain/embed.py` sends `num_gpu: 0` with every embedding (BRAIN_DESIGN). Measured: chat model 6.5 GB on the GPU + desktop ≈0.8 GB leave ≈0.9 GB, too little for the embedding model (≈1.3 GB); a chat query embedding on the CPU takes 0.09 s (7 s first load), so only a full re-ingest (fresh/restored database) is slow. User confirmed: embedding stays on the CPU. Fixed the misleading `--status` wording ("on the CPU (slow)" → "on the CPU, as intended"); the earlier advice to unload it was wrong (it reloads on the CPU) |
| ~~O36~~ | No abuse limits: `/api/auth/login` accepts unlimited password guesses (per account and per address), no per-user request rate, no request body cap. Every request reaches the API through the Next.js rewrite on 127.0.0.1, so the client address must come from `X-Forwarded-For`, trusted only from that proxy — _2026-11-11_ | **Solved 2026-11-11** (BACKLOG L16 S2): per account 5 free failures, back-off 1–30 s, 10 in 15 min → 15-min lock; per address 20 in 15 min → 15-min block; 120 requests/min per user (burst 60), 60/min per address without sign-in; 30 MB body cap; 429 + `Retry-After`. Next's rewrite adds no `X-Forwarded-For` and passes a forged one on, so `frontend/server.mjs` overwrites it with the socket address. One user per PC assumed. Admin unlock + CAPTCHA follow as S2b |
| ~~O37~~ | `python -m src.llm.assistant --eval` fails: `FileNotFoundError: data/assistant/eval.yaml`. `.gitignore` ignores `data/*` and keeps only `data/brain/`, `data/brain_eval/`, `data/router/` — the assistant eval cases (27 by 2026-11-02, more since: NVR, MikroTik, skills) were never committed and stayed behind on Windows (like O27) — _2026-11-11_ | **Solved 2026-11-11** (BACKLOG L20): the old cases were written down nowhere (docs give only counts), so a new set of 34 was written — every one of the 20 tools at least once, direct answers, clarifying questions, 4 skills; `data/assistant/` tracked from now on. Left: the user runs the eval |
| ~~O38~~ | Users cannot create an account themselves: `POST /api/auth/register` is switched off (403), only an admin adds users. User asks for self sign-up, and about Clerk and OAuth 2.0 — _2026-11-11_ | **Solved 2026-11-11** (BACKLOG L16 S7): own sign-up (username, email, optional phone, password) — open on the page (admin switch) or by a one-use 7-day invite link; every new account waits for an admin's approval; 5 sign-ups per address per day. Google/Microsoft sign-in later (needs HTTPS); no Clerk |
| ~~O39~~ | After S2 a locked account or blocked PC can only wait 15 min — an admin cannot unlock it; and since 10 wrong passwords lock an account, anyone can lock someone else out on purpose. Nothing tells a script from a person on the login and sign-up forms — _2026-11-11_ | **Fixed 2026-11-11, live check passed** (BACKLOG L16 S2b): admin unlock (accounts and computers) + a self-hosted image CAPTCHA (6 characters, valid 5 min, works once; sign-up always, login from the 3rd failure; a try without it is not counted). Closed after the user's live check |
| ~~O40~~ | `./start.sh --stop` says "api/ui not started by this script" and leaves both running: it knows the API and UI only from `logs/*.pid`, which were gone (the API on 8787 and the UI on 3000 ran, both from this project, started with `setsid`). `stop_bg` also deleted a PID file even when its `kill` failed, without saying so — _2026-11-11_ | **Solved 2026-11-11** (BACKLOG L20): `--stop` finds the API and UI by their ports (and the PID file), stops only this project's processes (working directory inside the project) with their whole process group, waits up to 15 s for the port to close, then forces (SIGKILL), and says what it did; a process from elsewhere on the port is left alone and named. Checked on two stand-in services (spare ports); the real API/UI were not touched |
| ~~O41~~ | The docs are hard to find and keep up to date: 20 files in `doc/` with no index, BACKLOG.md (623 lines) mixes plans, long how-it-works notes of finished items and a progress log in no fixed order; the Open issues table mixes open and solved rows out of order; how the login protection (S1, S2, S2b, S7) works is spread over BACKLOG entries — _2026-11-11_ | **Solved 2026-11-11** (BACKLOG L21): `doc/README.md` indexes every doc by kind (working files, guides, decisions, plans, reference) with where new information goes; `doc/SECURITY.md` explains sign-in protection in plain words + technical reference + every number; BACKLOG has an "At a glance" table, items in number order, finished L16 parts shortened to a link; progress log moved to `doc/PROGRESS_LOG.md` (newest first); this table split into Open and Solved. Docs keep their paths (code comments link to them). Root README links fixed |
| ~~O42~~ | The UI's look lives in two places: about 60 hand-written classes in `globals.css` (`btn`, `md-*`, `app-*`, `confirm-*` …) next to Tailwind, and the short Tailwind names for the colour tokens (`text-muted`, `bg-panel` …) were never defined, so they gave no CSS — _2026-11-11_ | **Fixed 2026-11-11, checked by the user in both themes** (BACKLOG L22): `@theme inline` maps the tokens; every class moved into Tailwind utilities in its component, shared ones (buttons, icon buttons, message actions, brand mark, diagram card) in `frontend/src/lib/ui.ts`; globals.css 659 → 143 lines: tokens, theme mapping, base styles. Every class checked by compiling it with the project's Tailwind |
| ~~O43~~ | `frontend/src/components/chat/` holds 21 files, most of them not chat: the login screen, every Settings section (users, invites, servers, skills, usage, specialists); diagram and markdown components lie loose in `components/` — hard to find where something lives — _2026-11-11_ | **Fixed 2026-11-11** (BACKLOG L23): one folder per feature: `app`, `auth`, `chat`, `answer`, `settings` (+ `users/`, `skills/`); file names unchanged; every import now `@/components/<folder>/<Name>`; comments, `scripts/validate_mermaid.mjs` and docs updated; layout described in `doc/README.md`. tsc and eslint clean, dev server serves `/` and `/settings` |
| ~~O45~~ | "Download PNG" on a Mermaid flowchart fails ("Tainted canvases may not be exported"): Mermaid 11 ignores the deprecated `flowchart.htmlLabels: false`, draws the labels as HTML (`foreignObject`), and a canvas with HTML in it may not be exported. Found while testing the CSP (L16 S4); happens with or without it — _2026-11-11_ | **Fixed 2026-11-11**: `htmlLabels: false` at the top level of the Mermaid config (`components/answer/MermaidDiagram.tsx`); checked in headless Chrome with the app's settings: 0 HTML labels, PNG exported |
| ~~O49~~ | The web search runs on the user's raw message (typos included: "controvery", "chiness"), not on the search words the model wrote in web_search's `query`; a follow-up ("and his age?") searches without its subject. Cause: the worker passes only `intent` and `news` to `run_chat`, the tool's `query` is dropped — _2026-11-13_ | **Solved** 2026-11-13: the worker passes `query` to `run_chat(web_query=…)` → `answer_from_web(search_query=…)`; it finds and ranks the pages, the raw message only when it is empty ([HOW_QUERY_WORKS.md](HOW_QUERY_WORKS.md) B) |
| ~~O50~~ | News questions answered from the model's memory: "latest controversy of nvidia ceo jensen houng…" and "fastest chips china makes…" got "Not found in the knowledge base" + a General-knowledge answer, no sources (jobs 4a655cec, dc511bed, 2026-11-13). Cause: the tool-choice call returned 3–4 tokens, no text and no tool call (`llm_usage`: assistant output 3, 4); `decide()` then falls back to the knowledge base, which has nothing on news, and that falls back to memory. Why the call came back empty is not logged (no `done_reason`) — _2026-11-13_ | **Solved** 2026-11-13: a knowledge-base miss searches the web (sources, role "Web search") before the model's memory; memory only when web search is off or down (`answer_question(general_fallback=False)`). An empty tool-choice reply logs `done_reason`, output tokens and thinking length; the next one shows the cause ([HOW_QUERY_WORKS.md](HOW_QUERY_WORKS.md) A) |
| ~~O51~~ | Questions about things that change answered directly from memory, despite the O46 rule: GTA 6 download time (size guessed "100–150 GB", job 9e287471), "are Chinese allowed to use Nvidia chips" (job f97bb19d, says "based on recent reports" without a search), "does China allow importing Nvidia chips" (job 70618e1e, contradicts itself: "Yes … does NOT allow"). The GTA answer also stops after "Speed: 100 Mbps": the model generated 134 tokens and ended there, no math. Eval 25/36: 3 of the 8 shown misses come from the pre-routing keyword lists ("15% of 2400" and "compare my site …" nudged to web_search, a clear security check sent to clarify) — _2026-11-13_ The size is on the web: run by hand on 2026-11-13, the search finds Rockstar's Newswire (pre-load ~212 GB) and 6 outlets agreeing (209.75–212 GB); the chat never searched. Even when it searches, social-media results (Reddit, X, Facebook, YouTube) take the 4 read slots and refuse page readers, so all 4 passages were snippets and Rockstar's page (result 5) was never read | **Solved** 2026-11-13 (eval 31/38, was 25/36; the router's own 2 misses fixed, see O44): user decided 2026-11-13: a short routing call (search / clarify / model) replaces the keyword lists; a `calculate` tool. Also: a page that cannot be read frees its read slot for the next result. Part 1 built 2026-11-13: the routing call ([HOW_QUERY_WORKS.md](HOW_QUERY_WORKS.md) Layer 5); it also sets the search words, `news` and `learning` (quiz only then). Part 2: an unreadable page frees its read slot (GTA: 3 real pages read, was 0). Part 3: `calculate` (exact arithmetic in the tool choice and the web answer; the local model computed 212 GB at 100 Mbps = 4.71 h). Waiting: the eval rerun |
| ~~O54~~ | Web answers mix in facts from earlier in the chat: "who is afia sadiqui" (job 13e1f64d) says U.S. agencies allege she "was recruited by China's Ministry of State Security" and that the claims were dismissed as "groundless smears and slanders" — both from the Jensen/Alice Huang answer earlier in the same chat; the children's names (Zaynab, Marium…) are Moazzam Begg's family, from his own article on cage.ngo. Cause: the web answer gets the whole prior conversation (summary + earlier answers) above the sources, and the 9B model blends them. Also "who is emmila clark" was answered from memory with invented roles (Jane Hawking in The Theory of Everything, Top Boy) and a "[1]" with no sources — _2026-11-13_ | **Solved** 2026-11-13: a web or deep answer sees the user's last 2 questions only, never earlier answers or the chat summary; the prompt says every fact comes from the sources; questions about real people, films and games search ([HOW_QUERY_WORKS.md](HOW_QUERY_WORKS.md) B, Layer 5) |
| ~~O55~~ | Follow-ups get a clarifying question though the subject is plain: "name of lead wowen actress" after a Spider-Man answer (options: The Matrix, Inception…), the user's typed answer "Spider-Man: Brand New Day" (asked again: "What platform?"), "who is she", "what did she did…" (jobs e9536ff6, 960c2f86, cf7a8e71, 5af222a8). The router saw the previous message and still said `clarify`; the clarifying call never sees the chat — _2026-11-13_ | **Solved** 2026-11-13: the routing prompt clarifies only a recommendation missing details and joins a follow-up with the previous message (live: all 4 messages now search with the subject filled in); never clarify right after a clarifying reply (a code check); the clarifying call sees the previous message ([HOW_QUERY_WORKS.md](HOW_QUERY_WORKS.md) Layer 5) |
| ~~O56~~ | One page listed as several sources (filmibeat 1 and 2, dadnology 2 and 3, Wikipedia 1 and 2), with junk section labels ("› No entries found"); answers still say "Based on the provided sources…" — _2026-11-13_ | **Solved** 2026-11-13: one numbered source per page (`by_page`, web and deep research), section label only when one section; the prompt names "the provided sources" as wording to avoid ([HOW_QUERY_WORKS.md](HOW_QUERY_WORKS.md) B) |
| ~~O57~~ | GTA download time: "1,696 ÷ 100 = 16.96 hours" (right is 4.71 h): the model skipped the unit steps (Mb to Gb, seconds to hours) and never called `calculate` (no `calculate` line in the log). The `job_timing` step "calculator" is the whole web answer, a misleading name — _2026-11-13_ | **Solved** 2026-11-13: the router marks `calculation`; the web answer then gets the instruction right after the question; `calculate` takes units (`212 GB / 100 Mbps in hours`) and converts them, since with plain numbers the model still skipped steps (16.8 h). Live: 4.71 h (GTA 6), 9.09 h (Black Ops 7), 20–23 s per web answer. The timing step is now `web_search` ([HOW_QUERY_WORKS.md](HOW_QUERY_WORKS.md) Layer 5, B) |
| ~~O58~~ | The chat summarises after ~3 exchanges (17–19 s each time): the context is 8,192 tokens (8 GB Quadro P4000, 7 GB used) and history gets 35% of it (~2,870 tokens). The model keeps memory per token in 8 of 32 layers only (~32 KB/token): 16k costs ~256 MB more, ~1.1 GB is free — _2026-11-13_ | **Solved** 2026-11-13: measured with `bench_resources` (8192 / 12288 / 16384): 12288 costs 140 MB over 8192 and stays on the GPU, 16384 spills 12% to the CPU; Ollama puts at most ~5.9 GB of the model on the card. The user set **12288** (history ~4,300 tokens). Results, limits and options: [RESOURCES.md](RESOURCES.md). Earlier plan: user sets `LLM_NUM_CTX=16384` in `.env` and checks `ollama ps` / `nvidia-smi` Measured 2026-11-13 at 16384: the model takes 6.7 GB and runs 16% on the CPU (`ollama ps` 16%/84%), 7 of 8 GB used, and the embedding model (knowledge base, example router) no longer fits beside it: an embedding call waited for minutes. The context share was right (~512 MB), the compute buffers grow with it too. Next: back to 8192, or try 12288 and check `ollama ps` shows 100% GPU To measure each size: `python -m src.llm.bench_resources` (user runs it) |
| ~~O59~~ | User messages reach the model and the database as typed: an API key, private key, token or `PASSWORD=` value pasted into a chat is saved in `jobs.query` and the chat history and can go out in a web search; chat-template tags (`<|im_start|>system`) in pasted text can open a fake system turn; no size limit on a message — _2026-11-13_ | **Solved** 2026-11-13: `src/llm/prompt_filter.py` checks each message in `create_job`, before routing and saving: secrets and control tags masked (earlier turns too), over `PROMPT_FILTER_MAX_CHARS` refused, an optional fast-model check on long messages (`PROMPT_FILTER_LLM`, off). No list of attack phrases (the no-keyword-lists rule; it would refuse "you are now my tutor") ([SECURITY.md §1.8](SECURITY.md#18-what-a-message-may-carry-s8), BACKLOG L16 S8) |
| ~~O60~~ | "tell me the issues in this website from seo prespective https://clickmasters.pk/" (Marketing specialist, job 54807aa1) went to web search, which found the site's own service pages and answered "Based on the provided sources, I could not find specific technical issues". The routing call (O51) chose `search` for a named site to check; `site_audit` was never offered the message — _2026-11-13_ | **Solved** 2026-11-13: the routing prompt starts with the rule: a named website, URL, IP, device or file to check, audit, scan, compare or find issues in → the tool choice; keyword and ranking research too. Live: 9 such messages route right, reviews, prices and versions still search; the tool choice then calls `site_audit` (17 s). Eval case added ([HOW_QUERY_WORKS.md](HOW_QUERY_WORKS.md) Layer 5) |
| ~~O61~~ | The tool choice answers directly where a tool was wanted (eval 2026-11-13, in O44 since 2026-11-11): `about_multeagent` ("which model are you running"), the MikroTik VLAN steps (from memory), `nvr_cameras`, `write_content` (blog post written directly). The tool choice also costs ~20 s on an ~8,000-token prompt (O52) — _2026-11-13_ | **Solved** 2026-11-13, eval **36/38**, median 5.2 s (was 31/38, 9.6 s); the 2 left are the clarify cases, where the model asks in prose instead of with buttons. User decided 2026-11-13: the routing call may name the tool. Built: route `tool` with arguments, checked in code (offered here, required arguments filled, never `operate_server`), else the tool choice. Router alone on the 38 eval texts (live): right tool 29, wrong 0; the rest handed to the tool choice or clarify as designed; 4.7 s median, a ~1,900-token prompt. |
