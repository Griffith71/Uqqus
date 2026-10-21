"""The site as an OpenID Connect provider for exactly one client, its own chat server (Synapse), so a member signs in
to an outside Matrix app (Element Web and Desktop, FluffyChat, Cinny...) with "Sign in with <site>" and lands on the
Matrix account the site already made for them (the owner's choice, 2026-10-21: not Element X, which needs Matrix
Authentication Service and would break the site's own chat sign-in). The routes are routes/oidc_provider.py.

Pure: no Flask, no database. Codes and access tokens live only in Redis and are kept here by their SHA-256.
"""
import base64
import hashlib
import hmac
import json
import re

CODE_SECONDS = 60             # an authorization code is used within a minute, once
TOKEN_SECONDS = 300           # the access token only reads /oidc/userinfo, right after the code is swapped
ID_TOKEN_SECONDS = 300
SCOPES = ("openid",)
_VERIFIER = re.compile(r"^[A-Za-z0-9\-._~]{43,128}$")
_CHALLENGE = re.compile(r"^[A-Za-z0-9\-_]{43}$")
_STATE = re.compile(r"^[\x21-\x7e]{1,512}$")       # printable ASCII, no spaces: it goes back in the address

NOT_SET_UP = "Open Chat on the site once first, so your chats and their keys are ready."
REFUSED = "This account can't use chat apps."


def b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def digest(secret):
    """What Redis keeps for a code or a token: never the value itself."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def issuer(server_name, https):
    return f"{'https' if https else 'http'}://{server_name}"


def redirect_uri(matrix_public_url):
    """The one place a code is ever sent: Synapse's own OIDC callback."""
    return matrix_public_url.rstrip("/") + "/_synapse/client/oidc/callback"


def origin(url):
    """scheme://host[:port] of an address, for a Content-Security-Policy source."""
    from urllib.parse import urlsplit
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def configured(client_id, client_secret, private_key_pem):
    return bool(client_id and client_secret and private_key_pem)


def authorize_refusal(args, client_id, allowed_redirect):
    """Why an authorization request can't go on, or None. Anything wrong with the client or the redirect address is
    shown on the site and never sent anywhere (an unchecked redirect would hand codes to a stranger)."""
    if args.get("client_id") != client_id:
        return "unknown_client"
    if args.get("redirect_uri") != allowed_redirect:
        return "bad_redirect"
    if args.get("response_type") != "code":
        return "unsupported_response_type"
    if "openid" not in (args.get("scope") or "").split():
        return "invalid_scope"
    if not _STATE.match(args.get("state") or ""):
        return "invalid_state"
    if args.get("code_challenge_method") != "S256" or not _CHALLENGE.match(args.get("code_challenge") or ""):
        return "pkce_required"
    nonce = args.get("nonce")
    if nonce is not None and not _STATE.match(nonce):
        return "invalid_nonce"
    return None


def account_refusal(user, chat_ready):
    """Whether this account may sign in to a chat app: never a bot, a suspended or deleted account, and only once the
    member's chat keys exist (an app made first would make its own keys, and the site's chat would no longer open)."""
    if user is None or getattr(user, "is_bot", False) or getattr(user, "is_deleted", False) \
            or getattr(user, "is_suspended", False):
        return REFUSED
    if not chat_ready:
        return NOT_SET_UP
    return None


def pkce_ok(verifier, challenge):
    if not verifier or not _VERIFIER.match(verifier) or not challenge:
        return False
    return hmac.compare_digest(b64url(hashlib.sha256(verifier.encode("ascii")).digest()), challenge)


def client_ok(given_id, given_secret, client_id, client_secret):
    if not given_id or not given_secret:
        return False
    return hmac.compare_digest(given_id, client_id) and hmac.compare_digest(given_secret, client_secret)


def basic_auth(header):
    """client_id and client_secret from an HTTP Basic header (RFC 6749 2.3.1: each form-encoded), or (None, None)."""
    from urllib.parse import unquote_plus
    if not header or not header.startswith("Basic "):
        return None, None
    try:
        raw = base64.b64decode(header[6:], validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None, None
    if ":" not in raw:
        return None, None
    user, _, password = raw.partition(":")
    return unquote_plus(user), unquote_plus(password)


def code_record(user_id, nonce, challenge, now):
    return json.dumps({"user_id": int(user_id), "nonce": nonce, "challenge": challenge, "auth_time": int(now)})


def id_claims(iss, client_id, user_id, nonce, auth_time, now):
    claims = {"iss": iss, "sub": str(int(user_id)), "aud": client_id, "iat": int(now), "exp": int(now) + ID_TOKEN_SECONDS,
              "auth_time": int(auth_time)}
    if nonce:
        claims["nonce"] = nonce
    return claims


def userinfo(user_id):
    """Only the account's number: Synapse makes the Matrix id from it (ruqqus_<id>) and needs nothing else. No name,
    no e-mail: an outside app learns nothing about the member from signing in."""
    return {"sub": str(int(user_id))}


def discovery(iss):
    return {
        "issuer": iss,
        "authorization_endpoint": iss + "/oidc/authorize",
        "token_endpoint": iss + "/oidc/token",
        "userinfo_endpoint": iss + "/oidc/userinfo",
        "jwks_uri": iss + "/oidc/jwks",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "scopes_supported": list(SCOPES),
        "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
        "code_challenge_methods_supported": ["S256"],
        "claims_supported": ["sub", "iss", "aud", "exp", "iat", "auth_time", "nonce"],
    }


# ---- signing (RS256 with the `cryptography` package the site already uses) ----

def load_key(pem):
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    return load_pem_private_key(pem.encode("ascii") if isinstance(pem, str) else pem, password=None)


def key_from_env(value):
    """OIDC_PRIVATE_KEY holds the PEM base64-encoded on one line (an .env file can't hold its line breaks)."""
    if not value:
        return None
    text = value.strip()
    if not text.startswith("-----BEGIN"):
        text = base64.b64decode(text).decode("ascii")
    return text


def _int_b64(n):
    return b64url(n.to_bytes((n.bit_length() + 7) // 8, "big"))


def key_id(private_key):
    numbers = private_key.public_key().public_numbers()
    return hashlib.sha256(f"{numbers.n}:{numbers.e}".encode("ascii")).hexdigest()[:16]


def jwks(private_key):
    numbers = private_key.public_key().public_numbers()
    return {"keys": [{"kty": "RSA", "use": "sig", "alg": "RS256", "kid": key_id(private_key),
                      "n": _int_b64(numbers.n), "e": _int_b64(numbers.e)}]}


def sign(claims, private_key):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    header = {"alg": "RS256", "typ": "JWT", "kid": key_id(private_key)}
    signing_input = (b64url(json.dumps(header, separators=(",", ":")).encode()) + "."
                     + b64url(json.dumps(claims, separators=(",", ":")).encode()))
    signature = private_key.sign(signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    return signing_input + "." + b64url(signature)
