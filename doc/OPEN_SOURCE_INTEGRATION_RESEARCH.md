# Open-Source Project Integration Research

**Date:** 2026-10-30
**Status:** Research only — no code changed. All four themes complete.
**Purpose:** Evaluate a set of gathered open-source projects for possible *future* integration into
MulteAgent. Use this to pick one, then write an implementation plan. Nothing here has been built.

> How we got here: the URLs below were gathered as candidate projects to "embed." We split them into
> four themes and ran one read-only web researcher per theme, then synthesised. Figures (stars/commits)
> were verified against the GitHub API on 2026-10-30 where noted; treat others as approximate.

---

## Decision filters (apply BEFORE any feature discussion)

Two filters decide whether a project is even usable here; most candidates fail one of them:

1. **Local-first (hard constraint).** MulteAgent runs local models only via Ollama; **no cloud
   model/API calls**, secrets only in `.env`. A hosted SaaS, or a project whose core depends on a
   paid/cloud API, **cannot be embedded as a local capability** — at best it's a reference.
2. **License.** To embed *code* you need permissive (MIT/Apache). **AGPL** = copyleft, legally heavy
   for a product (borrow ideas, don't lift code). **No license** = all-rights-reserved by default
   (you can't even copy it safely). "Open" in a name guarantees neither.

**Integration shapes** a project can map to:
- **(A) Capability** — a backend tool/job (`web.server` + `src/jobs`) plus a frontend control.
- **(B) Brain KB source** — ingest its open docs into pgvector via `data/brain/sources.yaml`.
- **(C) Reference-only** — study the UI/architecture pattern and re-implement; don't take the code.

**Cross-cutting finding:** for almost every candidate, the embeddable value is the **UI/architecture
pattern (reference)**, *not* the code — blocked by either license or a cloud dependency. The one clear
exception is **OpenWhispr** (voice), which is MIT, truly local, and matches our frontend stack.

---

## Theme 1 — Voice / Speech-to-text

### OpenWhispr  ★~10,349 (verified 2026-10-30)
- **Repo/site:** https://github.com/OpenWhispr/openwhispr · https://openwhispr.com/
- **What:** Cross-platform voice-to-text dictation ("open alternative to WisprFlow"); global-hotkey
  dictation, meeting transcription w/ diarization, notes, audio/video import.
- **License:** **MIT** — permissive, ideal for reuse.
- **Local vs cloud:** **Local AND cloud.** Local models (Whisper / NVIDIA Parakeet) run on-device via
  **whisper.cpp + sherpa-onnx**, GPU-accelerated (Metal/CUDA/Vulkan), CPU works. Cloud providers
  (OpenAI/Anthropic/etc.) are **optional BYOK** → we simply disable them and run local-only. ✅ fits.
- **Stack:** React 19, TypeScript, Tailwind v4, Electron 41, better-sqlite3, shadcn/ui — **matches
  MulteAgent's frontend almost exactly.** Node 24+ to build.
- **Maturity:** Very active (last push 2026-10-30), ~2.5k commits, docs + CI. Desktop-first (global
  hotkey / paste-at-cursor), so the reusable part is the capture + local-Whisper transcription logic,
  not the desktop shell.
- **Fit:** **Best candidate overall.** (A) a local STT capability and/or a reusable mic component;
  MIT + stack match make real code reuse viable.
- **Verdict:** 🟢 **Primary candidate.** MIT, truly local via whisper.cpp, stack-matched.

### VoiceStudio  ★~94,935 (verified 2026-10-30 — unusually high; reported as-is)
- **Repo/site:** https://github.com/debpalash/VoiceStudio · http://voicestudio.sh/
- **What:** Broad "on-device AI voice studio" — voice cloning/design, dubbing, audiobooks, **plus** a
  local dictation widget + STT via whisper.cpp. STT is one feature among many.
- **License:** **AGPL-3.0** — copyleft. Model weights carry separate per-model licenses.
- **Local vs cloud:** Fully local (whisper.cpp / GGUF; CUDA/ROCm/Metal/Vulkan/CPU). (Intel-Mac build
  uses a remote backend — irrelevant on Windows.) ✅ local.
- **Maturity:** Explosive and actively maintained; named team, CHANGELOG, CI. A large Electron app.
- **Fit:** (C) **reference** for the whisper.cpp dictation pattern, or run its Python backend as a
  **local sidecar service**. **Do not lift its code** — AGPL would pull copyleft onto MulteAgent.
- **Verdict:** 🟡 Legit & local, but **AGPL makes code reuse heavy.** Reference / sidecar only.

### OpenWhisper (openwhisper.org)  ★~17
- **Repo/site:** https://openwhisper.org/ · https://github.com/JamesHuckle/OpenWhisper
- **What:** Tiny Windows/Android "floating pill" dictation tool.
- **License:** **Unknown** (none declared; repo tiny).
- **Local vs cloud:** **Cloud-only** — pipes audio to OpenAI, needs your OpenAI API key. ❌ violates
  local-first outright.
- **Maturity:** Minimal (single author, ~17 stars).
- **Verdict:** 🔴 **Reject.** Cloud-only + unverified license + immature.

**Theme verdict:** Pursue **OpenWhispr (MIT)** as the primary reference/component source; use
**VoiceStudio** as a secondary reference (no code copy — AGPL); drop **openwhisper.org** (cloud-only).

---

## Theme 2 — SEO

### OpenSEO  ★~25.7k
- **Repo/site:** https://github.com/every-app/open-seo · https://openseo.so/
  *(these are the SAME project — repo + its hosted SaaS/docs, not two projects.)*
- **What:** Self-hostable "open alternative to Semrush/Ahrefs": keyword research, rank tracking,
  backlinks, site audits, domain overview, Local SEO, "AI visibility." Ships an **MCP server +
  reusable "Agent Skills"** so coding agents can drive it. Data/analytics suite, **not** an on-page
  content-rewriting tool.
- **License:** **MIT.** (A third-party claim of "no sublicensing" is wrong — authoritative LICENSE is MIT.)
- **Local vs cloud:** **Two hard cloud dependencies.** (1) All SEO data comes from **DataForSEO**, a
  paid cloud API (`DATAFORSEO_API_KEY`); no offline data mode. (2) AI features run on Cloudflare
  Workers AI / cloud LLM gateways — **no Ollama / local-endpoint override found** in `.env.example`.
  ❌ cannot be a *local* SEO capability.
- **Stack:** TypeScript, TanStack Start, Vite, Drizzle, Postgres+Redis, heavy Cloudflare. Docker +
  Cloudflare self-host paths. Pre-v1.0 (v0.1.x) → API churn.
- **Maturity:** Very active (release 2026-10-30), real docs, Discord.
- **Fit:** (A) **blocked** by DataForSEO + cloud LLM. (B) its MIT docs can be ingested into the
  `marketing/seo` brain collection (modest value — product how-to, not SEO theory). (C) **strong
  reference** — the MCP-server + Agent-Skills pattern is a good model for our SEO specialist's tools.
- **Verdict:** 🟡 Impressive MIT project, but **SEO value is gated behind paid cloud data** → not a
  local embed. **Reference-only** (MCP/Agent-Skills pattern) + optional docs ingest.

**Theme verdict:** No local SEO capability here. Mine the **MCP/Agent-Skills architecture** as a
reference for the existing SEO specialist; optionally ingest its docs into the brain.

---

## Theme 3 — AI media / video ("open-higgsfield")

> Relationship note: **openhiggsfield.ai is the hosted deployment of `wide-trace/open-higgsfield`**
> (site vs. source). `Autom8AI/Open-Higgsfield-AI` is a separate lineage. **All three are front-ends
> that call a remote generation API — none run video/image weights locally.**

### openhiggsfield.ai  +  wide-trace/open-higgsfield  ★~5.1k (but only ~3 commits)
- **Repo/site:** https://github.com/wide-trace/open-higgsfield · https://www.openhiggsfield.ai/
- **What:** Next.js 16 / React 19 / Zustand studio UI; catalog of ~38 image/video models (Soul,
  Seedance, Kling, Wan, Flux, LTX…). A **UI wrapper, not a model.**
- **License:** **None declared** → all-rights-reserved → **can't copy code.**
- **Local vs cloud:** **Cloud-bound.** Server actions POST to `{HF_API_BASE_URL}/{model}` with an API
  key; uploads → Vercel Blob. Provider unnamed but almost certainly **Higgsfield's own paid cloud API**
  (inferred from key format + model brands). ❌ no local inference.
- **Fit:** (C) **UI reference only** — its stack matches ours exactly (Next 16 / React 19 / Zustand),
  so its prompt-bar / per-model-settings / job-object / gallery patterns are instructive for a media
  studio wired to **our own local SDXL backend**. Re-implement (no license).

### Autom8AI/Open-Higgsfield-AI  ★~1.7k (sources conflict: ~602–1.7k)
- **Repo:** https://github.com/Autom8AI/Open-Higgsfield-AI
- **What:** Next.js 14 / React 18 + Electron studio for image/video/lip-sync. **"Powered by Muapi.ai"**
  (paid cloud aggregator). Fork of Anil-matcha/Open-Generative-AI.
- **License:** **MIT.**
- **Local vs cloud:** **Cloud-bound** — every job goes to Muapi's servers; **no local pipeline.** ❌
- **Fit:** 🔴 Thin client over paid Muapi SaaS; remove Muapi and nothing generates. Older stack than ours.

**Theme verdict:** 🔴 **Reject all three as integrations** (no local video engine; cloud-bound). If we
want local video, the real path is a genuine **open-weights model (Wan / LTX / Hunyuan / CogVideoX-class)
served in-process like the existing SDXL** — not a wrapper. Best UI reference: `wide-trace` (stack match,
but re-implement — no license).

---

## Theme 4 — Agent / eval harness

### deepseek-ai/deepseek-harness  ★~266k (volatile; "very large and growing")
- **Repo:** https://github.com/deepseek-ai/deepseek-harness
- **What:** A full **agent runtime/harness** (`dsh`) — a Claude-Code / Gemini-CLI-style coding-and-task
  agent with Web UI, CLI, sessions, subagents, jobs, skills, MCP, plan/goal modes, sandbox, permission
  guard. **NOT an eval or benchmark harness** (its `benchmarks/` dir only measures its own TS
  performance, e.g. session latency — not model/agent quality).
- **License:** **MIT** (verified) — no blocker.
- **Local vs cloud:** Default/first-class provider is **DeepSeek's own cloud**. Local models **are**
  supported via a generic OpenAI-compatible / self-hosted adapter (`openai-completions` with a custom
  `baseURL` + placeholder key), so pointing it at Ollama's `http://localhost:11434/v1` is **technically
  possible** and the no-cloud rule is satisfiable — **but "Ollama" is never named in the docs**
  (inferred, not an officially tested recipe). ⚠️ unverified.
- **Stack:** **TypeScript / Node.js** pnpm monorepo (~20k files). The `python/` dir is only a **client
  SDK that launches the bundled Node `dsh` as a subprocess** over JSON-RPC — not a native Python
  library. Heavy/awkward to embed in a Python FastAPI service.
- **Maturity:** Genuinely from the `deepseek-ai` org; created 2026-08-13, last push 2026-10-30, very
  high velocity. **Explicit developer preview** (`0.2.x` RC) with bold "there will be
  compatibility-breaking changes" warning. Issues disabled (triage in Discussions).
- **Fit:** Does **not** fill MulteAgent's eval/benchmark gap (skip it for evals — keep
  `src/brain/evaluate.py`, `answer_eval.py`, `src/llm/bench_models.py`). As a runtime it **overlaps/
  competes** with MulteAgent's own backend/jobs/specialists rather than slotting in. **Real value =
  reference architecture:** its plugin/provider abstraction, subagent routing, durable-step retries,
  context-pressure "token-meter," plan/goal modes, and permission/guard design are a strong modern
  blueprint for our orchestration + specialist layer.
- **Verdict:** 🟡 **Reference/inspiration only — do not adopt as a library.** Wrong type (runtime, not
  eval), wrong stack (Node/TS), and an unstable preview; mine it for orchestration ideas.

**Theme verdict:** Does not meet an eval need and doesn't embed cleanly; study its architecture for
MulteAgent's own jobs/specialists/orchestration, don't vendor it.

---

## Comparison at a glance

| Project | Theme | License | Local-first fit | Maturity | Best use for MulteAgent |
|---|---|---|---|---|---|
| **OpenWhispr** | Voice/STT | **MIT** | ✅ local (whisper.cpp), cloud optional | High, active | 🟢 **(A) local STT capability + mic component** |
| VoiceStudio | Voice/STT | AGPL-3.0 | ✅ local | Very high, active | 🟡 (C) reference / sidecar; no code copy |
| openwhisper.org | Voice/STT | Unknown | ❌ cloud-only (OpenAI) | Tiny | 🔴 reject |
| OpenSEO | SEO | MIT | ❌ needs DataForSEO (paid) + cloud LLM | High, active | 🟡 (C) MCP/Agent-Skills pattern; (B) docs ingest |
| wide-trace/open-higgsfield | Media | **None** | ❌ cloud API | Hyped, ~3 commits | 🟡 (C) UI pattern (stack match); re-implement |
| openhiggsfield.ai | Media | n/a (hosted) | ❌ SaaS | Live site | 🔴 reject (= wide-trace hosted) |
| Autom8AI/Open-Higgsfield-AI | Media | MIT | ❌ needs Muapi (paid) | Medium | 🔴 reject |
| deepseek-harness | Harness | MIT | ⚠️ local possible (OpenAI-compat), unverified | Huge, preview/RC | 🟡 (C) architecture reference; don't vendor |

---

## Shortlist & recommended first pick

1. **Voice input (OpenWhispr-style local STT)** — the **only** theme with a permissive, truly-local,
   stack-matched option. Highest value-to-risk. Plan would be: a backend STT tool/job wrapping local
   Whisper (whisper.cpp or faster-whisper) + a mic control in the composer, referencing OpenWhispr's
   capture/transcription logic (MIT, safe to adapt).
2. **SEO** — no local embed; treat OpenSEO's **MCP + Agent-Skills** design as a reference to enrich the
   existing SEO specialist's tool surface; optionally ingest its docs into the brain.
3. **Media/video** — don't adopt any wrapper; if wanted later, evaluate an **open-weights video model**
   served like SDXL. Use `wide-trace`'s UI only as a pattern.
4. **Harness** — reference-only for orchestration ideas (plugin/provider abstraction, subagent routing,
   retries, plan/goal, guard). Does **not** fill the eval gap; keep our own eval scripts.

**Recommended first project to plan:** local **voice-to-text input** via the OpenWhispr pattern.

---

## Sources / tools used

Research was web-only (search + page/repo fetch); figures via GitHub API on 2026-10-30 where noted.

- Voice: https://github.com/OpenWhispr/openwhispr · https://openwhispr.com/ ·
  https://github.com/debpalash/VoiceStudio · http://voicestudio.sh/ ·
  https://openwhisper.org/ · https://github.com/JamesHuckle/OpenWhisper
- SEO: https://github.com/every-app/open-seo · https://openseo.so/ ·
  https://github.com/every-app/open-seo/blob/main/docs/DATAFORSEO_API_KEY.md ·
  https://raw.githubusercontent.com/every-app/open-seo/main/.env.example
- Media: https://www.openhiggsfield.ai/ · https://github.com/Autom8AI/Open-Higgsfield-AI ·
  https://github.com/wide-trace/open-higgsfield
- Harness: https://github.com/deepseek-ai/deepseek-harness ·
  https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/master/packages/llm/llm-pi-ai/README.md

### Flagged uncertainties
- VoiceStudio star count (~94,935) is unusually high; reported as the API returned it.
- Autom8AI star count conflicts across sources (~602 vs ~1.7k).
- `wide-trace`/`openhiggsfield.ai` generation provider is **inferred** (likely Higgsfield's cloud API), not stated in-repo.
- OpenSEO: no local-LLM override found in `.env.example`; a full source read of its AI layer would be needed to fully rule it out.
- deepseek-harness: local-Ollama use is **inferred** from documented OpenAI-compatible/self-hosted support, not an officially tested recipe; star count volatile (snapshots 22k–266k). Would need a hands-on spike to confirm local tool-calling/streaming.
- VRAM/model-size requirements for the voice/media projects were not published.
