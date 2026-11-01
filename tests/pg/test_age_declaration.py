"""The age declaration on the real app and a real Postgres (helpers/age_declaration.py has the rules): sign-up refuses
without an answer, a child account can't leave the Child filter by any route, the notice, and an admin changing an answer."""
import hmac
import importlib.util
import os
import secrets
import time
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

BASE = "http://localhost"
AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15"
HEADERS = {"User-Agent": AGENT, "Accept-Language": "en-GB,en;q=0.9"}
ADULT_CHOICE = "I'm 18 or over"


def address():
    return f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"


def get(client, path):
    return client.get(path, base_url=BASE, headers=HEADERS, environ_base={"REMOTE_ADDR": address()})


def post(client, path, **form):
    return client.post(path, base_url=BASE, headers=HEADERS, environ_base={"REMOTE_ADDR": address()}, data=form)


def answer(member, client, **form):
    return post(client, "/settings/age_declaration", formkey=member.formkey(client), **form)


def row(member):
    db = member.db_session()
    db.rollback()
    return db.execute(text("SELECT age_declared, age_declared_utc, filter_level, all_shows_sensitive FROM users "
                           "WHERE id = :u"), {"u": member.id}).first()


def make_child(member):
    db = member.db_session()
    db.execute(text("UPDATE users SET age_declared = 2, age_declared_utc = 5, filter_level = 2 WHERE id = :u"), {"u": member.id})
    db.commit()


def make_adult(member):
    db = member.db_session()
    db.execute(text("UPDATE users SET age_declared = 1, age_declared_utc = 5 WHERE id = :u"), {"u": member.id})
    db.commit()


# ---------------------------------------------------------------- sign-up

def sign_up_form(client, **fields):
    now = str(int(time.time()) - 60)
    token = secrets.token_hex(16)
    with client.session_transaction() as session:
        session["signup_token"] = token
    key = hmac.new(key=bytes(os.environ["MASTER_KEY"], "utf-16"), msg=bytes(now + token + AGENT, "utf-16"),
                   digestmod="md5").hexdigest()
    name = "ci" + "".join(secrets.choice("abcdefghjkmnoprstuvy") for _ in range(8))
    return dict(formkey=key, now=now, username=name, password="ci-Password-1", password_confirm="ci-Password-1", **fields)


def signed_up(real_app, username):
    _, db_session = real_app
    db = db_session()
    db.rollback()
    return db.execute(text("SELECT age_declared, age_declared_utc, filter_level, hide_offensive, all_shows_sensitive, "
                           "created_utc FROM users WHERE username = :u"), {"u": username}).first()


@pytest.mark.parametrize("given", [{}, {"age_declaration": ""}, {"age_declaration": "0"}, {"age_declaration": "3"}])
def test_sign_up_is_refused_without_an_answer(real_app, given):
    app, _ = real_app
    client = app.test_client()
    form = sign_up_form(client, **given)
    made = post(client, "/signup", **form)
    assert made.status_code == 302 and "/signup" in made.headers["Location"] and "error=" in made.headers["Location"]
    assert signed_up(real_app, form["username"]) is None


def test_the_sign_up_page_asks_and_answers_nothing_for_them(real_app):
    app, _ = real_app
    page = get(app.test_client(), "/signup").get_data(as_text=True)
    assert 'name="age_declaration" value="1" required' in page and 'name="age_declaration" value="2" required' in page
    assert ADULT_CHOICE in page and "setting this up for my child (13 to 17)" in page


def test_an_adult_signs_up_on_the_standard_filter(real_app):
    app, _ = real_app
    client = app.test_client()
    form = sign_up_form(client, age_declaration="1")
    assert post(client, "/signup", **form).status_code == 302
    made = signed_up(real_app, form["username"])
    assert made.age_declared == 1 and made.age_declared_utc > 0 and made.filter_level == 1
    assert abs(made.age_declared_utc - made.created_utc) <= 60, "or the Terms notice would count them as an old account"


def test_a_child_account_signs_up_on_the_child_filter_and_sees_no_notice(real_app):
    app, _ = real_app
    client = app.test_client()
    form = sign_up_form(client, age_declaration="2")
    assert post(client, "/signup", **form).status_code == 302
    made = signed_up(real_app, form["username"])
    assert made.age_declared == 2 and made.filter_level == 2 and made.hide_offensive is True and made.all_shows_sensitive is False
    assert 'id="age-notice"' not in get(client, "/all").get_data(as_text=True)


# ---------------------------------------------------------------- the notice's way back

def test_a_page_drawn_by_a_post_sends_the_answer_back_somewhere_that_opens(make_member):
    member = make_member()
    client = member.browser()
    refused = post(client, "/api/comment", formkey=member.formkey(client), parent_fullname="t2_zzzzzz", body="hi")
    page = refused.get_data(as_text=True)
    assert refused.status_code >= 400 and 'id="age-notice"' in page, refused.status_code
    assert 'name="next" value="/settings/content"' in page and 'name="next" value="/api/comment"' not in page
    back = answer(member, client, age_declaration="1", next="/settings/content")
    assert back.status_code == 303 and back.headers["Location"].endswith("/settings/content")
    assert get(client, "/settings/content").status_code == 200


def test_a_page_opened_normally_is_where_the_answer_comes_back_to(make_member):
    member = make_member()
    page = get(member.browser(), "/browse?sort=new").get_data(as_text=True)
    assert 'name="next" value="/browse?sort=new"' in page


# ---------------------------------------------------------------- the lock

def test_a_child_account_cant_leave_the_child_filter_by_either_address_even_with_its_password(make_member):
    member = make_member()
    make_child(member)
    client = member.browser()
    for path in ("/settings/profile", "/api/vue/settings/profile"):
        for level in ("0", "1"):
            refused = post(client, path, formkey=member.formkey(client), filter_level=level, password=member.password)
            assert refused.status_code == 403, (path, level)
            assert row(member).filter_level == 2


def test_a_refused_filter_change_changes_nothing_else_sent_with_it(make_member):
    member = make_member()
    make_child(member)
    client = member.browser()
    post(client, "/settings/profile", formkey=member.formkey(client), filter_level="1", password=member.password, private="true")
    assert member.user().is_private is not True


def test_a_child_account_cant_turn_on_sensitive_content(make_member):
    member = make_member()
    make_child(member)
    client = member.browser()
    assert post(client, "/settings/all_shows_sensitive", formkey=member.formkey(client), on="true").status_code == 403
    assert row(member).all_shows_sensitive is False
    assert post(client, "/settings/all_shows_sensitive", formkey=member.formkey(client), on="false").status_code == 200


def test_an_adults_filter_is_untouched_by_the_lock(make_member):
    member = make_member()
    make_adult(member)
    client = member.browser()
    assert post(client, "/settings/profile", formkey=member.formkey(client), filter_level="2").status_code == 200
    assert row(member).filter_level == 2
    assert post(client, "/settings/profile", formkey=member.formkey(client), filter_level="1",
                password="wrong").status_code == 403, "the old rule still holds"
    assert post(client, "/settings/profile", formkey=member.formkey(client), filter_level="1",
                password=member.password).status_code == 200
    assert row(member).filter_level == 1


def test_the_database_refuses_it_whatever_a_route_forgets(make_member):
    member = make_member()
    make_child(member)
    db = member.db_session()
    for change in ("filter_level = 1", "all_shows_sensitive = true"):
        with pytest.raises(IntegrityError):
            db.execute(text(f"UPDATE users SET {change} WHERE id = :u"), {"u": member.id})
            db.commit()
        db.rollback()
    with pytest.raises(IntegrityError):
        db.execute(text("UPDATE users SET age_declared = 2 WHERE id = :u AND filter_level <> 2"), {"u": member.id})
        db.execute(text("UPDATE users SET age_declared = 3 WHERE id = :u"), {"u": member.id})
        db.commit()
    db.rollback()


def test_settings_shows_the_state_plainly(make_member):
    member = make_member()
    client = member.browser()
    page = get(client, "/settings/content").get_data(as_text=True)
    assert "You haven't told us yet." in page and 'action="/settings/age_declaration"' in page

    make_adult(member)
    page = get(member.browser(), "/settings/content").get_data(as_text=True)
    assert "marked 18 or over since" in page and 'action="/settings/age_declaration"' not in page

    other = make_member()
    make_child(other)
    page = get(other.browser(), "/settings/content").get_data(as_text=True)
    assert "This is a child account, set up by a parent or guardian" in page
    select = page.split('id="filterlevel"', 1)[1].split(">", 1)[0]
    assert "disabled" in select
    assert 'id="filterlevel-locked"' in page
    switch = page.split('id="all-shows-sensitive"', 1)[1].split(">", 1)[0]
    assert " disabled" in switch and " checked" not in switch


# ---------------------------------------------------------------- the notice

def test_a_member_from_before_is_asked_on_a_page_that_still_works(make_member):
    member = make_member()
    client = member.browser()
    served = get(client, "/all")
    assert served.status_code == 200
    page = served.get_data(as_text=True)
    assert 'id="age-notice"' in page and ADULT_CHOICE in page
    assert 'id="posts"' in page or 'class="posts' in page or "You're all caught up" in page or "main-content-col" in page
    assert 'name="next" value="/all"' in page


def test_no_notice_in_a_side_panels_frame(make_member):
    member = make_member()
    client = member.browser()
    for path in ("/notifications?embed=1", "/all?embed=1"):
        assert 'id="age-notice"' not in get(client, path).get_data(as_text=True), path
    assert 'id="age-notice"' in get(client, "/notifications").get_data(as_text=True)


def test_no_notice_for_a_visitor(real_app):
    app, _ = real_app
    assert 'id="age-notice"' not in get(app.test_client(), "/all").get_data(as_text=True)


def test_an_adult_answer_ends_the_notice_for_good(make_member):
    member = make_member()
    client = member.browser()
    saved = answer(member, client, age_declaration="1", next="/all")
    assert saved.status_code == 303 and saved.headers["Location"].endswith("/all")
    now = row(member)
    assert now.age_declared == 1 and now.age_declared_utc > 0 and now.filter_level == 1
    assert 'id="age-notice"' not in get(client, "/all").get_data(as_text=True)
    assert 'id="age-notice"' not in get(member.browser(), "/all").get_data(as_text=True), "another browser too"


def test_answering_child_asks_first_what_it_means(make_member):
    member = make_member()
    client = member.browser()
    asked = answer(member, client, age_declaration="2", next="/all")
    assert asked.status_code == 200
    page = asked.get_data(as_text=True)
    assert "stay on the Child word filter" in page and 'name="confirm" value="1"' in page
    assert 'id="age-notice"' not in page
    assert row(member).age_declared is None and row(member).filter_level == 1, "nothing saved yet"

    saved = answer(member, client, age_declaration="2", confirm="1", next="/all")
    assert saved.status_code == 303
    now = row(member)
    assert now.age_declared == 2 and now.filter_level == 2 and now.all_shows_sensitive is False
    assert 'id="age-notice"' not in get(client, "/all").get_data(as_text=True)
    assert post(client, "/settings/profile", formkey=member.formkey(client), filter_level="1",
                password=member.password).status_code == 403, "the lock holds for an answer given through the notice"


def test_answering_child_turns_off_a_choice_made_before(make_member):
    member = make_member()
    db = member.db_session()
    db.execute(text("UPDATE users SET all_shows_sensitive = true, filter_level = 0 WHERE id = :u"), {"u": member.id})
    db.commit()
    client = member.browser()
    assert answer(member, client, age_declaration="2", confirm="1").status_code == 303
    now = row(member)
    assert now.filter_level == 2 and now.all_shows_sensitive is False


@pytest.mark.parametrize("first, second", [("1", "2"), ("2", "1"), ("1", "1")])
def test_an_answer_cant_be_changed_by_the_member(make_member, first, second):
    member = make_member()
    client = member.browser()
    answer(member, client, age_declaration=first, confirm="1")
    before = row(member)
    again = answer(member, client, age_declaration=second, confirm="1")
    assert again.status_code == 409
    assert row(member) == before


def test_an_answer_needs_the_form_key_and_a_real_choice(make_member):
    member = make_member()
    client = member.browser()
    assert post(client, "/settings/age_declaration", age_declaration="1").status_code in (302, 401, 403)
    assert row(member).age_declared is None
    assert answer(member, client, age_declaration="9").status_code == 400
    assert answer(member, client).status_code == 400
    assert row(member).age_declared is None


def test_a_visitor_cant_answer(real_app):
    app, _ = real_app
    assert post(app.test_client(), "/settings/age_declaration", age_declaration="1").status_code in (302, 401, 403)


@pytest.mark.parametrize("target", ["https://evil.example/", "//evil.example/", "/\\evil.example"])
def test_an_answer_never_sends_the_browser_to_another_site(make_member, target):
    member = make_member()
    client = member.browser()
    saved = answer(member, client, age_declaration="1", next=target)
    assert saved.status_code == 303 and saved.headers["Location"].endswith("/settings/content")


# ---------------------------------------------------------------- an admin

def admin(make_member, level=3):
    member = make_member()
    db = member.db_session()
    db.execute(text("UPDATE users SET admin_level = :l, age_declared = 1 WHERE id = :u"), {"u": member.id, "l": level})
    db.commit()
    return member


def change(by, client, target, **form):
    return post(client, "/admin/age_declaration", formkey=by.formkey(client), username=target.username, **form)


def test_an_admin_marks_a_child_account_and_it_is_logged_for_the_staff_and_told(make_member):
    boss, member = admin(make_member), make_member()
    db = member.db_session()
    db.execute(text("UPDATE users SET filter_level = 0, all_shows_sensitive = true WHERE id = :u"), {"u": member.id})
    db.commit()
    client = boss.browser()
    assert change(boss, client, member, answer="2", reason="their parent wrote in").status_code == 302
    now = row(member)
    assert now.age_declared == 2 and now.filter_level == 2 and now.all_shows_sensitive is False

    db.rollback()
    entry = db.execute(text("SELECT action, actor_id, actor_role, audience, reason, staff_note FROM log_entries "
                            "WHERE target_user_id = :u AND action = 'age_declaration' ORDER BY id DESC"), {"u": member.id}).first()
    assert entry.actor_id == boss.id and entry.actor_role == "admin" and entry.audience == 2
    assert entry.reason == "their parent wrote in" and entry.staff_note == "not asked to child account"

    told = db.execute(text("SELECT a.notice_msgid FROM notifications n JOIN comments_aux a ON a.id = n.comment_id "
                           "WHERE n.user_id = :u ORDER BY n.id DESC"), {"u": member.id}).first()
    assert "child account" in told.notice_msgid


def test_an_admin_takes_an_account_off_child_and_the_member_lowers_the_filter(make_member):
    boss, member = admin(make_member), make_member()
    make_child(member)
    client = boss.browser()
    assert change(boss, client, member, answer="1").status_code == 302
    assert row(member).age_declared == 1 and row(member).filter_level == 2, "the filter is the member's to lower"
    mine = member.browser()
    assert post(mine, "/settings/profile", formkey=member.formkey(mine), filter_level="1",
                password=member.password).status_code == 200
    assert row(member).filter_level == 1


def test_an_admin_clears_an_answer_and_the_member_is_asked_again(make_member):
    boss, member = admin(make_member), make_member()
    make_adult(member)
    client = boss.browser()
    assert change(boss, client, member, answer="").status_code == 302
    assert row(member).age_declared is None and row(member).age_declared_utc == 0
    assert 'id="age-notice"' in get(member.browser(), "/all").get_data(as_text=True)


def test_only_an_admin_above_the_account_may_change_it(make_member):
    low, member = admin(make_member, level=2), make_member()
    client = low.browser()
    assert change(low, client, member, answer="2").status_code in (403, 404)
    assert row(member).age_declared is None

    mine = member.browser()
    assert change(member, mine, member, answer="1").status_code in (403, 404), "a member can't use the admin's way"
    assert row(member).age_declared is None

    boss, peer = admin(make_member), admin(make_member)
    assert change(boss, boss.browser(), peer, answer="2").status_code == 404
    assert change(boss, boss.browser(), boss, answer="2").status_code == 404
    assert row(peer).age_declared == 1


def test_an_admin_cant_mark_a_bot_or_send_a_made_up_answer(make_member):
    boss, member = admin(make_member), make_member()
    client = boss.browser()
    assert change(boss, client, member, answer="9").status_code == 400
    db = member.db_session()
    db.execute(text("UPDATE users SET is_bot = true WHERE id = :u"), {"u": member.id})
    db.commit()
    assert change(boss, client, member, answer="1").status_code == 404
    assert row(member).age_declared is None


def test_the_panel_is_drawn_for_an_admin_only(make_member):
    boss, member = admin(make_member), make_member()
    assert 'action="/admin/age_declaration"' in get(boss.browser(), f"/@{member.username}").get_data(as_text=True)
    other = make_member()
    assert 'action="/admin/age_declaration"' not in get(other.browser(), f"/@{member.username}").get_data(as_text=True)


def test_nobody_else_can_read_an_answer(make_member):
    member, other = make_member(), make_member()
    make_child(member)
    make_adult(other)                                       # or the page carries the question, which names a child account
    page = get(other.browser(), f"/@{member.username}").get_data(as_text=True)
    assert "child account" not in page.lower()
    data = get(other.browser(), f"/@{member.username}.json")
    if data.status_code == 200:
        assert "age_declared" not in data.get_data(as_text=True)


# ---------------------------------------------------------------- what is kept

def test_the_data_export_and_closing_the_account(make_member):
    from ruqqus.helpers import account_cleanup, data_export
    member = make_member()
    make_child(member)
    db = member.db_session()
    exported = data_export.build(db, member.id)
    assert exported["age"] == [{"age_declared": "a child account made by a parent or guardian", "age_declared_utc": 5}]
    db.rollback()
    account_cleanup.close_account(db, member.id, int(time.time()))
    db.commit()
    assert row(member).age_declared is None and row(member).age_declared_utc == 0


def test_closing_an_account_takes_an_admins_note_and_notice_with_it(make_member):
    from ruqqus.helpers import account_cleanup
    boss, member, other = admin(make_member), make_member(), make_member()
    client = boss.browser()
    assert change(boss, client, member, answer="2", reason="their mother wrote to us: she is 14").status_code == 302
    assert change(boss, client, other, answer="2", reason="another one").status_code == 302

    def of(who):
        session = who.db_session()
        session.rollback()
        entry = session.execute(text("SELECT reason, staff_note FROM log_entries WHERE action = 'age_declaration' "
                                     "AND target_user_id = :u"), {"u": who.id}).first()
        notices = session.execute(text("SELECT a.body, a.body_html, a.notice_msgid, a.notice_params FROM notifications n "
                                       "JOIN comments_aux a ON a.id = n.comment_id WHERE n.user_id = :u AND "
                                       "a.notice_msgid LIKE 'An admin marked%'"), {"u": who.id}).all()
        return entry, notices

    entry, notices = of(member)
    assert entry.reason == "their mother wrote to us: she is 14" and "child account" in entry.staff_note and len(notices) == 1

    db = member.db_session()
    db.rollback()
    account_cleanup.close_account(db, member.id, int(time.time()))
    db.commit()

    entry, notices = of(member)
    assert entry is not None, "the entry stays: the Log is append-only"
    assert entry.reason == "" and "child" not in entry.staff_note and "not asked" not in entry.staff_note
    assert notices == []
    left = db.execute(text("SELECT count(*) FROM comments_aux WHERE body LIKE 'An admin marked this as a child account%' "
                           "AND id IN (SELECT comment_id FROM notifications WHERE user_id = :u)"), {"u": member.id}).scalar()
    assert left == 0

    page = get(client, f"/admin/log?action=age_declaration&target={member.username}").get_data(as_text=True)
    assert "she is 14" not in page and "child account" not in page

    entry, notices = of(other)
    assert entry.reason == "another one" and "child account" in entry.staff_note and len(notices) == 1, "nobody else's"


def test_the_terms_notice_is_sent_once_and_only_by_the_one_off(make_member):
    script = Path(__file__).resolve().parents[2] / "scripts" / "notify_terms_age.py"
    spec = importlib.util.spec_from_file_location("notify_terms_age_for_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    member, child = make_member(), make_member()
    make_child(child)
    db = member.db_session()
    db.execute(text("UPDATE users SET created_utc = 5 WHERE id = :u"), {"u": child.id})   # signed up at the moment it answered
    db.commit()

    def told(who):
        session = who.db_session()
        session.rollback()
        return session.execute(text("SELECT count(*) FROM notifications n JOIN comments_aux a ON a.id = n.comment_id "
                                    "WHERE n.user_id = :u AND a.notice_msgid LIKE 'We''ve changed the Age section%'"),
                               {"u": who.id}).scalar()

    assert module.run(dry_run=True) >= 1
    assert told(member) == 0, "a dry run sends nothing"
    assert module.run() >= 1
    assert told(member) == 1 and told(child) == 0
    module.run()
    assert told(member) == 1, "run again, told once"
