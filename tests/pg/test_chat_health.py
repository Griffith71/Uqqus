"""The guard between the site's database and Synapse's, on the real app and Postgres (helpers/chat_health.py). The chat
server is faked: these tests run with no Synapse."""
import pytest
from sqlalchemy import text


@pytest.fixture
def fake_chat(monkeypatch, real_app):
    """A chat server that has accounts for `server["accounts"]` and keeps the mark in `server["mark"]`."""
    import ruqqus.helpers.matrix_client as matrix
    from ruqqus.__main__ import r
    server = {"mark": {}, "accounts": set(), "made": []}

    def provision(user_id, adopt_existing=True):
        matrix.note_chat_id(user_id)
        mxid = matrix.mxid_for(user_id)
        if mxid in server["accounts"] and not adopt_existing:
            raise matrix.OrphanMatrixAccount(mxid)
        server["accounts"].add(mxid)
        server["made"].append(user_id)
        return mxid

    monkeypatch.setattr(matrix, "read_id_mark", lambda: dict(server["mark"]))
    monkeypatch.setattr(matrix, "write_id_mark", lambda mark: server.__setitem__("mark", dict(mark)))
    monkeypatch.setattr(matrix, "account_exists", lambda mxid: mxid in server["accounts"])
    monkeypatch.setattr(matrix, "provision_user", provision)
    monkeypatch.setattr(matrix, "set_display_name", lambda *a, **k: None)
    from ruqqus.helpers import chat_health
    for key in (chat_health.REFUSED_KEY, chat_health.HEALTH_KEY):
        r.delete(key)
    return server


def _next_id(db):
    return db.execute(text("SELECT CASE WHEN is_called THEN last_value + 1 ELSE last_value END FROM users_id_seq")).scalar()


def test_new_user_ids_start_past_the_mark_and_never_go_back(real_app):
    from ruqqus.helpers import chat_health
    db = real_app[1]()
    start = _next_id(db)
    assert chat_health.raise_user_ids(db, start + 50) is True
    assert _next_id(db) == start + 51
    assert chat_health.raise_user_ids(db, start) is False             # a lower mark never lowers the sequence
    assert _next_id(db) == start + 51


def test_an_id_that_had_a_chat_account_is_never_handed_out_again(real_app, make_member, fake_chat):
    """A site database restored without Synapse's: its sequence is behind ids the chat server already gave out."""
    from ruqqus.helpers import chat_health
    db = real_app[1]()
    taken = _next_id(db) + 3
    fake_chat["mark"] = {"high": taken, "sites": {"postgres": {"high": taken}}}
    fake_chat["accounts"].add(f"@ruqqus_{taken}:x")
    chat_health.run(db, None, fix=True)
    member = make_member()
    assert member.id > taken


def test_a_member_whose_id_is_taken_reads_why_and_the_admins_are_told_once(real_app, make_member, fake_chat):
    import ruqqus.helpers.matrix_client as matrix
    app, db_session = real_app
    admin = make_member()
    db = db_session()
    db.execute(text("UPDATE users SET admin_level = 4 WHERE id = :i"), {"i": admin.id})
    db.commit()
    member = make_member()
    fake_chat["accounts"].add(matrix.mxid_for(member.id))          # the chat server had someone else under this id
    browser = member.browser()

    page = browser.get("/chat")
    assert page.status_code == 503
    assert b"Chat isn&#39;t available for your account right now" in page.data

    answer = browser.post("/api/chat/token", data={"formkey": member.formkey(browser)})
    assert answer.status_code == 503 and "admins have been told" in answer.get_json()["error"]

    told = db.execute(text("SELECT count(*) FROM notifications n JOIN comments_aux a ON a.id = n.comment_id "
                           "WHERE n.user_id = :a AND a.body LIKE :b"),
                      {"a": admin.id, "b": f"%user id {member.id})%"}).scalar()
    assert told == 1                                                   # twice refused, told once
    assert db.execute(text("SELECT count(*) FROM chat_identities WHERE user_id = :u"), {"u": member.id}).scalar() == 0

    health = admin.browser().get("/admin/chat")
    assert health.status_code == 200 and f'data-user="{member.id}"'.encode() in health.data
    assert b'id="chat-signouts-pending"' in health.data and b'id="chat-signouts-missing"' not in health.data

    owner = admin.browser()
    given = owner.post("/admin/chat/give_back", data={"formkey": admin.formkey(owner), "user_id": member.id})
    assert given.status_code == 302
    db.expire_all()
    assert db.execute(text("SELECT matrix_user_id FROM chat_identities WHERE user_id = :u"),
                      {"u": member.id}).scalar() == matrix.mxid_for(member.id)
    assert db.execute(text("SELECT audience FROM log_entries WHERE action = 'chat_account_returned' AND target_user_id = :u"),
                      {"u": member.id}).scalar() == 2                  # staff only


def test_a_synapse_restored_without_the_site_is_reported(real_app, make_member, fake_chat):
    """Chat accounts the site knows that the chat server lacks: a problem, and nothing is written over it."""
    from ruqqus.helpers import chat_health
    db = real_app[1]()
    member = make_member()
    db.execute(text("INSERT INTO chat_identities (user_id, matrix_user_id) VALUES (:u, :m)"),
               {"u": member.id, "m": f"@ruqqus_{member.id}:x"})
    db.commit()
    report = chat_health.run(db, None, fix=True)
    assert not report["ok"] and member.id in report["missing"]
    assert fake_chat["mark"] == {}


def test_only_admins_open_the_page(real_app, make_member, fake_chat):
    assert make_member().browser().get("/admin/chat").status_code in (403, 404)


def test_the_page_counts_the_chat_sign_outs_still_out_and_shows_a_late_one_in_red(real_app, make_member, fake_chat):
    """helpers/chat_signouts.py: the queue is retried for ever; a row older than an hour means the chat server isn't hearing."""
    import re
    import time
    db = real_app[1]()
    admin = make_member()
    db.execute(text("UPDATE users SET admin_level = 3 WHERE id = :i"), {"i": admin.id})
    db.execute(text("""INSERT INTO chat_device_signouts (user_id, mxid, scope, spare_after_ms, reason, created_utc, next_try_utc)
                       VALUES (:u, :m, 'all', 0, 'reset', :old, :later)"""),
               {"u": admin.id, "m": f"@ruqqus_{admin.id}:x", "old": int(time.time()) - 7200, "later": int(time.time()) + 3600})
    db.commit()
    try:
        page = admin.browser().get("/admin/chat").get_data(as_text=True)
        pending = int(re.search(r'id="chat-signouts-pending">(\d+)<', page).group(1))
        assert pending >= 1 and 'id="chat-signouts-late"' in page
    finally:
        db.execute(text("DELETE FROM chat_device_signouts WHERE user_id = :u"), {"u": admin.id})
        db.commit()


# --- the review of 2026-11-06 ---------------------------------------------------------------------------------------------------

HOLD_PROVISIONING = """
import sys, time, psycopg2
conn = psycopg2.connect(sys.argv[1])
cur = conn.cursor()
cur.execute("SELECT pg_advisory_xact_lock(%s, %s)", (7004, int(sys.argv[2])))
print("held", flush=True)
time.sleep(float(sys.argv[3]))
cur.execute("INSERT INTO chat_identities (user_id, matrix_user_id) VALUES (%s, %s)", (int(sys.argv[2]), sys.argv[4]))
conn.commit()
"""


def test_a_first_chat_that_another_request_is_making_is_waited_for_not_refused(real_app, make_member, fake_chat):
    """C3: the member's own page and someone starting a chat with them both find no record. The other request has registered the account
    (so the chat server has it) and has not yet committed the record: this one must wait for it and use it, not be refused as an
    orphan with every admin told."""
    import os
    import subprocess
    import sys
    import ruqqus.helpers.matrix_client as matrix
    member = make_member()
    mxid = matrix.mxid_for(member.id)
    fake_chat["accounts"].add(mxid)                                   # registered by the other request
    holder = subprocess.Popen([sys.executable, "-I", "-c", HOLD_PROVISIONING, os.environ["PG_TEST_DATABASE_URL"], str(member.id), "1.5", mxid],
                              stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        page = member.browser().get("/chat")
    finally:
        holder.wait(timeout=30)
    assert page.status_code == 200, page.get_data(as_text=True)[:300]
    assert fake_chat["made"] == []                                    # it did not register a second time
    from ruqqus.__main__ import r
    from ruqqus.helpers import chat_health
    assert not r.sismember(chat_health.REFUSED_KEY, member.id)        # and nobody was told a false alarm


def test_a_refused_flag_clears_once_the_chat_server_no_longer_has_the_account(real_app, make_member, fake_chat):
    """C1: restore the pair, or remove the stray account, and the red 'members refused chat' note goes by itself."""
    import ruqqus.helpers.matrix_client as matrix
    from ruqqus.__main__ import r
    from ruqqus.helpers import chat_health
    db = real_app[1]()
    still, gone = make_member(), make_member()
    fake_chat["accounts"].add(matrix.mxid_for(still.id))              # the stray account is still there
    for member in (still, gone):
        chat_health.note_refused(r, member.id)
    report = chat_health.run(db, r, fix=False)
    assert report["refused"] == [still.id] and not report["ok"]
    assert r.sismember(chat_health.REFUSED_KEY, still.id) and not r.sismember(chat_health.REFUSED_KEY, gone.id)
    fake_chat["accounts"].discard(matrix.mxid_for(still.id))          # the pair was restored
    assert chat_health.run(db, r, fix=False)["refused"] == []
    assert not r.sismember(chat_health.REFUSED_KEY, still.id)


def test_the_first_runs_write_never_lowers_a_mark_a_first_chat_raised_meanwhile(real_app, make_member, fake_chat):
    """C2: the check reads the mark, asks the chat server about up to 20 accounts, and then writes: a first chat that raised the mark in
    between must not be written over by the older copy."""
    import ruqqus.helpers.matrix_client as matrix
    from ruqqus.helpers import chat_health
    db = real_app[1]()
    member = make_member()
    db.execute(text("INSERT INTO chat_identities (user_id, matrix_user_id) VALUES (:u, :m)"),
               {"u": member.id, "m": matrix.mxid_for(member.id)})
    db.commit()
    # every chat record the earlier tests left is known to the fake chat server, or they would read as a split and skip the fix
    fake_chat["accounts"].update(m for (m,) in db.execute(text("SELECT matrix_user_id FROM chat_identities")))
    far = member.id + 1000
    real_exists = matrix.account_exists

    def exists_then_a_first_chat_raises_the_mark(mxid):
        answer = real_exists(mxid)
        if not fake_chat["mark"].get("high", 0) >= far:
            matrix.note_chat_id(far)                                  # another process, between the read and the write
        return answer
    fake_chat["mark"] = {"high": 1, "sites": {chat_health.site_key(): {"high": 1}}}
    import pytest
    mp = pytest.MonkeyPatch()
    mp.setattr(matrix, "account_exists", exists_then_a_first_chat_raises_the_mark)
    try:
        chat_health.run(db, None, fix=True)
    finally:
        mp.undo()
    assert chat_health.high_of(fake_chat["mark"]) >= far, fake_chat["mark"]
