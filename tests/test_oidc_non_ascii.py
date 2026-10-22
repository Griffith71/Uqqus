"""A client's id or secret, or a PKCE challenge, with a letter outside ASCII is a refusal, never a 500
(hmac.compare_digest raises TypeError for such a str; bug hunt)."""
import hashlib

from ruqqus.helpers import oidc_provider as oidc

VERIFIER = "v" * 43
CHALLENGE = oidc.b64url(hashlib.sha256(VERIFIER.encode()).digest())


def test_a_non_ascii_secret_or_id_is_refused():
    assert oidc.client_ok("synapse", "sécret", "synapse", "secret") is False
    assert oidc.client_ok("synäpse", "secret", "synapse", "secret") is False
    assert oidc.client_ok("synapse", "secret", "synapse", "secret") is True


def test_a_non_ascii_challenge_is_refused():
    assert oidc.pkce_ok(VERIFIER, CHALLENGE) is True
    assert oidc.pkce_ok(VERIFIER, CHALLENGE[:-1] + "é") is False
