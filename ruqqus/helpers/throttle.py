"""Boiling/gear escalating spam throttle.

Two decaying accumulators per (namespace, key):
- "boil" heats up by HEAT_PER_ACTION on every gated action and constantly
  decays back toward zero (half-life decay - the literal math behind
  "water returns to room temperature"). Crossing the boiling point forces
  a cooldown and resets boil to zero.
- "gear" starts at (and decays back toward) a base cooldown duration, and
  is multiplied each time boil trips, so repeat offenders in a short span
  get progressively longer cooldowns while a quiet user drifts back to
  the base tier.

The content-action namespace (posts, comments, forwards, reposts) uses a
separate token bucket per user and per IP instead of a decaying
accumulator: a slot frees up steadily every few minutes rather than the
whole allowance cooling down in the background. It still combines both
buckets, so it catches both a single spammy account and many throwaway
accounts sharing one IP - see content_register_action()/
content_cooldown_remaining() below. Every other namespace uses the
boil/gear decay model described above unchanged.
"""

import time

from ruqqus.__main__ import r


HEAT_PER_ACTION = 20
BOILING_POINT = 400
# 1 hour half-life: a still-fresh (untripped) boil level decays back by
# about half every hour, so "20 per hour" is also true as a sustainable
# rate over time, not just as a one-time burst allowance.
BOIL_HALF_LIFE = 3600.0

GEAR_BASE_SECONDS = 300.0
GEAR_HALF_LIFE = 3600.0
GEAR_MULTIPLIER = 2.0
GEAR_CAP_SECONDS = 6 * 3600.0

SENSITIVITY_KEY = "throttle:sensitivity"
GEAR_BASE_KEY = "throttle:gear_base_seconds"

# Namespace-specific overrides. Anything not listed here uses the module
# defaults above. Namespace names are also used verbatim in Redis keys.
NAMESPACE_OVERRIDES = {
    "password_reset": {
        "HEAT_PER_ACTION": 34,
        "GEAR_BASE_SECONDS": 900.0,
    },
}

CONTENT_NAMESPACE = "content"

# The content namespace's allowance is a token bucket instead of a
# decaying accumulator: a slot frees up steadily every REFILL_SECONDS
# rather than the whole meter cooling down in the background, so "20 per
# hour" also means "the next slot is never more than 3 minutes away."
CONTENT_TOKEN_CAPACITY = 20
CONTENT_TOKEN_REFILL_SECONDS = 180.0  # 60min / 20 = one token every 3 minutes


def _config(namespace, name):
    overrides = NAMESPACE_OVERRIDES.get(namespace, {})
    return overrides.get(name, globals()[name])


def _get_sensitivity():
    if not r:
        return 1.0
    try:
        return max(0.1, float(r.get(SENSITIVITY_KEY)))
    except (TypeError, ValueError):
        return 1.0


def _get_gear_base(namespace):
    default = _config(namespace, "GEAR_BASE_SECONDS")
    if not r:
        return default
    try:
        return max(30.0, float(r.get(GEAR_BASE_KEY)))
    except (TypeError, ValueError):
        return default


def _read_decaying(key, half_life, now, floor=0.0):
    if not r:
        return floor
    data = r.hgetall(key)
    if not data:
        return floor
    try:
        val, ts = float(data.get("val", floor)), float(data.get("ts", now))
    except (TypeError, ValueError):
        return floor
    elapsed = max(0.0, now - ts)
    return floor + (val - floor) * (0.5 ** (elapsed / half_life))


def _write_decaying(key, value, now, ttl):
    r.hset(key, mapping={"val": value, "ts": now})
    r.expire(key, ttl)


def _token_key(namespace, key):
    return f"throttle:{namespace}:tokens:{key}"


def _peek_tokens(key, capacity, refill_seconds, now):
    """Current token count (after refill), without writing anything."""
    if not r:
        return capacity
    data = r.hgetall(key)
    if not data:
        return capacity
    try:
        tokens, ts = float(data.get("tokens", capacity)), float(data.get("ts", now))
    except (TypeError, ValueError):
        return capacity
    elapsed = max(0.0, now - ts)
    return min(capacity, tokens + elapsed / refill_seconds)


def _consume_tokens(key, capacity, refill_seconds, now, amount):
    """Refill by elapsed time, then subtract amount. Allowed to go
    negative - the caller uses a negative result to detect an over-budget
    action and pick the culprit, without a second read."""
    if not r:
        return capacity - amount
    current = _peek_tokens(key, capacity, refill_seconds, now) - amount
    r.hset(key, mapping={"tokens": current, "ts": now})
    r.expire(key, int(capacity * refill_seconds * 2))
    return current


def _boil_key(namespace, key):
    return f"throttle:{namespace}:boil:{key}"


def _gear_key(namespace, key):
    return f"throttle:{namespace}:gear:{key}"


def _cooldown_key(namespace, key):
    return f"throttle:{namespace}:cooldown:{key}"


def cooldown_remaining(namespace, key):
    if not r:
        return 0
    ttl = r.ttl(_cooldown_key(namespace, key))
    return max(0, ttl)


def _trigger_cooldown(namespace, key, now):
    gear_base = _get_gear_base(namespace)
    current_gear = _read_decaying(
        _gear_key(namespace, key), GEAR_HALF_LIFE, now, floor=gear_base)

    r.set(_cooldown_key(namespace, key), 1, ex=max(1, int(current_gear)))

    next_gear = min(current_gear * GEAR_MULTIPLIER, GEAR_CAP_SECONDS)
    _write_decaying(_gear_key(namespace, key), next_gear, now,
                     ttl=int(GEAR_HALF_LIFE * 10))
    r.delete(_boil_key(namespace, key))


def register_action(namespace, key):
    """Add heat for one action under (namespace, key). Triggers a
    cooldown (and resets boil) if this pushes the boil meter over its
    threshold."""

    if not r:
        return

    now = time.time()
    heat = _config(namespace, "HEAT_PER_ACTION")
    threshold = BOILING_POINT / _get_sensitivity()

    boil_key = _boil_key(namespace, key)
    new_boil = _read_decaying(boil_key, BOIL_HALF_LIFE, now) + heat

    if new_boil < threshold:
        _write_decaying(boil_key, new_boil, now, ttl=int(BOIL_HALF_LIFE * 10))
        return

    _trigger_cooldown(namespace, key, now)


def current_boil_pct(namespace, key):
    if not r:
        return 0
    now = time.time()
    current = _read_decaying(_boil_key(namespace, key), BOIL_HALF_LIFE, now)
    threshold = BOILING_POINT / _get_sensitivity()
    return int(min(100, round(100 * current / threshold)))


# --- Content-action namespace: combined user+IP token bucket ---
#
# Every action draws down BOTH a per-user and a per-IP token bucket under
# the same namespace. Many low-volume accounts sharing one IP each keep
# their own bucket healthy, but all drive down the *same* ip-bucket, so
# it still trips even though no single account looks suspicious alone.

def _content_user_key(uid):
    return f"user:{uid}"


def _content_ip_key(ip):
    return f"ip:{ip}"


def content_cooldown_remaining(uid, ip):
    return max(
        cooldown_remaining(CONTENT_NAMESPACE, _content_user_key(uid)),
        cooldown_remaining(CONTENT_NAMESPACE, _content_ip_key(ip)),
    )


def content_register_action(uid, ip, weight=1):
    """weight lets a single request that did the work of several actions
    (e.g. one /submit call that forwarded to N guilds at once) cost N+1
    tokens instead of registering as just one action regardless of how
    much it actually did."""

    if not r:
        return

    now = time.time()
    capacity = CONTENT_TOKEN_CAPACITY / _get_sensitivity()
    refill = CONTENT_TOKEN_REFILL_SECONDS

    user_key = _token_key(CONTENT_NAMESPACE, _content_user_key(uid))
    ip_key = _token_key(CONTENT_NAMESPACE, _content_ip_key(ip))

    user_tokens = _consume_tokens(user_key, capacity, refill, now, weight)
    ip_tokens = _consume_tokens(ip_key, capacity, refill, now, weight)

    if user_tokens < 0 or ip_tokens < 0:
        # Whichever identity (account or IP) ran further into deficit
        # takes the escalating gear penalty and gets its cooldown flag
        # set. content_cooldown_remaining() checks both the user's and
        # the IP's cooldown flags (taking the max), so setting it on just
        # the culprit is enough to block every account sharing that IP
        # too when the IP is the culprit, without needing to duplicate
        # the flag onto the other key.
        culprit_key = (
            _content_user_key(uid) if user_tokens <= ip_tokens
            else _content_ip_key(ip)
        )
        _trigger_cooldown(CONTENT_NAMESPACE, culprit_key, now)
        # The bucket overflowing has to reset BOTH sides together, or the
        # non-culprit side's leftover deficit would carry over into the
        # next burst and flip which identity looks like the culprit next
        # time, silently bypassing that identity's own escalated gear.
        r.hset(user_key, mapping={"tokens": 0, "ts": now})
        r.hset(ip_key, mapping={"tokens": 0, "ts": now})


def get_display_state(uid, ip):
    """Live (non-cached) content-action throttle state for the UI meter."""

    remaining = content_cooldown_remaining(uid, ip)
    if remaining > 0:
        return {"in_cooldown": True, "cooldown_remaining": remaining, "boil_pct": 100, "remaining_actions": 0}

    now = time.time()
    capacity = CONTENT_TOKEN_CAPACITY / _get_sensitivity()
    refill = CONTENT_TOKEN_REFILL_SECONDS

    user_tokens = _peek_tokens(
        _token_key(CONTENT_NAMESPACE, _content_user_key(uid)), capacity, refill, now)
    ip_tokens = _peek_tokens(
        _token_key(CONTENT_NAMESPACE, _content_ip_key(ip)), capacity, refill, now)
    available = max(0.0, min(user_tokens, ip_tokens))

    remaining_actions = int(available)
    pct = max(0, min(100, int(round(100 * (1 - available / capacity))))) if capacity > 0 else 100

    return {"in_cooldown": False, "cooldown_remaining": 0, "boil_pct": pct, "remaining_actions": remaining_actions}


# --- Admin-tunable knobs ---

def set_sensitivity(x):
    if r:
        r.set(SENSITIVITY_KEY, float(x))


def set_gear_base_seconds(x):
    if r:
        r.set(GEAR_BASE_KEY, float(x))


def get_sensitivity():
    return _get_sensitivity()


def get_gear_base_seconds():
    return _get_gear_base(CONTENT_NAMESPACE)
