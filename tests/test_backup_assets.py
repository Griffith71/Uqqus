"""Scheduled backups (the `backup` service, scripts/backup_loop.sh, restore.sh, verify_backup.sh, RESTORE.md). The
promises: both databases are dumped and restored as a pair; a dump is kept only when both dumped and both archives read
back; a failure is visible (the service goes unhealthy) and retried; the restore asks first and checks checksums; and the
runbook is honest about what it does not cover."""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


LOOP = read("scripts", "backup_loop.sh")
RESTORE = read("scripts", "restore.sh")
VERIFY = read("scripts", "verify_backup.sh")
COMPOSE = read("docker-compose.yml")
RUNBOOK = read("RESTORE.md")


def test_both_databases_are_dumped_together_and_kept_only_when_they_read_back():
    body = LOOP.split("backup_once() {", 1)[1].split("\n}\n", 1)[0]
    for step in ("pg_dump -Fc --no-owner --dbname=\"$SITE_URL\"", "pg_dump -Fc --no-owner --dbname=\"$SYNAPSE_URL\"",
                 'pg_restore -l "$tmp/site.dump"', 'pg_restore -l "$tmp/synapse.dump"'):
        assert step in body, step
    # written beside its name and renamed only when everything worked; a failure removes it
    assert body.index("sha256sum") < body.index('mv "$tmp"')
    failure = body.split("  fi\n", 1)[1]                 # what runs when a dump or a read-back failed
    assert 'rm -rf "$tmp"' in failure and "return 1" in failure
    assert "last_failure" in body and 'rm -f "$OUT/last_failure"' in body
    assert "sleep 3600" in LOOP                         # a failed night is retried within the hour


def test_old_backups_are_pruned_and_a_crash_leaves_nothing_half_written():
    assert 'prune "$OUT/daily" "$KEEP_DAILY"' in LOOP and 'prune "$OUT/weekly" "$KEEP_WEEKLY"' in LOOP
    assert 'rm -rf "$OUT"/daily/.tmp-*' in LOOP


def test_the_service_is_unhealthy_after_a_failure_or_a_missed_day():
    service = COMPOSE.split("  backup:\n", 1)[1].split("\n\n", 1)[0]
    assert "image: postgres:15" in service and '"backups:/backups"' in service and "restart: unless-stopped" in service
    assert "find /backups/last_success -mmin -1560" in service and "! test -e /backups/last_failure" in service
    assert re.search(r"^volumes:\n  backups:", COMPOSE, re.M)


def test_restore_asks_first_checks_checksums_and_restores_the_pair():
    assert 'read -r -p "Type YES to continue: "' in RESTORE and '[ "$answer" = "YES" ]' in RESTORE
    assert RESTORE.index("sha256sum -c SHA256SUMS") < RESTORE.index("docker compose stop ruqqus synapse")
    assert "site.dump" in RESTORE and "synapse.dump" in RESTORE and "pg_restore --clean --if-exists" in RESTORE


def test_the_drill_never_touches_the_live_databases():
    assert "drill_site" in VERIFY and "drill_synapse" in VERIFY
    assert "--clean" not in VERIFY                      # it restores into scratch databases only
    assert "DROP DATABASE IF EXISTS drill_site" in VERIFY and "trap " in VERIFY
    assert 'ls scripts/migrations/*.sql' in VERIFY


def test_the_runbook_says_what_it_does_not_cover():
    for gap in ("A copy off this machine", "Encryption", "Media", "signing keys", "MASTER_KEY", "restore drill"):
        assert gap in RUNBOOK, gap


def test_the_scripts_parse():
    for name in ("backup_loop.sh", "restore.sh", "verify_backup.sh"):
        try:
            done = subprocess.run(["bash", "-n", str(ROOT / "scripts" / name)], capture_output=True, text=True)
        except FileNotFoundError:
            return                                       # no bash on this machine: CI has one
        assert done.returncode == 0, (name, done.stderr)
