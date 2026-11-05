"""Confirming an age with an age-check service, called a vendor here (the owner, 2026-11-05). Where the law asks for it (the UK, the
EU/EEA and Australia: helpers/age_declaration.py `is_strict`), only a vendor's check lifts the child lock; elsewhere it is one way of
doing it, ticking "18 or over" being the other. **No vendor is chosen or paid for yet**: with `AGE_ASSURANCE_PROVIDER` unset
`configured()` is False, Settings says confirming isn't available yet, and the two routes are a 404. The site stores only that a check
happened and when (`users.age_assured_utc`), or that it found the holder under 18 (`users.age_under_utc`), never a birth date, an ID or a
selfie: those stay with the vendor, which the privacy page names once one is set up.

A provider is a small class with two methods and a name; the routes (routes/age_declaration_pages.py) do the rest, so adding one is a
class here and an entry in `PROVIDERS`:

    start(user, callback_url, state)  -> the address the browser is sent to (the vendor's page)
    finish(user, args)                -> one of the statuses below, from what the vendor sent back to `callback_url`

`state` is one-time and bound to the account that started it (Redis `age:state:<nonce>`, 30 minutes): a callback that doesn't carry the
signed-in account's own is refused, so nobody can finish another person's check or lift their own lock with someone else's result.
`dev` is a stand-in for local testing and tests, on only with AGE_ASSURANCE_DEV=1 (never on a live site), like the media stand-in.
tests/test_age_assurance.py."""
import os
import secrets

ADULT = "adult"                       # 18 or over
MINOR = "minor"                       # checked and under 18: the account stays locked
UNDER_MINIMUM = "under_minimum"       # under the youngest age allowed where they are (Australia: 16): the account is closed
FAILED = "failed"                     # no answer (cancelled, couldn't read the document): nothing changes, they can try again
STATUSES = (ADULT, MINOR, UNDER_MINIMUM, FAILED)

STATE_TTL = 30 * 60


class Provider:
    name = ""

    def start(self, user, callback_url, state):
        raise NotImplementedError

    def finish(self, user, args):
        raise NotImplementedError


class DevProvider(Provider):
    """Answers at once with whatever `AGE_ASSURANCE_DEV_RESULT` says (default adult), so a test or a developer can walk the flow."""
    name = "dev"

    def start(self, user, callback_url, state):
        return f"{callback_url}?state={state}&result={os.environ.get('AGE_ASSURANCE_DEV_RESULT', ADULT)}"

    def finish(self, user, args):
        result = args.get("result")
        return result if result in STATUSES else FAILED


def _providers():
    found = {}
    if os.environ.get("AGE_ASSURANCE_DEV") == "1":
        found["dev"] = DevProvider()
    return found


def provider():
    """The configured provider, or None."""
    return _providers().get((os.environ.get("AGE_ASSURANCE_PROVIDER") or "").strip().lower())


def configured():
    return provider() is not None


def new_state(r, user_id):
    """A one-time state for the account that is starting a check."""
    state = secrets.token_urlsafe(24)
    r.set(f"age:state:{state}", str(user_id), ex=STATE_TTL)
    return state


def take_state(r, state, user_id):
    """True once, for the account that started it."""
    if not isinstance(state, str) or not (8 <= len(state) <= 64):
        return False
    key = f"age:state:{state}"
    owner = r.get(key)
    if owner is None:
        return False
    if isinstance(owner, bytes):
        owner = owner.decode()
    if owner != str(user_id):
        return False
    return bool(r.delete(key))
