"""'Sign in with <site>' for outside Matrix apps (helpers/oidc_provider.py): the rules for an authorization request, PKCE,
the client's credentials, the signed id_token (checked here with the public key from the key set, as Synapse checks
it), and that nothing about the member but their account number leaves. The flow on a real app and Postgres is
tests/pg/test_oidc.py."""
import base64
import hashlib
import json
from types import SimpleNamespace

import pytest

from ruqqus.helpers import oidc_provider as oidc

CLIENT = "synapse"
REDIRECT = "http://localhost:8008/_synapse/client/oidc/callback"
VERIFIER = "v" * 43 + "-._~abc"
CHALLENGE = oidc.b64url(hashlib.sha256(VERIFIER.encode()).digest())


def good(**over):
    args = {"client_id": CLIENT, "redirect_uri": REDIRECT, "response_type": "code", "scope": "openid",
            "state": "abc123", "nonce": "n0nce", "code_challenge": CHALLENGE, "code_challenge_method": "S256"}
    args.update(over)
    return {k: v for k, v in args.items() if v is not None}


def test_the_callback_is_synapses_own():
    assert oidc.redirect_uri("http://localhost:8008/") == REDIRECT
    assert oidc.redirect_uri("https://matrix.example.org") == "https://matrix.example.org/_synapse/client/oidc/callback"
    assert oidc.issuer("example.org", True) == "https://example.org" and oidc.issuer("localhost", 0) == "http://localhost"
    assert oidc.origin("http://localhost:8008/x/y") == "http://localhost:8008"


def test_a_good_request_passes():
    assert oidc.authorize_refusal(good(), CLIENT, REDIRECT) is None
    assert oidc.authorize_refusal(good(scope="openid profile", nonce=None), CLIENT, REDIRECT) is None


@pytest.mark.parametrize("over, why", [
    ({"client_id": "someone"}, "unknown_client"),
    ({"client_id": None}, "unknown_client"),
    ({"redirect_uri": "https://evil.example/cb"}, "bad_redirect"),
    ({"redirect_uri": REDIRECT + "?x=1"}, "bad_redirect"),
    ({"response_type": "token"}, "unsupported_response_type"),
    ({"scope": "profile"}, "invalid_scope"),
    ({"state": None}, "invalid_state"),
    ({"state": "has space"}, "invalid_state"),
    ({"code_challenge_method": "plain"}, "pkce_required"),
    ({"code_challenge": None}, "pkce_required"),
    ({"code_challenge": "short"}, "pkce_required"),
    ({"nonce": "two words"}, "invalid_nonce"),
])
def test_anything_wrong_is_refused(over, why):
    assert oidc.authorize_refusal(good(**over), CLIENT, REDIRECT) == why


def test_pkce_needs_the_verifier_that_made_the_challenge():
    assert oidc.pkce_ok(VERIFIER, CHALLENGE)
    assert not oidc.pkce_ok("w" * 50, CHALLENGE)
    assert not oidc.pkce_ok(None, CHALLENGE) and not oidc.pkce_ok("", CHALLENGE)
    assert not oidc.pkce_ok("short", oidc.b64url(hashlib.sha256(b"short").digest())), "a verifier is 43 to 128 characters"
    assert not oidc.pkce_ok(VERIFIER, None)


def test_the_client_must_know_its_secret():
    assert oidc.client_ok(CLIENT, "s3cret", CLIENT, "s3cret")
    assert not oidc.client_ok(CLIENT, "wrong", CLIENT, "s3cret")
    assert not oidc.client_ok("other", "s3cret", CLIENT, "s3cret")
    assert not oidc.client_ok(None, None, CLIENT, "s3cret") and not oidc.client_ok(CLIENT, "", CLIENT, "s3cret")
    header = "Basic " + base64.b64encode(b"synapse:s%3Acret").decode()
    assert oidc.basic_auth(header) == ("synapse", "s:cret")
    for bad in (None, "", "Bearer x", "Basic !!!", "Basic " + base64.b64encode(b"no-colon").decode()):
        assert oidc.basic_auth(bad) == (None, None)


def test_who_may_sign_in_to_a_chat_app():
    member = SimpleNamespace(is_bot=False, is_deleted=False, is_suspended=False)
    assert oidc.account_refusal(member, True) is None
    assert oidc.account_refusal(member, False) == oidc.NOT_SET_UP
    for flag in ("is_bot", "is_deleted", "is_suspended"):
        assert oidc.account_refusal(SimpleNamespace(**{**vars(member), flag: True}), True) == oidc.REFUSED
    assert oidc.account_refusal(None, True) == oidc.REFUSED


def test_only_the_account_number_leaves():
    assert oidc.userinfo(42) == {"sub": "42"}
    claims = oidc.id_claims("http://localhost", CLIENT, 42, "n0nce", 1000, 2000)
    assert claims == {"iss": "http://localhost", "sub": "42", "aud": CLIENT, "iat": 2000, "exp": 2000 + oidc.ID_TOKEN_SECONDS,
                      "auth_time": 1000, "nonce": "n0nce"}
    assert "nonce" not in oidc.id_claims("http://localhost", CLIENT, 42, None, 1000, 2000)
    record = json.loads(oidc.code_record(42, "n", CHALLENGE, 1000))
    assert record == {"user_id": 42, "nonce": "n", "challenge": CHALLENGE, "auth_time": 1000}


def test_codes_and_tokens_are_kept_by_their_digest():
    assert oidc.digest("abc") == hashlib.sha256(b"abc").hexdigest() and oidc.digest("abc") != "abc"


def test_discovery_describes_what_is_served():
    d = oidc.discovery("https://example.org")
    assert d["issuer"] == "https://example.org" and d["jwks_uri"] == "https://example.org/oidc/jwks"
    assert d["code_challenge_methods_supported"] == ["S256"] and d["id_token_signing_alg_values_supported"] == ["RS256"]
    assert d["response_types_supported"] == ["code"] and d["scopes_supported"] == ["openid"]


@pytest.fixture(scope="module")
def key():
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return pem.decode()


def unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def test_the_key_comes_from_one_env_line_or_a_pem(key):
    assert oidc.key_from_env(base64.b64encode(key.encode()).decode()) == key
    assert oidc.key_from_env(key) == key.strip()
    assert oidc.key_from_env("") is None and oidc.key_from_env(None) is None


def test_the_id_token_verifies_with_the_published_key(key):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers

    private = oidc.load_key(key)
    claims = oidc.id_claims("http://localhost", CLIENT, 7, "n", 1, 2)
    token = oidc.sign(claims, private)
    head, body, signature = token.split(".")
    published = oidc.jwks(private)["keys"][0]
    assert published["kty"] == "RSA" and published["alg"] == "RS256" and published["use"] == "sig"
    assert json.loads(unb64(head)) == {"alg": "RS256", "typ": "JWT", "kid": published["kid"]}
    assert json.loads(unb64(body)) == claims
    public = RSAPublicNumbers(int.from_bytes(unb64(published["e"]), "big"), int.from_bytes(unb64(published["n"]), "big")).public_key()
    public.verify(unb64(signature), f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())   # raises if wrong
    assert set(published) == {"kty", "use", "alg", "kid", "n", "e"}, "the key set never carries the private parts"
