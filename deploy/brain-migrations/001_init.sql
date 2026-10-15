-- 001_init — Knowledge brain schema (doc/BRAIN_DESIGN.md §3).
-- Applied by: python -m src.brain.migrate   (never edit after it has been applied; add 002_… instead)

-- ── Fields of knowledge ────────────────────────────────────────────────
CREATE TABLE brain.collections (
  id          smallserial PRIMARY KEY,
  slug        text UNIQUE NOT NULL,
  name        text NOT NULL,
  description text
);

INSERT INTO brain.collections (slug, name, description) VALUES
  ('it',          'IT',          'Infrastructure, Linux/Windows, networking, security'),
  ('development', 'Development', 'Programming, architecture, DevOps'),
  ('marketing',   'Marketing',   'Campaigns, copy, SEO, analytics'),
  ('general',     'General',     'Knowledge shared by every field');

-- ── Where knowledge comes from (mirrors data/brain/sources.yaml) ──────
CREATE TABLE brain.sources (
  id              bigserial PRIMARY KEY,
  slug            text UNIQUE NOT NULL,
  kind            text NOT NULL CHECK (kind IN ('web_page','sitemap','feed','git_repo','api','upload')),
  url             text,
  title           text NOT NULL,
  publisher       text,
  licence         text,
  reason          text,
  added_by        text,
  collection_id   smallint NOT NULL REFERENCES brain.collections(id),
  scope           text NOT NULL DEFAULT 'public' CHECK (scope IN ('public','private')),
  owner_id        text,
  agent_id        text,
  include_pattern text,
  max_pages       int,
  refresh_every   interval NOT NULL DEFAULT '7 days',
  enabled         boolean NOT NULL DEFAULT false,
  last_checked_at timestamptz,
  last_changed_at timestamptz,
  last_error      text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT sources_scope_owner CHECK (
    (scope = 'public'  AND owner_id IS NULL) OR
    (scope = 'private' AND owner_id IS NOT NULL)),
  CONSTRAINT sources_enabled_needs_licence CHECK (NOT enabled OR scope = 'private' OR licence IS NOT NULL)
);

-- ── One page / file / advisory (one row per version) ──────────────────
CREATE TABLE brain.documents (
  id            bigserial PRIMARY KEY,
  source_id     bigint NOT NULL REFERENCES brain.sources(id) ON DELETE CASCADE,
  url           text,
  title         text NOT NULL,
  language      text,
  published_at  timestamptz,
  updated_at    timestamptz,
  fetched_at    timestamptz NOT NULL,
  content_md    text NOT NULL,
  content_hash  text NOT NULL,
  version       int NOT NULL DEFAULT 1,
  status        text NOT NULL DEFAULT 'active' CHECK (status IN ('active','superseded','removed')),
  etag          text,
  metadata      jsonb NOT NULL DEFAULT '{}',
  collection_id smallint NOT NULL REFERENCES brain.collections(id),
  scope         text NOT NULL CHECK (scope IN ('public','private')),
  owner_id      text,
  agent_id      text,
  CONSTRAINT documents_scope_owner CHECK (
    (scope = 'public'  AND owner_id IS NULL) OR
    (scope = 'private' AND owner_id IS NOT NULL))
);

-- At most one active version of the same URL per source
CREATE UNIQUE INDEX documents_one_active_version
  ON brain.documents (source_id, url) WHERE status = 'active';
CREATE INDEX documents_source_url ON brain.documents (source_id, url);
CREATE INDEX documents_title_trgm ON brain.documents USING gin (title gin_trgm_ops);

-- ── Searchable passages ────────────────────────────────────────────────
CREATE TABLE brain.chunks (
  id            bigserial PRIMARY KEY,
  document_id   bigint NOT NULL REFERENCES brain.documents(id) ON DELETE CASCADE,
  chunk_index   int NOT NULL,
  heading_path  text,
  content       text NOT NULL,
  token_count   int NOT NULL,
  embedding     vector(1024) NOT NULL,   -- qwen3-embedding:0.6b
  tsv           tsvector GENERATED ALWAYS AS
                  (to_tsvector('simple', coalesce(heading_path, '') || ' ' || content)) STORED,
  collection_id smallint NOT NULL REFERENCES brain.collections(id),
  scope         text NOT NULL CHECK (scope IN ('public','private')),
  owner_id      text,
  agent_id      text,
  UNIQUE (document_id, chunk_index),
  CONSTRAINT chunks_scope_owner CHECK (
    (scope = 'public'  AND owner_id IS NULL) OR
    (scope = 'private' AND owner_id IS NOT NULL))
);

CREATE INDEX chunks_embedding_hnsw ON brain.chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX chunks_tsv_gin        ON brain.chunks USING gin (tsv);
CREATE INDEX chunks_visibility     ON brain.chunks (scope, owner_id, collection_id);

-- ── Every ingest run ───────────────────────────────────────────────────
CREATE TABLE brain.ingest_runs (
  id           bigserial PRIMARY KEY,
  source_id    bigint REFERENCES brain.sources(id) ON DELETE SET NULL,
  started_at   timestamptz NOT NULL DEFAULT now(),
  finished_at  timestamptz,
  status       text NOT NULL CHECK (status IN ('running','ok','partial','failed')),
  docs_new     int NOT NULL DEFAULT 0,
  docs_changed int NOT NULL DEFAULT 0,
  docs_same    int NOT NULL DEFAULT 0,
  docs_removed int NOT NULL DEFAULT 0,
  error        text
);
CREATE INDEX ingest_runs_source ON brain.ingest_runs (source_id, started_at DESC);

-- ── Which passages supported which answer ──────────────────────────────
CREATE TABLE brain.retrieval_log (
  id         bigserial PRIMARY KEY,
  account_id text,                       -- who asked (questions can be private)
  job_id     text,
  query      text NOT NULL,
  chunk_ids  bigint[] NOT NULL,
  scores     real[] NOT NULL,
  answered   boolean NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX retrieval_log_job ON brain.retrieval_log (job_id);

-- ── One line per source: contents and health (open in pgAdmin) ─────────
CREATE VIEW brain.source_status WITH (security_invoker = true) AS
SELECT s.slug, s.title, s.enabled, c.slug AS collection, s.licence,
       count(DISTINCT d.id) FILTER (WHERE d.status = 'active') AS active_docs,
       count(ch.id)                                            AS chunks,
       s.last_checked_at, s.last_changed_at, s.last_error,
       (SELECT r.status FROM brain.ingest_runs r
         WHERE r.source_id = s.id ORDER BY r.started_at DESC LIMIT 1) AS last_run_status
FROM brain.sources s
JOIN brain.collections c ON c.id = s.collection_id
LEFT JOIN brain.documents d ON d.source_id = s.id
LEFT JOIN brain.chunks ch ON ch.document_id = d.id AND d.status = 'active'
GROUP BY s.id, c.slug;

-- ── Row-level security: private data never mixes with the shared brain ─
-- Session settings (set per transaction by src/brain/db.py):
--   brain.account_id = '<account>'   → which private rows this session may see/write
--   brain.system     = 'on'          → ingestion service; may write PUBLIC rows
-- Read:  public rows, or private rows owned by the current account.
-- Write: public rows only as the system; private rows only by their owner.
-- The table owner (migrations, pgAdmin) is not subject to RLS; the app connects as brain_app.

CREATE FUNCTION brain.can_read(row_scope text, row_owner text) RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT row_scope = 'public'
      OR (row_owner IS NOT NULL AND row_owner = nullif(current_setting('brain.account_id', true), ''))
$$;

CREATE FUNCTION brain.can_write(row_scope text, row_owner text) RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT (row_scope = 'public'  AND coalesce(current_setting('brain.system', true), '') = 'on')
      OR (row_scope = 'private' AND row_owner IS NOT NULL
          AND row_owner = nullif(current_setting('brain.account_id', true), ''))
$$;

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['sources', 'documents', 'chunks'] LOOP
    EXECUTE format('ALTER TABLE brain.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY %I ON brain.%I FOR SELECT USING (brain.can_read(scope, owner_id))', t || '_read', t);
    EXECUTE format('CREATE POLICY %I ON brain.%I FOR INSERT WITH CHECK (brain.can_write(scope, owner_id))', t || '_insert', t);
    EXECUTE format('CREATE POLICY %I ON brain.%I FOR UPDATE USING (brain.can_write(scope, owner_id)) WITH CHECK (brain.can_write(scope, owner_id))', t || '_update', t);
    EXECUTE format('CREATE POLICY %I ON brain.%I FOR DELETE USING (brain.can_write(scope, owner_id))', t || '_delete', t);
  END LOOP;
END $$;

-- Questions may contain private details: each account sees only its own log rows.
ALTER TABLE brain.retrieval_log ENABLE ROW LEVEL SECURITY;
CREATE POLICY retrieval_log_own ON brain.retrieval_log
  USING (account_id = nullif(current_setting('brain.account_id', true), '')
         OR coalesce(current_setting('brain.system', true), '') = 'on')
  WITH CHECK (account_id = nullif(current_setting('brain.account_id', true), '')
              OR coalesce(current_setting('brain.system', true), '') = 'on');
