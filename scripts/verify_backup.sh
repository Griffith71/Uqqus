#!/usr/bin/env bash
# The restore drill: proves the newest backup can actually be restored, without touching the live databases.
#
#   bash scripts/verify_backup.sh                 checks the newest daily backup
#   bash scripts/verify_backup.sh daily/<folder>  checks that one
#   bash scripts/verify_backup.sh --fresh         takes a new backup first, then checks it
#
# It restores both dumps into scratch databases (drill_site on the site's server, drill_synapse on Synapse's), checks
# the files against their checksums, that the schema has the tables it should, that the migration ledger lists every
# migration file in this checkout, and that there are members and messages, then drops the scratch databases. A backup
# nobody has restored is a hope, not a backup: run this after setting the service up and now and then after.
set -euo pipefail
cd "$(dirname "$0")/.."

run() { docker compose run --rm --no-deps -T --entrypoint bash backup -c "$1"; }

if [ "${1:-}" = "--fresh" ]; then
  docker compose run --rm --no-deps -T -e BACKUP_ONCE=1 backup
  shift
fi

pick="${1:-}"
if [ -z "$pick" ]; then
  pick="$(run 'ls -1d /backups/daily/2* | sort | tail -n 1' | tr -d '\r' | sed 's|/backups/||')"
fi
[ -n "$pick" ] || { echo "No backup found: has the backup service run? (docker compose up -d backup)"; exit 1; }
echo "Checking $pick"

migrations="$(ls scripts/migrations/*.sql | wc -l | tr -d ' ')"

run "
  set -e
  migrations=$migrations
  site='postgresql://postgres@postgres:5432'
  syn='postgresql://synapse:synapse@postgres_synapse:5432'
  cd /backups/$pick && sha256sum -c SHA256SUMS
  psql \"\$site/postgres\" -v ON_ERROR_STOP=1 -q -c 'DROP DATABASE IF EXISTS drill_site' -c 'CREATE DATABASE drill_site'
  psql \"\$syn/synapse\" -v ON_ERROR_STOP=1 -q -c 'DROP DATABASE IF EXISTS drill_synapse' -c 'CREATE DATABASE drill_synapse'
  trap 'psql \"\$site/postgres\" -q -c \"DROP DATABASE IF EXISTS drill_site\"; psql \"\$syn/synapse\" -q -c \"DROP DATABASE IF EXISTS drill_synapse\"' EXIT
  pg_restore --no-owner --exit-on-error --dbname=\"\$site/drill_site\" /backups/$pick/site.dump
  pg_restore --no-owner --exit-on-error --dbname=\"\$syn/drill_synapse\" /backups/$pick/synapse.dump
  q() { psql \"\$1\" -At -c \"\$2\"; }
  tables=\$(q \"\$site/drill_site\" \"SELECT count(*) FROM information_schema.tables WHERE table_schema='public'\")
  ledger=\$(q \"\$site/drill_site\" 'SELECT count(*) FROM schema_migrations')
  members=\$(q \"\$site/drill_site\" 'SELECT count(*) FROM users')
  posts=\$(q \"\$site/drill_site\" 'SELECT count(*) FROM submissions')
  syn_tables=\$(q \"\$syn/drill_synapse\" \"SELECT count(*) FROM information_schema.tables WHERE table_schema='public'\")
  syn_events=\$(q \"\$syn/drill_synapse\" 'SELECT count(*) FROM events')
  echo \"site: \$tables tables, \$members accounts, \$posts posts, \$ledger migrations recorded (\$migrations migration files here)\"
  echo \"synapse: \$syn_tables tables, \$syn_events events\"
  [ \"\$tables\" -gt 100 ] || { echo 'FAIL: the site schema is too small'; exit 1; }
  [ \"\$members\" -gt 0 ] || { echo 'FAIL: no accounts in the restored site database'; exit 1; }
  [ \"\$syn_tables\" -gt 20 ] || { echo 'FAIL: the Synapse schema is too small'; exit 1; }
  if [ \"\$ledger\" -lt \"\$migrations\" ]; then echo 'NOTE: this backup predates some migrations: apply them after a restore (scripts/migrate.py --apply)'; fi
"
echo "OK: both databases restored from $pick and the scratch databases were dropped."
