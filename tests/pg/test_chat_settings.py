"""Settings > Chat and the recovery code's "kept" time on the real app (routes/chat_settings_pages.py, routes/chat.py chat_code_saved):
only a time is kept, a new code clears it, the page says which, and a child account without chat is held off like the rest of chat."""
import secrets
import time

from sqlalchemy import text

BASE = "http://localhost"
HEADERS = {"User-Agent": "ci-chat-settings"}


def addr():
    return f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"


def get(client, path):
    return client.get(path, base_url=BASE, headers=HEADERS, environ_base={"REMOTE_ADDR": addr()})


def post(client, path, data):
    return client.post(path, base_url=BASE, headers=HEADERS, data=data, environ_base={"REMOTE_ADDR": addr()})


def with_chat(member, recovery=True):
    db = member.db_session()
    db.execute(text("INSERT INTO chat_identities (user_id, matrix_user_id, key_salt, key_blob_password, key_blob_recovery, ready_utc) "
                    "VALUES (:u, :m, 's', :b, :r, :t)"),
               {"u": member.id, "m": f"@ruqqus_{member.id}:ruqqus.local", "b": "x" * 60, "r": ("y" * 60) if recovery else None,
                "t": int(time.time())})
    db.commit()


def saved_at(member):
    db = member.db_session()
    db.rollback()
    return db.execute(text("SELECT code_saved_utc FROM chat_identities WHERE user_id = :u"), {"u": member.id}).scalar()


def test_saying_the_code_is_kept_keeps_a_time_and_a_new_code_clears_it(make_member):
    member = make_member()
    with_chat(member)
    client = member.browser()
    assert get(client, "/api/chat/key").get_json()["code_saved"] is False
    assert post(client, "/api/chat/code_saved", {"formkey": member.formkey(client)}).status_code == 204
    assert saved_at(member) > 0 and get(client, "/api/chat/key").get_json()["code_saved"] is True
    # a new code (the browser saves its locked copy) is not kept until the member says so again
    assert post(client, "/api/chat/key", {"formkey": member.formkey(client), "recovery_blob": "z" * 60}).status_code == 204
    assert saved_at(member) == 0
    # a password copy alone leaves it as it was
    post(client, "/api/chat/code_saved", {"formkey": member.formkey(client)})
    post(client, "/api/chat/key", {"formkey": member.formkey(client), "password_blob": "w" * 60})
    assert saved_at(member) > 0


def test_no_code_yet_is_refused(make_member):
    member = make_member()
    with_chat(member, recovery=False)
    client = member.browser()
    assert post(client, "/api/chat/code_saved", {"formkey": member.formkey(client)}).status_code == 409


def test_the_settings_page_says_whether_the_code_was_kept(make_member):
    member = make_member()
    client = member.browser()
    page = get(client, "/settings/chat")
    assert page.status_code == 200 and "Chat isn't set up on your account yet." in page.get_data(as_text=True)
    with_chat(member)
    page = get(client, "/settings/chat").get_data(as_text=True)
    assert "You haven't said you kept your recovery code." in page and 'href="/chat?recovery=new"' in page
    assert "Two-factor sign-in keeps someone" in page
    post(client, "/api/chat/code_saved", {"formkey": member.formkey(client)})
    assert "You said you kept your recovery code on" in get(client, "/settings/chat").get_data(as_text=True)


def test_a_child_account_without_chat_is_held_off(make_member):
    member = make_member()
    db = member.db_session()
    db.execute(text("UPDATE users SET age_declared = 2, age_lock = 1, filter_level = 2 WHERE id = :u"), {"u": member.id})
    db.commit()
    assert get(member.browser(), "/settings/chat").status_code in (302, 403)
