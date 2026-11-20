-- Team knowledge removed (the user, 2026-11-20: "no need of this — projects and share links cover documents"):
-- 005_team_scope.sql was applied, so it stays as it is and this file undoes it. Team rows go (none were ever
-- uploaded), and the scope rules are those of 001_init.sql again: public for everyone, private for one account.

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['chunks', 'documents', 'sources'] LOOP
    EXECUTE format('DELETE FROM brain.%I WHERE scope = %L', t, 'team');
    EXECUTE format('ALTER TABLE brain.%I DROP CONSTRAINT %I', t, t || '_scope_check');
    EXECUTE format('ALTER TABLE brain.%I DROP CONSTRAINT %I', t, t || '_scope_owner');
    EXECUTE format($f$ALTER TABLE brain.%I ADD CONSTRAINT %I CHECK (scope IN ('public', 'private'))$f$,
                   t, t || '_scope_check');
    EXECUTE format($f$ALTER TABLE brain.%I ADD CONSTRAINT %I CHECK (
                       (scope = 'public' AND owner_id IS NULL) OR
                       (scope = 'private' AND owner_id IS NOT NULL))$f$, t, t || '_scope_owner');
  END LOOP;
END $$;

CREATE OR REPLACE FUNCTION brain.can_read(row_scope text, row_owner text) RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT row_scope = 'public'
      OR (row_owner IS NOT NULL AND row_owner = nullif(current_setting('brain.account_id', true), ''))
$$;

CREATE OR REPLACE FUNCTION brain.can_write(row_scope text, row_owner text) RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT (row_scope = 'public'  AND coalesce(current_setting('brain.system', true), '') = 'on')
      OR (row_scope = 'private' AND row_owner IS NOT NULL
          AND row_owner = nullif(current_setting('brain.account_id', true), ''))
$$;
