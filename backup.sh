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
# Daily at 02:30: the systemd timer from  sudo deploy/install-services.sh  (BACKLOG L30 T1).
#
# Kept: the newest $BACKUP_KEEP automatic archives (default 14); archives made with --label are never removed.
# Off this disk: set BACKUP_COPY_TO (in the environment or as a line in .env) to a folder (a NAS mount, a USB
# drive) or an rsync target (user@nas:/backups/multeagent); each new archive is copied there, and a failed copy
# makes the run fail so the timer shows it.
#
# Check a backup without changing anything:  ./restore.sh --verify <archive>

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/deploy/shell-common.sh"

LABEL=""
while (($#)); do
    case "$1" in
        --label) LABEL="${2:?--label needs a word}"; shift 2 ;;
        -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $1 (see --help)"; exit 2 ;;
    esac
done
[[ "$LABEL" =~ ^[A-Za-z0-9_-]*$ ]] || { bad "--label: letters, digits, - and _ only"; exit 2; }

# a setting from the environment, else its line in .env (KEY=value, quotes allowed), else the default
setting() {  # setting <KEY> <default>
    local value="${!1:-}"
    if [[ -z "$value" && -f "$ROOT/.env" ]]; then
        value="$(grep -E "^[[:space:]]*$1=" "$ROOT/.env" | tail -1 | cut -d= -f2- | sed -E "s/^[\"']|[\"']\$//g")"
    fi
    echo "${value:-$2}"
}
BACKUP_KEEP="$(setting BACKUP_KEEP 14)"
BACKUP_COPY_TO="$(setting BACKUP_COPY_TO "")"
[[ "$BACKUP_KEEP" =~ ^[0-9]+$ ]] && ((BACKUP_KEEP > 0)) || { bad "BACKUP_KEEP must be a number above 0"; exit 2; }

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

step "Old archives"
# automatic ones only (no --label): multeagent-YYYYMMDD-HHMMSS.tar.gz, newest first; the rest beyond BACKUP_KEEP go
mapfile -t automatic < <(find "$BACKUP_DIR" -maxdepth 1 -type f -regextype posix-extended \
    -regex '.*/multeagent-[0-9]{8}-[0-9]{6}\.tar\.gz' -printf '%f\n' | sort -r)
if ((${#automatic[@]} > BACKUP_KEEP)); then
    for old in "${automatic[@]:BACKUP_KEEP}"; do
        rm -f -- "${BACKUP_DIR:?}/${old:?}"
        ok "removed $old (keeping the newest $BACKUP_KEEP)"
    done
else
    skip "${#automatic[@]} automatic archive(s), keeping up to $BACKUP_KEEP"
fi

step "Copy off this disk"
if [[ -z "$BACKUP_COPY_TO" ]]; then
    skip "BACKUP_COPY_TO is not set — the archive is only on this disk (a disk failure loses it)"
elif [[ "$BACKUP_COPY_TO" == *:* ]]; then
    if rsync -a --chmod=F600 "$archive" "$BACKUP_COPY_TO/"; then ok "copied to $BACKUP_COPY_TO"
    else bad "the copy to $BACKUP_COPY_TO failed"; exit 1; fi
else
    if [[ -d "$BACKUP_COPY_TO" ]] && cp -- "$archive" "$BACKUP_COPY_TO/"; then ok "copied to $BACKUP_COPY_TO"
    else bad "the copy to $BACKUP_COPY_TO failed (is the drive or share mounted?)"; exit 1; fi
fi
echo
echo "   Not encrypted and holds every secret: copy it off this machine and keep it like a password file."
echo "   Test it any time:  ./restore.sh --verify $archive"
