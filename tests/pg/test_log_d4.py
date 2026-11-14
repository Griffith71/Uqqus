"""The Log, slice D4, on the real app and Postgres (the rules and the SQL are tested without Postgres in tests/test_log_d4.py): the public Log is
ordered by when things happened with the older-entries link still working; a reason a reader's word filter hides is not drawn to that reader on the
Log, a guild's log or a history page; closing an account empties what it erased itself and nothing else; the export gives the account its own
copies and what the Log says about it; and a guild ban says why."""
import re
import secrets
import time

from sqlalchemy import text

BASE = "http://localhost"
AGENT = "ci-log-d4"
PROFANE = "this is fucking nonsense"


def address():
    return f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"


def get(client, path, country=None):
    headers = {"User-Agent": AGENT, "Accept-Language": "en"}
    if country:
        headers["CF-IPCountry"] = country
    return client.get(path, base_url=BASE, headers=headers, environ_base={"REMOTE_ADDR": address()})


def post(client, member, path, **fields):
    return client.post(path, base_url=BASE, headers={"User-Agent": AGENT}, data={"formkey": member.formkey(client), **fields},
                       environ_base={"REMOTE_ADDR": address()})


def admin_of(make_member, level=3):
    admin = make_member()
    db = admin.db_session()
    db.execute(text("UPDATE users SET admin_level = :l WHERE id = :u"), {"l": level, "u": admin.id})
    db.commit()
    return admin


def guild(real_app):
    from ruqqus.classes import Board
    _, db_session = real_app
    db = db_session()
    board = Board(name="ci_d4_" + secrets.token_hex(3), description="a guild for the D4 test", description_html="<p>a guild</p>",
                  creator_id=1, subcat_id=0, created_utc=int(time.time()), color="23a9f2")
    db.add(board)
    db.commit()
    return board.id, board.name


def write_entry(real_app, action="hide_post_from_guild", actor=1, board_id=None, **kw):
    from ruqqus.helpers import action_log
    _, db_session = real_app
    db = db_session()
    entry = action_log.record(db, action, actor, board_id=board_id, role=action_log.GUILDMASTER, **kw)
    db.commit()
    return entry


def add_post(member, board_id, title="A post", body="<p>words</p>", age=3600):
    from ruqqus.classes import Submission, SubmissionAux
    from ruqqus.helpers.base36 import base36encode
    db = member.db_session()
    db.rollback()
    with member.app.test_request_context("/"):
        row = Submission(author_id=member.id, board_id=board_id, original_board_id=board_id, created_utc=int(time.time()) - age, post_public=True)
    db.add(row)
    db.flush()
    db.add(SubmissionAux(id=row.id, title=title, body=re.sub("<[^>]+>", "", body), body_html=body))
    db.commit()
    return row.id, base36encode(row.id)


def ids_on(page):
    return [int(i) for i in re.findall(r'class="card mb-2 p-3 log-entry" id="log-(\d+)"', page)]


# ---------------------------------------------------------------- the order

def test_the_public_log_is_in_the_order_things_happened_and_the_link_to_older_entries_follows_it(real_app):
    board_id, board_name = guild(real_app)
    now = int(time.time())
    # a backfill run after live ones exist: the OLD entries get the higher ids
    live = write_entry(real_app, board_id=board_id, created_utc=now - 100, reason="live")
    old_a = write_entry(real_app, board_id=board_id, created_utc=now - 5 * 86400, reason="old a")
    old_b = write_entry(real_app, board_id=board_id, created_utc=now - 4 * 86400, reason="old b")
    app, _ = real_app
    client = app.test_client()
    page = get(client, f"/log?guild={board_name}").get_data(as_text=True)
    assert ids_on(page) == [live, old_b, old_a]
    # older entries: from the middle one on, still in time order
    again = get(client, f"/log?guild={board_name}&before={old_b}").get_data(as_text=True)
    assert ids_on(again) == [old_a]


# ---------------------------------------------------------------- the word filter

def test_a_rated_reason_is_not_drawn_to_a_reader_whose_filter_hides_it(real_app):
    board_id, board_name = guild(real_app)
    rude = write_entry(real_app, board_id=board_id, reason=PROFANE)
    calm = write_entry(real_app, board_id=board_id, reason="Off topic here.")
    app, _ = real_app
    us = get(app.test_client(), f"/log?guild={board_name}", country="US").get_data(as_text=True)
    assert PROFANE in us and "Off topic here." in us, "an ordinary reader's Standard filter hides only the extreme words"
    # a visitor in the UK is on the Child level until an age check says otherwise: profanity is absent for them, with no placeholder
    uk = get(app.test_client(), f"/log?guild={board_name}", country="GB").get_data(as_text=True)
    assert PROFANE not in uk and "Off topic here." in uk
    assert f'id="log-{rude}"' in uk and f'id="log-{calm}"' in uk, "the entry itself is still there: only the words leave"


def test_the_guilds_own_log_and_its_json_leave_out_a_reason_the_filter_hides(real_app):
    board_id, board_name = guild(real_app)
    from ruqqus.helpers import action_log
    from ruqqus.classes import ModAction
    _, db_session = real_app
    db = db_session()
    ma = ModAction(user_id=1, board_id=board_id, kind="hide_post_from_guild", note="")
    db.add(ma)
    db.flush()
    action_log.annotate(db, ma.id, reason=PROFANE)
    db.commit()
    app, _ = real_app
    page = get(app.test_client(), f"/+{board_name}/mod/log", country="GB").get_data(as_text=True)
    assert PROFANE not in page
    assert PROFANE in get(app.test_client(), f"/+{board_name}/mod/log", country="US").get_data(as_text=True)


def test_an_edit_summary_is_rated_and_the_history_page_hides_it_from_a_child_level_reader(real_app, make_member):
    from ruqqus.classes import ContentEditHistory
    board_id, _ = guild(real_app)
    author = make_member()
    post_id, base36 = add_post(author, board_id)
    db = author.db_session()
    db.add(ContentEditHistory(actor_id=author.id, target_submission_id=post_id, board_id=board_id, action="edit", summary=PROFANE,
                              previous_title="Before", previous_body="before", previous_body_html="<p>before</p>"))
    db.commit()
    stored = db.execute(text("SELECT text_severity FROM content_edit_history WHERE target_submission_id = :p"), {"p": post_id}).scalar()
    assert stored >= 1, "rated by the history model's before-insert hook, whoever wrote the row"
    app, _ = real_app
    assert PROFANE not in get(app.test_client(), f"/post/{base36}/history", country="GB").get_data(as_text=True)
    assert PROFANE in get(app.test_client(), f"/post/{base36}/history", country="US").get_data(as_text=True)


# ---------------------------------------------------------------- closing an account, and the export

def test_closing_an_account_empties_what_it_erased_and_the_export_holds_its_own_copies(real_app, make_member):
    from ruqqus.helpers import account_cleanup, action_log, data_export
    member = make_member()
    board_id, _ = guild(real_app)
    db = member.db_session()

    class Item:
        author_id, title, url, body, body_html = member.id, "A title", None, "words I erased", "<p>words I erased</p>"
    Item.board_id = board_id
    own = action_log.keep_snapshot(db, action_log.snapshot_of(Item, kind=action_log.GRACE))
    hide = action_log.keep_snapshot(db, action_log.snapshot_of(Item, kind=action_log.HIDE))
    legal = action_log.keep_snapshot(db, action_log.snapshot_of(Item, legal_hold=True))
    exiled = write_entry(real_app, action="exile_user", actor=1, board_id=board_id, target_user_id=member.id, reason="Spam links.")
    db.commit()

    exported = data_export.build(db, member.id)
    assert [c["id"] for c in exported["private_copies"]] == [own, hide], "never a copy kept for a legal duty"
    assert exported["private_copies"][0]["body"] == "words I erased"
    assert [e["id"] for e in exported["log_entries_about_you"]] == [exiled]

    account_cleanup.close_account(db, member.id, int(time.time()))
    db.commit()
    body = lambda i: db.execute(text("SELECT body FROM log_snapshots WHERE id = :i"), {"i": i}).scalar()
    assert body(own) is None and body(hide) == "words I erased" and body(legal) == "words I erased"


# ---------------------------------------------------------------- a guild ban says why

def test_a_guild_ban_needs_a_public_reason(real_app, make_member):
    boss = admin_of(make_member, 4)
    board_id, board_name = guild(real_app)
    client = boss.browser()
    from ruqqus.helpers.base36 import base36encode
    refused = post(client, boss, f"/api/ban_guild/{base36encode(board_id)}", reason="   ")
    assert refused.status_code == 400
    _, db_session = real_app
    db = db_session()
    db.rollback()
    assert db.execute(text("SELECT is_banned FROM boards WHERE id = :b"), {"b": board_id}).scalar() in (False, None)
    done = post(client, boss, f"/api/ban_guild/{base36encode(board_id)}", reason="Run for spam.")
    assert done.status_code in (302, 303)
    db.rollback()
    assert db.execute(text("SELECT is_banned FROM boards WHERE id = :b"), {"b": board_id}).scalar() is True
    assert db.execute(text("SELECT reason FROM log_entries WHERE action = 'ban_guild' AND board_id = :b"), {"b": board_id}).scalar() == "Run for spam."


# ---------------------------------------------------------------- a dead address is a banned page, not a broken link

def test_a_suspended_accounts_address_answers_its_banned_page_not_a_missing_one(real_app, make_member):
    """L5 of the audit said a sanction entry links an address that 404s: it answers the banned page, which is what its reader wants. Only the
    account's sub-pages (its lists, its own log) refuse."""
    target = make_member()
    db = target.db_session()
    db.execute(text("UPDATE users SET is_banned = 1 WHERE id = :u"), {"u": target.id})
    db.commit()
    app, _ = real_app
    assert get(app.test_client(), f"/@{target.username}").status_code == 200
    assert get(app.test_client(), f"/@{target.username}/log").status_code == 404
