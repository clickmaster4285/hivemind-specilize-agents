-- Whole answers reused for repeat questions (BACKLOG L31 step 2; the user's choice, 2026-11-20: reuse the answer
-- exactly, labelled, with a "Fresh answer" button — no rewording). A grounded answer to a general first question is
-- kept with its question's embedding; a later question that means the same (src/brain/answer_cache.py: similarity
-- >= 0.92, the same exact identifiers, young enough for its kind) gets it at once instead of a new answer.
-- Only answers whose every source is public are kept, and nothing about who asked: the table is readable by every
-- session and written by the system only.

CREATE TABLE brain.answer_cache (
  id          bigserial PRIMARY KEY,
  question    text NOT NULL,
  embedding   vector(1024) NOT NULL,       -- the question's, as embed_query makes it
  answer      text NOT NULL,
  extra       jsonb NOT NULL DEFAULT '{}', -- role, sources, verification: what the chat shows under the answer
  freshness   text NOT NULL,               -- news | changing | lasting (src/brain/learned.py)
  job_id      text,                        -- the job that wrote it
  hits        int NOT NULL DEFAULT 0,
  created_at  timestamptz NOT NULL DEFAULT now(),
  expires_at  timestamptz NOT NULL
);
CREATE INDEX answer_cache_embedding_hnsw ON brain.answer_cache USING hnsw (embedding vector_cosine_ops);
CREATE INDEX answer_cache_expires ON brain.answer_cache (expires_at);

ALTER TABLE brain.answer_cache ENABLE ROW LEVEL SECURITY;
CREATE POLICY answer_cache_read ON brain.answer_cache FOR SELECT USING (true);
CREATE POLICY answer_cache_write ON brain.answer_cache FOR ALL
  USING (coalesce(current_setting('brain.system', true), '') = 'on')
  WITH CHECK (coalesce(current_setting('brain.system', true), '') = 'on');
