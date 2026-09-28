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

The content-action namespace additionally combines a per-user and a
per-IP accumulator into one score, so it catches both a single spammy
account and many throwaway accounts sharing one IP - see
content_register_action()/content_cooldown_remaining() below.
"""

import time

from ruqqus.__main__ import r


HEAT_PER_ACTION = 20
# 20 actions is the actual allowance, at any pace: for the content
# namespace, a solo user's own action heats both their user-key and their
# ip-key by HEAT_PER_ACTION, so their combined score rises by 2x that per
# action (see CONTENT_THRESHOLD_MULTIPLIER below) - 400 * 2 / (2*20) = 20
# actions before the 21st trips it, holding regardless of how bunched
# together they are (no meaningful decay happens within a normal burst).
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

# Content-action namespace uses a combined (user + ip) score, so its
# effective threshold is doubled to keep a solo user's calibration the
# same as every other namespace - see the plan for the worked math.
CONTENT_NAMESPACE = "content"
CONTENT_THRESHOLD_MULTIPLIER = 2


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


# --- Content-action namespace: combined user+IP score ---
#
# Every action heats BOTH a per-user and a per-IP accumulator under the
# same namespace. A solo user acting from a stable IP feeds both
# identically (so their combined score is ~2x a single meter's value) -
# CONTENT_THRESHOLD_MULTIPLIER compensates so a lone user's calibration
# matches every other namespace's. Many low-volume accounts sharing one
# IP each keep their own user-score low, but all drive the *same*
# ip-score up, so the combined sum still trips even though no single
# account looks suspicious alone.

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
    times the heat of a plain post, instead of registering as just one
    action regardless of how much it actually did."""

    if not r:
        return

    now = time.time()
    heat = _config(CONTENT_NAMESPACE, "HEAT_PER_ACTION") * weight
    threshold = (BOILING_POINT * CONTENT_THRESHOLD_MULTIPLIER) / _get_sensitivity()

    user_key = _boil_key(CONTENT_NAMESPACE, _content_user_key(uid))
    ip_key = _boil_key(CONTENT_NAMESPACE, _content_ip_key(ip))

    new_user_boil = _read_decaying(user_key, BOIL_HALF_LIFE, now) + heat
    new_ip_boil = _read_decaying(ip_key, BOIL_HALF_LIFE, now) + heat

    _write_decaying(user_key, new_user_boil, now, ttl=int(BOIL_HALF_LIFE * 10))
    _write_decaying(ip_key, new_ip_boil, now, ttl=int(BOIL_HALF_LIFE * 10))

    if new_user_boil + new_ip_boil >= threshold:
        # Whichever identity (account or IP) contributed more heat takes
        # the escalating gear penalty and gets its cooldown flag set.
        # content_cooldown_remaining() checks both the user's and the
        # IP's cooldown flags (taking the max), so setting it on just the
        # culprit is enough to block every account sharing that IP too
        # when the IP is the culprit, without needing to duplicate the
        # flag onto the other key.
        culprit_key = (
            _content_user_key(uid) if new_user_boil >= new_ip_boil
            else _content_ip_key(ip)
        )
        _trigger_cooldown(CONTENT_NAMESPACE, culprit_key, now)
        # _trigger_cooldown only clears the culprit's own boil key - the
        # pot boiling over has to cool BOTH sides back down together, or
        # the non-culprit side's leftover heat would carry over into the
        # next burst and flip which identity looks like the culprit next
        # time, silently bypassing that identity's own escalated gear.
        r.delete(_boil_key(CONTENT_NAMESPACE, _content_user_key(uid)))
        r.delete(_boil_key(CONTENT_NAMESPACE, _content_ip_key(ip)))


def get_display_state(uid, ip):
    """Live (non-cached) content-action throttle state for the UI meter."""

    remaining = content_cooldown_remaining(uid, ip)
    if remaining > 0:
        return {"in_cooldown": True, "cooldown_remaining": remaining, "boil_pct": 100, "remaining_actions": 0}

    now = time.time()
    threshold = (BOILING_POINT * CONTENT_THRESHOLD_MULTIPLIER) / _get_sensitivity()
    user_boil = _read_decaying(
        _boil_key(CONTENT_NAMESPACE, _content_user_key(uid)), BOIL_HALF_LIFE, now)
    ip_boil = _read_decaying(
        _boil_key(CONTENT_NAMESPACE, _content_ip_key(ip)), BOIL_HALF_LIFE, now)
    combined = user_boil + ip_boil
    pct = int(min(100, round(100 * combined / threshold)))

    # A solo user's own next action heats BOTH their user-key and their
    # ip-key by HEAT_PER_ACTION, raising the combined score by 2x that -
    # so that (not the single-meter HEAT_PER_ACTION) is the right divisor
    # here. Worst-case estimate (assumes no decay between actions), so it
    # never overpromises - real gaps between actions only buy more room
    # than this shows, never less.
    heat_per_action = _config(CONTENT_NAMESPACE, "HEAT_PER_ACTION")
    remaining_heat = max(0.0, threshold - combined)
    remaining_actions = int(remaining_heat // (2 * heat_per_action))

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
