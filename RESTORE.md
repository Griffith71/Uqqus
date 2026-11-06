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

**The guard** (`ruqqus/helpers/chat_health.py`): Synapse keeps a high-water mark, the highest user id ever given a chat
account, and the site moves new user ids past it when it starts, every five minutes and whenever chat is refused, so after
a restore of the site's database alone new members get ids that never had a chat account. That needs the chat server to
answer: the start-up check keeps trying for about five minutes, and until one check has worked a sign-up can still get
an old id. Such a member is never handed the old member's chat: the account is refused (see below) until the pair is
restored or an owner says it is the same person. The restore drill and `restore.sh` check
that a pair agrees (`scripts/chat_pair_check.sh`: every chat account the site knows must be on the chat server).
`/admin/chat` shows the same check, lists members chat was refused to (they read "Chat isn't available for your account
right now", and the admins get a notice once), and lets an owner give a chat account back when it is the same person.

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

**On a deploy, apply migrations before restarting the app** (and after a restore, before anyone uses the site). The app
works without the newest ones but some things wait for them: without `2026-12-20_chat_sign_outs.sql`, a password change or
Log out everywhere still goes through but its chat sign-out is only logged (never retried), signing one chat device out in
Settings > Chat apps can't be queued when the chat server fails, and chat answers "try again in a moment" until it is
applied (`/admin/chat` says it is missing). `chat_site_devices` and `chat_device_signouts` are in the site's dump and pair
with Synapse's: a sign-out still pending in the dump is retried after the restore.

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

## Resetting chat on a development machine (never on a live site)

Every site database that points at one Synapse must agree with it about who has a chat account: Matrix ids come from
user ids (`@ruqqus_<id>`), and chat refuses an account Synapse already has for someone else (a bare 503 on `/chat`).
On a development machine they drift apart when a site database is reseeded or restored on its own, or when the app is
switched between `postgres` and `ui_preview`, which have their own user ids but share the one Synapse (that is how chat
broke on 2026-11-05). To start chat afresh on both sides, keeping every post and account:

1. `bash scripts/backup.sh` first.
2. `docker compose stop synapse postgres_synapse && docker compose rm -f synapse postgres_synapse`, then
   `docker volume rm ruqqus_postgres-synapse-data ruqqus_synapse-media` (keep `synapse-signing-key`).
3. In **every** site database that uses this Synapse (`postgres` and `ui_preview`), in one transaction
   (`psql -1 -v ON_ERROR_STOP=1`): delete the rows of `guild_channel_reads`, `guild_channel_posts`, `guild_channel_pins`,
   `guild_channel_mentions`, `guild_channel_reports`, `guild_channel_prefs`, `guild_channel_rules_ack`,
   `guild_channel_feeds`, `guild_channel_overwrites`, `guild_channels`, `broadcast_posts`, `broadcast_reports`,
   `broadcast_members`, `broadcast_circles`, `broadcasts`, `chat_group_adds`, `chat_group_members`, `chat_groups`,
   `chat_calls`, `chat_unread`, `chat_conversations`, `chat_site_devices`, `chat_device_signouts` and `chat_identities`, and
   `UPDATE spaces SET matrix_room_id = NULL`. Guild settings that hold no room (categories, roles, automod, `chatbans`)
   stay. Clear any `call:*` keys in Redis.
4. `docker compose up -d postgres_synapse synapse`, wait for healthy, then run `scripts/sync_chat_names.py --once` in
   the app container.
5. The new Synapse has no high-water mark yet: each site database records its chat accounts in it the next time the app
   starts or `/admin/chat` is opened. Until then, keep the databases' new user ids apart by hand if both will make
   chat accounts (on 2026-11-05 `postgres`'s `users_id_seq` was set to 100000; `ui_preview` hands out ids under it).
6. Clear the browser's site data (old crypto stores name a server that no longer knows them).

Guild channels are recreated from each guild's Channels page. Never do this on a live site: it deletes every chat.

## Before the site goes live (chat)

- **`MATRIX_SERVER_NAME` is the real domain, set once and never changed.** Matrix writes it into every account and room
  id for good; the development server's `ruqqus.local` chats can't be carried over, so the live chat server starts
  empty.
- `MATRIX_PUBLIC_URL` is https. The call relay has its own DNS-only host, TLS on 5349, and the origin's firewall accepts
  80/443 from Cloudflare only (CLAUDE.md, "Call relay and live updates").
- **An offsite, encrypted copy of the backup pair** (above), with `MASTER_KEY` and the Synapse signing keys kept apart
  from the dumps.
- The live chat server serves this one site database: `/admin/chat` must not say it is also used by another.
- Messages are kept forever (Synapse's `retention:` stays off), so watch the size of Synapse's database:
  `docker compose exec postgres_synapse psql -U synapse -Atc "SELECT pg_size_pretty(pg_database_size('synapse'))"`.
- Run `bash scripts/verify_backup.sh --fresh` once with real data, and open `/admin/chat`.

## How long it takes

Dump and restore time grows with the database. The dev data (about 4 MB) takes seconds. Time the drill on your real
data once and write the number here.
