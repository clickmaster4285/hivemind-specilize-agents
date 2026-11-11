# MulteAgent docs — start here

Every doc in this folder: what it is for, and where new information goes. To set up and run the app, see the
project's [README.md](../README.md).

## 1. Working files: updated with every piece of work

| Doc | What it holds |
|---|---|
| [BACKLOG.md](BACKLOG.md) | **What is planned and its status.** "At a glance" table first; one `### Ln.` section per item, in number order |
| [ISSUES_LOG.md](ISSUES_LOG.md) | **Problems:** what we saw, the cause, the fix. Open and Solved tables (O-numbers) |
| [PROGRESS_LOG.md](PROGRESS_LOG.md) | **What got done, by date**, newest first |

## 2. How it works: guides, kept up to date with the code

| Doc | Covers |
|---|---|
| [HOW_QUERY_WORKS.md](HOW_QUERY_WORKS.md) | A chat message from Send to the answer, layer by layer |
| [SECURITY.md](SECURITY.md) | Sign-in and accounts: sessions, abuse limits, CAPTCHA, admin unlock, self sign-up; all the numbers |
| [DATA_MODEL.md](DATA_MODEL.md) | The database: the `app` and `brain` schemas and why they are separate |
| [BRAIN_DESIGN.md](BRAIN_DESIGN.md) | The knowledge brain (RAG): tables, ingestion, retrieval, answers |
| [DOCKER_FULL_STACK_PLAN.md](DOCKER_FULL_STACK_PLAN.md) | What runs where (hybrid: Docker for db + SearXNG, the rest on the host) |
| [FULL_PROJECT_PLAN.md](FULL_PROJECT_PLAN.md) | The whole system for a reviewer: goals, architecture, libraries and why |

## 3. Decisions: why we chose what we chose

Each one weighs the options and records the user's decision. Edit one only to add a new decision or a
"Built" section.

| Doc | Question | Status (as written in the doc) |
|---|---|---|
| [TOOLS_DECISION.md](TOOLS_DECISION.md) | Should the assistant *do* things (run tools), not only advise? | decided 2026-10-17, Option A built |
| [SECURITY_TOOLS_DECISION.md](SECURITY_TOOLS_DECISION.md) | Which machines may the security tools look at? | decided 2026-10-19 |
| [SERVER_ACCESS_DECISION.md](SERVER_ACCESS_DECISION.md) | Signing in to servers (SSH, RDP) and making changes; the device vault | to discuss (2026-10-21), parts built since |
| [CHAT_HISTORY_DECISION.md](CHAT_HISTORY_DECISION.md) | Chats and history per account and per machine | to discuss (2026-10-19) |
| [SEO_TOOLS.md](SEO_TOOLS.md) | What Semrush / Screaming Frog do and what we add (L15) | decided 2026-11-02; P2 on hold |

## 4. Plans and checklists: work on one topic

| Doc | Topic |
|---|---|
| [RESPONSE_QUALITY.md](RESPONSE_QUALITY.md) | Faster and deeper answers: plan by phase, plus its own log |
| [BRAIN_KB_PLAN.md](BRAIN_KB_PLAN.md) | Knowledge-base calibration and evals, one item at a time |
| [IMPROVEMENT_ROADMAP.md](IMPROVEMENT_ROADMAP.md) | Speed and capability tiers (GPU, cold start, documents) |
| [requirment.md](requirment.md) | The IT specialist's full feature set (P0–P4) and its build status |

## 5. Research and reference: read for background, rarely edited

| Doc | What it is |
|---|---|
| [OPEN_SOURCE_INTEGRATION_RESEARCH.md](OPEN_SOURCE_INTEGRATION_RESEARCH.md) | Open-source projects weighed for later (voice, SEO, media, evals); research only |
| [model_bench.md](model_bench.md) | Model comparison results, **generated** by `python -m src.llm.bench_models --judge-model qwen3.5:9b --out doc/model_bench.md`. Don't edit by hand |
| [image-generator.md](image-generator.md) | The first SDXL image-generation plan. File paths are from before the `src/` reorganisation (ISSUES_LOG #42); the current state is in the code and ISSUES_LOG #17–#28 |
| [raw-data.md](raw-data.md) | The original learning-path conversation the project grew from (BRAIN_DESIGN's Phase 1 checklist comes from it) |

---

## Where new information goes

| You have … | Put it in |
|---|---|
| a new task or idea | BACKLOG: a new `### Ln.` (next number) or a new line under an existing item, plus a row in "At a glance" |
| a problem, bug or surprise | ISSUES_LOG: next O-number in **Open issues**, linked from the BACKLOG item |
| a problem fixed | ISSUES_LOG: move the row to **Solved issues**, strike the number (`~~O41~~`), add the fix |
| finished work | PROGRESS_LOG: a row at the **top**. BACKLOG: tick the line, keep it to one or two lines, link the guide |
| how a finished feature works | the guide for its area (section 2), or a new guide added to this index |
| a choice between options | a `*_DECISION.md` (question → options → side by side → recommendation → user's decision) |
| a number someone may tune | a named constant in the code, listed in its guide (example: SECURITY.md Part 3) |

## Conventions

- **Numbers:**
  - `Ln` is a backlog item (L16).
  - A letter and number is a part of one item (L16 **S2b**, L19 **D3**).
  - `On` is an issue (O39). `#n` is one of the early issues (#1–#44).
- **Dates** are always `YYYY-MM-DD`.
- **Status words:**
  - "planned": decided, not started.
  - "in progress".
  - "built": works, waiting for the user's live check.
  - "done": checked.
  - "on hold": waiting for a decision.
- **Keep docs short:**
  - When a section of BACKLOG grows past about a screen, the detail belongs in a guide and BACKLOG keeps a link.
  - A doc past about 500 lines is a sign it holds two topics.
- **Links** are relative (`[SECURITY.md](SECURITY.md)`, code as `../src/...`). Code comments refer to docs as
  `doc/NAME.md`, so **rename or move a doc only together with a search for its old name**.
