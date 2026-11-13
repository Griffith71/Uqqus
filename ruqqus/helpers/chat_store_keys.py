"""The keys that lock a browser's chat crypto store (chat plan, phase 2, build step 3; pure, stdlib only).

Each chat device the site made for an account (`chat_site_devices`, helpers/chat_tokens.py) has its own 32-byte store key:
HMAC-SHA256 of the Matrix id and the device id under a sub-key of the site's secret. It is answered by /api/chat/token and
/api/chat/store_keys only for this account's own site devices. Until build step 4 no browser uses it; from step 4 the browser keeps it
in memory only and opens its store with it, so another account, or anyone holding a copy of the store without a signed-in session,
can't.

- The secret is CHAT_STORE_SECRET when set, else SECRET_KEY (MASTER_KEY): `secret_of`. Setting CHAT_STORE_SECRET once, before
  launch, lets the cookie secret be rotated without making every browser's store unreadable (RESTORE.md "Rotating the chat store
  secret"). Rotating whichever one is in use changes every key and `key_id`; from build step 4 each browser then starts a new device.
- The raw secret is never used as a key: everything is under `_root`, an HMAC of a fixed label (domain separation).
- Every input is length-prefixed, so ("ab", "c") and ("a", "bc") can never give one key.
- The device id is the site's own choice (`new_device_id`: "UQ" and 10 base32 letters, never one of Synapse's 10-letter ids), so
  the key can be worked out before the chat server is asked to make the device.
- `waiting_key` is account-wide: first messages still waiting to be sent survive a new device.
- tests/data/chat_store_key_vectors.json pins the derivation (made with node's crypto, apart from this file); tests/test_chat_store_keys.py.
  The browser's half of that test (each key decodes to 32 bytes and seals and opens a token) needs src/seal.js, so it comes with build
  step 4.
"""
import base64
import hashlib
import hmac
import re
import secrets
import struct

DEVICE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")         # always fullmatch: `$` would let a trailing newline through
SITE_DEVICE = re.compile(r"UQ[A-Z2-7]{10}")             # what the site makes; Synapse's own ids are 10 letters [A-Z]
_ROOT = b"uqqus/chat-store/v1"


def secret_of(config) -> str:
    """CHAT_STORE_SECRET when set, else SECRET_KEY (== MASTER_KEY). Neither: ValueError (no key is ever made from nothing)."""
    secret = config.get("CHAT_STORE_SECRET") or config.get("SECRET_KEY") or ""
    if not secret:
        raise ValueError("no secret")
    return secret


def valid_device(device_id) -> bool:
    return isinstance(device_id, str) and DEVICE_ID.fullmatch(device_id) is not None


def is_site_made(device_id) -> bool:
    return isinstance(device_id, str) and SITE_DEVICE.fullmatch(device_id) is not None


def _root(secret: str) -> bytes:
    if not secret:
        raise ValueError("no secret")
    return hmac.new(secret.encode("utf-8"), _ROOT, hashlib.sha256).digest()


def _lp(s: str) -> bytes:
    b = s.encode("utf-8")
    return struct.pack(">H", len(b)) + b


def _mxid(mxid) -> str:
    if not isinstance(mxid, str) or not mxid.startswith("@") or len(mxid.encode("utf-8")) > 255:
        raise ValueError("mxid")
    return mxid


def store_key(secret: str, mxid: str, device_id: str) -> bytes:
    """The store key of one device: exactly 32 bytes."""
    if not valid_device(device_id):
        raise ValueError("device")
    return hmac.new(_root(secret), b"store\x00" + _lp(_mxid(mxid)) + _lp(device_id), hashlib.sha256).digest()


def waiting_key(secret: str, mxid: str) -> bytes:
    """The account's key for first messages still waiting to be sent (every device of the account has the same one)."""
    return hmac.new(_root(secret), b"waiting\x00" + _lp(_mxid(mxid)), hashlib.sha256).digest()


def key_id(secret: str) -> str:
    """Names the secret, never a key: changes only when the secret is rotated. From build step 4 the browser tags each store with
    it, and a store tagged with an older one is never opened again (ensureSession replaces the device)."""
    return hmac.new(_root(secret), b"kid", hashlib.sha256).hexdigest()[:12]


def new_device_id() -> str:
    """"UQ" + 10 base32 letters: never one of Synapse's own 10-letter ids."""
    return "UQ" + base64.b32encode(secrets.token_bytes(10)).decode("ascii")[:10]


def b64(key: bytes) -> str:
    """43 characters, base64url, no padding."""
    return base64.urlsafe_b64encode(key).rstrip(b"=").decode("ascii")
