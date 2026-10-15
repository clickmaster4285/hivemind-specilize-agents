-- Runs once, on the first start of an empty database volume.
-- vector  : embeddings + similarity search (pgvector)
-- pg_trgm : trigram matching for spelling variants (Hinglish: kaise / kese)
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
