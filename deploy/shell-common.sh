# Shared by start.sh, backup.sh and restore.sh (sourced, not run).

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT/.venv/bin/python"

# Same default as src/core/config.py (API_PORT); override in the environment if changed
API_PORT="${API_PORT:-8787}"

# The database and its owner login, as docker-compose.yml creates them (POSTGRES_DB / POSTGRES_USER)
DB_NAME=multeagent
DB_OWNER=multeagent

step() { printf '\n\033[36m== %s\033[0m\n' "$1"; }
ok()   { printf '   \033[32mOK\033[0m   %s\n' "$1"; }
skip() { printf '   \033[90m--   %s\033[0m\n' "$1"; }
bad()  { printf '   \033[31mFAIL\033[0m %s\n' "$1"; }

port_open() { ss -ltnH "sport = :$1" 2>/dev/null | grep -q .; }

# docker compose in the project folder, so it finds docker-compose.yml and .env
compose() { (cd "$ROOT" && docker compose "$@"); }

db_running() { [[ -n "$(compose ps --status running -q db 2>/dev/null)" ]]; }

# SQL as the owner, inside the database container (local socket: no password needed)
db_sql() {  # db_sql <database> <sql>  — prints unaligned rows, stops on the first error
    # warnings and errors only: no "does not exist, skipping" notices from IF EXISTS
    compose exec -T -e PGOPTIONS='-c client_min_messages=warning' db \
        psql -U "$DB_OWNER" -d "$1" -v ON_ERROR_STOP=1 -qtA -F $'\t' -c "$2"
}

# Row count of every table outside the system schemas: what a backup holds, at a glance
ROW_COUNTS_SQL="SELECT n.nspname || '.' || c.relname,
       (xpath('/row/n/text()', query_to_xml(format('SELECT count(*) AS n FROM %I.%I', n.nspname, c.relname),
                                            false, true, '')))[1]::text
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relkind IN ('r', 'p') AND n.nspname NOT IN ('pg_catalog', 'information_schema')
   AND n.nspname NOT LIKE 'pg\\_toast%'
 ORDER BY 1"

# Besides the database, what a backup carries (paths in the project; git-ignored, so git does not keep them).
# .env must travel with the dump: its DEVICE_KEY decrypts the saved device logins.
# Not carried, as they rebuild themselves: data/brain_cache (fetched sources), models, .venv, node_modules.
BACKUP_PATHS=(.env artifacts agents data/brain_eval/results)
