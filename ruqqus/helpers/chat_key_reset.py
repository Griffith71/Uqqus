"""Starting chat over: the site replaces a member's cross-signing keys on the chat server for them (the owner, 2026-11-12).

Synapse wants a sign-in step before it replaces cross-signing keys that already exist (`/keys/device_signing/upload`), and
the site's chat accounts have no password there, so a member who lost their key could never start over. Synapse skips
that step for a request made with the appservice's own token (`SigningKeyUploadServlet`: `requester.app_service_id`), so the
site makes it, as the member, once the member has proved who they are here: their password, and their two-factor code when
they have one. That proof buys a GRANT, one random string kept in Redis for five minutes by its hash, good once, for this
account only, and only while its credentials are as they were (`login_nonce`); the browser then sends the upload's body with
it and the site passes it on. Rules (pure) here; the routes are routes/chat_key_reset_pages.py.

What passes through is public: three public keys and their signatures. `check_upload` keeps it to exactly that shape, for
this account's own Matrix id, so a grant can never be spent on anything else."""
import hashlib
import json
import re
import secrets

GRANT_SECONDS = 300
MAX_BODY = 8192
KINDS = ("master_key", "self_signing_key", "user_signing_key")
USAGE = {"master_key": "master", "self_signing_key": "self_signing", "user_signing_key": "user_signing"}

# unpadded standard base64 of a 32-byte ed25519 key (43 characters), and of a 64-byte signature (86)
_KEY = re.compile(r"[A-Za-z0-9+/]{43}\Z")
_SIG = re.compile(r"[A-Za-z0-9+/]{86}\Z")
_SIG_NAME = re.compile(r"ed25519:[A-Za-z0-9+/_\-.=]{1,64}\Z")


def new_grant():
    """A grant the browser keeps for one upload: 32 random bytes, base64url."""
    return secrets.token_urlsafe(32)


def redis_key(grant):
    """Where a grant is kept: by its hash, never as it is."""
    return "chat:kr:" + hashlib.sha256(str(grant).encode("utf-8")).hexdigest()


def sign_in_of(session_id):
    """Which sign-in a grant belongs to: a hash of the cookie's session id (never the id), "" without one."""
    return hashlib.sha256(str(session_id).encode("utf-8")).hexdigest() if session_id else ""


def grant_record(user_id, nonce, now, sign_in=""):
    return json.dumps({"uid": int(user_id), "nonce": int(nonce or 0), "t": int(now), "sid": sign_in or ""})


def seconds_left(raw, now):
    """What is left of a grant's five minutes (0 when it can't be read)."""
    try:
        return max(0, GRANT_SECONDS - (int(now) - int(json.loads(raw)["t"])))
    except (ValueError, TypeError, KeyError):
        return 0


def grant_refusal(raw, user_id, nonce, now, sign_in=""):
    """None when a grant taken from Redis is good for this account and this sign-in now, else a reason (for tests and logs
    only: the member is told one sentence whatever the reason). A grant is the sign-in's that asked for it: copied to
    another sign-in of the same account, it is refused."""
    if not raw:
        return "gone"
    try:
        record = json.loads(raw)
        uid, kept_nonce, made = int(record["uid"]), int(record["nonce"]), int(record["t"])
    except (ValueError, TypeError, KeyError):
        return "gone"
    if uid != int(user_id):
        return "another account"
    if kept_nonce != int(nonce or 0):
        return "credentials changed"
    if record.get("sid", "") != (sign_in or ""):
        return "another sign-in"
    if now - made > GRANT_SECONDS:
        return "too old"
    return None


def _signatures(value, mxid):
    if not isinstance(value, dict) or not value:
        return False
    for signer, sigs in value.items():
        if not isinstance(signer, str) or not signer.startswith("@") or len(signer) > 255:
            return False
        if not isinstance(sigs, dict) or not sigs or len(sigs) > 4:
            return False
        for name, sig in sigs.items():
            if not isinstance(name, str) or not _SIG_NAME.match(name) or not isinstance(sig, str) or not _SIG.match(sig):
                return False
    return True


def _one_key(kind, value, mxid):
    if not isinstance(value, dict):
        return False
    if set(value) - {"user_id", "usage", "keys", "signatures"} or not {"user_id", "usage", "keys"} <= set(value):
        return False
    if value["user_id"] != mxid or value["usage"] != [USAGE[kind]]:
        return False
    keys = value["keys"]
    if not isinstance(keys, dict) or len(keys) != 1:
        return False
    (name, key), = keys.items()
    if not isinstance(name, str) or not name.startswith("ed25519:") or not isinstance(key, str) or not _KEY.match(key):
        return False
    if name != "ed25519:" + key:
        return False                                        # a cross-signing key is named by itself
    if "signatures" in value and not _signatures(value["signatures"], mxid):
        return False
    if kind != "master_key" and "signatures" not in value:
        return False                                        # the other two are signed by the master key
    return True


def check_upload(raw, mxid):
    """The upload's body as a dict when it is exactly a cross-signing key upload for `mxid`, else None. `raw` is the
    request's text (bytes or str). Only the three key kinds, at least one, each as the spec shapes it, no `auth`."""
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        if len(raw) > MAX_BODY:
            return None
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_BODY:
        return None
    try:
        body = json.loads(raw)
    except (ValueError, RecursionError):                    # deeply nested text inside 8 KB
        return None
    if not isinstance(body, dict) or not body or set(body) - set(KINDS):
        return None
    if not all(_one_key(kind, value, mxid) for kind, value in body.items()):
        return None
    return body
