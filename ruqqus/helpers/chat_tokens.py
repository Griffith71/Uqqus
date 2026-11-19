"""The chat token route's work (routes/chat.py chat_token and chat_store_keys), the chat plan's phase 2: build step 2 recorded every
device the site hands out in `chat_site_devices` (helpers/chat_signouts.py has the table's other half); build step 3 added the store
keys (helpers/chat_store_keys.py), the modes, renewal through Synapse's /refresh, and /api/chat/store_keys; build step 5 made every token
expire (an hour) and renew through /refresh. Plain `text()`
SQL, tested on SQLite with a fake Matrix (tests/test_chat_tokens.py, tests/test_chat_signouts.py) and on the real app
(tests/pg/test_chat_token.py, tests/pg/test_chat_signout_routes.py).

The request (`_parse`): `kind` chat or call, `mode` boot, renew or replace, `want` key or token (renew always means token), `device_id`,
`why` (replace only: store_unopenable, store_missing, key_changed, taken_over) and `mint_key` (boot only: 32 hex characters, the same on every retry
of one page start). **An absent field takes today's meaning** (boot, token, chat), so the browser from before build step 4, which posts
only `formkey` and `device_id`, keeps working and gets today's answer plus the new fields. A value that is present and not allowed: 400.
`renew` must name its device (400 without one, a deliberate narrowing of the plan's field table, where `device_id` is required only for
`replace`): a renewal is always of the device a running client holds, so one without a device is a client's mistake, not a device that
went; a named device with no row of this account still answers 409 `deleted_elsewhere`.

- `mode=replace` revokes the named device when it is this account's, of this kind and not revoked yet (`revoked_reason` is `why`) and
  queues its Synapse sign-out in the same transaction (chat_signouts.queue, `listed`). It mints nothing, and its answer is the same
  whatever the id was, so ids can't be probed.
- A device the browser names is **usable** when it is THIS account's row, of this kind, not revoked. Then:
  - a key-only start (`boot` + `want=key`, and the row holds a sealed refresh token, so the browser's own sealed token can be renewed
    when it ends) answers the keys with `access_token: null` and no Synapse call. A row with none (a device made before tokens expired,
    or one whose seal was lost) is given a token instead, as below, and so moves to tokens that expire;
  - otherwise a token: Synapse's /refresh with the row's sealed refresh token when it has one; when that is refused (RefreshGone), the
    seal won't open (MASTER_KEY rotated) or there is none, the device is listed on Synapse and re-tokened (`login_as`) only while it is
    still there. A device no longer listed is revoked (`deleted_elsewhere`) and is not usable. A re-token leaves the device's earlier
    refresh tokens working on Synapse (they never expire), so a MASTER_KEY rotation after a leak is followed by signing every device
    out (RESTORE.md, scripts/queue_chat_signouts.py --reason=master_key_rotated).
- Not usable (no row of this account, another kind, revoked, or just revoked): `renew` answers 409 `{device_gone, reason}` (a running
  client is bound to its device and never silently given another); `boot` mints a new device, and says which device it replaces and why
  (`previous`: the row's `revoked_reason`, `other_kind`, or `legacy` for a device with no row of this account).
- A caller with no `mode` field (a page from before build step 4, an outside script) names a device from before rows were kept: it
  is given a row (`created_ms` 0) and taken up while Synapse still has it, it is named as the site's (chat_devices.is_site_device) and
  no pending sign-out covers it (`_adopt`, committed under the mint lock first). Only a caller that sends no `mode` reaches this: none
  of the site's own pages since build step 4, whose client always sends `mode` and gets a new device with `previous.reason = legacy`
  instead.
- A new device takes an id the site chooses (`new_device_id`: "UQ" and 10 base32 letters, never one of Synapse's 10-letter ids). Its
  row is committed BEFORE Synapse is asked to make it, under the account's mint lock (`chat_signouts.lock_account`), which a queued
  sign-out takes too: a device is made either before a sign-out (and revoked by it) or after it (and never touched by it). A row a
  sign-out revoked while Synapse was making the device answers 503 and queues that device's sign-out: the browser's retry makes another.
  A retry of the same page start (`mint_key`) is given the device minted for it, never a second one.
- "After it" is only safe for a request that may still have chat AFTER it, and the route's wrappers read the account when the request
  began: a request already past them can wait on the mint lock while an account deletion, a parent switching chat off or a credential
  event commits. So the account is read again under the lock, before the row (`_refusal`: deleted, a sign-in the nonce has ended, or no
  chat for a child-locked account or a held one), before a device from before the rows is given its row (`_adopt`), and before a device is served
  again. Refused: 401 or 403, nothing written.
- A Synapse call that fails, the server not answering included (matrix_client raises `Unreachable`, a MatrixError), after the new row was
  committed: the row is revoked and its device's sign-out queued, both by `chat_signouts.queue`, which takes the mint lock BEFORE it
  touches the row (the order every writer keeps: the lock, then rows), and the route answers 503. Synapse can finish a login after the
  client gave up on it, so that sign-out (`mint_failed`) waits for its device for MINT_GRACE_SECONDS rather than calling it gone at once
  (chat_signouts.run_one). Any other MatrixError, and a row lock
  not had within 15 s (Postgres's lock_timeout), answer 503 `{retry}` too. A 200 from Synapse whose body isn't JSON or lacks the token
  is a MatrixError as well (matrix_client._answer), so it is cleaned up the same way, never a 500 after the row was committed.
- Every answer that serves a device writes `sign_in` (a SHA-256 of the cookie's session id) and `last_used_utc`, which is how decision 2
  finds the devices a credential event spares (account_sessions.revoke).
- Tokens expire (build step 5): `login_as` always asks Synapse for a refresh token, every token answered carries Synapse's
  `expires_in_ms` (an hour: refreshable_access_token_lifetime in synapse/homeserver.yaml; CHAT_TOKEN_REPORT_SECONDS, dev and specs only,
  reports it shorter), and the browser renews before the end (keeper.js). The refresh token is kept only sealed (secret_box,
  `refresh_sealed`) and never in an answer. Synapse's answer is never logged.
"""
import re
import time
from collections import namedtuple

from sqlalchemy import bindparam, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from ruqqus.helpers import age_declaration, chat_signouts, chat_store_keys
from ruqqus.helpers.chat_devices import is_site_device
from ruqqus.helpers.chat_store_keys import DEVICE_ID, SITE_DEVICE, new_device_id   # the one definition; still read from here

KINDS = ("chat", "call")
MODES = ("boot", "renew", "replace")
WANTS = ("key", "token")
WHYS = ("store_unopenable", "store_missing", "key_changed", "taken_over")   # taken_over: minted, then dropped by its page (token.js)
MINT_KEY = re.compile(r"[0-9a-f]{32}")
MAX_STORE_KEYS = 4                 # /api/chat/store_keys: ids asked for at once
ROW_LOCK_SECONDS = 15              # a device's row held this long by another request (a renewal, a sign-out): 503, never a 500
RETRY = {"retry": True}            # the route adds the sentence, in the reader's language
SIGNED_OUT = {"signed_out": True}
HELD = {"held": True}              # the route adds the sentence (the hold's own: helpers/quarantine.py)
BAD_REQUEST = {"bad_request": True}
REPLACED = {"replaced": True}

Request = namedtuple("Request", "kind mode want device_id why mint_key today")


def _parse(form):
    """The request's fields, or None for a value that is present and not allowed. Absent ones take today's meaning."""
    def field(name):
        value = form.get(name)
        return value if value not in (None, "") else None
    kind, mode, want = field("kind") or "chat", field("mode") or "boot", field("want") or "token"
    device_id, why, mint_key = field("device_id"), field("why"), field("mint_key")
    if kind not in KINDS or mode not in MODES or want not in WANTS:
        return None
    if device_id is not None and not chat_store_keys.valid_device(device_id):
        return None
    if mode == "replace":
        if device_id is None or why not in WHYS:
            return None
    elif why is not None:
        return None
    if mint_key is not None and (mode != "boot" or not MINT_KEY.fullmatch(mint_key)):
        return None
    if mode == "renew":
        if device_id is None:
            return None
        want = "token"
    return Request(kind, mode, want, device_id, why, mint_key, field("mode") is None)


def _lock_timeout(db):
    db.execute(text(f"SET LOCAL lock_timeout = '{int(ROW_LOCK_SECONDS)}s'"))


def _row(db, user_id, device_id):
    if chat_signouts._pg(db):
        _lock_timeout(db)
        lock = " FOR UPDATE"
    else:
        lock = ""
    return db.execute(text("SELECT kind, revoked_utc, revoked_reason, refresh_sealed FROM chat_site_devices "
                           "WHERE user_id = :u AND device_id = :d" + lock), {"u": user_id, "d": device_id}).first()


def _someone_elses(db, user_id, device_id):
    return db.execute(text("SELECT 1 FROM chat_site_devices WHERE device_id = :d AND user_id <> :u"),
                      {"u": user_id, "d": device_id}).first() is not None


def _refusal(db, user_id, nonce):
    """Whether this request may still be handed a chat device, read now (not when its wrappers ran): None, or the (status, body) to
    answer. The account deleted, or its sign-in ended by a raised nonce since (`nonce` is the cookie's): 401. A child-locked account
    without chat (a standalone one, or a sub-account whose parent hasn't switched chat on: the `messaging` feature): 403. **A held account
    (the signup guard's or an automatic check's: helpers/quarantine.py) has no chat at all** (the owner, 2026-11-19): 403 `held`, the route adds the hold's sentence."""
    row = db.execute(text("""SELECT u.login_nonce, u.is_deleted, u.age_declared, u.age_lock, c.chat, u.quarantined_utc, u.admin_level, u.is_bot
                             FROM users u LEFT JOIN child_controls c ON c.child_id = u.id WHERE u.id = :u"""), {"u": int(user_id)}).first()
    if row is None or row[1] or int(row[0] or 0) > int(nonce or 0):
        return 401, SIGNED_OUT
    _nonce, _deleted, declared, lock, chat, held, admin_level, is_bot = row
    if held and not admin_level and not is_bot:        # a held account (helpers/quarantine.py) has no chat: a request already past its wrappers when the hold landed
        return 403, HELD
    if (declared == age_declaration.CHILD or lock is not None) and not (lock == age_declaration.PARENT_LOCK and chat):
        return 403, {"age_locked": True, "parent": lock == age_declaration.PARENT_LOCK}
    return None


def _adopt(db, user_id, device_id, kind, sign_in, now, nonce):
    """Gives a device from before rows were kept its row (`created_ms` 0), and COMMITS it, under the account's mint lock, before it is
    taken up. A row only this transaction could see would be invisible to a sign-out queued meanwhile: nothing would revoke it or wait
    for it, the sign-out would delete the device, and the take-up's login would then make it again with a live token nothing would ever
    sign out. Committed under the lock, a sign-out is either queued before (and still pending: `pending_covers`, so no row, and the route
    makes a new device; or done: the device is gone from the list read again under the row lock) or after it (it sees the row, revokes
    it, and waits on the take-up's row lock). False: not given a row. A row written meanwhile (a sign-out from Settings > Chat apps) wins.
    The account is read again under the lock first, as `mint` does (`_refusal`): an account deleted, a sign-in ended or chat switched off
    while this listed the devices is answered with its refusal (a (status, body) tuple) and given no row, so no deleted account is left
    with a live row (the deletion's sign-out may have finished, so `pending_covers` alone wouldn't stop it)."""
    chat_signouts.lock_account(db, user_id)
    refused = _refusal(db, user_id, nonce)
    if refused:
        db.rollback()
        return refused
    if chat_signouts.pending_covers(db, user_id, device_id):
        db.rollback()
        return False
    db.execute(text("""INSERT INTO chat_site_devices (user_id, device_id, kind, sign_in, created_ms, last_used_utc)
                       VALUES (:u, :d, :k, :h, 0, :now) ON CONFLICT (user_id, device_id) DO NOTHING"""),
               {"u": user_id, "d": device_id, "k": kind, "h": sign_in, "now": now})
    db.commit()
    return True


def keys_for(secret, mxid, device_id):
    """The browser's keys for one device: its store key, the account's key for waiting first messages, and the secret's id."""
    return {"kid": chat_store_keys.key_id(secret),
            "storage_key": chat_store_keys.b64(chat_store_keys.store_key(secret, mxid, device_id)),
            "waiting_key": chat_store_keys.b64(chat_store_keys.waiting_key(secret, mxid))}


def seal(refresh_token):
    """A refresh token as it is kept (secret_box, under MASTER_KEY). Imported here: the unit tests' environment has no `cryptography`."""
    from ruqqus.helpers import secret_box
    return secret_box.encrypt_secret(refresh_token)


def unseal(sealed):
    from ruqqus.helpers import secret_box
    return secret_box.decrypt_secret(sealed)


class _Issue:
    def __init__(self, db, matrix, user_id, mxid, session_id, public_url, now, nonce, site_name, secret, report_seconds, sealer):
        self.db, self.matrix, self.user_id, self.mxid = db, matrix, int(user_id), mxid
        self.sign_in = chat_signouts.sign_in_hash(session_id)
        self.public_url, self.now, self.nonce, self.site_name = public_url, now, nonce, site_name
        self.secret, self.report_seconds = secret, report_seconds
        self.seal, self.unseal_with = sealer

    def unseal(self, sealed):
        """The row's refresh token, or None when there is none or the seal won't open (MASTER_KEY was rotated: Fernet raises)."""
        if not sealed:
            return None
        try:
            return self.unseal_with(sealed)
        except Exception:
            return None

    def sealed(self, token):
        refresh_token = token.get("refresh_token")
        return self.seal(refresh_token) if refresh_token else None

    # --- answers --------------------------------------------------------------------------------------------------------------

    def answer(self, device_id, kind, token=None, new_device=False, previous=None):
        body = {"homeserver_url": self.public_url, "user_id": self.mxid, "device_id": device_id, "kind": kind,
                "access_token": token["access_token"] if token else None, "new_device": bool(new_device)}
        body.update(keys_for(self.secret, self.mxid, device_id))
        expires = (token or {}).get("expires_in_ms")
        if isinstance(expires, int) and expires > 0:
            if self.report_seconds:
                expires = min(expires, int(self.report_seconds) * 1000)
            body["expires_in_ms"] = expires
        if previous:
            body["previous"] = previous
        return 200, body                   # never the refresh token: it is kept here, sealed

    # --- serving a device -----------------------------------------------------------------------------------------------------

    def token_for(self, device_id, sealed):
        """A token for a usable device: /refresh with its sealed refresh token, else (refused, unsealable, none) a re-token while
        Synapse still lists it. None: the device is gone from Synapse."""
        refresh_token = self.unseal(sealed)
        if refresh_token:
            try:
                return self.matrix.refresh(refresh_token)
            except self.matrix.RefreshGone:
                pass
        if device_id in {d["device_id"] for d in self.matrix.list_devices(self.mxid)}:
            token = self.matrix.login_as(self.mxid, device_id)
            if token.get("device_id", device_id) != device_id:
                raise self.matrix.MatrixError("login_as answered another device")
            return token
        return None

    def served(self, device_id, token=None):
        """The device served: this sign-in, now, and (with a token) Synapse's new refresh token sealed, or none."""
        if token is None:
            self.db.execute(text("UPDATE chat_site_devices SET sign_in = :h, last_used_utc = :now WHERE user_id = :u AND device_id = :d"),
                            {"h": self.sign_in, "now": self.now, "u": self.user_id, "d": device_id})
            return
        self.db.execute(text("""UPDATE chat_site_devices SET sign_in = :h, last_used_utc = :now, last_token_utc = :now, refresh_sealed = :r
                                WHERE user_id = :u AND device_id = :d"""),
                        {"h": self.sign_in, "now": self.now, "u": self.user_id, "d": device_id, "r": self.sealed(token)})

    # --- the modes ------------------------------------------------------------------------------------------------------------

    def replace(self, req):
        row = self.db.execute(text("SELECT kind, revoked_utc FROM chat_site_devices WHERE user_id = :u AND device_id = :d"),
                              {"u": self.user_id, "d": req.device_id}).first()
        if row is not None and row[0] == req.kind and not row[1]:
            # queue takes the mint lock, then revokes the row (revoked_reason = why) in this transaction: the lock, then rows
            chat_signouts.queue(self.db, self.user_id, req.why, "listed", device_ids=[req.device_id], mxid=self.mxid)
        self.db.commit()
        return 200, REPLACED

    def run(self, req):
        if req.mode == "replace":
            return self.replace(req)
        previous = None
        if req.device_id:
            row = _row(self.db, self.user_id, req.device_id)
            if row is None and req.today and not _someone_elses(self.db, self.user_id, req.device_id):
                # a caller with no `mode` (an older cached bundle, an outside script) naming a device from before rows were kept: given a
                # row only while Synapse has it, it is named as the site's and no pending sign-out covers it (_adopt), then held, checked
                # and listed again like any other
                listed = self.matrix.list_devices(self.mxid)
                found = next((d for d in listed if d["device_id"] == req.device_id), None)
                if found is not None and is_site_device(found.get("name"), self.site_name):
                    adopted = _adopt(self.db, self.user_id, req.device_id, req.kind, self.sign_in, self.now, self.nonce)
                    if isinstance(adopted, tuple):
                        return adopted                         # refused under the lock: nothing written
                    if adopted:
                        row = _row(self.db, self.user_id, req.device_id)
            if row is not None and row[0] == req.kind and not row[1]:
                refused = _refusal(self.db, self.user_id, self.nonce)  # the row is held: a sign-out revoking it waits for this answer
                if refused:
                    self.db.rollback()
                    return refused
                if req.mode == "boot" and req.want == "key" and row[3]:
                    self.served(req.device_id)                         # the browser's own token still works: keys only, no Synapse call
                    self.db.commit()
                    return self.answer(req.device_id, req.kind)
                token = self.token_for(req.device_id, row[3])
                if token is not None:
                    self.served(req.device_id, token)
                    self.db.commit()
                    return self.answer(req.device_id, req.kind, token)
                chat_signouts.revoke_rows(self.db, self.user_id, [req.device_id], "deleted_elsewhere", self.now)
                reason = "deleted_elsewhere"
            elif row is None:
                reason = "legacy"
            elif row[1]:
                reason = row[2] or "revoked"
            else:
                reason = "other_kind"
            previous = {"device_id": req.device_id, "reason": reason}
        if req.mode == "renew":
            self.db.commit()                   # a deleted_elsewhere revocation stands
            reason = previous["reason"]
            return 409, {"device_gone": True, "reason": "deleted_elsewhere" if reason == "legacy" else reason}
        return self.mint(req, previous)

    # --- a new device ---------------------------------------------------------------------------------------------------------

    def retried(self, req, previous):
        """A retry of the same page start (`mint_key`): the device minted for it, when it can still be served; else the key is
        freed (the unique index allows one row per key) for the new device to take. None: mint one."""
        lock = ""
        if chat_signouts._pg(self.db):
            _lock_timeout(self.db)
            lock = " FOR UPDATE"
        row = self.db.execute(text("SELECT device_id, kind, revoked_utc, refresh_sealed FROM chat_site_devices "
                                   "WHERE user_id = :u AND mint_key = :k" + lock), {"u": self.user_id, "k": req.mint_key}).first()
        if row is None:
            return None
        device_id, kind, revoked_utc, sealed = row
        if kind == req.kind and not revoked_utc:
            refused = _refusal(self.db, self.user_id, self.nonce)
            if refused:
                self.db.rollback()
                return refused
            token = self.token_for(device_id, sealed)
            if token is not None:
                self.served(device_id, token)
                self.db.commit()
                return self.answer(device_id, req.kind, token, new_device=True, previous=previous)
            chat_signouts.revoke_rows(self.db, self.user_id, [device_id], "deleted_elsewhere", self.now)
        self.db.execute(text("UPDATE chat_site_devices SET mint_key = NULL WHERE user_id = :u AND mint_key = :k"),
                        {"u": self.user_id, "k": req.mint_key})
        self.db.commit()
        return None

    def mint(self, req, previous):
        """A new device with an id the site chose, its row committed under the mint lock before Synapse is asked to make it."""
        db, user_id = self.db, self.user_id
        db.commit()                    # whatever was revoked above, and the row lock released: the mint never waits on a lock holding one
        if req.mint_key:
            again = self.retried(req, previous)
            if again is not None:
                return again
        device_id = None
        for _ in range(3):
            try:
                chat_signouts.lock_account(db, user_id)
                refused = _refusal(db, user_id, self.nonce)   # read under the lock: a deletion or chat switched off that it waited for counts
                if refused:
                    db.rollback()
                    return refused
                candidate = new_device_id()
                db.execute(text("""INSERT INTO chat_site_devices (user_id, device_id, kind, sign_in, created_ms, last_used_utc, mint_key)
                                   VALUES (:u, :d, :k, :h, :ms, :now, :mk)"""),
                           {"u": user_id, "d": candidate, "k": req.kind, "h": self.sign_in, "ms": chat_signouts._now_ms(),
                            "now": self.now, "mk": req.mint_key})
                db.commit()
                device_id = candidate
                break
            except IntegrityError:
                db.rollback()
        if device_id is None:
            return 503, RETRY
        try:
            token = self.matrix.login_as(self.mxid, device_id)
            if token.get("device_id") != device_id:
                raise self.matrix.MatrixError("login_as made another device")
        except self.matrix.MatrixError:            # Unreachable too: a timeout may come after Synapse made the device
            db.rollback()
            # queue revokes the row itself, after it holds the mint lock: never a row lock first (a sign-out holding the lock waits for the row)
            chat_signouts.queue(db, user_id, "mint_failed", "listed", device_ids=[device_id], mxid=self.mxid)   # Synapse may have made it
            db.commit()
            return 503, RETRY
        made = db.execute(text("""UPDATE chat_site_devices SET last_token_utc = :now, refresh_sealed = :r
                                  WHERE user_id = :u AND device_id = :d AND revoked_utc = 0"""),
                          {"now": self.now, "u": user_id, "d": device_id, "r": self.sealed(token)}).rowcount
        if not made:
            # a sign-out revoked the row while Synapse was making the device, and may already have listed the devices without it
            chat_signouts.queue(db, user_id, "revoked_while_made", "listed", device_ids=[device_id], mxid=self.mxid)
            db.commit()
            return 503, RETRY
        db.commit()
        return self.answer(device_id, req.kind, token, new_device=True, previous=previous)


def issue(db, matrix, user_id, mxid, form, session_id, public_url, now=None, site_name="Laba", nonce=0, *,
          secret, report_seconds=None, sealer=(seal, unseal)):
    """(status, body) for POST /api/chat/token. `matrix` is helpers/matrix_client or a fake; `form` the request's values; `nonce` the
    cookie's login_nonce; `secret` chat_store_keys.secret_of(app.config); `report_seconds` CHAT_TOKEN_REPORT_SECONDS (dev and specs only: caps the `expires_in_ms` reported); `sealer` (seal, unseal) for the refresh token
    (secret_box; the unit tests pass their own). Commits."""
    if not chat_signouts.tables_ready(db):        # no device without its row: before the migration chat waits (the route asks first too)
        return 503, RETRY
    req = _parse(form)
    if req is None:
        return 400, BAD_REQUEST
    if not secret:
        raise ValueError("no chat store secret")
    work = _Issue(db, matrix, user_id, mxid, session_id, public_url, int(now or time.time()), nonce, site_name, secret, report_seconds,
                  sealer)
    try:
        return work.run(req)
    except DBAPIError as e:
        db.rollback()
        if chat_signouts.lock_timeout(e):
            return 503, RETRY
        raise
    except matrix.MatrixError:
        db.rollback()
        return 503, RETRY


MAX_STORE_KEY_CHARS = (64 + 1) * MAX_STORE_KEYS   # four of the longest ids and their commas: anything longer is refused unread


def parse_store_key_ids(value):
    """/api/chat/store_keys's `device_ids`: up to MAX_STORE_KEYS ids, comma-separated. None for anything else. The work is bounded
    before the loop (the value's length: a url-encoded body is capped only by MAX_CONTENT_LENGTH, 64 MB) and inside it (the fifth
    distinct id ends it): a long list of short ids made this quadratic, and gevent can't switch away from a loop with no I/O."""
    value = value or ""
    if len(value) > MAX_STORE_KEY_CHARS:
        return None
    ids = []
    for device_id in value.split(","):
        if not device_id:
            continue
        if not chat_store_keys.valid_device(device_id):
            return None
        if device_id not in ids:
            ids.append(device_id)
            if len(ids) > MAX_STORE_KEYS:
                return None
    return ids


def store_keys(db, user_id, mxid, device_ids, secret):
    """The keys of this account's own site devices among `device_ids`, revoked or not (the harvest, Phase 3's rescue). Any other id,
    another account's, an outside app's or made up, is left out, and never told apart from one that doesn't exist. No Synapse call."""
    if not device_ids or not mxid or not chat_signouts.tables_ready(db):
        return {"keys": {}}
    statement = text("SELECT device_id FROM chat_site_devices WHERE user_id = :u AND device_id IN :ids") \
        .bindparams(bindparam("ids", expanding=True))
    found = [d for (d,) in db.execute(statement, {"u": int(user_id), "ids": list(device_ids)}).all()]
    kid = chat_store_keys.key_id(secret)
    return {"keys": {d: {"key": chat_store_keys.b64(chat_store_keys.store_key(secret, mxid, d)), "kid": kid} for d in sorted(found)}}
