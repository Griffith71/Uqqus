"""Authenticator codes (RFC 6238), accepted once.

`pyotp`'s `verify` says only whether a code is right. A code stays right for the step it was made in and one step
either side (about a minute and a half), so someone who saw it over a shoulder, or a proxy that logged it, could use it
again in that time. `matched_step` says WHICH step matched and `claim` remembers it: the second use of the same step is
refused (RFC 6238 section 5.2). The memory is a Redis key that expires after the code could no longer be valid.
"""
import hmac
import time

import pyotp

STEP_SECONDS = 30
WINDOW = 1                      # accept the step before and after, as `valid_window=1` did
REMEMBER_SECONDS = (2 * WINDOW + 1) * STEP_SECONDS + 30


def matched_step(secret, token, now=None):
    """The step number (time // 30) whose code equals `token`, or None. Constant-time comparison."""
    token = "".join(str(token or "").split())
    if not token.isdigit() or len(token) != 6:
        return None
    now = time.time() if now is None else now
    totp = pyotp.TOTP(secret)
    found = None
    for offset in range(-WINDOW, WINDOW + 1):
        step = int(now // STEP_SECONDS) + offset
        if hmac.compare_digest(totp.at(step * STEP_SECONDS), token):
            found = step
    return found


def claim(redis, user_id, step):
    """True the first time this account uses this step. With no Redis there is nothing to remember, so it allows it."""
    if redis is None:
        return True
    try:
        return bool(redis.set(f"totp:{user_id}:{step}", 1, nx=True, ex=REMEMBER_SECONDS))
    except Exception:
        return True             # a Redis outage must not lock everyone out of their accounts
