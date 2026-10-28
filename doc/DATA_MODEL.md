# MulteAgent — Data model (two Postgres schemas)

*Last updated 2026-10-28.*

MulteAgent stores everything in **one Postgres database** split into **two schemas** that are two different
systems with different data, access libraries, and security models:

- **`app`** — the product *running*: who's using it, what they asked, the chat UI, feedback.
- **`brain`** — the product's *knowledge*: ingested documents → embeddings → retrieval (RAG).

(`public` is the empty default schema; we don't use it.) They live in the same database for simple ops —
one Postgres, one backup — but stay cleanly separated: the app only looks in its own schema, and the brain
is always addressed explicitly as `brain.*`. See [db.py](../src/core/db.py) (*"the brain lives in the same
database"*).

---

## `app` schema — operational / runtime data

SQLAlchemy ORM ([src/core/models_db.py](../src/core/models_db.py)), created with `create_all` + lightweight
column adds (`_ensure_column`), accessed through the app's normal login. Also runs on SQLite by default
(dev); your Postgres deployment puts these in the `app` schema.

| Table | Purpose |
|---|---|
| `users` | accounts — username, password hash, role (member/admin), daily job quota |
| `jobs` | one row per request/task — query, mode (auto/chat/agentic), status, payload, model plan |
| `job_events` | the ordered (`seq`) stream of events per job — the timeline the UI replays when you reopen a chat |
| `chat_threads` | chat metadata per user — title, pin, project, deletion — so history syncs across devices |
| `projects` | grouping of chats into projects |
| `feedback` | 👍/👎 + optional reason note, one row per (user, job) — see [RESPONSE_QUALITY.md](RESPONSE_QUALITY.md) feedback phases |
| `model_registry` | known models — backend, VRAM class, role (fast/strong/vision/image), enabled/available flags |
| `command_log` | audit trail of commands the agent ran on a server (host + command kept, **never** the password) — see [SERVER_ACCESS_DECISION.md](SERVER_ACCESS_DECISION.md) |

A chat is not its own table of messages: it's a `chat_threads` row (metadata) plus the `jobs` that carry its
`thread_id`, each job's turn rebuilt from its `job_events`. See [CHAT_HISTORY_DECISION.md](CHAT_HISTORY_DECISION.md).

---

## `brain` schema — knowledge base (RAG)

Raw **psycopg + pgvector** ([src/brain/db.py](../src/brain/db.py)), **not** the ORM — it needs the `vector`
type and hand-written similarity SQL. Fully documented in **[BRAIN_DESIGN.md](BRAIN_DESIGN.md) §3**; summary:

| Table | Purpose |
|---|---|
| `sources` | configured knowledge sources (the slugs from `sources.yaml`), public or private/owned |
| `documents` | documents ingested from each source |
| `chunks` | chunked text + **embedding vectors** (pgvector) — what similarity search runs over |
| `collections` | groupings of documents (e.g. per-specialist knowledge sets) |
| `ingest_runs` | a log of each ingestion run |
| `retrieval_log` | per-query log of which chunks were retrieved + their scores (job_id → chunk_ids) |
| `schema_migrations` | the brain's own migration version tracking |

---

## Why they're separated (not one schema)

1. **Different access + types.** `app` is ordinary relational ORM data; `brain` needs pgvector and custom
   vector SQL. One ORM over both would be awkward.
2. **Different security model.** The brain uses **two Postgres roles** — an *owner* login that runs
   migrations, and a locked-down `brain_app` login for runtime — with **row-level security always on**: each
   request sets `brain.account_id` / `brain.system` so private documents stay per-account and only the
   ingestion service writes public knowledge ([BRAIN_DESIGN.md §3.4](BRAIN_DESIGN.md)). The `app` schema has
   no per-row isolation; it's plain app data behind the app's auth.
3. **Different lifecycle.** The brain has its own migration runner (`schema_migrations`, owner-run) and an
   ingestion/scheduler pipeline; the app uses `create_all` + `_ensure_column`.
4. **One instance, clean namespacing.** Same database = simple ops; two schemas = independent, isolated,
   separately migratable.

**In one line:** `app` = *the assistant running*; `brain` = *the assistant's knowledge*. Different jobs,
different rules, co-located in one Postgres.
