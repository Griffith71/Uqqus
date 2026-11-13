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

### After restoring an older pair

A pair is restored together, so the site and the chat server agree with each other, but both have forgotten what happened
after the backup was made:

- **sign-ins that ended later** (`ended_sign_ins`: a Log out, a sign-in over another): a copy of a cookie ended after the
  backup works again, until its account's sign-in counter (`login_nonce`) moves;
- **chat devices signed out later**, and sign-outs that were still pending: those devices are usable again, on both sides.

So, after `scripts/restore.sh` and the migrations, in the app container:

1. Sign every chat device out (it also empties `chat_site_devices`, so every browser starts as a new device the next time it
   opens chat, keeps its locked store, and asks for the member's password or recovery code once; outside chat apps sign in
   again):
   ```
   PYTHONPATH=. python scripts/queue_chat_signouts.py --all --reason=restore --once --confirm
   ```
   Room keys that weren't in the key backup yet are lost: with `chat_site_devices` emptied the site serves no key for the
   browsers' kept stores, so the harvest can't move what they held (CLAUDE.md "Chat encryption").
2. End every sign-in: `UPDATE users SET login_nonce = login_nonce + 1;` (in `psql`). Everyone signs in again.

`scripts/chat_pair_check.sh` (which `restore.sh` runs) also counts the chat devices the site still serves that the chat
server lacks: a few may differ in a good pair (one signed out between the two dumps); many say the site's dump is older
than Synapse's.

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

**A lighter reset, keeping the chats: sign every chat device out** (done on the dev machine when the chat client began
locking its stores, 2026-11, because the devices the older client made keep unkeyed crypto stores and tokens that don't expire). In the
app container:

```
PYTHONPATH=. python scripts/queue_chat_signouts.py --all --reason=legacy --once
```

It queues a sign-out of every chat device of every account (apps included) and works it once, and empties
`chat_site_devices`; what the chat server can't take at once is retried by `scripts/recomputes.py`. It refuses a database
that isn't a scratch one (`ci_*`, `test*`, `ui_*`) unless `--confirm` says so, and says first which database it opened: run
it once against each site database that uses this Synapse (`postgres` with `--confirm`, then `ui_preview`). The app connects
to `DATABASE_CONNECTION_POOL_URL` before `DATABASE_URL`, and docker-compose.yml sets both to `postgres`, so the second run
names `ui_preview` in both (setting `DATABASE_URL` alone still opens `postgres`):

```
docker exec -e DATABASE_CONNECTION_POOL_URL=postgresql://postgres@postgres:5432/ui_preview \
  -e DATABASE_URL=postgresql://postgres@postgres:5432/ui_preview -w /opt/ruqqus/service ruqqus-ruqqus-1 \
  env PYTHONPATH=. venv/bin/python scripts/queue_chat_signouts.py --all --reason=legacy --once
```

Every browser then
starts as a new device the next time it opens chat: the member's password pre-key (or the password, or the recovery code)
unlocks it and history comes back from the key backup. A browser keeps its old store (locked, or the older client's unkeyed
one, which the new client deletes once the site has answered: `migrateLegacy`). A device signed out the ordinary way (Log out
everywhere, Settings > Chat apps, a reset) keeps its row in `chat_site_devices`, revoked, so once chat unlocks on that browser
the harvest opens its kept store with the key the site still serves for it and moves into the new device what the backup
lacked, then deletes the store (CLAUDE.md "Chat encryption", the harvest). **This script empties `chat_site_devices`, so the
site serves no key for those devices any more and their kept stores can't be harvested**: they stay, locked, `removed` in the
browser's index, until the account is deleted there or a cap removes them (four accounts a browser, six removed stores an
account), and room keys that weren't in the backup yet are lost. Doing it by hand instead is emptying
`chat_site_devices` and `chat_device_signouts` and deleting every device on Synapse.

## Before the site goes live (chat)

- **`MATRIX_SERVER_NAME` is the real domain, set once and never changed.** Matrix writes it into every account and room
  id for good; the development server's `ruqqus.local` chats can't be carried over, so the live chat server starts
  empty.
- `MATRIX_PUBLIC_URL` is https. The call relay has its own DNS-only host, TLS on 5349, and the origin's firewall accepts
  80/443 from Cloudflare only (CLAUDE.md, "Call relay and live updates").
- **An offsite, encrypted copy of the backup pair** (above), with `MASTER_KEY` and the Synapse signing keys kept apart
  from the dumps.
- **`CHAT_STORE_SECRET` is set, once** (64 random hex characters, e.g. `python3 -c "import secrets; print(secrets.token_hex(32))"`),
  kept with `MASTER_KEY` and never changed. Unset, the chat store keys are made from `MASTER_KEY`, and rotating that would
  also start every browser's chat over (see "Rotating the chat store secret").
- The live chat server serves this one site database: `/admin/chat` must not say it is also used by another.
- Messages are kept forever (Synapse's `retention:` stays off), so watch the size of Synapse's database:
  `docker compose exec postgres_synapse psql -U synapse -Atc "SELECT pg_size_pretty(pg_database_size('synapse'))"`.
- Run `bash scripts/verify_backup.sh --fresh` once with real data, and open `/admin/chat`.

## Rotating the chat store secret

Each chat device the site makes for an account has a store key that the token route hands only to a signed-in session of
that account (`ruqqus/helpers/chat_store_keys.py`): an HMAC of the Matrix id and the device id under a sub-key of
`CHAT_STORE_SECRET`, or of `SECRET_KEY` (`MASTER_KEY`) while `CHAT_STORE_SECRET` is unset. The answer also carries `kid`,
which names the secret. Set `CHAT_STORE_SECRET` once, before launch (above), so `MASTER_KEY` can be rotated on its own.

Rotating whichever secret the keys come from (a new `CHAT_STORE_SECRET`, or a new `MASTER_KEY` while it is unset) changes
every store key and the `kid`. A browser that sees a new `kid`:
1. never opens its old store again: it asks the token route to replace that device (`mode=replace`, which revokes it and
   queues its sign-out on the chat server), so the old device id is never used with a fresh store;
2. deletes the old store and starts a new device;
3. gets its history back from the key backup once the member unlocks chat, as on any new device.

What is lost: room keys that weren't in the backup yet. Every browser sees a rotation, so only rotate after a
leak of the secret, since it costs every member a new chat device.

A new `MASTER_KEY` also makes the sealed refresh tokens of `chat_site_devices.refresh_sealed` unreadable (`secret_box`):
the site keeps one per chat device to renew its hour-long token through Synapse's `/refresh`, and with it unreadable each
device would be re-tokened at its next renewal or page start instead (`login_as` on the same device, after the token route
has checked that the device still exists on the chat server), and given a new refresh token, sealed under the new key.
**That alone does not end the old ones**: a login on a device leaves the device's earlier refresh tokens working on
Synapse, and they never expire (`refresh_token_lifetime` is unset, below). So whoever holds the leaked key and a dump
(`refresh_sealed` is in every dump) could open them and keep getting hour-long tokens for every device, on the public chat
server, after the rotation. **Right after the new `MASTER_KEY` is in place and the app restarted, sign every chat device
out** (in the app's container, against each site database that uses this Synapse, as for the reset above):

```
PYTHONPATH=. python scripts/queue_chat_signouts.py --all --reason=master_key_rotated --once --confirm
```

It deletes every chat device on Synapse (and every refresh token with it), outside chat apps' too, and empties
`chat_site_devices`. Every browser then starts again as a new device the next time it opens chat, keeping its locked store,
and asks for the member's password (or recovery code) once, as any new device does; room keys that weren't in the key
backup yet are lost (the browsers' kept stores can't be harvested: the script empties `chat_site_devices`, so the site serves
no key for them), and outside apps sign in again. A device minted after the rotation and before the script ran is signed
out too: its refresh token was already sealed under the new key, so that costs a new device, nothing else. The store key
stays the same only if `CHAT_STORE_SECRET` is set and didn't change (while it is unset, a new `MASTER_KEY` also changes
every store key and the `kid`, and every browser replaces its device, as above).

### Chat token lifetime

Every chat token the site hands a browser lasts an hour: `refreshable_access_token_lifetime: 1h` in
`synapse/homeserver.yaml`, with the site asking Synapse for a refresh token at every login (`matrix_client.login_as`). The
browser renews before the end through the site, which renews through `/refresh`. Keep the setting when Synapse's config is
rebuilt or restored: without it Synapse uses its default of 5 minutes (it still gives a refresh token, since the site asks
for one), so chat keeps working and its tokens still expire and are renewed, but every few minutes instead of hourly (each
renewal a request to the site and a `/refresh` on Synapse, for every open chat device), and apps that refresh fall back to
5 minutes too. `session_lifetime`, `nonrefreshable_access_token_lifetime` and `refresh_token_lifetime` are left unset on
purpose: each would also cut outside chat apps' sessions short. Apps that ask for refresh tokens refresh hourly (Synapse's
default was 5 minutes); Element Web asks for none, so its token has no expiry (checked on Synapse: no `valid_until_ms`, no
refresh token; nobody kept it open for the full hour).

## How long it takes

Dump and restore time grows with the database. The dev data (about 4 MB) takes seconds. Time the drill on your real
data once and write the number here.
