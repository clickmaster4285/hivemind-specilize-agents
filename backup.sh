#!/usr/bin/env bash
# Back up MulteAgent into one dated archive: everything needed to bring it back on this or another machine.
#
#   db.dump     the whole database (pg_dump custom format): users, chats, jobs, devices, skills, knowledge base
#   files/      .env, artifacts/, agents/, data/brain_eval/results/ — see BACKUP_PATHS in deploy/shell-common.sh
#   MANIFEST    when, from which commit, Postgres version, applied migrations
#
# The archive is NOT encrypted (user's choice for now) and holds every secret (.env, DEVICE_KEY, password
# hashes): treat it like a password file. It is readable by you only; copy it off this machine.
# Safe while the API runs: pg_dump reads one consistent snapshot.
#
#   ./backup.sh                          archive into $BACKUP_DIR (default: backups/ in the project)
#   BACKUP_DIR=/media/usb ./backup.sh    somewhere else, e.g. a USB drive or NAS mount
#   ./backup.sh --label before-update    adds a word to the file name
#
# Daily, e.g. at 02:30 (crontab -e):
#   30 2 * * *  cd /path/to/multeagent && ./backup.sh >> logs/backup.log 2>&1
#
# Check a backup without changing anything:  ./restore.sh --verify <archive>

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/deploy/shell-common.sh"

BACKUP_DIR="${BACKUP_DIR:-$ROOT/backups}"
LABEL=""
while (($#)); do
    case "$1" in
        --label) LABEL="${2:?--label needs a word}"; shift 2 ;;
        -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $1 (see --help)"; exit 2 ;;
    esac
done
[[ "$LABEL" =~ ^[A-Za-z0-9_-]*$ ]] || { bad "--label: letters, digits, - and _ only"; exit 2; }

umask 077   # the archive holds every secret: this user only
name="multeagent-$(date +%Y%m%d-%H%M%S)${LABEL:+-$LABEL}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/$name/files" "$BACKUP_DIR"

step "Database"
if ! db_running; then
    bad "the database container is not running — start it:  ./start.sh  (or: docker compose up -d db)"
    exit 1
fi
compose exec -T db pg_dump -U "$DB_OWNER" -d "$DB_NAME" -Fc >"$work/$name/db.dump"
ok "dumped ($(du -h "$work/$name/db.dump" | cut -f1))"

step "Files"
for path in "${BACKUP_PATHS[@]}"; do
    if [[ -e "$ROOT/$path" ]]; then
        mkdir -p "$work/$name/files/$(dirname "$path")"
        cp -a "$ROOT/$path" "$work/$name/files/$path"
        ok "$path"
    else
        skip "$path (not there)"
    fi
done

{
    echo "created:    $(date --iso-8601=seconds)"
    echo "host:       $(hostname)"
    echo "commit:     $(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)$(git -C "$ROOT" diff --quiet HEAD 2>/dev/null || echo ' (with uncommitted changes)')"
    echo "postgres:   $(db_sql "$DB_NAME" "SHOW server_version")"
    echo "migrations: $(db_sql "$DB_NAME" "SELECT string_agg(filename, ' ' ORDER BY filename) FROM brain.schema_migrations" 2>/dev/null || echo none)"
    echo "files:      ${BACKUP_PATHS[*]}"
} >"$work/$name/MANIFEST"

step "Archive"
archive="$BACKUP_DIR/$name.tar.gz"
tar -C "$work" -czf "$archive" "$name"
ok "$archive ($(du -h "$archive" | cut -f1))"
echo
echo "   Not encrypted and holds every secret: copy it off this machine and keep it like a password file."
echo "   Test it any time:  ./restore.sh --verify $archive"
