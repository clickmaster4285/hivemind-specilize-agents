-- 003_chat_passages — chat recall (ISSUES_LOG O63): each finished exchange of a saved chat, in pieces, with
-- its embedding, so a long chat can bring back the original words of turns already folded into its summary.
-- Only the chat's owner reads or writes them (brain.account_id, set per transaction by src/brain/db.py).
CREATE TABLE brain.chat_passages (
  id          bigserial PRIMARY KEY,
  owner_id    text NOT NULL,             -- the user id, as brain.account_id
  thread_id   text NOT NULL,
  job_id      text NOT NULL,             -- the exchange: one question and its answer
  piece_index int NOT NULL,
  asked_at    timestamptz NOT NULL,      -- when the question was asked: shown with a recalled passage
  content     text NOT NULL,
  embedding   vector(1024) NOT NULL,     -- qwen3-embedding:0.6b
  UNIQUE (job_id, piece_index)
);

-- A search looks at one chat's passages only (tens to hundreds of rows) and compares them all: exact, and fast
-- at that size. An HNSW index would filter after the nearest-neighbour step and could miss the chat's rows.
CREATE INDEX chat_passages_thread ON brain.chat_passages (owner_id, thread_id);

ALTER TABLE brain.chat_passages ENABLE ROW LEVEL SECURITY;
CREATE POLICY chat_passages_own ON brain.chat_passages
  USING (owner_id = nullif(current_setting('brain.account_id', true), ''))
  WITH CHECK (owner_id = nullif(current_setting('brain.account_id', true), ''));
