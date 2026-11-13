"""Web Push without a third party's library (RFC 8030 delivery, RFC 8291 message encryption, RFC 8292 VAPID), on the
`cryptography` package the site already uses. Pure apart from `deliver`, which makes the one HTTP request.

A browser that allows notifications gives the site a push subscription: an `endpoint` on its maker's push service (Google's,
Mozilla's, Apple's, Microsoft's) and two keys. A message to it is encrypted for that browser alone (`encrypt`: the push
service can't read it), signed with the site's own key pair (`VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`: `vapid_headers`), and
POSTed to the endpoint. Only an endpoint on a known push service is ever accepted (`endpoint_ok`), so a stored
subscription can't make the site send a request anywhere else.

What a notification says is never a message's words (helpers/push_rules.py decides what it says).
"""
import base64
import json
import os
import struct
import time
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

RECORD_SIZE = 4096
MAX_PAYLOAD = 3000               # well under the 4 KB a push service takes, after encryption
TTL = 60 * 60 * 24               # a notification nobody's browser was there for waits a day, then goes

# the push services browsers use: a subscription's endpoint must be https on one of these (nothing else is ever fetched)
PUSH_HOSTS = ("fcm.googleapis.com", "updates.push.services.mozilla.com", "web.push.apple.com", "push.apple.com")
PUSH_SUFFIXES = (".push.apple.com", ".notify.windows.com", ".push.services.mozilla.com")


def b64u(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(text):
    text = str(text or "").strip()
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def endpoint_ok(endpoint):
    """An https address on a known push service, with no credentials or odd port: the only kind ever stored or POSTed to."""
    if not isinstance(endpoint, str) or len(endpoint) > 1024:
        return False
    try:
        parts = urlsplit(endpoint)
        port = parts.port
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or parts.username or parts.password or port not in (None, 443) or not parts.path:
        return False
    return host in PUSH_HOSTS or host.endswith(PUSH_SUFFIXES)


def keys_ok(p256dh, auth):
    """The browser's public key (65 bytes, uncompressed P-256) and its 16-byte secret, as it sends them."""
    try:
        public, secret = unb64u(p256dh), unb64u(auth)
    except (ValueError, TypeError):
        return False
    if len(public) != 65 or public[0] != 4 or len(secret) != 16:
        return False
    try:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), public)
    except ValueError:
        return False
    return True


def _hkdf(salt, ikm, info, length):
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def encrypt(payload, p256dh, auth, salt=None, private_key=None):
    """`payload` (bytes) encrypted for one browser (RFC 8291, aes128gcm): the body to POST. `salt` and `private_key` are
    for the tests; every message has its own."""
    ua_public = unb64u(p256dh)
    auth_secret = unb64u(auth)
    salt = salt or os.urandom(16)
    private_key = private_key or ec.generate_private_key(ec.SECP256R1())
    as_public = private_key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    shared = private_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public))
    ikm = _hkdf(auth_secret, shared, b"WebPush: info\x00" + ua_public + as_public, 32)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)      # one record: the last, padded with nothing
    header = salt + struct.pack("!I", RECORD_SIZE) + bytes([len(as_public)]) + as_public
    return header + ciphertext


def decrypt(body, ua_private, auth):
    """The other side of `encrypt`, for the tests (a browser does this)."""
    salt, idlen = body[:16], body[20]
    as_public = body[21:21 + idlen]
    ua_public = ua_private.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    shared = ua_private.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public))
    ikm = _hkdf(unb64u(auth), shared, b"WebPush: info\x00" + ua_public + as_public, 32)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    plain = AESGCM(cek).decrypt(nonce, body[21 + idlen:], None)
    return plain.rstrip(b"\x00")[:-1]


# --- the site's own key pair (VAPID) -----------------------------------------------------------------------------------

def new_keys():
    """A key pair for VAPID_PRIVATE_KEY / VAPID_PUBLIC_KEY (scripts/gen_dev_env.py): the private one as base64url of its
    32-byte number, the public one as base64url of the uncompressed point (what a browser's subscribe asks for)."""
    key = ec.generate_private_key(ec.SECP256R1())
    number = key.private_numbers().private_value.to_bytes(32, "big")
    public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return b64u(number), b64u(public)


def private_key_from(text):
    value = int.from_bytes(unb64u(text), "big")
    return ec.derive_private_key(value, ec.SECP256R1())


def configured(environ=os.environ):
    """The site's key pair, or None when push is off (either key missing or unreadable)."""
    private, public = (environ.get("VAPID_PRIVATE_KEY") or "").strip(), (environ.get("VAPID_PUBLIC_KEY") or "").strip()
    if not private or not public:
        return None
    try:
        key = private_key_from(private)
    except (ValueError, TypeError):
        return None
    mine = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return (key, public) if b64u(mine) == public else None


def vapid_headers(endpoint, key, public, subject, now=None):
    """The Authorization header that says this site sent it (a JWT signed with ES256, valid 12 hours, for the endpoint's
    push service only)."""
    parts = urlsplit(endpoint)
    claims = {"aud": f"{parts.scheme}://{parts.netloc}", "exp": int(now or time.time()) + 12 * 3600, "sub": subject}
    head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    body = b64u(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{head}.{body}".encode()
    r, s = decode_dss_signature(key.sign(signing_input, ec.ECDSA(hashes.SHA256())))
    token = f"{head}.{body}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"
    return {"Authorization": f"vapid t={token}, k={public}"}


def request_for(subscription, payload, key, public, subject, urgency="normal"):
    """(url, headers, body) of one push: the payload (a dict, as JSON) encrypted for that browser and signed by the site."""
    data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()[:MAX_PAYLOAD]
    body = encrypt(data, subscription["p256dh"], subscription["auth"])
    headers = {"Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream", "TTL": str(TTL),
               "Urgency": urgency, **vapid_headers(subscription["endpoint"], key, public, subject)}
    return subscription["endpoint"], headers, body


GONE = "gone"            # the push service says the subscription no longer exists: forget it
FAILED = "failed"        # anything else: tried again with the next message
SENT = "sent"


def deliver(subscription, payload, key, public, subject, urgency="normal", post=None):
    """POST one push. Answers SENT, GONE (404 or 410: the browser unsubscribed) or FAILED. Never raises."""
    if not endpoint_ok(subscription.get("endpoint")):
        return GONE
    if post is None:
        import requests
        post = requests.post
    try:
        url, headers, body = request_for(subscription, payload, key, public, subject, urgency)
        answer = post(url, data=body, headers=headers, timeout=10, allow_redirects=False)
    except Exception:
        return FAILED
    if answer.status_code in (404, 410):
        return GONE
    return SENT if 200 <= answer.status_code < 300 else FAILED
