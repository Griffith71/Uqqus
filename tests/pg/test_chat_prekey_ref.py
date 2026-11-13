"""The id of the pre-key a sign-in's form kept (assets/js/chat_key.js, helpers/session_cookie.py PREKEY_REF_KEY), on the real app: the
sign-in keeps the id its form sent, through the two-factor step too, a later sign-in replaces it or drops it, and signing out takes it.
The chat page uses only a pre-key with this id (keylock.js takePrekey, tests/test_chat_unlock_stops.py), so one an earlier sign-in of the
same account left in another tab, from a password changed since elsewhere, is never used to lock the chat key."""
import re
import secrets

import pyotp

BASE = "http://localhost"
HEADERS = {"User-Agent": "ci-prekey-ref"}


def fresh_address():
    return f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"


def ref_of(client):
    with client.session_transaction() as session:
        return session.get("chat_prekey_ref")


def sign_in(member, client=None, ref=None):
    client = client or member.app.test_client()
    data = {"username": member.username, "password": member.password}
    if ref is not None:
        data["chat_prekey_ref"] = ref
    answer = client.post("/login", base_url=BASE, headers=HEADERS, environ_base={"REMOTE_ADDR": fresh_address()}, data=data)
    return client, answer


def test_a_password_sign_in_keeps_the_id_its_form_sent_and_the_settings_form_names_it(make_member):
    member = make_member()
    ref = secrets.token_hex(16)
    client, answer = sign_in(member, ref=ref)
    assert answer.status_code == 302
    assert ref_of(client) == ref
    page = client.get("/settings/security", base_url=BASE, headers=HEADERS).get_data(as_text=True)
    assert f'data-chat-prekey-ref="{ref}" data-chat-rekey>' in page


def test_a_later_sign_in_replaces_the_id_or_leaves_none(make_member):
    member = make_member()
    first = secrets.token_hex(16)
    client, _ = sign_in(member, ref=first)
    second = secrets.token_hex(16)
    sign_in(member, client, ref=second)
    assert ref_of(client) == second
    # a form sent without the script (no id) or with something that isn't one: no earlier sign-in's id outlives it
    for sent in (None, "not-an-id", second.upper()):
        sign_in(member, client, ref=first)
        sign_in(member, client, ref=sent)
        assert ref_of(client) is None, sent


def test_the_two_factor_step_carries_the_id_to_the_sign_in(make_member):
    member = make_member()
    secret = pyotp.random_base32()
    db = member.db_session()
    user = member.user()
    user.mfa_secret = secret
    db.add(user)
    db.commit()
    ref = secrets.token_hex(16)
    client = member.app.test_client()
    address = fresh_address()
    first = client.post("/login", base_url=BASE, headers=HEADERS, environ_base={"REMOTE_ADDR": address},
                        data={"username": member.username, "password": member.password, "chat_prekey_ref": ref})
    page = first.get_data(as_text=True)
    assert first.status_code == 200 and f'name="chat_prekey_ref" value="{ref}"' in page, page[:300]
    assert ref_of(client) is None                                     # not signed in yet
    form = {"username": member.username, "2fa_token": pyotp.TOTP(secret).now(), "redirect": "/", "chat_prekey_ref": ref,
            "time": re.search(r'name="time" value="(\d+)"', page).group(1),
            "hash": re.search(r'name="hash" value="([^"]+)"', page).group(1)}
    second = client.post("/login", base_url=BASE, headers=HEADERS, environ_base={"REMOTE_ADDR": address}, data=form)
    assert second.status_code == 302, second.get_data(as_text=True)[:300]
    assert ref_of(client) == ref


def test_signing_out_takes_the_id(make_member):
    member = make_member()
    client, _ = sign_in(member, ref=secrets.token_hex(16))
    out = client.post("/logout", base_url=BASE, headers=HEADERS, data={"formkey": member.formkey(client)})
    assert out.status_code in (200, 204, 302), out.status_code
    assert ref_of(client) is None
