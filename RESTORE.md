# Backups and restoring

## What runs

The `backup` service in `docker-compose.yml` (script: `scripts/backup_loop.sh`) dumps **both databases as a pair**, the
site's and Synapse's, once a day into the `backups` Docker volume:

```
/backups/daily/<UTC time>/{site.dump, synapse.dump, SHA256SUMS}    the newest 7
/backups/weekly/<UTC time>/...                                     the Sunday ones, the newest 4
/backups/last_success      the time of the last good backup
/backups/last_failure      present only while the latest attempt failed
```

They are always restored together. Matrix ids come from user ids, so restoring one without the other can hand a new
member an old member's chat account.

A dump is kept only if both databases dumped and both archives read back, so a failed night never leaves a half-written
backup that looks good. A failure is retried within the hour. `docker compose ps backup` shows **healthy** while a backup
succeeded in the last 26 hours and the latest attempt did not fail; watch that, or `docker compose logs backup`.

Settings (environment of the `backup` service): `BACKUP_INTERVAL_HOURS` (24), `BACKUP_KEEP_DAILY` (7),
`BACKUP_KEEP_WEEKLY` (4), `BACKUP_SITE_URL` and `BACKUP_SYNAPSE_URL` (the dev defaults point at the compose databases:
**set them for a real site**).

## Check that a backup can be restored (the restore drill)

```
bash scripts/verify_backup.sh            # the newest daily backup
bash scripts/verify_backup.sh --fresh    # take a new one first
```

It checks the files against their checksums, restores both dumps into scratch databases (`drill_site`,
`drill_synapse`), checks the schema, accounts, posts and the migration ledger, and drops the scratch databases. It never
touches the live databases. **Run it after setting this up, and now and then after.** A backup nobody has restored is a
hope, not a backup.

## Restore

```
bash scripts/restore.sh                  # list the backups
bash scripts/restore.sh latest           # or: daily/20261021T020000Z
```

It asks you to type YES, checks the checksums, stops the site and Synapse, **replaces both databases**, and starts them
again. Anything written after the backup is lost. Then apply any migration newer than the backup:

```
docker compose exec ruqqus sh -c 'PYTHONPATH=. python scripts/migrate.py --status'
docker compose exec ruqqus sh -c 'PYTHONPATH=. python scripts/migrate.py --apply'
```

The dumps are made and restored with the `postgres:15` client image, which reads the site's Postgres 12 and Synapse's 15.

## What this does NOT cover: read this

- **A copy off this machine.** The `backups` volume lives on the same disk as the databases. If the disk dies, you lose
  the site and its backups together. Copy `/backups` somewhere else (another machine, object storage: `rclone`, `aws s3 sync`
  or your host's snapshot feature) on a schedule. **Until you do, you have a convenience, not a disaster-recovery plan.**
- **Encryption.** The dumps hold every account's data (emails, addresses, chat key backups, private posts). They are not
  encrypted. Encrypt the copy you send off the machine (`gpg`, `age`, or an encrypted bucket) and keep the key somewhere
  other than the server.
- **Media.** Pictures, audio and video live in members' own storage (see "Linked media storage" in `CLAUDE.md`), but
  these volumes are **not** in the dumps and need their own copy: `synapse-media` (chat attachments), `media-dev` (the
  development stand-in), and the avatars, banners and guild images in the site's S3 bucket.
- **The Synapse signing keys** (the `synapse-signing-key` volume). Losing them changes the server's identity. Copy them
  once and keep them with the encryption key.
- **Secrets and settings.** `.env`, `MASTER_KEY` and the Matrix tokens are not in any dump. Without `MASTER_KEY`, the
  encrypted values in the database (OAuth refresh tokens, linked-storage tokens) can't be read again.
- **Redis.** It holds only caches, rate limits and who is in a call right now. Nothing there needs a backup.

## How long it takes

Dump and restore time grows with the database. The dev data (about 4 MB) takes seconds. Time the drill on your real
data once and write the number here.
