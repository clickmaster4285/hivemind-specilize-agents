-- 002_source_config — per-source settings that do not deserve their own column
-- (page URL template, repo branch, local folder path, document language, …).
ALTER TABLE brain.sources ADD COLUMN config jsonb NOT NULL DEFAULT '{}';
