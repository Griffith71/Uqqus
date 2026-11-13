"""Starting chat over on the real app (routes/chat_key_reset_pages.py), with the chat server faked: a grant takes the password
(and the two-factor or a backup code), is good once, for its own account, and only while the account's credentials are as they
were; the upload is checked and sent AS the member with the appservice's token, never anything else; the member is told."""
import base64
import json
import secrets
import sys
import time

import gevent
import pyotp
import pytest
from sqlalchemy import text

BASE = "http://localhost"
HEADERS = {"User-Agent": "ci-key-reset"}


def addr():
    return f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"


def post(client, path, data):
    return client.post(path, base_url=BASE, headers=HEADERS, data=data, environ_base={"REMOTE_ADDR": addr()})


def b64(raw):
    return base64.b64encode(raw).decode().rstrip("=")


def keys_for(mxid, n=1):
    master, ssk, usk = (b64(secrets.token_bytes(32)) for _ in range(3))
    sig = b64(secrets.token_bytes(64))
    return {
        "master_key": {"user_id": mxid, "usage": ["master"], "keys": {f"ed25519:{master}": master},
                       "signatures": {mxid: {"ed25519:UQabcdefgh": sig}}},
        "self_signing_key": {"user_id": mxid, "usage": ["self_signing"], "keys": {f"ed25519:{ssk}": ssk},
                             "signatures": {mxid: {f"ed25519:{master}": sig}}},
        "user_signing_key": {"user_id": mxid, "usage": ["user_signing"], "keys": {f"ed25519:{usk}": usk},
                             "signatures": {mxid: {f"ed25519:{master}": sig}}},
    }


@pytest.fixture
def server(monkeypatch, real_app):
    import ruqqus.helpers.matrix_client as matrix
    state = {"uploads": [], "status": 200, "down": False}

    def upload_signing_keys(mxid, keys):
        if state["down"]:
            raise matrix.Unreachable("down")
        state["uploads"].append((mxid, keys))
        return state["status"], ({} if state["status"] == 200 else {"errcode": "M_UNKNOWN"})

    monkeypatch.setattr(matrix, "upload_signing_keys", upload_signing_keys)
    return state


@pytest.fixture
def mail(real_app, monkeypatch):
    sent = []
    monkeypatch.setattr(sys.modules["ruqqus.routes.chat_key_reset_pages"], "send_mail_quietly",
                        lambda to, subject, html: sent.append((to, subject, html)))
    return sent


@pytest.fixture
def member(make_member, real_app):
    from ruqqus.__main__ import r
    m = make_member()
    m.mxid = f"@ruqqus_{m.id}:ruqqus.local"
    db = m.db_session()
    db.execute(text("INSERT INTO chat_identities (user_id, matrix_user_id, key_salt, ready_utc) VALUES (:u, :m, 's', :t)"),
               {"u": m.id, "m": m.mxid, "t": int(time.time())})
    db.commit()
    for k in r.scan_iter("LIMITER*chat_key_reset*"):
        r.delete(k)
    for k in r.scan_iter("LIMITER*chat_signing_keys*"):
        r.delete(k)
    return m


def set_user(member, **values):
    db = member.db_session()
    db.execute(text("UPDATE users SET " + ", ".join(f"{k} = :{k}" for k in values) + " WHERE id = :u"), dict(values, u=member.id))
    db.commit()


def grant(member, client, **extra):
    return post(client, "/api/chat/key_reset/grant", {"formkey": member.formkey(client), "password": member.password, **extra})


def upload(member, client, granted, keys=None):
    body = json.dumps(keys if keys is not None else keys_for(member.mxid))
    return post(client, "/api/chat/signing_keys", {"formkey": member.formkey(client), "grant": granted, "keys": body})


def notices(member):
    db = member.db_session()
    db.rollback()
    return db.execute(text("SELECT count(*) FROM notifications n JOIN comments_aux a ON a.id = n.comment_id "
                           "WHERE n.user_id = :u AND a.body LIKE 'Your chat encryption was started over%'"), {"u": member.id}).scalar()


def log_entries(member):
    db = member.db_session()
    db.rollback()
    return db.execute(text("SELECT audience FROM log_entries WHERE action = 'chat_keys_reset' AND target_user_id = :u"),
                      {"u": member.id}).scalars().all()


def test_the_password_buys_one_upload_sent_as_the_member(member, server, mail):
    client = member.browser()
    answer = grant(member, client)
    assert answer.status_code == 200 and answer.headers["Cache-Control"] == "no-store"
    granted = answer.get_json()["grant"]
    sent = upload(member, client, granted)
    assert sent.status_code == 200, sent.get_data(as_text=True)
    assert [m for m, _ in server["uploads"]] == [member.mxid]
    # used once
    again = upload(member, client, granted)
    assert again.status_code == 403 and again.get_json()["grant"] == "gone"
    assert len(server["uploads"]) == 1
    # told: a notice, a staff-only Log entry, and no email for an address that isn't verified
    from ruqqus.helpers import action_log
    assert notices(member) == 1 and log_entries(member) == [action_log.STAFF]
    gevent.sleep(0.1)
    assert mail == []


def test_a_verified_address_is_emailed(member, server, mail):
    set_user(member, is_activated=True)
    client = member.browser()
    assert upload(member, client, grant(member, client).get_json()["grant"]).status_code == 200
    gevent.sleep(0.2)
    assert len(mail) == 1 and "started over" in mail[0][1] and mail[0][0] == f"{member.username}@example.com"


def test_a_wrong_password_buys_nothing(member, server):
    client = member.browser()
    answer = post(client, "/api/chat/key_reset/grant", {"formkey": member.formkey(client), "password": "wrong"})
    assert answer.status_code == 403 and answer.get_json()["field"] == "password"
    assert upload(member, client, "made-up").status_code == 403
    assert server["uploads"] == []


def test_two_factor_needs_the_code_and_takes_a_backup_code(member, server):
    secret = pyotp.random_base32()
    set_user(member, mfa_secret=secret)
    client = member.browser()
    assert grant(member, client).get_json()["field"] == "otp"
    assert grant(member, client, otp="000000").get_json()["field"] == "otp"
    assert grant(member, client, otp=pyotp.TOTP(secret).now()).status_code == 200
    # a backup code, once
    from ruqqus.helpers import mfa_backup, mfa_backup_store
    db = member.db_session()
    codes = mfa_backup.generate()
    mfa_backup_store.replace(db, member.id, codes, int(time.time()))
    db.commit()
    assert grant(member, client, otp=codes[0]).status_code == 200
    assert grant(member, client, otp=codes[0]).status_code == 403


def test_a_grant_is_only_for_its_own_account(member, make_member, server):
    other = make_member()
    other.mxid = f"@ruqqus_{other.id}:ruqqus.local"
    other_client = other.browser()
    db = other.db_session()
    db.execute(text("INSERT INTO chat_identities (user_id, matrix_user_id, key_salt) VALUES (:u, :m, 's')"),
               {"u": other.id, "m": f"@ruqqus_{other.id}:ruqqus.local"})
    db.commit()
    granted = grant(member, member.browser()).get_json()["grant"]
    assert upload(other, other_client, granted).status_code == 403
    # and an upload naming another account's keys is refused, the grant spent
    client = member.browser()
    granted = grant(member, client).get_json()["grant"]
    assert upload(member, client, granted, keys_for(f"@ruqqus_{other.id}:ruqqus.local")).status_code == 400
    assert upload(member, client, granted).status_code == 403
    assert server["uploads"] == []


def test_a_grant_ends_with_the_credentials(member, server):
    client = member.browser()
    granted = grant(member, client).get_json()["grant"]
    # Log out everywhere elsewhere raises the nonce: this browser's sign-in ends with it, and so does the grant
    set_user(member, login_nonce=member.user().login_nonce + 1)
    fresh = member.browser()
    assert upload(member, fresh, granted).status_code == 403
    assert server["uploads"] == []


def test_anything_but_the_keys_is_refused(member, server):
    client = member.browser()
    body = dict(keys_for(member.mxid), auth={"type": "m.login.dummy"})
    assert upload(member, client, grant(member, client).get_json()["grant"], body).status_code == 400
    assert server["uploads"] == []


def test_a_chat_server_that_is_down_answers_503_keeps_the_grant_and_tells_nobody(member, server):
    server["down"] = True
    client = member.browser()
    granted = grant(member, client).get_json()["grant"]
    answer = upload(member, client, granted)
    assert answer.status_code == 503 and answer.get_json()["grant"] == "kept"
    assert notices(member) == 0 and log_entries(member) == []
    # back up: the same grant goes through, once
    server["down"] = False
    assert upload(member, client, granted).status_code == 200
    assert upload(member, client, granted).status_code == 403


def test_the_chat_servers_refusal_is_passed_back_never_as_the_sites_own_code(member, server):
    for status in (400, 401, 403):
        server["status"] = status
        client = member.browser()
        answer = upload(member, client, grant(member, client).get_json()["grant"])
        assert answer.status_code == 502, status
        assert answer.get_json()["matrix_status"] == status and answer.get_json()["body"]["errcode"] == "M_UNKNOWN"
    assert notices(member) == 0 and log_entries(member) == []


def test_a_grant_is_only_for_the_sign_in_that_asked(member, server):
    granted = grant(member, member.browser()).get_json()["grant"]
    other = member.browser()                    # the same account, another sign-in
    assert upload(member, other, granted).status_code == 403
    assert server["uploads"] == []


def test_held_accounts_and_qr_sign_ins_are_held_off(member, server):
    client = member.browser()
    with client.session_transaction() as session:
        session["qr_limited"] = True
        session["qr_expires_utc"] = int(time.time()) + 3600
    answer = grant(member, client)
    assert answer.status_code in (401, 403) and server["uploads"] == []
    set_user(member, age_declared=2, age_lock=1, filter_level=2)
    answer = grant(member, member.browser())
    assert answer.status_code == 403 and answer.get_json().get("age_locked") is True


def test_the_key_info_says_whether_two_factor_is_on(member, server):
    client = member.browser()
    assert client.get("/api/chat/key", base_url=BASE, headers=HEADERS).get_json()["mfa"] is False
    set_user(member, mfa_secret=pyotp.random_base32())
    assert member.browser().get("/api/chat/key", base_url=BASE, headers=HEADERS).get_json()["mfa"] is True
