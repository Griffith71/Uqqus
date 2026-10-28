"""What the signup guard counts and remembers in Redis (the rules are helpers/signup_risk.py).

* **Bursts**: sign-ups in the last hour from one network (`pfx`, a salted hash of its /24 or /64), with one name shape
  (`shape`) and across the site (`all`), in ten-minute buckets that expire. Counts only: no address is stored.
* **Tallies** for /admin/signups: each sign-up's outcome (allow, quarantine, or the refusal's reason) per hour, kept
  8 days. Nothing about a refused person is kept but that count.
* **What a quarantined account tried** (helpers/quarantine.py): a count per action, kept 30 days, so the admins see
  what held accounts go on to attempt.
* **Settings** the admins change: the quarantine threshold, the surge switch and the surge ceiling.
* **Throwaway email domains**: `disposable_domains.txt` (CC0 list), read once per worker.

Every function takes the Redis client (`r`), so tests use tests/fake_redis.py; a missing Redis reads as zero and
writes nothing, so sign-up never fails for it. tests/test_signup_signals.py.
"""
import hashlib
import hmac
import time
from functools import lru_cache
from pathlib import Path

from ruqqus.helpers import signup_risk

BUCKET = 600
WINDOW = 3600
TALLY_HOURS = 8 * 24
TRIED_SECONDS = 30 * 86400

QUARANTINE_AT_KEY = "signup:quarantine_at"
SURGE_KEY = "signup:surge"
SURGE_CEILING_KEY = "signup:surge_ceiling"

DOMAINS_FILE = Path(__file__).with_name("disposable_domains.txt")


def network_hash(prefix, secret):
    """The stored name of a network: a keyed hash, so a row in signup_reviews never holds an address."""
    return hmac.new((secret or "").encode(), (prefix or "").encode(), hashlib.sha256).hexdigest()[:16]


def _bucket(now):
    return int(now) // BUCKET


def recent(r, kind, value, now=None):
    """Sign-ups counted for `kind`/`value` in the last hour (this ten-minute bucket and the five before it)."""
    if r is None:
        return 0
    now = now or time.time()
    current = _bucket(now)
    total = 0
    try:
        for bucket in range(current - WINDOW // BUCKET + 1, current + 1):
            total += int(r.get(f"su:{kind}:{value}:{bucket}") or 0)
    except Exception:
        return 0
    return total


def note(r, kind, value, now=None):
    if r is None:
        return
    key = f"su:{kind}:{value}:{_bucket(now or time.time())}"
    try:
        r.incr(key)
        r.expire(key, WINDOW + BUCKET)
    except Exception:
        pass


def note_sign_up(r, network, shape, now=None):
    """Count a sign-up that made an account (allowed or quarantined) in the three bursts."""
    note(r, "pfx", network, now)
    note(r, "all", "site", now)
    if signup_risk.shape_counts(shape):
        note(r, "shape", shape, now)


# ---------------------------------------------------------------- tallies

def _hour(now):
    return int(now) // 3600


def tally(r, outcome, now=None):
    """`outcome` is "allow", "quarantine" or a refusal's reason key."""
    if r is None:
        return
    key = f"su:tally:{_hour(now or time.time())}"
    try:
        r.hincrby(key, outcome, 1)
        r.expire(key, TALLY_HOURS * 3600)
    except Exception:
        pass


def tallies(r, hours, now=None):
    """Outcome -> count over the last `hours` hours."""
    totals = {}
    if r is None:
        return totals
    current = _hour(now or time.time())
    try:
        for hour in range(current - hours + 1, current + 1):
            for outcome, count in (r.hgetall(f"su:tally:{hour}") or {}).items():
                outcome = outcome.decode() if isinstance(outcome, bytes) else outcome
                totals[outcome] = totals.get(outcome, 0) + int(count)
    except Exception:
        return {}
    return totals


# ---------------------------------------------------------------- what held accounts tried

def note_attempt(r, user_id, action):
    if r is None:
        return
    key = f"su:tried:{int(user_id)}"
    try:
        r.hincrby(key, action, 1)
        r.expire(key, TRIED_SECONDS)
    except Exception:
        pass


def attempts(r, user_id):
    if r is None:
        return {}
    try:
        found = r.hgetall(f"su:tried:{int(user_id)}") or {}
    except Exception:
        return {}
    return {(k.decode() if isinstance(k, bytes) else k): int(v) for k, v in found.items()}


def forget_attempts(r, user_id):
    if r is None:
        return
    try:
        r.delete(f"su:tried:{int(user_id)}")
    except Exception:
        pass


# ---------------------------------------------------------------- settings

def _int(value, default):
    try:
        return int(value.decode() if isinstance(value, bytes) else value)
    except (TypeError, ValueError, AttributeError):
        return default


def settings(r):
    defaults = {"quarantine_at": signup_risk.QUARANTINE_AT, "surge": False,
                "surge_ceiling": signup_risk.SURGE_CEILING}
    if r is None:
        return defaults
    try:
        return {"quarantine_at": max(1, _int(r.get(QUARANTINE_AT_KEY), defaults["quarantine_at"])),
                "surge": _int(r.get(SURGE_KEY), 0) == 1,
                "surge_ceiling": max(1, _int(r.get(SURGE_CEILING_KEY), defaults["surge_ceiling"]))}
    except Exception:
        return defaults


def save_settings(r, quarantine_at, surge, surge_ceiling):
    r.set(QUARANTINE_AT_KEY, str(max(1, min(500, int(quarantine_at)))))
    r.set(SURGE_KEY, "1" if surge else "0")
    r.set(SURGE_CEILING_KEY, str(max(1, min(10000, int(surge_ceiling)))))


# ---------------------------------------------------------------- throwaway domains

@lru_cache(maxsize=1)
def disposable_domains():
    lines = DOMAINS_FILE.read_text(encoding="utf-8").splitlines()
    return frozenset(line.strip().lower() for line in lines if line.strip() and not line.startswith("#"))


def disposable(email):
    domain = signup_risk.email_domain(email)
    return bool(domain) and signup_risk.is_disposable(domain, disposable_domains())
