-- Team knowledge (BACKLOG L30 T4; the user's design, 2026-11-20): next to public (everyone) and private (one
-- account), a TEAM scope — documents an admin uploads for one team, found only by that team's members.
-- A team row has owner_id = 'team:<team name>'. src/brain/db.py sets brain.team per transaction from the signed-in
-- account's team. Writing team rows is the system's (the upload goes through the API, which checks who may upload);
-- one person's private rows still never mix with shared ones.

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['sources', 'documents', 'chunks'] LOOP
    EXECUTE format('ALTER TABLE brain.%I DROP CONSTRAINT %I', t, t || '_scope_check');
    EXECUTE format('ALTER TABLE brain.%I DROP CONSTRAINT %I', t, t || '_scope_owner');
    EXECUTE format($f$ALTER TABLE brain.%I ADD CONSTRAINT %I CHECK (scope IN ('public', 'private', 'team'))$f$,
                   t, t || '_scope_check');
    EXECUTE format($f$ALTER TABLE brain.%I ADD CONSTRAINT %I CHECK (
                       (scope = 'public' AND owner_id IS NULL) OR
                       (scope IN ('private', 'team') AND owner_id IS NOT NULL))$f$, t, t || '_scope_owner');
  END LOOP;
END $$;

CREATE OR REPLACE FUNCTION brain.can_read(row_scope text, row_owner text) RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT row_scope = 'public'
      OR (row_scope = 'private' AND row_owner IS NOT NULL
          AND row_owner = nullif(current_setting('brain.account_id', true), ''))
      OR (row_scope = 'team' AND row_owner IS NOT NULL
          AND row_owner = 'team:' || nullif(current_setting('brain.team', true), ''))
$$;

CREATE OR REPLACE FUNCTION brain.can_write(row_scope text, row_owner text) RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT (row_scope IN ('public', 'team') AND coalesce(current_setting('brain.system', true), '') = 'on')
      OR (row_scope = 'private' AND row_owner IS NOT NULL
          AND row_owner = nullif(current_setting('brain.account_id', true), ''))
$$;
