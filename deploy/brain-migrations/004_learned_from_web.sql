-- Knowledge learned from web searches (BACKLOG L31, the user's ask 2026-11-20: "the first time we search the web,
-- the next time we serve it from the knowledge base, faster").
-- After a web answer, the pages it cited are kept as documents of one source in the collection 'web', public (no
-- owner: page text only, nothing about who asked), each with metadata.expires_at — news 1 day, versions / prices /
-- "latest" 7 days, the rest 90 days (src/brain/learned.py). Search leaves expired pages out (src/brain/retrieve.py);
-- the source is never crawled (enabled = false): its pages come only from answers.

INSERT INTO brain.collections (slug, name, description)
VALUES ('web', 'Learned from the web', 'Pages that web answers were written from, kept until they expire')
ON CONFLICT (slug) DO NOTHING;

INSERT INTO brain.sources (slug, kind, title, publisher, reason, added_by, collection_id, scope, enabled)
VALUES ('learned-from-web', 'web_page', 'Learned from web searches', 'the web',
        'pages a web answer was written from, reused for similar questions until they expire', 'multeagent',
        (SELECT id FROM brain.collections WHERE slug = 'web'), 'public', false)
ON CONFLICT (slug) DO NOTHING;

CREATE INDEX IF NOT EXISTS documents_expires_at ON brain.documents (((metadata->>'expires_at')));
