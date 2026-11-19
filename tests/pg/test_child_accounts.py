"""Parent and child accounts on the real app and a real Postgres (helpers/child_accounts.py, routes/children_pages.py; CLAUDE.md "Age declaration and the child
lock"): who may make one and how many, what a parent's switches do to what the child may do and to who may reach them, the child's requests, every road that
releases or removes a child, and a parent's ban or deletion taking its children with it."""
import contextlib
import re
import secrets
import time

import pytest
from sqlalchemy import text


@pytest.fixture(autouse=True)
def children_on(monkeypatch):
    """These tests are about child and parent accounts, which are switched off unless CHILD_ACCOUNTS_ENABLED is set (the owner,
    2026-11-19: accounts are for adults only). tests/pg/test_adults_only.py holds the site with them off."""
    monkeypatch.setenv("CHILD_ACCOUNTS_ENABLED", "1")

BASE = "http://localhost"
AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15"
YEAR = time.gmtime().tm_year


def address():
    return f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"


def headers(country=None):
    found = {"User-Agent": AGENT, "Accept-Language": "en-GB,en;q=0.9"}
    if country:
        found["CF-IPCountry"] = country
    return found


def get(client, path, country=None):
    return client.get(path, base_url=BASE, headers=headers(country), environ_base={"REMOTE_ADDR": address()})


def post(client, path, country=None, **form):
    return client.post(path, base_url=BASE, headers=headers(country), environ_base={"REMOTE_ADDR": address()}, data=form)


def page_of(client, path, country=None):
    """A page, through one redirect to the item's own address (a post's address names its author)."""
    answer = get(client, path, country)
    if answer.status_code in (301, 302, 303, 308):
        answer = get(client, answer.headers["Location"].replace(BASE, ""), country)
    return answer


def row(member_or_id, *names, db=None):
    db = db or member_or_id.db_session()
    db.rollback()
    uid = getattr(member_or_id, "id", member_or_id)
    return db.execute(text(f"SELECT {', '.join(names)} FROM users WHERE id = :u"), {"u": uid}).first()


def set_columns(member, **values):
    db = member.db_session()
    db.execute(text("UPDATE users SET " + ", ".join(f"{k} = :{k}" for k in values) + " WHERE id = :u"), dict(values, u=member.id))
    db.commit()


@contextlib.contextmanager
def before_the_lock(member, kid):
    """The child account as it was before it was locked, for the tests that need something it "wrote": a child account only reads (CLAUDE.md "Age
    declaration and the child lock"), so a post or a comment of its own is made with the lock lifted in SQL and put back as it was after."""
    db = member.db_session()
    db.execute(text("UPDATE users SET age_declared = 1, age_lock = NULL, filter_level = 1 WHERE id = :u"), {"u": kid.id})
    db.commit()
    try:
        yield
    finally:
        db.rollback()
        db.execute(text("UPDATE users SET age_declared = 2, age_lock = 2, filter_level = 2, all_shows_sensitive = false WHERE id = :u"), {"u": kid.id})
        db.commit()


def parent(make_member):
    member = make_member()
    set_columns(member, age_declared=1, age_declared_utc=5)
    return member


def new_child(member, client=None, country=None, name=None, year=YEAR - 15, month="6", **over):
    client = client or member.browser()
    name = name or "kid" + secrets.token_hex(3)
    form = dict(formkey=member.formkey(client), username=name, password="Child-Password-1", password_confirm="Child-Password-1", birth_year=str(year),
                birth_month=month, terms="1")
    form.update(over)
    return post(client, "/settings/children/new", country, **form), name


def user_id(member, name):
    db = member.db_session()
    db.rollback()
    return db.execute(text("SELECT id FROM users WHERE lower(username) = lower(:n)"), {"n": name}).scalar()


class Kid:
    """The child account's own row and a browser signed in as it, for the tests that act as the child."""

    def __init__(self, app, db_session, id_, name):
        self.app, self.db_session, self.id, self.username, self.password = app, db_session, id_, name, "Child-Password-1"

    def user(self):
        from ruqqus.classes import User
        db = self.db_session()
        db.expire_all()
        return db.query(User).filter_by(id=self.id).first()

    def browser(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = self.id
            session["login_nonce"] = self.user().login_nonce
            session["session_id"] = secrets.token_hex(16)
        return client

    @staticmethod
    def formkey(client):
        from ruqqus.helpers.security import generate_hash
        with client.session_transaction() as session:
            return generate_hash(f"{session['session_id']}+{session['user_id']}+{session['login_nonce']}")


@pytest.fixture
def made(make_member, real_app):
    """A factory: `made()` is (parent, kid) with the kid made through the real route."""
    app, db_session = real_app

    def make(country=None, **over):
        mum = parent(make_member)
        answer, name = new_child(mum, country=country, **over)
        assert answer.status_code == 303 and "msg=" in answer.headers["Location"], answer.headers.get("Location")
        return mum, Kid(app, db_session, user_id(mum, name), name)
    return make


def controls(member, kid_id):
    db = member.db_session()
    db.rollback()
    r = db.execute(text("SELECT chat, spaces, money, anonymous_guilds, ask_release_utc FROM child_controls WHERE child_id = :c"),
                   {"c": kid_id}).first()
    return None if r is None else r


def switch(mum, kid, name, on):
    client = mum.browser()
    return post(client, f"/settings/children/{kid.username}/controls", formkey=mum.formkey(client), control=name, on="true" if on else "false")


# ---------------------------------------------------------------- making one

def test_an_adult_makes_a_child_account_with_the_lock_the_filter_and_nothing_on(made):
    mum, kid = made()
    r = row(kid.id, "age_declared", "age_lock", "filter_level", "all_shows_sensitive", "following_visibility", "tos_agreed_utc",
            "age_assured_utc", "email", db=mum.db_session())
    assert (r.age_declared, r.age_lock, r.filter_level, r.all_shows_sensitive) == (2, 2, 2, False)
    assert r.following_visibility == 2 and r.tos_agreed_utc > 0 and r.age_assured_utc == 0 and r.email is None
    c = controls(mum, kid.id)
    assert tuple(c)[:4] == (False, False, False, False) and c.ask_release_utc == 0
    assert "posting" not in c._fields and "commenting" not in c._fields and "ask_private_utc" not in c._fields, "a child account only reads: no switch for it"
    db = mum.db_session()
    db.rollback()
    assert db.execute(text("SELECT parent_id FROM account_links WHERE child_id = :c"), {"c": kid.id}).scalar() == mum.id


def test_the_child_is_told_what_the_parent_can_see(made):
    mum, kid = made()
    db = mum.db_session()
    db.rollback()
    told = db.execute(text("SELECT a.notice_msgid FROM notifications n JOIN comments_aux a ON a.id = n.comment_id WHERE n.user_id = :u"), {"u": kid.id}).all()
    assert any("never shows them what your chats say" in (t[0] or "") for t in told)


def test_the_child_signs_in_with_what_the_parent_chose(made):
    mum, kid = made()
    visitor = kid.app.test_client()
    answer = post(visitor, "/login", username=kid.username, password=kid.password)
    assert answer.status_code == 302 and "/login" not in answer.headers["Location"]


@pytest.mark.parametrize("over, wanted", [({"year": YEAR - 12}, "Nobody under 13"), ({"year": YEAR - 30}, "18 or over"), ({"year": "abc"}, "month and year"),
                                          ({"month": "13"}, "month and year"), ({"terms": ""}, "Terms for them"), ({"password_confirm": "other-pass-1"}, "did not match"),
                                          ({"password": "short", "password_confirm": "short"}, "between 8 and 100"), ({"username": "no spaces!"}, "Invalid username")])
def test_a_form_that_isnt_right_is_refused_and_nothing_is_made(make_member, over, wanted):
    mum = parent(make_member)
    answer, name = new_child(mum, **over)
    assert answer.status_code == 303 and "error=" in answer.headers["Location"]
    from urllib.parse import unquote
    assert wanted in unquote(answer.headers["Location"])
    assert user_id(mum, over.get("username") or name) is None
    db = mum.db_session()
    db.rollback()
    assert db.execute(text("SELECT COUNT(*) FROM account_links WHERE parent_id = :p"), {"p": mum.id}).scalar() == 0


def test_a_taken_username_is_refused(made, make_member):
    mum, kid = made()
    other = parent(make_member)
    answer, _ = new_child(other, name=kid.username.upper())
    assert "error=" in answer.headers["Location"]


def test_in_australia_nobody_under_16_even_with_a_parent_but_a_german_teen_is_welcome(make_member):
    mum = parent(make_member)
    set_columns(mum, age_assured_utc=int(time.time()))          # both are strict countries: the parent's age is confirmed
    answer, name = new_child(mum, country="AU", year=YEAR - 15)
    assert "error=" in answer.headers["Location"] and user_id(mum, name) is None
    answer, name = new_child(mum, country="DE", year=YEAR - 15)
    assert "msg=" in answer.headers["Location"] and user_id(mum, name)


def test_the_birth_month_and_year_are_not_kept(made):
    mum, kid = made(year=YEAR - 14, month="3")
    db = mum.db_session()
    db.rollback()
    for table, column in db.execute(text("SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'public'")).all():
        assert "birth" not in column.lower(), (table, column)


@pytest.mark.parametrize("who", ["child", "locked_alone", "bot", "not_asked"])
def test_only_a_confirmed_adult_may(make_member, who):
    member = make_member()
    set_columns(member, **{"child": dict(age_declared=2, age_lock=2, age_declared_utc=5, filter_level=2), "locked_alone": dict(age_declared=2, age_lock=1, age_declared_utc=5, filter_level=2),
                           "bot": dict(age_declared=1, is_bot=True), "not_asked": dict(age_declared=None)}[who])
    answer, name = new_child(member)
    assert "error=" in answer.headers["Location"] and user_id(member, name) is None


def test_in_a_strict_country_the_age_must_be_confirmed_first(make_member):
    member = parent(make_member)
    answer, name = new_child(member, country="GB")
    assert "error=" in answer.headers["Location"] and user_id(member, name) is None
    set_columns(member, age_assured_utc=int(time.time()))
    answer, name = new_child(member, country="GB")
    assert "msg=" in answer.headers["Location"] and user_id(member, name)


def test_at_most_five_children_and_three_in_a_day(make_member):
    mum = parent(make_member)
    names = []
    for _ in range(3):
        answer, name = new_child(mum)
        assert "msg=" in answer.headers["Location"], answer.headers["Location"]
        names.append(name)
    answer, name = new_child(mum)
    assert "error=" in answer.headers["Location"] and "3" in answer.headers["Location"] and user_id(mum, name) is None
    # an older two, so the day's limit isn't what is hit
    db = mum.db_session()
    db.execute(text("UPDATE account_links SET created_utc = created_utc - 200000 WHERE parent_id = :p"), {"p": mum.id})
    db.execute(text("UPDATE child_family SET made_utc = made_utc - 200000 WHERE parent_id = :p"), {"p": mum.id})      # the limits read the ledger
    db.commit()
    for _ in range(2):
        assert "msg=" in new_child(mum)[0].headers["Location"]
    answer, name = new_child(mum)
    assert "error=" in answer.headers["Location"] and user_id(mum, name) is None


def test_a_held_account_and_a_qr_sign_in_cant_make_one(make_member):
    held = parent(make_member)
    set_columns(held, quarantined_utc=int(time.time()))
    answer, name = new_child(held)
    assert answer.status_code == 403 and user_id(held, name) is None
    limited = parent(make_member)
    client = limited.browser()
    with client.session_transaction() as session:
        session["qr_limited"] = True
        session["qr_expires_utc"] = int(time.time()) + 3600
    answer, name = new_child(limited, client=client)
    assert answer.status_code in (200, 403) and user_id(limited, name) is None


def test_a_visitor_and_a_form_without_the_key_make_nothing(real_app, make_member):
    app, _ = real_app
    assert post(app.test_client(), "/settings/children/new", username="x").status_code in (302, 401, 403)
    mum = parent(make_member)
    client = mum.browser()
    answer = post(client, "/settings/children/new", username="keyless1", password="Child-Password-1", password_confirm="Child-Password-1",
                  birth_year=str(YEAR - 15), birth_month="6", terms="1")
    assert answer.status_code in (302, 401, 403) and user_id(mum, "keyless1") is None


# ---------------------------------------------------------------- what the switches do

def test_a_child_is_refused_each_feature_by_its_parents_decision_until_the_parent_switches_it_on(made, real_app):
    app, _ = real_app
    mum, kid = made()
    client = kid.browser()
    chat = post(client, "/api/chat/start", formkey=kid.formkey(client), username="someone")
    assert chat.status_code == 403 and "parent or guardian has turned this off" in chat.get_data(as_text=True)
    assert switch(mum, kid, "chat", True).status_code == 200
    after = post(client, "/api/chat/start", formkey=kid.formkey(client), username="someone-who-doesnt-exist")
    assert "parent or guardian" not in after.get_data(as_text=True) and "age_locked" not in after.get_data(as_text=True)
    assert switch(mum, kid, "chat", False).status_code == 200
    again = post(client, "/api/chat/start", formkey=kid.formkey(client), username="someone")
    assert again.status_code == 403 and "age_locked" in again.get_data(as_text=True)


def test_money_spaces_and_guilds_follow_their_own_switches(made, real_app):
    app, _ = real_app
    mum, kid = made()
    client = kid.browser()
    pages = {"money": ("/settings/premium/redeem", {}), "spaces": ("/api/spaces", {"title": "t"}), "anonymous_guilds": ("/create_guild", {"name": "kidsguild"})}
    for name, (path, form) in pages.items():
        assert post(client, path, formkey=kid.formkey(client), **form).status_code == 403, name
    switch(mum, kid, "money", True)
    assert "age_locked" not in post(client, "/settings/premium/redeem", formkey=kid.formkey(client)).get_data(as_text=True)
    assert post(client, "/api/spaces", formkey=kid.formkey(client), title="t").status_code == 403, "another switch is still off"


def test_the_sensitive_switch_and_the_word_filter_are_never_the_parents(made):
    mum, kid = made()
    for name in ("chat", "spaces", "money", "anonymous_guilds"):
        switch(mum, kid, name, True)
    client = kid.browser()
    refused = post(client, "/api/vue/submit", formkey=kid.formkey(client), title="hello there", body="words", post_to_profile="1", sensitive="true")
    assert refused.status_code == 403
    assert post(client, "/settings/profile", formkey=kid.formkey(client), filter_level="0", password=kid.password).status_code == 403
    assert row(kid.id, "filter_level", db=mum.db_session()).filter_level == 2


def test_a_child_account_only_reads_whatever_the_parent_switches(made, make_member):
    """The owner's rule (the Log plan's decision 14): a child-locked account can't create content at all, and there is no switch for it."""
    mum, kid = made()
    for name in ("chat", "spaces", "money", "anonymous_guilds"):
        assert switch(mum, kid, name, True).status_code == 200
    client = kid.browser()
    key = kid.formkey(client)
    refused = post(client, "/api/vue/submit", formkey=key, title=f"Kid post {secrets.token_hex(3)}", body="words", post_to_profile="1")
    assert refused.status_code == 403 and "age-restricted" in refused.get_data(as_text=True), "the lock's own words, never 'your parent turned this off'"
    assert "parent or guardian" not in refused.get_data(as_text=True)
    author = parent(make_member)
    posted = post((_c := author.browser()), "/api/vue/submit", formkey=author.formkey(_c), title="Adult post", body="x", post_to_profile="1")
    parent_post = posted.get_json()["id"]
    assert post(client, "/api/comment", formkey=key, parent_fullname=f"t2_{parent_post}", body=f"hello {secrets.token_hex(3)}").status_code == 403
    for name in ("posting", "commenting", "private_posting"):
        assert switch(mum, kid, name, True).status_code == 400, f"{name} is not a switch any more"
    assert post(client, "/settings/private_posting", formkey=key, on="false").status_code in (404, 405)


def test_what_a_child_account_can_still_do_is_reading_voting_following_bookmarking_and_taking_its_own_words_down(made, make_member):
    mum, kid = made()
    author = parent(make_member)
    ac = author.browser()
    post_id = post(ac, "/api/vue/submit", formkey=author.formkey(ac), title=f"Adult post {secrets.token_hex(3)}", body="x", post_to_profile="1").get_json()["id"]
    client = kid.browser()
    key = kid.formkey(client)
    assert page_of(client, f"/post/{post_id}").status_code == 200
    assert post(client, f"/api/vote/post/{post_id}/1", formkey=key).status_code in (200, 204), "voting is not creating"
    assert post(client, f"/api/follow/{author.username}", formkey=key).status_code in (200, 204, 302, 303), "following is not creating"
    assert post(client, f"/save_post/{post_id}", formkey=key).status_code in (200, 204), "bookmarking is not creating"
    with before_the_lock(mum, kid):
        mine = post(client, "/api/vue/submit", formkey=key, title=f"Written before {secrets.token_hex(3)}", body="w", post_to_profile="1").get_json()["id"]
    assert post(client, f"/delete_post/{mine}", formkey=key).status_code in (200, 204, 303), "a child can always take its own words down"


def test_a_child_account_is_refused_every_way_of_making_something(made, make_member):
    """Every route that makes something a reader sees, on real items (a refusal that came only from a missing id would prove nothing)."""
    from ruqqus.helpers.base36 import base36encode
    mum, kid = made()
    for name in ("chat", "spaces", "money", "anonymous_guilds"):
        switch(mum, kid, name, True)
    author = parent(make_member)
    ac = author.browser()
    their_post = post(ac, "/api/vue/submit", formkey=author.formkey(ac), title=f"Adult post {secrets.token_hex(3)}", body="x", post_to_profile="1").get_json()["id"]
    their_comment = post(ac, "/api/comment", formkey=author.formkey(ac), parent_fullname=f"t2_{their_post}", body="an adult's comment")
    assert their_comment.status_code == 200, their_comment.get_data(as_text=True)[:200]
    their_cid = base36encode(db_scalar(mum, "SELECT id FROM comments WHERE author_id = :u ORDER BY id DESC LIMIT 1", u=author.id))
    client = kid.browser()
    key = kid.formkey(client)
    with before_the_lock(mum, kid):
        mine = post(client, "/api/vue/submit", formkey=key, title=f"Kid post {secrets.token_hex(3)}", body="first", post_to_profile="1").get_json()["id"]
        assert post(client, "/api/comment", formkey=key, parent_fullname=f"t2_{mine}", body="a first comment").status_code == 200
    my_cid = base36encode(db_scalar(mum, "SELECT id FROM comments WHERE author_id = :u ORDER BY id DESC LIMIT 1", u=kid.id))
    routes = (("/api/vue/submit", dict(title="t", body="b", post_to_profile="1")), ("/api/comment", dict(parent_fullname=f"t2_{their_post}", body="b")),
              (f"/edit_post/{mine}", dict(title="t", body="b")), (f"/edit_comment/{my_cid}", dict(body="b")),
              ("/api/stories", dict(kind="text", text="hi", audience="0")),
              (f"/post/{their_post}/forward", dict(board="x")), (f"/post/{their_post}/repost", {}),
              (f"/comment/{their_cid}/forward", dict(board="x")), (f"/comment/{their_cid}/repost", {}),
              ("/api/collab/1/publish", {}), ("/collab/new", {}), (f"/collab/fork/{their_post}", {}),
              ("/api/spaces", dict(title="t")), ("/api/spaces/abc/messages", dict(body="b")))
    for path, form in routes:
        answer = post(client, path, formkey=key, **form)
        assert answer.status_code == 403, (path, answer.status_code, answer.get_data(as_text=True)[:160])
    saved = post(client, "/api/drafts", formkey=key, title="a draft", body="b")
    assert saved.status_code == 403 and saved.get_json()["age_locked"] is True, "a draft is a way of making a post too (a scheduled one is published as the child)"


def test_the_composers_and_comment_boxes_are_not_drawn_for_a_child_account(made, make_member):
    mum, kid = made()
    author = parent(make_member)
    ac = author.browser()
    post_id = post(ac, "/api/vue/submit", formkey=author.formkey(ac), title=f"Adult post {secrets.token_hex(3)}", body="x", post_to_profile="1").get_json()["id"]
    client = kid.browser()
    page = page_of(client, f"/post/{post_id}").get_data(as_text=True)
    assert 'id="comment-restriction-locked"' in page and "comment-form-space-" not in page
    feed = get(client, "/all").get_data(as_text=True)
    assert 'id="inline-composer"' not in feed and "data-compose" not in feed
    assert 'class="dropdown-item" href="/submit"' not in feed
    assert get(client, "/submit").status_code == 403
    adult_feed = get(author.browser(), "/all").get_data(as_text=True)
    assert 'id="inline-composer"' in adult_feed and "data-compose" in adult_feed, "an adult's composer is drawn as ever"


def test_starting_a_guild_is_the_one_thing_the_anonymous_guilds_switch_allows(made):
    mum, kid = made()
    client = kid.browser()
    assert post(client, "/create_guild", formkey=kid.formkey(client), name="kidsguild").status_code == 403
    switch(mum, kid, "anonymous_guilds", True)
    answer = post(client, "/create_guild", formkey=kid.formkey(client), name="kidsguild")
    assert "age_locked" not in answer.get_data(as_text=True) and "parent or guardian" not in answer.get_data(as_text=True)
    anon = post(client, "/api/vue/submit", formkey=kid.formkey(client), title=f"Anon {secrets.token_hex(3)}", body="w", post_to_profile="1", anonymous="true")
    assert anon.status_code == 403, "the switch no longer opens posting"


def test_only_a_parent_with_a_child_can_switch_and_only_known_names_do_anything(made, make_member):
    mum, kid = made()
    stranger = parent(make_member)
    client = stranger.browser()
    assert post(client, f"/settings/children/{kid.username}/controls", formkey=stranger.formkey(client), control="chat", on="true").status_code == 404
    assert controls(mum, kid.id)[0] is False
    mine = mum.browser()
    for bad in ("", "chat; DROP TABLE users", "child_id", "age_lock", "ask_release_utc", "posting", "commenting", "private_posting"):
        assert post(mine, f"/settings/children/{kid.username}/controls", formkey=mum.formkey(mine), control=bad, on="true").status_code == 400, bad
    assert tuple(controls(mum, kid.id))[:4] == (False, False, False, False)
    assert post(mine, f"/settings/children/{kid.username}/controls", control="chat", on="true").status_code in (302, 401, 403), "no form key"


def test_others_can_chat_with_or_tip_a_child_only_if_the_parent_allows_it(made, make_member):
    mum, kid = made()
    other = parent(make_member)
    client = other.browser()
    start = lambda: post(client, "/api/chat/start", formkey=other.formkey(client), username=kid.username)
    assert start().status_code == 403
    switch(mum, kid, "chat", True)
    import requests
    try:
        status = start().status_code
    except requests.exceptions.ConnectionError:      # it got as far as the chat server (not there in the tests): past every gate of ours
        status = None
    assert status != 403


# ---------------------------------------------------------------- the child's requests

def notices(member):
    db = member.db_session()
    db.rollback()
    return [t[0] or "" for t in db.execute(text("SELECT a.notice_msgid FROM notifications n JOIN comments_aux a ON a.id = n.comment_id WHERE n.user_id = :u ORDER BY n.id"), {"u": member.id})]


def test_the_one_request_a_child_can_make_is_to_be_released_and_nothing_asks_for_private_posting_any_more(made):
    mum, kid = made()
    client = kid.browser()
    refused = post(client, "/settings/parent/ask", formkey=kid.formkey(client), kind="private", next="/settings/content")
    assert refused.status_code == 404, "there is no Private posting to ask about"
    assert not any("Private posting" in n for n in notices(mum)) and controls(mum, kid.id).ask_release_utc == 0
    page = get(mum.browser(), "/settings/children").get_data(as_text=True)
    assert "Private posting" not in page and "They asked you" not in page


def test_saying_18_asks_the_parent_in_a_country_that_takes_a_tick_and_the_parent_can_release(made):
    mum, kid = made()
    client = kid.browser()
    asked = post(client, "/settings/age_declaration", "US", formkey=kid.formkey(client), age_declaration="1")
    assert asked.status_code == 303 and asked.headers["Location"].endswith("age=asked_release")
    assert row(kid.id, "age_lock", db=mum.db_session()).age_lock == 2, "a tick doesn't lift a sub-account's lock"
    assert any("asked you to release their account" in n for n in notices(mum))
    page = get(kid.browser(), "/settings/content", "US").get_data(as_text=True)
    assert "asked your parent or guardian to release your account" in page
    mine = mum.browser()
    done = post(mine, f"/settings/children/{kid.username}/answer", "US", formkey=mum.formkey(mine), request="release", answer="allow", parent_password=mum.password)
    assert done.status_code == 303 and "msg=" in done.headers["Location"]
    r = row(kid.id, "age_declared", "age_lock", "filter_level", db=mum.db_session())
    assert (r.age_declared, r.age_lock, r.filter_level) == (1, None, 1)
    assert controls(mum, kid.id) is None and any("no longer" in n and "see it" in n for n in notices(kid))
    assert get(mine, f"/settings/children/{kid.username}").status_code == 404, "the parent's access ends"


def test_in_a_strict_country_a_tick_doesnt_release_a_child_and_a_request_made_there_is_never_granted(made):
    mum, kid = made()
    client = kid.browser()
    refused = post(client, "/settings/age_declaration", "GB", formkey=kid.formkey(client), age_declaration="1")
    assert refused.status_code == 403 and "age-check service" in refused.get_data(as_text=True)
    assert controls(mum, kid.id).ask_release_utc == 0, "nothing was asked from a strict country"
    # a request that was made from a strict country (a country list that grew since, say) is judged by where the CHILD asked from
    db = mum.db_session()
    db.execute(text("UPDATE child_controls SET ask_release_utc = 99, ask_release_country = 'GB' WHERE child_id = :c"), {"c": kid.id})
    db.commit()
    mine = mum.browser()
    done = post(mine, f"/settings/children/{kid.username}/answer", "US", formkey=mum.formkey(mine), request="release", answer="allow", parent_password=mum.password)
    assert "error=" in done.headers["Location"] and row(kid.id, "age_lock", db=mum.db_session()).age_lock == 2


def test_where_the_parent_answers_from_decides_nothing_only_where_the_child_asked_from(made):
    mum, kid = made()
    post((_c := kid.browser()), "/settings/age_declaration", "US", formkey=kid.formkey(_c), age_declaration="1")
    assert controls(mum, kid.id).ask_release_utc > 0
    mine = mum.browser()
    done = post(mine, f"/settings/children/{kid.username}/answer", "GB", formkey=mum.formkey(mine), request="release", answer="allow", parent_password=mum.password)
    assert "msg=" in done.headers["Location"] and row(kid.id, "age_lock", db=mum.db_session()).age_lock is None, "a parent on holiday still answers"


def test_a_child_an_age_check_found_under_18_cannot_be_released_by_a_tick_or_by_a_parent(made):
    mum, kid = made()
    post((_c := kid.browser()), "/settings/age_declaration", "US", formkey=kid.formkey(_c), age_declaration="1")
    db = mum.db_session()
    db.execute(text("UPDATE users SET age_under_utc = 5 WHERE id = :u"), {"u": kid.id})
    db.commit()
    mine = mum.browser()
    done = post(mine, f"/settings/children/{kid.username}/answer", "US", formkey=mum.formkey(mine), request="release", answer="allow", parent_password=mum.password)
    assert "error=" in done.headers["Location"] and row(kid.id, "age_lock", db=mum.db_session()).age_lock == 2
    again = kid.browser()
    asked = post(again, "/settings/age_declaration", "US", formkey=kid.formkey(again), age_declaration="1")
    assert asked.status_code == 403 and "under 18" in asked.get_data(as_text=True)


def test_a_release_can_be_refused_and_the_child_is_told(made):
    mum, kid = made()
    post((_c := kid.browser()), "/settings/age_declaration", "US", formkey=kid.formkey(_c), age_declaration="1")
    mine = mum.browser()
    post(mine, f"/settings/children/{kid.username}/answer", "US", formkey=mum.formkey(mine), request="release", answer="deny")
    assert row(kid.id, "age_lock", db=mum.db_session()).age_lock == 2 and controls(mum, kid.id).ask_release_utc == 0
    assert any("didn't agree to release" in n for n in notices(kid))


def test_a_standalone_child_cant_ask_a_parent_it_hasnt_got(make_member):
    member = make_member()
    set_columns(member, age_declared=2, age_lock=1, age_declared_utc=5, filter_level=2)
    client = member.browser()
    assert post(client, "/settings/parent/ask", formkey=member.formkey(client), kind="release").status_code == 404
    assert post(client, "/settings/parent/ask", formkey=member.formkey(client), kind="private").status_code == 404
    adult = parent(make_member)
    adult_client = adult.browser()
    assert post(adult_client, "/settings/parent/ask", formkey=adult.formkey(adult_client), kind="release").status_code == 404


# ---------------------------------------------------------------- the other roads out

def test_an_age_check_releases_a_sub_account_and_tells_the_parent(made, monkeypatch):
    mum, kid = made()
    monkeypatch.setenv("AGE_ASSURANCE_PROVIDER", "dev")
    monkeypatch.setenv("AGE_ASSURANCE_DEV", "1")
    monkeypatch.setenv("AGE_ASSURANCE_DEV_RESULT", "adult")
    client = kid.browser()
    started = post(client, "/settings/age_assurance/start", "GB", formkey=kid.formkey(client))
    done = get(client, started.headers["Location"].replace(BASE, ""), "GB")
    assert done.status_code == 303 and done.headers["Location"].endswith("age=confirmed")
    r = row(kid.id, "age_lock", "age_assured_utc", db=mum.db_session())
    assert r.age_lock is None and r.age_assured_utc > 0 and controls(mum, kid.id) is None
    assert any("age was confirmed as 18 or over" in n for n in notices(mum))


def test_an_admin_lifting_the_lock_releases_a_sub_account(made, make_member):
    mum, kid = made()
    boss = make_member()
    set_columns(boss, admin_level=3, age_declared=1, age_declared_utc=5)
    client = boss.browser()
    done = post(client, "/admin/age_declaration", formkey=boss.formkey(client), username=kid.username, answer="confirmed", reason="id seen")
    assert done.status_code == 302 and row(kid.id, "age_lock", db=mum.db_session()).age_lock is None and controls(mum, kid.id) is None
    assert any("age was confirmed as 18 or over" in n for n in notices(mum))


def test_stopping_managing_leaves_a_standalone_child_account(made):
    mum, kid = made()
    mine = mum.browser()
    done = post(mine, f"/settings/children/{kid.username}/stop", formkey=mum.formkey(mine), parent_password=mum.password)
    assert done.status_code == 303 and "msg=" in done.headers["Location"]
    r = row(kid.id, "age_declared", "age_lock", db=mum.db_session())
    assert (r.age_declared, r.age_lock) == (2, 1) and controls(mum, kid.id) is None
    assert any("stopped managing your account" in n for n in notices(kid))
    assert get(mine, f"/settings/children/{kid.username}").status_code == 404


def test_a_new_password_needs_the_parents_and_signs_the_child_out(made):
    mum, kid = made()
    mine = mum.browser()
    nonce = row(kid.id, "login_nonce", db=mum.db_session()).login_nonce
    wrong = post(mine, f"/settings/children/{kid.username}/password", formkey=mum.formkey(mine), parent_password="wrong", new_password="Another-Pass-2", new_password_confirm="Another-Pass-2")
    assert "error=" in wrong.headers["Location"] and row(kid.id, "login_nonce", db=mum.db_session()).login_nonce == nonce
    done = post(mine, f"/settings/children/{kid.username}/password", formkey=mum.formkey(mine), parent_password=mum.password, new_password="Another-Pass-2", new_password_confirm="Another-Pass-2")
    assert "msg=" in done.headers["Location"] and row(kid.id, "login_nonce", db=mum.db_session()).login_nonce == nonce + 1
    visitor = kid.app.test_client()
    assert post(visitor, "/login", username=kid.username, password="Child-Password-1").status_code != 302
    assert post(kid.app.test_client(), "/login", username=kid.username, password="Another-Pass-2").status_code == 302


def test_deleting_a_child_needs_the_parents_password_and_keeps_the_parent_signed_in(made):
    mum, kid = made()
    mine = mum.browser()
    wrong = post(mine, f"/settings/children/{kid.username}/delete", formkey=mum.formkey(mine), parent_password="wrong")
    assert "error=" in wrong.headers["Location"] and row(kid.id, "is_deleted", db=mum.db_session()).is_deleted is False
    done = post(mine, f"/settings/children/{kid.username}/delete", formkey=mum.formkey(mine), parent_password=mum.password)
    # the answer is the page itself, which forgets the child's chat here (a family shares a browser); the parent's own is untouched
    page = done.get_data(as_text=True)
    assert done.status_code == 200 and f"The account @{kid.username} is deleted." in page
    assert f'forgetChildChatDevices(["@ruqqus_{kid.id}:' in page and "forgetChatDevices(" not in page
    assert "forgetChildChatDevices" not in get(mine, "/settings/children?msg=x").get_data(as_text=True), "an address never forgets anything"
    assert row(kid.id, "is_deleted", db=mum.db_session()).is_deleted is True and controls(mum, kid.id) is None
    assert get(mine, "/settings/children").status_code == 200, "this browser is still the parent's"


def test_a_parent_can_download_a_childs_data_with_their_password_and_nobody_else_can(made, make_member):
    mum, kid = made()
    mine = mum.browser()
    refused = post(mine, f"/settings/children/{kid.username}/export", formkey=mum.formkey(mine), parent_password="wrong")
    assert "error=" in refused.headers["Location"]
    data = post(mine, f"/settings/children/{kid.username}/export", formkey=mum.formkey(mine), parent_password=mum.password)
    assert data.status_code == 200 and data.headers["Content-Type"].startswith("application/json") and "attachment" in data.headers["Content-Disposition"]
    assert kid.username in data.headers["Content-Disposition"] and "end-to-end encrypted" in data.get_data(as_text=True)
    stranger = parent(make_member)
    stranger_client = stranger.browser()
    assert post(stranger_client, f"/settings/children/{kid.username}/export", formkey=stranger.formkey(stranger_client), parent_password=stranger.password).status_code == 404


# ---------------------------------------------------------------- what a parent sees

def test_the_parent_sees_the_childs_activity_escaped_and_never_anyone_elses(made, make_member):
    mum, kid = made()
    client = kid.browser()
    with before_the_lock(mum, kid):
        made_post = post(client, "/api/vue/submit", formkey=kid.formkey(client), title="Tom & Jerry <b>x</b>", body="w", post_to_profile="1")
    assert made_post.status_code == 200
    page = get(mum.browser(), f"/settings/children/{kid.username}")
    body = page.get_data(as_text=True)
    assert page.status_code == 200 and "Tom &amp; Jerry &lt;b&gt;x&lt;/b&gt;" in body and "<b>x</b>" not in body
    assert "can't see what their chats say" in body or "can't see what your chats say" in body or "end-to-end encrypted" in body
    stranger = parent(make_member)
    assert get(stranger.browser(), f"/settings/children/{kid.username}").status_code == 404
    assert get(kid.browser(), f"/settings/children/{kid.username}").status_code == 403, "a child can't open their own parent view"


def test_the_page_lists_the_children_with_every_switch_and_the_form_when_eligible(made):
    mum, kid = made()
    body = get(mum.browser(), "/settings/children").get_data(as_text=True)
    assert f"@{kid.username}" in body and 'id="children-new"' in body
    for name in ("chat", "spaces", "money", "anonymous_guilds"):
        assert f'id="child-{kid.username}-{name}"' in body
    for gone in ("posting", "commenting", "private"):
        assert f'id="child-{kid.username}-{gone}"' not in body, gone
    assert "chats are end-to-end encrypted" in body
    assert 'name="birth_year"' in body and "isn't kept" in body


def test_a_child_or_an_unconfirmed_member_gets_a_reason_instead_of_the_form(make_member):
    child = make_member()
    set_columns(child, age_declared=2, age_lock=1, age_declared_utc=5, filter_level=2)
    body = get(child.browser(), "/settings/children").get_data(as_text=True)
    assert 'id="children-new"' not in body and "confirmed as 18 or over" in body
    unconfirmed = parent(make_member)
    assert 'id="children-new"' not in get(unconfirmed.browser(), "/settings/children", "GB").get_data(as_text=True)


def test_the_tab_is_for_adults_only(make_member):
    adult, child = parent(make_member), make_member()
    set_columns(child, age_declared=2, age_lock=1, age_declared_utc=5, filter_level=2)
    assert 'href="/settings/children"' in get(adult.browser(), "/settings/profile").get_data(as_text=True)
    assert 'href="/settings/children"' not in get(child.browser(), "/settings/profile").get_data(as_text=True)


def test_the_childs_settings_say_who_decides_and_what_is_on(made):
    mum, kid = made()
    switch(mum, kid, "chat", True)
    body = get(kid.browser(), "/settings/content", "US").get_data(as_text=True)
    assert 'id="parent-controls"' in body and "held by your parent or guardian" in body
    assert re.search(r"Chat, group chats and calls: <strong>On</strong>", body) and re.search(r"Spaces and live threads: <strong>Off</strong>", body)
    assert "never shows them what your chats say" in body.replace("&#39;", "'")
    assert "Private posting" not in body and 'id="private-posting-switch"' not in body
    assert "only reads" in body.replace("&#39;", "'") or "can't post or comment" in body.replace("&#39;", "'")


# ---------------------------------------------------------------- a parent that goes

def test_a_parent_deleting_their_account_deletes_the_children_and_is_warned_first(made):
    mum, kid = made()
    kid2_answer, name2 = new_child(mum)
    kid2 = user_id(mum, name2)
    page = get(mum.browser(), "/settings/security").get_data(as_text=True)
    assert 'id="delete-children-warning"' in page and "Your child accounts will be deleted with yours." in page
    client = mum.browser()
    done = post(client, "/settings/delete_account", formkey=mum.formkey(client), password=mum.password, delete_reason="test")
    assert done.status_code == 200
    # the page clears the parent's chat from this browser, and the children's closed with it (a family shares a browser)
    forget = done.get_data(as_text=True).split("forgetChatDevices(", 1)[1].split(");", 1)[0]
    assert forget.startswith(f'"@ruqqus_{mum.id}:') and f'"@ruqqus_{kid.id}:' in forget and f'"@ruqqus_{kid2}:' in forget
    for uid in (kid.id, kid2):
        assert row(uid, "is_deleted", db=mum.db_session()).is_deleted is True
    db = mum.db_session()
    db.rollback()
    assert db.execute(text("SELECT COUNT(*) FROM account_links WHERE parent_id = :p OR child_id IN (:a, :b)"), {"p": mum.id, "a": kid.id, "b": kid2}).scalar() == 0


def ban(boss, target, days):
    client = boss.browser()
    return post(client, f"/api/ban_user/{target.id}", formkey=boss.formkey(client), reason="For the test", rule="evasion", days=str(days) if days else "")


def admin(make_member):
    member = make_member()
    set_columns(member, admin_level=4, age_declared=1, age_declared_utc=5)
    return member


def test_a_parents_suspension_suspends_the_children_for_the_same_time_and_they_come_back_with_it(made, make_member):
    mum, kid = made()
    boss = admin(make_member)
    assert ban(boss, mum, 3).status_code in (200, 302)
    p, c = row(mum, "is_banned", "unban_utc"), row(kid.id, "is_banned", "unban_utc", "ban_reason", "is_deleted", db=mum.db_session())
    assert p.is_banned and c.is_banned and c.is_deleted is False and abs(c.unban_utc - p.unban_utc) <= 5
    assert c.ban_reason == "This account is suspended.", "the reason a profile or the API prints says nothing of a parent"
    db = mum.db_session()
    db.rollback()
    entries = db.execute(text("SELECT audience FROM log_entries WHERE target_user_id = :u AND action = 'suspend_user'"), {"u": kid.id}).all()
    assert entries and all(e.audience >= 2 for e in entries), "a public line would say the child has a parent"
    client = boss.browser()
    assert post(client, f"/api/unban_user/{mum.id}", formkey=boss.formkey(client)).status_code in (200, 302)
    assert row(kid.id, "is_banned", db=mum.db_session()).is_banned in (0, False, None) or not row(kid.id, "is_banned", db=mum.db_session()).is_banned


def test_a_parents_permanent_ban_deletes_the_children_and_tells_only_the_staff(made, make_member):
    mum, kid = made()
    boss = admin(make_member)
    assert ban(boss, mum, 0).status_code in (200, 302)
    assert row(kid.id, "is_deleted", db=mum.db_session()).is_deleted is True and controls(mum, kid.id) is None
    db = mum.db_session()
    db.rollback()
    entry = db.execute(text("SELECT audience, actor_role FROM log_entries WHERE target_user_id = :u AND action = 'child_removed_with_parent'"), {"u": kid.id}).first()
    assert entry is not None and entry.audience >= 2 and entry.actor_role == "system"


def test_banning_a_child_doesnt_reach_the_parent(made, make_member):
    mum, kid = made()
    boss = admin(make_member)
    assert ban(boss, kid, 0).status_code in (200, 302)
    assert not row(mum, "is_banned").is_banned, "the other way round isn't a thing"


def test_a_parent_and_its_child_signed_in_on_one_browser_are_not_alts(made):
    mum, kid = made()
    visitor = kid.app.test_client()
    assert post(visitor, "/login", username=mum.username, password=mum.password).status_code == 302
    assert post(visitor, "/login", username=kid.username, password=kid.password).status_code == 302
    db = mum.db_session()
    db.rollback()
    assert db.execute(text("SELECT COUNT(*) FROM alts WHERE (user1 = :a AND user2 = :b) OR (user1 = :b AND user2 = :a)"), {"a": mum.id, "b": kid.id}).scalar() == 0



# ---------------------------------------------------------------- after the reviews: the loopholes they found, each closed

def test_stopping_or_deleting_a_child_doesnt_free_a_slot_in_the_limits(make_member):
    mum = parent(make_member)
    names = [new_child(mum)[1] for _ in range(3)]
    client = mum.browser()
    post(client, f"/settings/children/{names[0]}/stop", formkey=mum.formkey(client), parent_password=mum.password)
    post(client, f"/settings/children/{names[1]}/delete", formkey=mum.formkey(client), parent_password=mum.password)
    answer, name = new_child(mum)
    assert "error=" in answer.headers["Location"] and user_id(mum, name) is None, "three were made today, whatever became of two of them"
    db = mum.db_session()
    db.rollback()
    assert db.execute(text("SELECT COUNT(*) FROM child_family WHERE parent_id = :p"), {"p": mum.id}).scalar() == 3


def test_two_children_of_one_parent_and_a_former_child_are_not_alts_of_each_other(made):
    mum, kid = made()
    second = new_child(mum)[1]
    visitor = kid.app.test_client()
    for who, password in ((kid.username, kid.password), (second, "Child-Password-1")):
        assert post(visitor, "/login", username=who, password=password).status_code == 302
    client = mum.browser()
    post(client, f"/settings/children/{kid.username}/stop", formkey=mum.formkey(client), parent_password=mum.password)
    assert post(visitor, "/login", username=mum.username, password=mum.password).status_code == 302
    db = mum.db_session()
    db.rollback()
    a, b, c = mum.id, kid.id, user_id(mum, second)
    assert db.execute(text("SELECT COUNT(*) FROM alts WHERE user1 IN (:a, :b, :c) AND user2 IN (:a, :b, :c)"), {"a": a, "b": b, "c": c}).scalar() == 0, \
        "a family signs in on one browser on purpose; a stopped link doesn't turn it into alts"


def test_a_child_whose_controls_are_lost_is_refused_everything(made):
    mum, kid = made()
    db = mum.db_session()
    db.execute(text("DELETE FROM child_controls WHERE child_id = :c"), {"c": kid.id})
    db.commit()
    client = kid.browser()
    assert post(client, "/settings/premium/redeem", formkey=kid.formkey(client)).status_code == 403, "it fails closed"
    assert post(client, "/api/vue/submit", formkey=kid.formkey(client), title="Hello there", body="w", post_to_profile="1").status_code == 403


def test_a_child_with_nothing_switched_on_has_no_chat_session_or_chat_app_or_guild_channel(made):
    mum, kid = made()
    client = kid.browser()
    key = kid.formkey(client)
    for path in ("/api/chat/token", "/api/chat/check_password", "/api/chat/key"):
        answer = post(client, path, formkey=key) if path != "/api/chat/key" else get(client, path)
        assert answer.status_code == 403 and "age_locked" in answer.get_data(as_text=True), path
    for path in ("/api/guilds/channels/1/search?q=hello", "/api/guilds/channels/1/pins", "/api/guilds/channels/1/senders?ids=1"):
        answer = get(client, path)
        assert answer.status_code == 403, path
    assert post(client, "/api/guilds/channels/1/mark_read", formkey=key).status_code == 403


def test_spending_is_refused_but_stopping_a_payment_leaving_the_fund_and_a_free_circle_are_not(made):
    mum, kid = made()
    client = kid.browser()
    key = kid.formkey(client)
    assert post(client, "/settings/premium/auto_renew", formkey=key, setting="true").status_code == 403
    assert post(client, "/settings/premium/auto_renew", formkey=key, setting="false").status_code == 200, "an account that may not spend can always stop a payment"
    assert "error=" in post(client, "/settings/creator_fund", formkey=key, agree="1").headers["Location"]
    assert "msg=" in post(client, "/settings/creator_fund", formkey=key, action="leave").headers["Location"]
    free = post(client, "/api/circles", formkey=key, kind="custom", name="Family")
    assert free.status_code == 200, "a free custom circle is not money, and not posting"
    paid = post(client, "/api/circles", formkey=key, kind="paid", name="Paid", price="5")
    assert paid.status_code == 403 and "turned this off" in paid.get_data(as_text=True)


def test_a_new_password_is_told_to_the_child_and_needs_the_parents_two_factor_code_when_they_have_one(made):
    import pyotp
    mum, kid = made()
    secret = pyotp.random_base32()
    set_columns(mum, mfa_secret=secret)
    mine = mum.browser()
    form = dict(formkey=mum.formkey(mine), parent_password=mum.password, new_password="Another-Pass-2", new_password_confirm="Another-Pass-2")
    nonce = row(kid.id, "login_nonce", db=mum.db_session()).login_nonce
    without = post(mine, f"/settings/children/{kid.username}/password", **form)
    assert "error=" in without.headers["Location"], "the password alone isn't enough when two-factor is on"
    wrong = post(mine, f"/settings/children/{kid.username}/password", twofactor="000000", **form)
    assert "error=" in wrong.headers["Location"] and row(kid.id, "login_nonce", db=mum.db_session()).login_nonce == nonce
    done = post(mine, f"/settings/children/{kid.username}/password", twofactor=pyotp.TOTP(secret).now(), **form)
    assert "msg=" in done.headers["Location"] and row(kid.id, "login_nonce", db=mum.db_session()).login_nonce == nonce + 1
    assert any("set a new password for your account" in n for n in notices(kid)), "a changed password is never silent"
    page = get(mine, "/settings/children").get_data(as_text=True)
    assert 'name="twofactor"' in page


def test_an_account_that_owes_money_is_not_deleted_to_escape_it(made):
    mum, kid = made()
    db = mum.db_session()
    db.execute(text("UPDATE users SET negative_balance_cents = 500 WHERE id = :u"), {"u": kid.id})
    db.commit()
    mine = mum.browser()
    answer = post(mine, f"/settings/children/{kid.username}/delete", formkey=mum.formkey(mine), parent_password=mum.password)
    assert "error=" in answer.headers["Location"] and row(kid.id, "is_deleted", db=mum.db_session()).is_deleted is False


def test_a_parent_downloads_only_the_sections_a_parent_may_have(made):
    import json
    mum, kid = made()
    mine = mum.browser()
    data = json.loads(post(mine, f"/settings/children/{kid.username}/export", formkey=mum.formkey(mine), parent_password=mum.password).get_data(as_text=True))
    assert set(data) <= {"note", "account", "posts", "comments", "votes", "bookmarks", "following", "guilds", "private_guilds"}
    text_of = json.dumps(data)
    for secret in ("passhash", "email", "creation_ip", "mfa_secret", "login_nonce"):
        assert secret not in text_of, secret


def test_the_parents_suspension_is_neutral_in_public_remembered_by_its_end_and_lifts_only_its_own(made, make_member):
    mum, kid = made()
    boss = admin(make_member)
    # a ban the child earned itself, longer than the parent's, is never shortened and never lifted by the parent's unban
    assert ban(boss, kid, 30).status_code in (200, 302)
    own_end = row(kid.id, "unban_utc", db=mum.db_session()).unban_utc
    assert ban(boss, mum, 3).status_code in (200, 302)
    assert row(kid.id, "unban_utc", db=mum.db_session()).unban_utc == own_end
    assert db_scalar(mum, "SELECT suspended_until_utc FROM child_family WHERE child_id = :c", c=kid.id) == 0, "the site gave nothing here: nothing to lift later"
    client = boss.browser()
    assert post(client, f"/api/unban_user/{mum.id}", formkey=boss.formkey(client)).status_code in (200, 302)
    assert row(kid.id, "is_banned", db=mum.db_session()).is_banned, "the child's own ban stays"


def test_a_suspension_the_site_gave_a_child_is_lifted_with_the_parents_and_forgotten(made, make_member):
    mum, kid = made()
    boss = admin(make_member)
    assert ban(boss, mum, 3).status_code in (200, 302)
    given = db_scalar(mum, "SELECT suspended_until_utc FROM child_family WHERE child_id = :c", c=kid.id)
    assert given and given == row(kid.id, "unban_utc", db=mum.db_session()).unban_utc
    shown = get(boss.browser(), f"/@{kid.username}")
    page = shown.get_data(as_text=True).lower()
    assert shown.status_code == 200 and "suspended" in page, "the profile of a suspended account says so"
    assert "guardian" not in page and "your parent" not in page, "...and says nothing of whose child this is"
    client = boss.browser()
    assert post(client, f"/api/unban_user/{mum.id}", formkey=boss.formkey(client)).status_code in (200, 302)
    assert not row(kid.id, "is_banned", db=mum.db_session()).is_banned
    assert db_scalar(mum, "SELECT suspended_until_utc FROM child_family WHERE child_id = :c", c=kid.id) == 0


def db_scalar(member, sql, **params):
    db = member.db_session()
    db.rollback()
    return db.execute(text(sql), params).scalar()


def test_deleting_a_parent_deletes_a_child_made_at_that_moment_too(made):
    """The parent's lock is taken by both: a child made while the parent is being closed waits, then is one of the children closed."""
    mum, kid = made()
    second = new_child(mum)[1]
    client = mum.browser()
    deleted = post(client, "/settings/delete_account", formkey=mum.formkey(client), password=mum.password)
    assert deleted.status_code in (200, 303)
    assert row(kid.id, "is_deleted", db=mum.db_session()).is_deleted is True
    assert row(user_id(mum, second), "is_deleted", db=mum.db_session()).is_deleted is True


# ---------------------------------------------------------------- after the second review round

def test_spaces_alone_give_no_chat_token_and_no_call(made):
    mum, kid = made()
    assert switch(mum, kid, "spaces", True).status_code == 200
    client = kid.browser()
    key = kid.formkey(client)
    token = post(client, "/api/chat/token", formkey=key)
    assert token.status_code == 403 and "age_locked" in token.get_data(as_text=True), "a Matrix session is chat, whatever Spaces says"
    call = post(client, "/api/spaces", formkey=key, title="A call", space_kind="call", space_hours="1", space_start="now")
    assert call.status_code == 403 and "age-restricted" in call.get_data(as_text=True), "starting a Space is making something: a child account only reads"


def test_a_childs_old_words_can_be_taken_down_but_not_changed(made):
    mum, kid = made()
    assert switch(mum, kid, "spaces", True).status_code == 200
    client = kid.browser()
    key = kid.formkey(client)
    with before_the_lock(mum, kid):
        mine = post(client, "/api/vue/submit", formkey=key, title=f"Kid post {secrets.token_hex(3)}", body="first", post_to_profile="1").get_json()["id"]
        assert post(client, "/api/comment", formkey=key, parent_fullname=f"t2_{mine}", body="a first comment").status_code == 200
    from ruqqus.helpers.base36 import base36encode
    cid = base36encode(db_scalar(mum, "SELECT id FROM comments WHERE author_id = :u ORDER BY id DESC LIMIT 1", u=kid.id))
    edited = post(client, f"/edit_comment/{cid}", formkey=key, body="words changed after the lock")
    assert edited.status_code == 403
    edited_post = post(client, f"/edit_post/{mine}", formkey=key, title="A new title", body="new words")
    assert edited_post.status_code == 403
    board = post(client, "/api/spaces/abc/messages", formkey=key, body="hello")
    assert board.status_code == 403, "checked before the Space is even looked up"
    deleted = post(client, f"/delete/comment/{cid}", formkey=key)
    assert deleted.status_code in (200, 204, 303), "a child can always take its own words down"


def test_a_locked_child_is_not_put_in_a_circle_a_shared_draft_or_a_byline_by_a_stranger(made, make_member):
    mum, kid = made()
    adult = parent(make_member)
    client = adult.browser()
    key = adult.formkey(client)
    circle = post(client, "/api/circles", formkey=key, kind="custom", name="Mates").get_json()
    refused = post(client, f"/api/circles/{circle['id']}/add", formkey=adult.formkey(client), username=kid.username)
    assert refused.status_code == 403, "no private channel with a child whose parent hasn't switched chat on"
    mine = mum.browser()
    family = post(mine, "/api/circles", formkey=mum.formkey(mine), kind="custom", name="Family").get_json()
    assert post(mine, f"/api/circles/{family['id']}/add", formkey=mum.formkey(mine), username=kid.username).status_code == 200, "a parent may add their own child"
    # a shared draft: proposing the child as an author is refused without saying why
    drafted = post(client, "/collab/new", formkey=adult.formkey(client))
    assert drafted.status_code == 302, drafted.status_code
    doc = int(drafted.headers["Location"].rsplit("/", 1)[1])
    proposed = post(client, f"/api/collab/{doc}/propose", formkey=adult.formkey(client), kind="add_author", username=kid.username)
    assert proposed.status_code in (400, 403, 409) and "can't be added" in proposed.get_data(as_text=True)
    assert db_scalar(adult, "SELECT COUNT(*) FROM collab_proposals WHERE doc_id = :d", d=doc) == 0, "no invitation was made"


def test_a_locked_account_runs_no_bot_and_gets_no_key(made):
    mum, kid = made()
    db = mum.db_session()
    db.execute(text("UPDATE users SET created_utc = 1500000000 WHERE id = :u"), {"u": kid.id})
    db.commit()
    client = kid.browser()
    answer = post(client, "/settings/bots/apply", formkey=kid.formkey(client), kind="member", username="kidbot" + secrets.token_hex(2), purpose="a feed",
                  sources="https://example.com/feed.xml")
    assert "age-restricted" in answer.get_data(as_text=True) or "age-restricted" in answer.headers.get("Location", ""), answer.status_code


def test_switching_money_off_stops_what_was_set_up(made, make_member):
    mum, kid = made()
    owner = parent(make_member)
    db = mum.db_session()
    db.execute(text("INSERT INTO circles (user_id, kind, name, price_coins, created_utc) VALUES (:o, 'paid', :n, 5, 1)"), {"o": owner.id, "n": "Paid" + secrets.token_hex(2)})
    circle = db.execute(text("SELECT id FROM circles WHERE user_id = :o AND kind = 'paid'"), {"o": owner.id}).scalar()
    db.execute(text("INSERT INTO circle_members (circle_id, user_id, tier, status, renews_utc, price_coins) VALUES (:c, :u, 'subscriber', 'active', 2100000000, 5)"),
               {"c": circle, "u": kid.id})
    db.execute(text("UPDATE users SET premium_auto_renew = true, creator_fund_opt_in_utc = 5, coin_balance = 50 WHERE id = :u"), {"u": kid.id})
    db.commit()
    assert switch(mum, kid, "money", True).status_code == 200
    assert switch(mum, kid, "money", False).status_code == 200
    db.rollback()
    state = db.execute(text("SELECT premium_auto_renew, creator_fund_opt_in_utc FROM users WHERE id = :u"), {"u": kid.id}).first()
    assert not state.premium_auto_renew and state.creator_fund_opt_in_utc == 0
    assert db.execute(text("SELECT cancelled FROM circle_members WHERE circle_id = :c AND user_id = :u"), {"c": circle, "u": kid.id}).scalar() is True
    # and the jobs refuse a locked payer even if a row said otherwise
    db.execute(text("UPDATE circle_members SET cancelled = false, renews_utc = 1 WHERE circle_id = :c AND user_id = :u"), {"c": circle, "u": kid.id})
    db.execute(text("UPDATE users SET premium_auto_renew = true, premium_expires_utc = 5 WHERE id = :u"), {"u": kid.id})
    db.commit()
    from ruqqus.helpers import circle_store, premium
    assert kid.id not in premium.due_ids(db, int(time.time()))
    events = circle_store.renew_due(db, int(time.time()))
    assert not [e for e in events if e[1] == kid.id and e[0] == "renewed"], "nothing moves for a locked payer"
    db.rollback()
    assert db.execute(text("SELECT coin_balance FROM users WHERE id = :u"), {"u": kid.id}).scalar() == 50


def test_auto_renew_refused_for_a_locked_account_is_not_kept_by_the_commit(made):
    mum, kid = made()
    client = kid.browser()
    assert post(client, "/settings/premium/auto_renew", formkey=kid.formkey(client), setting="true").status_code == 403
    assert row(kid.id, "premium_auto_renew", db=mum.db_session()).premium_auto_renew in (False, None), "refused before the flag is touched"


def test_stopping_and_allowing_ask_for_the_parents_proof_and_stopping_signs_the_child_out_of_chat(made):
    mum, kid = made()
    mine = mum.browser()
    refused = post(mine, f"/settings/children/{kid.username}/stop", formkey=mum.formkey(mine), parent_password="wrong")
    assert "error=" in refused.headers["Location"] and row(kid.id, "age_lock", db=mum.db_session()).age_lock == 2
    post((_c := kid.browser()), "/settings/age_declaration", "US", formkey=kid.formkey(_c), age_declaration="1")
    no_proof = post(mine, f"/settings/children/{kid.username}/answer", formkey=mum.formkey(mine), request="release", answer="allow")
    assert "error=" in no_proof.headers["Location"] and row(kid.id, "age_lock", db=mum.db_session()).age_lock == 2
    deny = post(mine, f"/settings/children/{kid.username}/answer", formkey=mum.formkey(mine), request="release", answer="deny")
    assert "msg=" in deny.headers["Location"], "keeping things as they are never asks"
    done = post(mine, f"/settings/children/{kid.username}/stop", formkey=mum.formkey(mine), parent_password=mum.password)
    assert "msg=" in done.headers["Location"] and row(kid.id, "age_lock", db=mum.db_session()).age_lock == 1


def test_a_child_asking_to_be_released_with_an_odd_country_header_is_not_a_server_error(made):
    mum, kid = made()
    client = kid.browser()
    answer = post(client, "/settings/age_declaration", "USA", formkey=kid.formkey(client), age_declaration="1")
    assert answer.status_code == 303, answer.status_code
    assert controls(mum, kid.id).ask_release_utc > 0


def test_a_suspension_is_lifted_even_when_the_link_ended_meanwhile(made, make_member):
    mum, kid = made()
    boss = admin(make_member)
    assert ban(boss, mum, 3).status_code in (200, 302)
    assert row(kid.id, "is_banned", db=mum.db_session()).is_banned
    db = mum.db_session()
    db.execute(text("DELETE FROM account_links WHERE child_id = :c"), {"c": kid.id})
    db.execute(text("DELETE FROM child_controls WHERE child_id = :c"), {"c": kid.id})
    db.commit()                                    # released by an age check or an admin while the parent is suspended
    client = boss.browser()
    assert post(client, f"/api/unban_user/{mum.id}", formkey=boss.formkey(client)).status_code in (200, 302)
    assert not row(kid.id, "is_banned", db=mum.db_session()).is_banned, "found by the ledger, not by the link"
    assert db_scalar(mum, "SELECT suspended_until_utc FROM child_family WHERE child_id = :c", c=kid.id) == 0


def test_a_shorter_ban_of_the_childs_own_is_extended_and_comes_back(made, make_member):
    mum, kid = made()
    boss = admin(make_member)
    assert ban(boss, kid, 1).status_code in (200, 302)
    own = row(kid.id, "unban_utc", "ban_reason", db=mum.db_session())
    assert ban(boss, mum, 5).status_code in (200, 302)
    extended = row(kid.id, "unban_utc", "ban_reason", db=mum.db_session())
    parent_end = row(mum, "unban_utc").unban_utc
    assert abs(extended.unban_utc - parent_end) <= 5 and extended.ban_reason == own.ban_reason, "its own reason stays: the public one says nothing of a parent"
    assert db_scalar(mum, "SELECT prior_until_utc FROM child_family WHERE child_id = :c", c=kid.id) == own.unban_utc
    client = boss.browser()
    assert post(client, f"/api/unban_user/{mum.id}", formkey=boss.formkey(client)).status_code in (200, 302)
    back = row(kid.id, "is_banned", "unban_utc", db=mum.db_session())
    assert back.is_banned and back.unban_utc == own.unban_utc, "the day the child earned is not erased by the parent's early unban"


def test_a_second_shorter_suspension_of_the_parent_moves_the_childs_end_down(made, make_member):
    mum, kid = made()
    boss = admin(make_member)
    assert ban(boss, mum, 30).status_code in (200, 302)
    long_end = row(kid.id, "unban_utc", db=mum.db_session()).unban_utc
    assert ban(boss, mum, 2).status_code in (200, 302)
    short_end = row(kid.id, "unban_utc", db=mum.db_session()).unban_utc
    parent_end = row(mum, "unban_utc", db=mum.db_session()).unban_utc
    assert short_end < long_end and abs(short_end - parent_end) <= 5, "the child isn't left suspended after the parent is back"


def test_a_parent_with_a_child_that_owes_money_cannot_delete_their_account(made):
    mum, kid = made()
    db = mum.db_session()
    db.execute(text("UPDATE users SET negative_balance_cents = 400 WHERE id = :u"), {"u": kid.id})
    db.commit()
    client = mum.browser()
    answer = post(client, "/settings/delete_account", formkey=mum.formkey(client), password=mum.password)
    assert "child accounts owes money" in answer.get_data(as_text=True)
    assert row(mum, "is_deleted").is_deleted is False and row(kid.id, "is_deleted", db=mum.db_session()).is_deleted is False


def test_the_parents_download_and_page_hold_no_deleted_words_and_no_reporters_words(made, make_member):
    import json
    mum, kid = made()
    client = kid.browser()
    with before_the_lock(mum, kid):
        shown = post(client, "/api/vue/submit", formkey=kid.formkey(client), title="A post kept", body="kept body", post_to_profile="1").get_json()["id"]
        gone = post(client, "/api/vue/submit", formkey=kid.formkey(client), title="A post deleted later", body="deleted body", post_to_profile="1").get_json()["id"]
    post(client, f"/delete_post/{gone}", formkey=kid.formkey(client))
    reporter = parent(make_member)
    rc = reporter.browser()
    post(rc, f"/api/user/{kid.username}/report", formkey=reporter.formkey(rc), reason="I am in his class and his dad hits him")
    mine = mum.browser()
    data = post(mine, f"/settings/children/{kid.username}/export", formkey=mum.formkey(mine), parent_password=mum.password).get_data(as_text=True)
    parsed = json.loads(data)
    assert set(parsed) <= {"note", "account", "posts", "comments", "votes", "bookmarks", "following", "guilds", "private_guilds"}
    assert "A post kept" in data and "A post deleted later" not in data and "deleted body" not in data and "kept body" not in data
    page = get(mine, f"/settings/children/{kid.username}").get_data(as_text=True)
    assert "A member reported this account." in page and "his dad hits him" not in page and "in his class" not in page
