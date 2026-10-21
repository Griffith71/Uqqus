"""'Sign in with <site>' on the real app and a real Postgres (routes/oidc_pages.py): only the chat server is a client, a
code is used once and only with its PKCE verifier, the member confirms with their password, accounts whose chat isn't
set up and bots are refused, and the id_token names only the account number."""
import base64
import hashlib
import json
import secrets
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import text

BASE = "http://localhost"
SECRET = "ci-oidc-" + secrets.token_hex(16)


def addr():
    return f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"


@pytest.fixture(scope="module")
def oidc_on(real_app):
    import os
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from ruqqus.routes import oidc_pages
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    saved = {k: os.environ.get(k) for k in ("OIDC_CLIENT_SECRET", "OIDC_PRIVATE_KEY")}
    os.environ["OIDC_CLIENT_SECRET"] = SECRET
    os.environ["OIDC_PRIVATE_KEY"] = base64.b64encode(pem).decode()
    oidc_pages._KEY.clear()
    yield real_app[0], key
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    oidc_pages._KEY.clear()


def chat_ready(member, ready=True):
    db = member.db_session()
    db.execute(text("DELETE FROM chat_identities WHERE user_id = :u"), {"u": member.id})
    if ready:
        db.execute(text("INSERT INTO chat_identities (user_id, matrix_user_id, key_salt, key_blob_password, ready_utc) "
                        "VALUES (:u, :m, 'salt', :blob, :t)"),
                   {"u": member.id, "m": f"@ruqqus_{member.id}:ruqqus.local", "blob": "x" * 60, "t": int(time.time())})
    db.commit()


def redirect_uri(app):
    return app.config["MATRIX_PUBLIC_URL"].rstrip("/") + "/_synapse/client/oidc/callback"


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def params(app, challenge, **over):
    p = {"client_id": "synapse", "redirect_uri": redirect_uri(app), "response_type": "code", "scope": "openid",
         "state": "st" + secrets.token_hex(4), "nonce": "nn" + secrets.token_hex(4), "code_challenge": challenge,
         "code_challenge_method": "S256"}
    p.update(over)
    return p


def consent(member, client, app, challenge, password=None, decision="allow", **over):
    form = {**params(app, challenge, **over), "formkey": member.formkey(client), "decision": decision,
            "password": member.password if password is None else password}
    return client.post("/oidc/authorize", base_url=BASE, data=form, environ_base={"REMOTE_ADDR": addr()})


def token(client, app, code, verifier, secret=SECRET, **over):
    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri(app), "client_id": "synapse",
            "client_secret": secret, "code_verifier": verifier}
    form.update(over)
    return client.post("/oidc/token", base_url=BASE, data=form, environ_base={"REMOTE_ADDR": addr()})


def unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def test_discovery_and_keys_are_published(oidc_on):
    app, key = oidc_on
    client = app.test_client()
    d = client.get("/.well-known/openid-configuration", base_url=BASE).get_json()
    assert d["authorization_endpoint"].endswith("/oidc/authorize") and d["code_challenge_methods_supported"] == ["S256"]
    keys = client.get("/oidc/jwks", base_url=BASE).get_json()["keys"]
    assert len(keys) == 1 and set(keys[0]) == {"kty", "use", "alg", "kid", "n", "e"}
    well = client.get("/.well-known/matrix/client", base_url=BASE)
    assert well.get_json()["m.homeserver"]["base_url"] == app.config["MATRIX_PUBLIC_URL"]
    assert well.headers["Access-Control-Allow-Origin"] == "*"


def test_a_stranger_client_or_redirect_is_shown_an_error_and_sent_nowhere(oidc_on, make_member):
    app, _ = oidc_on
    member = make_member()
    chat_ready(member)
    client = member.browser()
    _, challenge = pkce()
    for over in ({"client_id": "evil"}, {"redirect_uri": "https://evil.example/cb"}):
        answer = client.get("/oidc/authorize", base_url=BASE, query_string=params(app, challenge, **over))
        assert answer.status_code == 400 and "Location" not in answer.headers, over
        assert "can't go on" in answer.get_data(as_text=True)
    answer = client.get("/oidc/authorize", base_url=BASE, query_string=params(app, challenge, code_challenge_method="plain"))
    assert answer.status_code == 302 and answer.headers["Location"].startswith(redirect_uri(app))
    assert "error=invalid_request" in answer.headers["Location"]
    visitor = app.test_client().get("/oidc/authorize", base_url=BASE, query_string=params(app, challenge))
    assert visitor.status_code in (302, 401), "a visitor signs in on the site first"


def test_the_consent_page_may_send_the_browser_only_to_the_chat_server(oidc_on, make_member):
    app, _ = oidc_on
    member = make_member()
    chat_ready(member)
    _, challenge = pkce()
    page = member.browser().get("/oidc/authorize", base_url=BASE, query_string=params(app, challenge))
    assert page.status_code == 200 and 'id="oidc-password"' in page.get_data(as_text=True)
    origin = "{0.scheme}://{0.netloc}".format(urlsplit(app.config["MATRIX_PUBLIC_URL"]))
    assert f"form-action 'self' {origin};" in page.headers["Content-Security-Policy"]
    assert page.headers["Cache-Control"] == "no-store"


def test_the_whole_flow_once_and_never_again(oidc_on, make_member):
    app, key = oidc_on
    member = make_member()
    chat_ready(member)
    client = member.browser()
    verifier, challenge = pkce()

    wrong = consent(member, client, app, challenge, password="not-it")
    assert wrong.status_code == 401 and "That password isn" in wrong.get_data(as_text=True)
    answer = consent(member, client, app, challenge, state="st-123", nonce="nn-456")
    assert answer.status_code == 302
    sent = urlsplit(answer.headers["Location"])
    assert f"{sent.scheme}://{sent.netloc}{sent.path}" == redirect_uri(app)
    query = parse_qs(sent.query)
    assert query["state"] == ["st-123"]
    code = query["code"][0]

    server = app.test_client()                      # Synapse, with no browser session
    assert token(server, app, code, verifier, secret="wrong").status_code == 401
    assert token(server, app, code, "x" * 50).get_json() == {"error": "invalid_grant"}, "a wrong verifier spends the code"
    answer = consent(member, client, app, challenge)
    code = parse_qs(urlsplit(answer.headers["Location"]).query)["code"][0]
    good = token(server, app, code, verifier)
    assert good.status_code == 200 and good.headers["Cache-Control"] == "no-store"
    body = good.get_json()
    assert token(server, app, code, verifier).get_json() == {"error": "invalid_grant"}, "a code is good once"

    head, claims, signature = body["id_token"].split(".")
    claims = json.loads(unb64(claims))
    assert claims["sub"] == str(member.id) and claims["aud"] == "synapse" and claims["exp"] > time.time()
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    key.public_key().verify(unb64(signature), f"{head}.{body['id_token'].split('.')[1]}".encode(), padding.PKCS1v15(),
                            hashes.SHA256())
    assert set(claims) <= {"iss", "sub", "aud", "iat", "exp", "auth_time", "nonce"}, "nothing about the member but the number"

    info = server.get("/oidc/userinfo", base_url=BASE, headers={"Authorization": "Bearer " + body["access_token"]})
    assert info.get_json() == {"sub": str(member.id)}
    assert server.get("/oidc/userinfo", base_url=BASE, headers={"Authorization": "Bearer nope"}).status_code == 401


def test_cancel_sends_back_access_denied(oidc_on, make_member):
    app, _ = oidc_on
    member = make_member()
    chat_ready(member)
    client = member.browser()
    _, challenge = pkce()
    answer = consent(member, client, app, challenge, decision="deny", password="", state="st-9")
    assert answer.status_code == 302 and "error=access_denied" in answer.headers["Location"] and "state=st-9" in answer.headers["Location"]


def test_no_code_before_chat_is_set_up_and_never_for_a_bot(oidc_on, make_member):
    app, _ = oidc_on
    member = make_member()
    chat_ready(member, ready=False)
    client = member.browser()
    _, challenge = pkce()
    page = client.get("/oidc/authorize", base_url=BASE, query_string=params(app, challenge)).get_data(as_text=True)
    assert "Open Chat on the site once first" in page and 'id="oidc-password"' not in page
    assert consent(member, client, app, challenge).status_code == 403

    bot = make_member()
    chat_ready(bot)
    db = bot.db_session()
    db.execute(text("UPDATE users SET is_bot = true WHERE id = :u"), {"u": bot.id})
    db.commit()
    assert consent(bot, bot.browser(), app, challenge).status_code == 403


def test_a_code_for_an_account_suspended_since_is_refused(oidc_on, make_member):
    app, _ = oidc_on
    member = make_member()
    chat_ready(member)
    client = member.browser()
    verifier, challenge = pkce()
    code = parse_qs(urlsplit(consent(member, client, app, challenge).headers["Location"]).query)["code"][0]
    db = member.db_session()
    db.execute(text("UPDATE users SET is_banned = 1, unban_utc = 0 WHERE id = :u"), {"u": member.id})
    db.commit()
    assert token(app.test_client(), app, code, verifier).get_json() == {"error": "invalid_grant"}
