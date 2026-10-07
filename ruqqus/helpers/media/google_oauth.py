"""Linking a member's Google account (the Drive and YouTube providers use it).

The member approves once on Google's own page; Google gives the server a refresh token,
which is the member's standing permission. It is stored encrypted on the account row and
never sent to a browser. Short-lived access tokens are made from it when needed.

Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET (a "Web application" OAuth client in a
Google Cloud project, with <site>/settings/media/google/callback as its redirect).

Scopes:
* drive.file: only files this site created in the member's Drive. Not a sensitive scope.
* youtube.upload: upload videos to the member's channel. Sensitive: Google must verify
  the app before the public can grant it. Asked for only when a member first uploads a
  video, so someone who only posts pictures never sees it.
"""
import base64
import json
import time
from os import environ
from urllib.parse import urlencode

import requests

from .base import AccountLost, ProviderDown

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"

SCOPE_DRIVE = "https://www.googleapis.com/auth/drive.file"
SCOPE_YOUTUBE = "https://www.googleapis.com/auth/youtube.upload"
# what is asked for, by what the member wants to do
WANTS = {
    "storage": ("openid", SCOPE_DRIVE),
    "video": ("openid", SCOPE_DRIVE, SCOPE_YOUTUBE),
}
STATE_SECONDS = 600
TIMEOUT = 20

_local_tokens = {}      # used when there is no Redis (tests)


def client():
    return environ.get("GOOGLE_CLIENT_ID", "").strip(), environ.get("GOOGLE_CLIENT_SECRET", "").strip()


def configured():
    return all(client())


def _http(method, url, **kwargs):
    """Every request to Google goes through here (tests replace it)."""
    kwargs.setdefault("timeout", TIMEOUT)
    try:
        return requests.request(method, url, **kwargs)
    except requests.RequestException as e:
        raise ProviderDown(str(e))


# --- the link ------------------------------------------------------------------------

def make_state(user_id, want, now, sign):
    """A value Google hands back unchanged: proves the answer belongs to this member's own
    request, made in the last few minutes. `sign` is helpers.security.generate_hash."""
    return f"{now}.{want}.{sign(f'{now}+{user_id}+google+{want}')}"


def read_state(state, user_id, now, verify):
    """The `want` of a state made by make_state for this member, or None."""
    parts = (state or "").split(".")
    if len(parts) != 3 or not parts[0].isdigit() or parts[1] not in WANTS:
        return None
    made = int(parts[0])
    if made > now + 60 or made < now - STATE_SECONDS:
        return None
    return parts[1] if verify(f"{made}+{user_id}+google+{parts[1]}", parts[2]) else None


def authorize_url(redirect_uri, state, want):
    """Where to send the member. They can pick which Google account to use, or make a new one."""
    client_id, _ = client()
    return AUTH_URL + "?" + urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(WANTS[want]),
        "access_type": "offline",               # a refresh token, so uploads work later without asking again
        "include_granted_scopes": "true",       # keep what was approved before when adding video
        "prompt": "consent select_account",
        "state": state,
    })


def subject(id_token):
    """Google's id for the account, from the id token that came straight from Google's own
    token endpoint over TLS (so its signature is not checked again here)."""
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return str(json.loads(base64.urlsafe_b64decode(payload))["sub"])
    except (AttributeError, IndexError, KeyError, ValueError):
        return ""


def exchange(code, redirect_uri):
    """Turn the code Google sent back into the member's permission.
    Returns {"refresh_token", "scopes" (list), "sub"}; raises ValueError with a message."""
    client_id, secret = client()
    r = _http("POST", TOKEN_URL, data={"code": code, "client_id": client_id, "client_secret": secret,
                                       "redirect_uri": redirect_uri, "grant_type": "authorization_code"})
    data = _json(r)
    if r.status_code != 200 or not data.get("access_token"):
        raise ValueError("Google did not accept the connection. Try again.")
    if not data.get("refresh_token"):
        raise ValueError("Google did not give lasting access. Remove this site under your Google "
                         "account's third-party access, then connect again.")
    sub = subject(data.get("id_token", ""))
    if not sub:
        raise ValueError("Google did not say which account this is. Try again.")
    return {"refresh_token": data["refresh_token"], "scopes": sorted((data.get("scope") or "").split()), "sub": sub}


def _json(response):
    try:
        return response.json() or {}
    except ValueError:
        return {}


# --- access tokens -------------------------------------------------------------------

def _redis():
    try:
        from ruqqus.__main__ import r
        return r
    except Exception:
        return None


def _cached(key):
    store = _redis()
    if store is not None:
        try:
            value = store.get(key)
            return value.decode() if isinstance(value, bytes) else value
        except Exception:
            pass
    value, until = _local_tokens.get(key, (None, 0))
    return value if until > time.time() else None


def _cache(key, value, seconds):
    store = _redis()
    if store is not None:
        try:
            store.setex(key, seconds, value)
            return
        except Exception:
            pass
    _local_tokens[key] = (value, time.time() + seconds)


def remember(key, value, seconds):
    """Keep a small value for a while (an upload session's address)."""
    _cache(key, value, seconds)


def recall(key):
    return _cached(key)


def forget_key(key):
    _local_tokens.pop(key, None)
    store = _redis()
    if store is not None:
        try:
            store.delete(key)
        except Exception:
            pass


def forget(account):
    key = f"media:google:access:{account.id}"
    _local_tokens.pop(key, None)
    store = _redis()
    if store is not None:
        try:
            store.delete(key)
        except Exception:
            pass


def access_token(account, decrypt=None):
    """A valid access token for the account. AccountLost when the member took the access
    away at Google (or it expired): they have to connect again."""
    key = f"media:google:access:{account.id}"
    token = _cached(key)
    if token:
        return token
    if not account.refresh_token_encrypted:
        raise AccountLost()
    if decrypt is None:
        from ruqqus.helpers.secret_box import decrypt_secret as decrypt
    client_id, secret = client()
    r = _http("POST", TOKEN_URL, data={"client_id": client_id, "client_secret": secret, "grant_type": "refresh_token",
                                       "refresh_token": decrypt(account.refresh_token_encrypted)})
    data = _json(r)
    if r.status_code == 200 and data.get("access_token"):
        _cache(key, data["access_token"], max(int(data.get("expires_in", 3600)) - 120, 60))
        return data["access_token"]
    if r.status_code in (400, 401) and data.get("error") in ("invalid_grant", "unauthorized_client", "invalid_client"):
        raise AccountLost()
    raise ProviderDown(f"token endpoint answered {r.status_code}")


def api(account, method, url, **kwargs):
    """A request to a Google API as the member. A token Google no longer accepts is
    replaced once; after that the account counts as lost."""
    headers = dict(kwargs.pop("headers", None) or {})
    for attempt in (1, 2):
        headers["Authorization"] = f"Bearer {access_token(account)}"
        response = _http(method, url, headers=headers, **kwargs)
        if response.status_code != 401 or not _token_refused(response):
            return response
        forget(account)
    raise AccountLost()


# 401 answers that are about the account, not about the token: the caller explains them
_NOT_THE_TOKEN = {"youtubeSignupRequired"}


def _token_refused(response):
    error = _json(response).get("error")
    reasons = {e.get("reason") for e in (error.get("errors") or []) if isinstance(e, dict)} if isinstance(error, dict) else set()
    return not (reasons & _NOT_THE_TOKEN)


def revoke(refresh_token):
    """Give the permission back at Google. Best effort: the token is forgotten here either way."""
    try:
        _http("POST", REVOKE_URL, data={"token": refresh_token}, headers={"Content-Type": "application/x-www-form-urlencoded"})
    except ProviderDown:
        pass
