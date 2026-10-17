#!/usr/bin/env bash
# Backs up the site's database and Synapse's together (audit D-07). They must always be restored as a pair: Matrix
# ids come from user ids, so restoring one without the other can hand a new member an old member's chat account
# (routes/chat.py then refuses chat for that member until the pair is put back, or MATRIX_ADOPT_EXISTING=1).
#
#   bash scripts/backup.sh [directory]          writes <directory>/<UTC time>/{ruqqus,synapse}.dump
#
# Restore both, with the site stopped:
#   docker compose exec -T postgres pg_restore -U postgres -d postgres --clean --if-exists < ruqqus.dump
#   docker compose exec -T postgres_synapse pg_restore -U synapse -d synapse --clean --if-exists < synapse.dump
# then apply any newer migrations: PYTHONPATH=. python scripts/migrate.py --apply
#
# Docker images and build cache are not part of a backup; a host that builds often should prune them now and then
# (`docker image prune` and `docker builder prune`, audit D-09): a full disk stops Postgres writing.
set -euo pipefail
cd "$(dirname "$0")/.."
out="${1:-backups}/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$out"
docker compose exec -T postgres pg_dump -U postgres -Fc postgres > "$out/ruqqus.dump"
docker compose exec -T postgres_synapse pg_dump -U synapse -Fc synapse > "$out/synapse.dump"
ls -l "$out"
