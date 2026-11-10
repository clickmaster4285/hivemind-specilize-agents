#!/usr/bin/env bash
# Bring MulteAgent back from an archive made by backup.sh.
#
#   ./restore.sh --verify <archive>   test it: restore the database into a scratch database, show its row
#                                     counts, drop it again. Changes nothing; the API may keep running.
#   ./restore.sh <archive>            put everything back: replaces the database, .env, artifacts/, agents/
#                                     and the eval results. Asks first; the API must be stopped.
#   ./restore.sh --yes <archive>      the same without the question
#
# A real restore first saves the current state with backup.sh (label "before-restore"), so it can be undone.
# On a new machine: clone the repo, install as in the README, then run this — it starts the database itself.

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/deploy/shell-common.sh"

VERIFY=0; YES=0; ARCHIVE=""
while (($#)); do
    case "$1" in
        --verify) VERIFY=1; shift ;;
        --yes) YES=1; shift ;;
        -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        -*) echo "unknown option: $1 (see --help)"; exit 2 ;;
        *) ARCHIVE="$1"; shift ;;
    esac
done
[[ -n "$ARCHIVE" ]] || { echo "which archive? (see --help)"; exit 2; }
[[ -f "$ARCHIVE" ]] || { bad "not found: $ARCHIVE"; exit 1; }

# A scratch database next to the real one, for --verify
VERIFY_DB="${DB_NAME}_verify"

restore_dump() {  # restore_dump <database> <dump file> — into an empty database, all or nothing
    # Owners and grants are left out: a dump carries no logins, and the target may not have them yet.
    # migrate + app_db setup re-create the logins and give back what is theirs (see below).
    compose exec -T db pg_restore -U "$DB_OWNER" -d "$1" --no-owner --no-privileges \
        --single-transaction --exit-on-error <"$2"
}

recreate_db() {  # recreate_db <database> — drop (closing open connections) and create it empty
    db_sql postgres "DROP DATABASE IF EXISTS \"$1\" WITH (FORCE)"
    db_sql postgres "CREATE DATABASE \"$1\" OWNER \"$DB_OWNER\""
}

step "Archive"
work="$(mktemp -d)"
cleanup() {
    rm -rf "$work"
    if ((VERIFY)) && db_running; then db_sql postgres "DROP DATABASE IF EXISTS \"$VERIFY_DB\" WITH (FORCE)" || true; fi
}
trap cleanup EXIT
umask 077
tar -C "$work" -xzf "$ARCHIVE"
top=("$work"/*)
if ((${#top[@]} != 1)) || [[ ! -f "${top[0]}/db.dump" || ! -f "${top[0]}/MANIFEST" ]]; then
    bad "not a backup.sh archive (expected one folder with db.dump and MANIFEST)"
    exit 1
fi
backup="${top[0]}"
sed 's/^/   /' "$backup/MANIFEST"

if ((VERIFY)); then
    step "Test restore into the scratch database $VERIFY_DB"
    db_running || { bad "the database container is not running — start it:  ./start.sh"; exit 1; }
    recreate_db "$VERIFY_DB"
    restore_dump "$VERIFY_DB" "$backup/db.dump"
    ok "the dump restores without errors; rows per table:"
    db_sql "$VERIFY_DB" "$ROW_COUNTS_SQL" | awk -F'\t' '{printf "        %-32s %8s\n", $1, $2}'
    ok "scratch database dropped — nothing was changed"
    exit 0
fi

step "Checks"
if port_open "$API_PORT"; then
    bad "the API is running — stop it first:  ./start.sh --stop"
    exit 1
fi
ok "API stopped"
if ((!YES)); then
    echo "   This replaces the database, .env, artifacts/, agents/ and the eval results with the archive's."
    read -r -p "   Type 'restore' to go on: " answer
    [[ "$answer" == "restore" ]] || { skip "cancelled — nothing changed"; exit 1; }
fi

step "Saving the current state first"
if db_running; then
    # stops here if it fails: never replace anything without a way back
    "$ROOT/backup.sh" --label before-restore
    ok "saved — to undo the restore, restore that archive"
else
    skip "database not running — no current database to save (a new machine?)"
fi

step "Files"
for path in "${BACKUP_PATHS[@]}"; do
    if [[ -e "$backup/files/$path" ]]; then
        rm -rf "${ROOT:?}/$path"
        mkdir -p "$ROOT/$(dirname "$path")"
        cp -a "$backup/files/$path" "$ROOT/$path"
        ok "$path"
    else
        skip "$path (not in the archive — left as it is)"
    fi
done

step "Database"
# started after .env is back: on a new machine its POSTGRES_PASSWORD becomes the owner's password
compose up -d --wait db >/dev/null
recreate_db "$DB_NAME"
restore_dump "$DB_NAME" "$backup/db.dump"
ok "database restored"
(cd "$ROOT" && "$PYTHON" -m src.brain.migrate && "$PYTHON" -m src.core.app_db setup) | sed 's/^/   /'
ok "logins and grants re-created from .env"

echo
echo "   Done. Start everything with:  ./start.sh"
