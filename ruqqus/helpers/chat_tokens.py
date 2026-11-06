"""The chat token route's work (routes/chat.py chat_token), build step 2 of the chat plan's phase 2: today's answer, with every device
the site hands out recorded in `chat_site_devices` (helpers/chat_signouts.py has the table's other half). Plain `text()` SQL, tested on
SQLite with a fake Matrix (tests/test_chat_signouts.py) and on the real app (tests/pg/test_chat_signout_routes.py).

- A device the browser names is taken up again only when it is THIS account's, of this kind, not revoked, and still on Synapse (the
  list check). A device made before rows were kept, still on Synapse and named as the site's (chat_devices.is_site_device), is given a
  row as it is taken up (`created_ms` 0: made before any sign-out queued since, so an `all` sign-out still covers it), committed under
  the mint lock first and only when no pending sign-out covers it (`_adopt`), then taken up through the held row. Anything else,
  a revoked row, another account's row, an outside app's device, one signed out on Synapse, is a device missing from the list: a NEW
  device is made, and Synapse is never asked to re-token the old id (that would hand a live token to a device a queued sign-out is
  about to delete).
- A new device takes an id the site chooses (`new_device_id`: "UQ" and 10 base32 letters, never one of Synapse's 10-letter ids). Its
  row is committed BEFORE Synapse is asked to make it, under the account's mint lock (`chat_signouts.lock_account`), which a queued
  sign-out takes too: a device is made either before a sign-out (and revoked by it) or after it (and never touched by it). A row a
  sign-out revoked while Synapse was making the device answers 503 and queues that device's sign-out: the browser's retry makes another.
- "After it" is only safe for a request that may still have chat AFTER it, and the route's wrappers read the account when the request
  began: a request already past them can wait on the mint lock while an account deletion, a parent switching chat off or a credential
  event commits. So the account is read again under the lock, before the row (`_refusal`: deleted, a sign-in the nonce has ended, or no
  chat for a child-locked account), and the same check is made before a device is taken up again. Refused: 401 or 403, nothing written.
- A Synapse call that fails, the server not answering included (matrix_client raises `Unreachable`, a MatrixError, for `requests`' own
  errors), after the new row was committed: the row is revoked and its device's sign-out queued, both by `chat_signouts.queue`, which
  takes the mint lock BEFORE it touches the row (the order every writer keeps: the lock, then rows), and the route answers 503.
- Every answer that serves a device writes `sign_in` (a SHA-256 of the cookie's session id) and `last_used_utc`, which is how decision 2
  finds the devices a credential event spares (account_sessions.revoke).
- The answer is today's shape: homeserver_url, user_id, access_token, device_id. Synapse's answer is never logged.
"""
import base64
import re
import secrets
import time

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from ruqqus.helpers import age_declaration, chat_signouts
from ruqqus.helpers.chat_devices import is_site_device

DEVICE_ID = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
SITE_DEVICE = re.compile(r"^UQ[A-Z2-7]{10}$")          # what the site makes; Synapse's own ids are 10 letters [A-Z]
KINDS = ("chat", "call")
RETRY = {"retry": True}           # the route adds the sentence, in the reader's language
SIGNED_OUT = {"signed_out": True}


def new_device_id():
    """"UQ" + 10 base32 letters: never one of Synapse's own 10-letter ids."""
    return "UQ" + base64.b32encode(secrets.token_bytes(10)).decode("ascii")[:10]


def _answer(token, public_url):
    return {"homeserver_url": public_url, "user_id": token["user_id"], "access_token": token["access_token"],
            "device_id": token["device_id"]}


def _row(db, user_id, device_id):
    if chat_signouts._pg(db):
        db.execute(text("SET LOCAL lock_timeout = '15s'"))
        lock = " FOR UPDATE"
    else:
        lock = ""
    return db.execute(text("SELECT kind, revoked_utc FROM chat_site_devices WHERE user_id = :u AND device_id = :d" + lock),
                      {"u": user_id, "d": device_id}).first()


def _someone_elses(db, user_id, device_id):
    return db.execute(text("SELECT 1 FROM chat_site_devices WHERE device_id = :d AND user_id <> :u"),
                      {"u": user_id, "d": device_id}).first() is not None


def _refusal(db, user_id, nonce):
    """Whether this request may still be handed a chat device, read now (not when its wrappers ran): None, or the (status, body) to
    answer. The account deleted, or its sign-in ended by a raised nonce since (`nonce` is the cookie's): 401. A child-locked account
    without chat (a standalone one, or a sub-account whose parent hasn't switched chat on: the `messaging` feature): 403."""
    row = db.execute(text("""SELECT u.login_nonce, u.is_deleted, u.age_declared, u.age_lock, c.chat
                             FROM users u LEFT JOIN child_controls c ON c.child_id = u.id WHERE u.id = :u"""), {"u": int(user_id)}).first()
    if row is None or row[1] or int(row[0] or 0) > int(nonce or 0):
        return 401, SIGNED_OUT
    _nonce, _deleted, declared, lock, chat = row
    if (declared == age_declaration.CHILD or lock is not None) and not (lock == age_declaration.PARENT_LOCK and chat):
        return 403, {"age_locked": True, "parent": lock == age_declaration.PARENT_LOCK}
    return None


def _adopt(db, user_id, device_id, kind, sign_in, now):
    """Gives a device from before rows were kept its row (`created_ms` 0), and COMMITS it, under the account's mint lock, before it is
    taken up. A row only this transaction could see would be invisible to a sign-out queued meanwhile: nothing would revoke it or wait
    for it, the sign-out would delete the device, and the take-up's login would then make it again with a live token nothing would ever
    sign out. Committed under the lock, a sign-out is either queued before (and still pending: `pending_covers`, so no row, and the route
    makes a new device; or done: the device is gone from the list read again under the row lock) or after it (it sees the row, revokes
    it, and waits on the take-up's row lock). False: not given a row. A row written meanwhile (a sign-out from Settings > Chat apps) wins."""
    chat_signouts.lock_account(db, user_id)
    if chat_signouts.pending_covers(db, user_id, device_id):
        db.rollback()
        return False
    db.execute(text("""INSERT INTO chat_site_devices (user_id, device_id, kind, sign_in, created_ms, last_used_utc)
                       VALUES (:u, :d, :k, :h, 0, :now) ON CONFLICT (user_id, device_id) DO NOTHING"""),
               {"u": user_id, "d": device_id, "k": kind, "h": sign_in, "now": now})
    db.commit()
    return True


def _served(db, user_id, device_id, sign_in, now):
    db.execute(text("""UPDATE chat_site_devices SET sign_in = :h, last_used_utc = :now, last_token_utc = :now
                       WHERE user_id = :u AND device_id = :d"""), {"h": sign_in, "now": now, "u": user_id, "d": device_id})


def issue(db, matrix, user_id, mxid, form, session_id, public_url, now=None, site_name="Laba", nonce=0):
    """(status, body) for POST /api/chat/token. `matrix` is helpers/matrix_client or a fake; `form` the request's values; `nonce` the
    cookie's login_nonce. Commits."""
    now = int(now or time.time())
    kind = form.get("kind") or "chat"
    device_id = form.get("device_id") or None
    if kind not in KINDS or (device_id and not DEVICE_ID.match(device_id)):
        return 400, {"bad_request": True}
    sign_in = chat_signouts.sign_in_hash(session_id)
    try:
        if device_id:
            row = _row(db, user_id, device_id)
            if row is None and not _someone_elses(db, user_id, device_id):
                # a device from before rows were kept: given a row only while Synapse has it, it is named as the site's and no pending
                # sign-out covers it (_adopt), then held, checked and listed again like any other
                listed = matrix.list_devices(mxid)
                found = next((d for d in listed if d["device_id"] == device_id), None)
                if found is not None and is_site_device(found.get("name"), site_name) and _adopt(db, user_id, device_id, kind, sign_in, now):
                    row = _row(db, user_id, device_id)
            if row is not None and row[0] == kind and not row[1]:
                refused = _refusal(db, user_id, nonce)       # the row is held (and committed): a sign-out revoking it waits for this answer
                if refused:
                    db.rollback()
                    return refused
                if device_id in {d["device_id"] for d in matrix.list_devices(mxid)}:
                    token = matrix.login_as(mxid, device_id)
                    _served(db, user_id, device_id, sign_in, now)
                    db.commit()
                    return 200, _answer(token, public_url)
                chat_signouts.revoke_rows(db, user_id, [device_id], "deleted_elsewhere", now)      # signed out on Synapse meanwhile
        return _mint(db, matrix, user_id, mxid, kind, sign_in, now, public_url, nonce)
    except DBAPIError as e:
        db.rollback()
        if chat_signouts.lock_timeout(e):
            return 503, RETRY
        raise
    except matrix.MatrixError:
        db.rollback()
        return 503, RETRY


def _mint(db, matrix, user_id, mxid, kind, sign_in, now, public_url, nonce=0):
    """A new device with an id the site chose, its row committed under the mint lock before Synapse is asked to make it."""
    db.commit()                    # whatever was revoked above, and the row lock released: the mint never waits on a lock holding one
    device_id = None
    for _ in range(3):
        try:
            chat_signouts.lock_account(db, user_id)
            refused = _refusal(db, user_id, nonce)    # read under the lock: a deletion or chat switched off that it waited for counts
            if refused:
                db.rollback()
                return refused
            candidate = new_device_id()
            db.execute(text("""INSERT INTO chat_site_devices (user_id, device_id, kind, sign_in, created_ms, last_used_utc)
                               VALUES (:u, :d, :k, :h, :ms, :now)"""),
                       {"u": user_id, "d": candidate, "k": kind, "h": sign_in, "ms": chat_signouts._now_ms(), "now": now})
            db.commit()
            device_id = candidate
            break
        except IntegrityError:
            db.rollback()
    if device_id is None:
        return 503, RETRY
    try:
        token = matrix.login_as(mxid, device_id)
        if token.get("device_id") != device_id:
            raise matrix.MatrixError("login_as made another device")
    except matrix.MatrixError:                     # Unreachable too: a timeout may come after Synapse made the device
        db.rollback()
        # queue revokes the row itself, after it holds the mint lock: never a row lock first (a sign-out holding the lock waits for the row)
        chat_signouts.queue(db, user_id, "mint_failed", "listed", device_ids=[device_id], mxid=mxid)   # Synapse may have made it
        db.commit()
        return 503, RETRY
    made = db.execute(text("""UPDATE chat_site_devices SET last_token_utc = :now WHERE user_id = :u AND device_id = :d AND revoked_utc = 0"""),
                      {"now": now, "u": user_id, "d": device_id}).rowcount
    if not made:
        # a sign-out revoked the row while Synapse was making the device, and may already have listed the devices without it
        chat_signouts.queue(db, user_id, "revoked_while_made", "listed", device_ids=[device_id], mxid=mxid)
        db.commit()
        return 503, RETRY
    db.commit()
    return 200, _answer(token, public_url)
