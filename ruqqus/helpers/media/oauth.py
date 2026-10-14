"""Linking a member's account on a hosting service, for the services added after Google
(Microsoft for OneDrive, Dailymotion). One `Service` per site; the provider modules use it.

The member approves once on the service's own page; the service gives the server a refresh
token, the member's standing permission. It is stored encrypted on the account row
(helpers/secret_box.py) and never sent to a browser. Short-lived access tokens are made from
it when needed and kept in Redis for less than their lifetime.

Some services hand out a new refresh token each time one is used (Microsoft does). The new one
is written onto the account object and `account._rotated` is set, so the route that used it
commits the row.

google_oauth.py is the same idea written for Google alone, before this module existed; it is
left as it is because its tests replace its own functions.
"""
import base64
import json
import time
from os import environ
from urllib.parse import urlencode

import requests

from .base import AccountLost, ProviderDown

STATE_SECONDS = 600
TIMEOUT = 20

_local_tokens = {}      # used when there is no Redis (tests)


def _http(method, url, **kwargs):
    """Every request to a service goes through here (tests replace it)."""
    kwargs.setdefault("timeout", TIMEOUT)
    try:
        return requests.request(method, url, **kwargs)
    except requests.RequestException as e:
        raise ProviderDown(str(e))


def _json(response):
    try:
        return response.json() or {}
    except ValueError:
        return {}


def id_token_subject(id_token):
    """The account id from an id token that came straight from the service's own token endpoint
    over TLS (so its signature is not checked again here)."""
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return str(json.loads(base64.urlsafe_b64decode(payload))["sub"])
    except (AttributeError, IndexError, KeyError, ValueError):
        return ""


# --- small values kept for a while (Redis, or memory in tests) ----------------------------

def _redis():
    try:
        from ruqqus.__main__ import r
        return r
    except Exception:
        return None


def recall(key):
    store = _redis()
    if store is not None:
        try:
            value = store.get(key)
            return value.decode() if isinstance(value, bytes) else value
        except Exception:
            pass
    value, until = _local_tokens.get(key, (None, 0))
    return value if until > time.time() else None


def remember(key, value, seconds):
    store = _redis()
    if store is not None:
        try:
            store.setex(key, seconds, value)
            return
        except Exception:
            pass
    _local_tokens[key] = (value, time.time() + seconds)


def forget_key(key):
    _local_tokens.pop(key, None)
    store = _redis()
    if store is not None:
        try:
            store.delete(key)
        except Exception:
            pass


class Service:
    """One hosting service's OAuth 2 settings.

    `wants` maps what the member wants to do to the scopes asked for. `identity(token_data)`
    returns the service's id for the account (the access token is in `token_data`), so a
    reconnect can be told from a different account. `required` is the scope without which
    nothing works (the callback refuses a grant that left it out)."""

    def __init__(self, name, label, id_env, secret_env, auth_url, token_url, wants, required,
                 identity, auth_params=None, refresh_scope=None, revoke=None):
        self.name = name
        self.label = label
        self.id_env = id_env
        self.secret_env = secret_env
        self._auth_url = auth_url          # an address, or a function that makes it (a setting it depends on)
        self._token_url = token_url
        self.wants = wants
        self.required = required
        self.identity = identity
        self.auth_params = auth_params or {}
        self.refresh_scope = refresh_scope        # sent with a refresh when the service wants the scopes again
        self._revoke = revoke

    # --- configuration ------------------------------------------------------------------

    @property
    def auth_url(self):
        return self._auth_url() if callable(self._auth_url) else self._auth_url

    @property
    def token_url(self):
        return self._token_url() if callable(self._token_url) else self._token_url

    def client(self):
        return environ.get(self.id_env, "").strip(), environ.get(self.secret_env, "").strip()

    def configured(self):
        return all(self.client())

    # --- the link -----------------------------------------------------------------------

    def make_state(self, user_id, want, now, sign):
        """A value the service hands back unchanged: proves the answer belongs to this member's
        own request, made in the last few minutes. `sign` is helpers.security.generate_hash."""
        return f"{now}.{want}.{sign(f'{now}+{user_id}+{self.name}+{want}')}"

    def read_state(self, state, user_id, now, verify):
        """The `want` of a state made by make_state for this member and service, or None."""
        parts = (state or "").split(".")
        if len(parts) != 3 or not parts[0].isdigit() or parts[1] not in self.wants:
            return None
        made = int(parts[0])
        if made > now + 60 or made < now - STATE_SECONDS:
            return None
        return parts[1] if verify(f"{made}+{user_id}+{self.name}+{parts[1]}", parts[2]) else None

    def authorize_url(self, redirect_uri, state, want):
        client_id, _ = self.client()
        params = {"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code",
                  "scope": " ".join(self.wants[want]), "state": state}
        params.update(self.auth_params)
        return self.auth_url + "?" + urlencode(params)

    def has_scope(self, scopes, wanted):
        """Is `wanted` among the granted `scopes`? Services write a scope with or without their
        resource address in front, and not always in the same case."""
        wanted = wanted.lower()
        return any(s.lower() == wanted or s.lower().endswith("/" + wanted) for s in scopes)

    def exchange(self, code, redirect_uri, want=None):
        """Turn the code the service sent back into the member's permission.
        Returns {"refresh_token", "scopes" (list), "sub"}; raises ValueError with a message.
        A service that does not say which scopes it granted is taken to have granted what
        `want` asked for."""
        client_id, secret = self.client()
        r = _http("POST", self.token_url, data={"code": code, "client_id": client_id, "client_secret": secret,
                                                "redirect_uri": redirect_uri, "grant_type": "authorization_code"})
        data = _json(r)
        if r.status_code != 200 or not data.get("access_token"):
            raise ValueError(f"{self.label} did not accept the connection. Try again.")
        if not data.get("refresh_token"):
            raise ValueError(f"{self.label} did not give lasting access. Remove this site from your "
                             f"{self.label} account's connected apps, then connect again.")
        sub = self.identity(data)
        if not sub:
            raise ValueError(f"{self.label} did not say which account this is. Try again.")
        scopes = (data.get("scope") or (" ".join(self.wants[want]) if want in self.wants else "")).split()
        return {"refresh_token": data["refresh_token"], "scopes": sorted(scopes), "sub": sub,
                "access_token": data["access_token"], "expires_in": data.get("expires_in")}

    # --- access tokens ------------------------------------------------------------------

    def _key(self, account):
        return f"media:{self.name}:access:{account.id}"

    def forget(self, account):
        forget_key(self._key(account))

    def access_token(self, account, decrypt=None, encrypt=None):
        """A valid access token for the account. AccountLost when the member took the access
        away at the service (or it expired): they have to connect again."""
        token = recall(self._key(account))
        if token:
            return token
        if not account.refresh_token_encrypted:
            raise AccountLost()
        if decrypt is None:
            from ruqqus.helpers.secret_box import decrypt_secret as decrypt
        client_id, secret = self.client()
        current = decrypt(account.refresh_token_encrypted)
        body = {"client_id": client_id, "client_secret": secret, "grant_type": "refresh_token", "refresh_token": current}
        if self.refresh_scope:
            body["scope"] = self.refresh_scope
        r = _http("POST", self.token_url, data=body)
        data = _json(r)
        if r.status_code == 200 and data.get("access_token"):
            new_refresh = data.get("refresh_token")
            if new_refresh and new_refresh != current:
                if encrypt is None:
                    from ruqqus.helpers.secret_box import encrypt_secret as encrypt
                account.refresh_token_encrypted = encrypt(new_refresh)
                account._rotated = True          # the caller commits the row
            remember(self._key(account), data["access_token"], max(int(data.get("expires_in") or 3600) - 120, 60))
            return data["access_token"]
        if r.status_code in (400, 401) and data.get("error") in ("invalid_grant", "unauthorized_client", "invalid_client", "invalid_token"):
            raise AccountLost()
        raise ProviderDown(f"{self.name} token endpoint answered {r.status_code}")

    def api(self, account, method, url, **kwargs):
        """A request to the service's API as the member. A token the service no longer accepts is
        replaced once; after that the account counts as lost."""
        headers = dict(kwargs.pop("headers", None) or {})
        for _attempt in (1, 2):
            headers["Authorization"] = f"Bearer {self.access_token(account)}"
            response = _http(method, url, headers=headers, **kwargs)
            if response.status_code != 401:
                return response
            self.forget(account)
        raise AccountLost()

    def revoke(self, account):
        """Give the permission back at the service, where it has a way to. Best effort: the
        token is forgotten here either way."""
        if self._revoke is not None:
            try:
                self._revoke(self, account)
            except Exception:
                pass
        self.forget(account)
