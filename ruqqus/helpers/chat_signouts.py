"""Durable chat sign-outs (chat plan, phase 2, step 2). Plain `text()` SQL, tested on SQLite (tests/test_chat_signouts.py) and on
the real app (tests/pg/test_chat_signout_routes.py).

A credential event (account_sessions.revoke), a child's chat switched off, an account newly child-locked, one device signed out in
Settings > Chat apps, a device the token route had to give up: each writes a row in `chat_device_signouts` IN THE SAME TRANSACTION as
the change that asked for it (`queue`), and the Synapse calls run only after the commit (`run_for_user`, appended to `g.after_commit`),
retried for ever with a back-off (`run_one`), and by every cycle of scripts/recomputes.py (`run_due`). A Synapse that is down never
stops a password change, and a sign-out it missed lands when it is back.

`chat_site_devices` holds the chat devices the site made (helpers/chat_tokens.py): `queue` revokes the ones a sign-out covers in the
same transaction, so the token route stops serving them before Synapse has heard (the logical revocation). `queue` and a device being
made take one lock per account (`pg_advisory_xact_lock(MINT_LOCK, user_id)`, Postgres only), so a device is either made before a
sign-out (and revoked by it) or after it (`created_ms > spare_after_ms`, and never touched by it).

THE INTERIM (build steps 2 and 3). Until the browser keeps its crypto stores (step 4), today's chat page deletes its store when its device
is signed out, which would destroy room keys nothing else holds. So while `KEEP_SITE_BROWSERS` is on, an `all` sign-out for any reason
but the ones that already signed the site's own browsers out before this queue existed (`SIGNS_OUT_SITE_BROWSERS_TODAY`: a child's chat
switched off, a parent stopping managing, an account deleted) is written with the word `site` in `spared`: it signs out chat APPS only,
exactly as `chat_devices.sign_out(apps_only=True)` did, and revokes no site row. Step 4 turns it off in the same deploy as the client
that keeps its stores. `run_one` keeps understanding the word, so a row queued before still finishes as it was meant.
"""
import functools
import hashlib
import logging
import re
import time

from sqlalchemy import bindparam, text
from sqlalchemy.exc import DBAPIError

# THE INTERIM SWITCH: build step 4 sets this False, in the same deploy as the browser that keeps its stores (see the docstring).
KEEP_SITE_BROWSERS = True
SIGNS_OUT_SITE_BROWSERS_TODAY = frozenset({"child_chat_off", "child_stopped", "deleted"})
SITE = "site"                       # in `spared`: every site device is kept (today's apps-only rule)

MINT_LOCK = 7003                    # pg_advisory_xact_lock(MINT_LOCK, user_id): a sign-out and a device being made, one at a time
BACKOFF = (60, 120, 300, 900, 3600)  # after each failed try; the last repeats for ever
KEEP_DAYS = 30                       # helpers/retention.py: a finished row goes this long after it was done
OIDC_WAIT_SECONDS = 86400            # /oidc/token waits for a pending row younger than this
WAIT_LOCK_SECONDS = 15               # ...and waits this long for a row another worker is on, then answers "try again"
LATE_SECONDS = 3600                  # /admin/chat shows a row pending longer than this in red
DEVICE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")

_ready = []


def _now_ms():
    return int(time.time() * 1000)


def sign_in_hash(session_id):
    """What `chat_site_devices.sign_in` holds: a SHA-256 of the cookie's session id, never the id."""
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest() if session_id else None


def _pg(db):
    return db.get_bind().dialect.name == "postgresql"


def missing_table(error):
    """The database saying a table doesn't exist (Postgres 42P01, or SQLite's words in the unit tests)."""
    original = getattr(error, "orig", None)
    if getattr(original, "pgcode", None) == "42P01":
        return True
    return "no such table" in str(original or error).lower()


def lock_timeout(error):
    """Postgres giving up on a lock (`SET LOCAL lock_timeout`): 55P03."""
    return getattr(getattr(error, "orig", None), "pgcode", None) == "55P03"


def tables_ready(db):
    """Has this database got the migration (2026-12-20_chat_sign_outs.sql)? Asked in a savepoint, remembered per worker once true.
    Without it a credential change still goes through (its chat sign-out is only logged) and the token route answers 503."""
    if _ready:
        return True
    try:
        with db.begin_nested():
            db.execute(text("SELECT 1 FROM chat_device_signouts LIMIT 0"))
            db.execute(text("SELECT 1 FROM chat_site_devices LIMIT 0"))
    except DBAPIError as e:
        if not missing_table(e):
            raise
        return False
    _ready.append(True)
    return True


def lock_account(db, user_id):
    """The account's mint lock, held until the caller's transaction ends (Postgres; SQLite has one writer anyway)."""
    if _pg(db):
        db.execute(text("SELECT pg_advisory_xact_lock(:k, :u)"), {"k": MINT_LOCK, "u": int(user_id)})


def mxid_of(db, user_id):
    row = db.execute(text("SELECT matrix_user_id FROM chat_identities WHERE user_id = :u"), {"u": int(user_id)}).first()
    return row[0] if row else None


def interim_spared(reason):
    """('site',) while the interim keeps the site's own browsers, for every reason but the ones that signed them out before."""
    return (SITE,) if KEEP_SITE_BROWSERS and reason not in SIGNS_OUT_SITE_BROWSERS_TODAY else ()


def _ids(value):
    return [x for x in (value or "").split(",") if x]


def this_sign_in(db, user_id, session_id):
    """The site devices decision 2 spares: of the non-revoked rows started by this cookie's sign-in, at most one chat row and one call
    row, the most recently used of each. Found here, on the server; a copy of the cookie can shelter no more than these two."""
    h = sign_in_hash(session_id)
    if not h:
        return ()
    try:
        with db.begin_nested():
            rows = db.execute(text("""SELECT device_id, kind FROM chat_site_devices WHERE user_id = :u AND sign_in = :h AND revoked_utc = 0
                                      ORDER BY last_used_utc DESC, created_ms DESC"""), {"u": int(user_id), "h": h}).all()
    except DBAPIError as e:
        if not missing_table(e):
            raise
        return ()
    kept = {}
    for device_id, kind in rows:
        kept.setdefault(kind, device_id)
    return tuple(kept[k] for k in sorted(kept))


def pending_covers(db, user_id, device_id):
    """Would a sign-out of the account still pending (queued, not done) delete this device if it had no site row, or one made before rows
    were kept (`created_ms` 0, older than every sign-out)? A `listed` one naming it, or an `all` one sparing neither it nor the site's
    browsers. The token route asks this, under the mint lock, before it gives a device from before the rows a row (chat_tokens._adopt)."""
    for scope, device_ids, spared in db.execute(text("""SELECT scope, device_ids, spared FROM chat_device_signouts
                                                         WHERE user_id = :u AND done_utc = 0"""), {"u": int(user_id)}).all():
        if scope == "listed":
            if device_id in _ids(device_ids):
                return True
        elif not {SITE, device_id} & set(_ids(spared)):
            return True
    return False


def revoke_rows(db, user_id, device_ids, reason, now=None):
    """Marks these site rows of the account revoked (no Synapse call). Returns how many changed."""
    device_ids = [d for d in device_ids if d]
    if not device_ids:
        return 0
    statement = text("""UPDATE chat_site_devices SET revoked_utc = :now, revoked_reason = :r
                        WHERE user_id = :u AND revoked_utc = 0 AND device_id IN :ids""").bindparams(bindparam("ids", expanding=True))
    return db.execute(statement, {"now": int(now or time.time()), "r": reason[:24], "u": int(user_id), "ids": device_ids}).rowcount


def revoke_device(db, user_id, device_id, reason, now=None):
    """One of the site's devices, signed out from Settings > Chat apps: its row revoked BEFORE Synapse deletes it (the caller commits
    first). The UPDATE waits for a token request still holding the row, and every later one finds it revoked, so nothing re-tokens, and
    so re-creates, the device after Synapse deleted it. A site device from before rows were kept is given a revoked row for the same
    reason (the token route's own INSERT of it then does nothing, and it reads the row it finds)."""
    now = int(now or time.time())
    db.execute(text("""INSERT INTO chat_site_devices (user_id, device_id, kind, created_ms, last_used_utc, revoked_utc, revoked_reason)
                       VALUES (:u, :d, 'chat', 0, 0, :now, :r) ON CONFLICT (user_id, device_id) DO NOTHING"""),
               {"u": int(user_id), "d": device_id, "now": now, "r": reason[:24]})
    return revoke_rows(db, user_id, [device_id], reason, now)


def queue(db, user_id, reason, scope="all", device_ids=(), spared=(), mxid=None, now_ms=None):
    """Queues a chat sign-out of the account, in the caller's transaction: `all` (every device but `spared`, and site devices made
    after it) or `listed` (`device_ids`). Revokes the site rows it covers in the same transaction. No Synapse call here: they run after
    the commit (`run_for_user`, scheduled on `g.after_commit` inside a request). An account with no chat gets no row. A database
    without the migration keeps nothing and logs it (the credential change itself goes through). Returns the row's id, or None.
    `spared` may be a callable, worked out only once the mint lock is held (account_sessions.revoke's `this_sign_in`): a device of
    this sign-in made and committed just before the lock is then seen and spared, never revoked as one made before the sign-out."""
    if scope not in ("all", "listed"):
        raise ValueError(scope)
    try:
        with db.begin_nested():
            mxid = mxid or mxid_of(db, user_id)
            if not mxid:
                return None
            lock_account(db, user_id)
            at = max(int(now_ms or 0), _now_ms())          # read after the lock: a device made after this is spared by run_one
            now = at // 1000
            if callable(spared):
                spared = spared()                          # under the lock too: what it names is what `at` covers
            listed = [d for d in device_ids if DEVICE.match(d or "")] if scope == "listed" else []
            spared = tuple(d for d in spared if d) + (interim_spared(reason) if scope == "all" else ())
            row = db.execute(text("""INSERT INTO chat_device_signouts (user_id, mxid, scope, device_ids, spared, spare_after_ms, reason,
                                                                      created_utc, next_try_utc)
                                     VALUES (:u, :m, :s, :d, :sp, :at, :r, :now, :now) RETURNING id"""),
                             {"u": int(user_id), "m": mxid, "s": scope, "d": ",".join(listed) or None,
                              "sp": ",".join(spared) or None, "at": at, "r": reason[:24], "now": now}).first()
            if scope == "listed":
                revoke_rows(db, user_id, listed, reason, now)
            elif SITE not in spared:
                covered = [d for (d,) in db.execute(text("""SELECT device_id FROM chat_site_devices WHERE user_id = :u
                                                            AND revoked_utc = 0 AND created_ms <= :at"""),
                                                    {"u": int(user_id), "at": at}).all() if d not in spared]
                revoke_rows(db, user_id, covered, reason, now)
    except DBAPIError as e:
        if not missing_table(e):
            raise
        logging.error(f"chat sign-out not queued: migration missing (user {user_id}, {reason}): "
                      "apply scripts/migrations/2026-12-20_chat_sign_outs.sql")
        return None
    _schedule(user_id)
    return row[0] if row else None


def _schedule(user_id):
    """Inside a request: run the account's sign-outs once the request's changes are committed (`__main__.after_request` spawns each
    `g.after_commit`, with no app or request context: run_for_user makes its own)."""
    from flask import current_app, g, has_request_context
    if not has_request_context():
        return
    done = g.setdefault("chat_signouts_scheduled", set())
    if user_id in done:
        return
    done.add(user_id)
    if not hasattr(g, "after_commit"):
        g.after_commit = []
    g.after_commit.append(functools.partial(run_for_user, current_app._get_current_object(), int(user_id)))


def _site_name():
    try:
        from flask import current_app
        return current_app.config["SITE_NAME"]
    except Exception:
        import os
        return os.environ.get("SITE_NAME", "Laba").strip()


def run_one(db, matrix, row_id, now=None, wait=False, site_name=None):
    """Works one queued sign-out and commits: lists the account's devices on Synapse, deletes the ones it covers, then marks the row
    done, or backs it off (BACKOFF) and leaves it for later: it never gives up. Takes the row with `FOR UPDATE SKIP LOCKED` (a row
    another worker is on is left to it) or, with `wait`, plain `FOR UPDATE` under a `WAIT_LOCK_SECONDS` lock timeout (/oidc/token
    waits for it). Returns "done", "no_user", "failed", or None for a row that was taken or finished. `matrix` is helpers/matrix_client or a fake."""
    now = int(now or time.time())
    lock = ""
    if _pg(db):
        if wait:
            db.execute(text(f"SET LOCAL lock_timeout = '{int(WAIT_LOCK_SECONDS)}s'"))
            lock = " FOR UPDATE"
        else:
            lock = " FOR UPDATE SKIP LOCKED"
    row = db.execute(text("SELECT id, user_id, mxid, scope, device_ids, spared, spare_after_ms, attempts FROM chat_device_signouts "
                          "WHERE id = :i AND done_utc = 0" + lock), {"i": int(row_id)}).first()
    if row is None:
        db.commit()
        return None
    _id, user_id, mxid, scope, device_ids, spared, spare_after_ms, attempts = row
    unknown = getattr(matrix, "UserUnknown", None)
    errors = []
    try:
        devices = matrix.list_devices(mxid)
    except Exception as e:
        if unknown is not None and isinstance(e, unknown):          # not on Synapse (a dev reset, a restore that didn't pair)
            db.execute(text("UPDATE chat_device_signouts SET done_utc = :now, last_error = 'no_user' WHERE id = :i"),
                       {"now": now, "i": _id})
            db.commit()
            return "no_user"
        errors.append(type(e).__name__)
        devices = []
    if not errors:
        present = [d["device_id"] for d in devices if d.get("device_id")]
        if scope == "listed":
            targets = [d for d in present if d in set(_ids(device_ids))]
        else:
            keep = set(_ids(spared))
            # read AFTER the list: a device made while this ran has its row committed before Synapse made it, so it is seen here
            site_rows = db.execute(text("SELECT device_id, created_ms, revoked_utc FROM chat_site_devices WHERE user_id = :u"),
                                   {"u": user_id}).all()
            keep |= {d for d, created, revoked in site_rows if not revoked and created > spare_after_ms}
            if SITE in keep:                                   # the interim: today's apps-only rule
                from ruqqus.helpers.chat_devices import is_site_device
                name = site_name or _site_name()
                keep |= {d for d, _c, _r in site_rows}
                keep |= {d["device_id"] for d in devices if is_site_device(d.get("name"), name)}
            targets = [d for d in present if d not in keep]
        for device_id in targets:
            try:
                matrix.delete_device(mxid, device_id)
            except Exception as e:
                errors.append(type(e).__name__)
    if errors:
        tries = (attempts or 0) + 1
        db.execute(text("""UPDATE chat_device_signouts SET attempts = :a, last_error = :e, next_try_utc = :next WHERE id = :i"""),
                   {"a": tries, "e": errors[0][:64], "next": now + BACKOFF[min(tries, len(BACKOFF)) - 1], "i": _id})
        db.commit()
        print(f"chat sign-out {_id} for {user_id} not done yet: {errors[0]}", flush=True)
        return "failed"
    db.execute(text("UPDATE chat_device_signouts SET done_utc = :now, last_error = NULL WHERE id = :i"), {"now": now, "i": _id})
    db.commit()
    return "done"


def _due_ids(db, now, user_id=None, limit=50):
    where = "done_utc = 0 AND next_try_utc <= :now" + (" AND user_id = :u" if user_id is not None else "")
    return [i for (i,) in db.execute(text(f"SELECT id FROM chat_device_signouts WHERE {where} ORDER BY next_try_utc, id LIMIT :n"),
                                     {"now": now, "u": user_id, "n": limit}).all()]


def run_for_user(app, user_id):
    """What every queue in a request appends to g.after_commit: the account's due sign-outs, oldest first, each committed on its own.
    Runs in a greenlet with no app or request context, so it makes both its own. Best effort: run_due is the backstop."""
    from ruqqus.__main__ import db_session
    import ruqqus.helpers.matrix_client as matrix
    with app.app_context():
        db = db_session()
        try:
            for row_id in _due_ids(db, int(time.time()), user_id=user_id):
                run_one(db, matrix, row_id)
        except Exception as e:
            db.rollback()
            print(f"chat sign-outs for {user_id} not run: {type(e).__name__}", flush=True)
        finally:
            db_session.remove()


def run_due(db, matrix, limit=50, now=None):
    """scripts/recomputes.py's step: every due row, whoever's. Returns {outcome: count}."""
    done = {}
    for row_id in _due_ids(db, int(now or time.time()), limit=limit):
        outcome = run_one(db, matrix, row_id, now=now)
        if outcome:
            done[outcome] = done.get(outcome, 0) + 1
    return done


def flush_for_oidc(db, matrix, user_id, now=None):
    """/oidc/token asks this before it lets an outside app sign in: an app signed in while a sign-out of the account is still out would
    be deleted by it (its device has no site row). Waits for the account's pending rows (a row another worker is on is waited for, not
    skipped) and runs each. False while one younger than a day is still pending, or a lock wasn't had in WAIT_LOCK_SECONDS."""
    now = int(now or time.time())
    if not tables_ready(db):
        return True
    try:
        for (row_id,) in db.execute(text("SELECT id FROM chat_device_signouts WHERE user_id = :u AND done_utc = 0 ORDER BY id"),
                                    {"u": int(user_id)}).all():
            run_one(db, matrix, row_id, now=now, wait=True)
    except DBAPIError as e:
        db.rollback()
        if lock_timeout(e):
            return False
        raise
    left = db.execute(text("""SELECT count(*) FROM chat_device_signouts WHERE user_id = :u AND done_utc = 0 AND created_utc > :since"""),
                      {"u": int(user_id), "since": now - OIDC_WAIT_SECONDS}).scalar()
    return not left


def pending_summary(db, now=None):
    """For /admin/chat: whether the tables exist, how many sign-outs are pending, and how many have waited longer than an hour."""
    now = int(now or time.time())
    if not tables_ready(db):
        return {"ready": False, "pending": 0, "late": 0}
    pending, late = db.execute(text("""SELECT count(*), COALESCE(SUM(CASE WHEN created_utc < :late THEN 1 ELSE 0 END), 0)
                                       FROM chat_device_signouts WHERE done_utc = 0"""), {"late": now - LATE_SECONDS}).first()
    return {"ready": True, "pending": int(pending or 0), "late": int(late or 0)}
