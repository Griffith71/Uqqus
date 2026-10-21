#!/usr/bin/env bash
# Restores a backup pair taken by the `backup` service (scripts/backup_loop.sh), with the site stopped.
#
#   bash scripts/restore.sh                      lists the backups there are
#   bash scripts/restore.sh daily/20261021T0200Z restores that one (a folder under the backups volume)
#   bash scripts/restore.sh latest               restores the newest daily one
#
# It stops the site and Synapse, REPLACES both databases with the dumps, starts them again and tells you to apply any
# migration newer than the dump (the app also prints which are missing at start-up).
# It asks first. Both databases always go back together (Matrix ids come from user ids: audit D-07).
# The media volumes and the Synapse signing keys are separate: RESTORE.md.
set -euo pipefail
cd "$(dirname "$0")/.."

run() { docker compose run --rm --no-deps -T --entrypoint bash backup -c "$1"; }

if [ $# -eq 0 ]; then
  echo "Backups in the volume (newest last):"
  run 'ls -1d /backups/daily/2* /backups/weekly/2* 2>/dev/null | sed "s|/backups/||"' || true
  echo
  echo "Restore one with: bash scripts/restore.sh <folder>   (or: latest)"
  exit 0
fi

pick="$1"
if [ "$pick" = "latest" ]; then
  pick="$(run 'ls -1d /backups/daily/2* | sort | tail -n 1' | tr -d '\r' | sed 's|/backups/||')"
fi
[ -n "$pick" ] || { echo "No backup found."; exit 1; }

echo "This REPLACES the site's database and Synapse's with the backup '$pick'."
echo "Everything written since that backup is lost."
read -r -p "Type YES to continue: " answer
[ "$answer" = "YES" ] || { echo "Cancelled."; exit 1; }

run "cd /backups/$pick && sha256sum -c SHA256SUMS"      # the dumps are the ones that were written

docker compose stop ruqqus synapse
run "
  set -e
  site='postgresql://postgres@postgres:5432'
  syn='postgresql://synapse:synapse@postgres_synapse:5432'
  psql \"\$site/postgres\" -v ON_ERROR_STOP=1 -c \"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='postgres' AND pid <> pg_backend_pid()\" > /dev/null
  pg_restore --clean --if-exists --no-owner --dbname=\"\$site/postgres\" /backups/$pick/site.dump
  pg_restore --clean --if-exists --no-owner --dbname=\"\$syn/synapse\" /backups/$pick/synapse.dump
"
docker compose up -d synapse ruqqus
echo
echo "Restored. Now apply any migration newer than the backup (the app prints which are missing at start-up):"
echo "  docker compose exec ruqqus sh -c 'PYTHONPATH=. python scripts/migrate.py --status'"
echo "  docker compose exec ruqqus sh -c 'PYTHONPATH=. python scripts/migrate.py --apply'"
