#!/usr/bin/env bash
# The `backup` service's loop (docker-compose.yml): a dump of the site's database and Synapse's, together, every
# BACKUP_INTERVAL_HOURS (default 24), into the `backups` volume. They are always taken and restored as a pair: Matrix ids
# come from user ids (audit D-07), so one without the other can hand a new member an old member's chat account.
#
#   /backups/daily/<UTC time>/{site.dump,synapse.dump,SHA256SUMS}   the newest BACKUP_KEEP_DAILY (default 7)
#   /backups/weekly/<UTC time>/...                                  the Sunday ones, the newest BACKUP_KEEP_WEEKLY (4)
#   /backups/last_success, /backups/last_failure                    when it last worked; present while the latest try failed
#
# A dump is written beside its final name and renamed only when both databases dumped and both archives read back, so
# a failure never leaves a half-written backup that looks like a good one. The newer pg_dump of the postgres:15 client
# image reads the site's Postgres 12 as well as Synapse's 15; restore with the same image (scripts/restore.sh).
# BACKUP_ONCE=1 makes one backup and exits (scripts/verify_backup.sh, the restore drill).
#
# NOT here, and the runbook (RESTORE.md) says so: a copy off this machine, encryption of the dumps, and the media
# volumes (media-dev, synapse-media, the Synapse signing keys). Until a copy exists elsewhere, a dead disk loses both
# the site and its backups.
set -u

OUT="${BACKUP_DIR:-/backups}"
INTERVAL=$(( ${BACKUP_INTERVAL_HOURS:-24} * 3600 ))
KEEP_DAILY="${BACKUP_KEEP_DAILY:-7}"
KEEP_WEEKLY="${BACKUP_KEEP_WEEKLY:-4}"
SITE_URL="${BACKUP_SITE_URL:-postgresql://postgres@postgres:5432/postgres}"
SYNAPSE_URL="${BACKUP_SYNAPSE_URL:-postgresql://synapse:synapse@postgres_synapse:5432/synapse}"

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) backup: $*"; }

# keep the newest $2 of the dated folders in $1
prune() {
  local dir="$1" keep="$2"
  [ -d "$dir" ] || return 0
  ls -1d "$dir"/2* 2>/dev/null | sort | head -n "-$keep" | xargs -r rm -rf
}

backup_once() {
  local stamp tmp
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  tmp="$OUT/daily/.tmp-$stamp"
  mkdir -p "$tmp" || return 1
  if pg_dump -Fc --no-owner --dbname="$SITE_URL" -f "$tmp/site.dump" \
     && pg_dump -Fc --no-owner --dbname="$SYNAPSE_URL" -f "$tmp/synapse.dump" \
     && pg_restore -l "$tmp/site.dump" > /dev/null \
     && pg_restore -l "$tmp/synapse.dump" > /dev/null; then
    (cd "$tmp" && sha256sum site.dump synapse.dump > SHA256SUMS)
    mv "$tmp" "$OUT/daily/$stamp"
    if [ "$(date -u +%u)" = "7" ]; then
      mkdir -p "$OUT/weekly" && cp -r "$OUT/daily/$stamp" "$OUT/weekly/$stamp"
    fi
    prune "$OUT/daily" "$KEEP_DAILY"
    prune "$OUT/weekly" "$KEEP_WEEKLY"
    date -u +%Y-%m-%dT%H:%M:%SZ > "$OUT/last_success"
    rm -f "$OUT/last_failure"       # the service reads as healthy again only once a backup worked
    log "ok $stamp ($(du -sh "$OUT/daily/$stamp" | cut -f1))"
    return 0
  fi
  rm -rf "$tmp"
  date -u +%Y-%m-%dT%H:%M:%SZ > "$OUT/last_failure"
  log "FAILED: a database did not dump or an archive did not read back (nothing was kept)"
  return 1
}

mkdir -p "$OUT/daily"
rm -rf "$OUT"/daily/.tmp-* 2>/dev/null   # a dump a crash left half-written

if [ "${BACKUP_ONCE:-0}" = "1" ]; then
  backup_once
  exit $?
fi

while true; do
  if backup_once; then
    sleep "$INTERVAL"
  else
    sleep 3600     # try again within the hour: a night's failure shouldn't cost a day
  fi
done
