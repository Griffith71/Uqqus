"""QR sign-in: the rules (no I/O but the Redis calls the routes hand it a client for). A computer shows a code, a phone that is
signed in approves it, and the computer asks "Sign in as @name?" before anything happens. The routes are
routes/qr_login_pages.py; CLAUDE.md "QR sign-in" says what this defends against and what it doesn't.

What Redis holds, under the code's digest (never the code):

    qr:wait:<digest>   {ua, cc, t}          120 s   the computer's browser and system by name (never its header), country and time
    qr:ok:<digest>     {uid, nonce, t}       60 s   only after an atomic DEL of wait returned 1
    qr:no:<digest>     1                    120 s   the same rule
    qr:try:<digest>    wrong answers        120 s   five delete wait

The code travels only in the address's fragment, which a browser sends to nobody; the computer's own cookie holds the digest
(`qr_pending`), so its polling and finishing carry no code, and that is what binds a code to the browser that asked for it.
"""
import json
import secrets

from ruqqus.helpers import oidc_provider

SWITCH = "QR_LOGIN_ENABLED"
DEFAULT = "1"
_OFF = ("", "0", "false", "no", "off")

CODE_SECONDS = 120
APPROVED_SECONDS = 60
WRONG_ANSWERS = 5
# what a QR sign-in lasts, from the moment it is made; nothing ever moves it
SESSION_SECONDS = 12 * 3600
# how long after pressing the button the cookie keeps its digest: the code's time and the approval's
PENDING_SECONDS = CODE_SECONDS + APPROVED_SECONDS

# limited per code rather than per address (routes/qr_login_pages.py), so routes/errors.py doesn't count their 429s against the address
PER_CODE_PATHS = ("/login/qr/status", "/login/qr/finish")

PENDING_KEY = "qr_pending"
LIMITED_KEY = "qr_limited"
EXPIRES_KEY = "qr_expires_utc"

# deleted, a bot, suspended, the member's own switch or a sign-in ended since: nobody is told which
DELETED, BOT, SUSPENDED, SWITCHED_OFF, NONCE_MOVED = "deleted", "bot", "suspended", "switched_off", "nonce_moved"

# Atomic: two requests for one approval can't both get it. A compare-and-delete of one key needs no compare, so GET and DEL.
_TAKE = "local v = redis.call('GET', KEYS[1]); if v then redis.call('DEL', KEYS[1]) end; return v"


def switched_on(environ):
    return (environ.get(SWITCH, DEFAULT) or "").strip().lower() not in _OFF


def offered(environ, redis, read_only):
    """The flow keeps every code in Redis and a sign-in writes to the database, so with no Redis or in read-only mode it is
    hidden too. `redis` is the configured client or None. A Redis that is configured but down isn't asked about here: every page
    is a 500 then anyway, because the limiter and the ban check use the same Redis (`before_request`)."""
    return switched_on(environ) and redis is not None and not read_only


def new_code():
    """(the code, for the address only; its digest, for everything else)."""
    code = secrets.token_urlsafe(32)
    return code, oidc_provider.digest(code)


def digest_of(code):
    """The digest of what a phone sent. Nothing that long or that isn't a string is a code."""
    if not isinstance(code, str) or not code or len(code) > 128:
        return None
    return oidc_provider.digest(code)


def approve_address(server_name, https, code):
    """What the picture says. The code is in the fragment: no access log, Referer, error tracker or CDN ever sees it."""
    return f"{'https' if https else 'http'}://{server_name}/login/qr/approve#c={code}"


def wait_key(digest):
    return f"qr:wait:{digest}"


def ok_key(digest):
    return f"qr:ok:{digest}"


def no_key(digest):
    return f"qr:no:{digest}"


def try_key(digest):
    return f"qr:try:{digest}"


def read_json(raw):
    """A stored value as a dict, or None: an expired key, or anything that isn't what this module wrote."""
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def take(redis, key):
    """The value of `key`, deleted in the same step: only one caller ever gets it (tests/pg/test_qr_login.py, on real Redis)."""
    return redis.eval(_TAKE, 1, key)


def status_of(redis, digest):
    """(state, approval): `denied`, `approved` with its {uid, nonce, t}, `waiting`, or `expired`. Denied first: nothing else can
    be there with it, since both come only from the one DEL of wait."""
    if not digest:
        return "expired", None
    if redis.get(no_key(digest)):
        return "denied", None
    approval = read_json(redis.get(ok_key(digest)))
    if approval:
        return "approved", approval
    if redis.get(wait_key(digest)):
        return "waiting", None
    return "expired", None


def wrong_answer(redis, digest):
    """Counts a wrong password or code against this code; the fifth deletes it. True while the code still stands."""
    key = try_key(digest)
    count = redis.incr(key)
    redis.expire(key, CODE_SECONDS)
    if int(count) >= WRONG_ANSWERS:
        redis.delete(wait_key(digest))
        return False
    return True


def refusal(user, nonce=None):
    """Why this account can't be signed in with a code, or None. A closed list; the routes say one generic sentence for all of
    it, so nobody learns whether an account has the switch off. An account the signup guard is holding may: it only stops
    acting on others, and a held account can sign in with its password too."""
    if user is None or getattr(user, "is_deleted", False):
        return DELETED
    if getattr(user, "is_bot", False):
        return BOT
    if getattr(user, "is_suspended", False):
        return SUSPENDED
    if getattr(user, "qr_login_off", False):
        return SWITCHED_OFF
    if nonce is not None and nonce != getattr(user, "login_nonce", None):
        return NONCE_MOVED
    return None


def session_over(session, now):
    """Is this sign-in past its end? Only a QR sign-in has one. A limited session with no end, or one that can't be read, is
    over: the cookie is signed, so that can only be an old or broken one, and it fails closed."""
    expires = session.get(EXPIRES_KEY)
    if expires is None:
        return bool(session.get(LIMITED_KEY))
    return not isinstance(expires, int) or isinstance(expires, bool) or now >= expires


def is_limited(session):
    return bool(session.get(LIMITED_KEY))


def begin_limited(session, now):
    session[LIMITED_KEY] = True
    session[EXPIRES_KEY] = int(now) + SESSION_SECONDS


def end_limited(session):
    """The password was given: this is an ordinary sign-in from now on."""
    session.pop(LIMITED_KEY, None)
    session.pop(EXPIRES_KEY, None)


def pending_digest(session, now):
    """The digest this browser asked for, while it can still count."""
    pending = session.get(PENDING_KEY)
    if not isinstance(pending, dict):
        return None
    digest, made = pending.get("d"), pending.get("t")
    if not isinstance(digest, str) or not isinstance(made, int) or now - made > PENDING_SECONDS:
        return None
    return digest


def remember_pending(session, digest, now):
    session[PENDING_KEY] = {"d": digest, "t": int(now)}


def settle_pending(session, now):
    """Takes a digest out of the cookie once it is useless, so a cookie that has to stay (`history`) doesn't carry it."""
    if session.get(PENDING_KEY) is not None and pending_digest(session, now) is None:
        session.pop(PENDING_KEY, None)
