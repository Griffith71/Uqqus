"""Sign-up on the real app and a real Postgres: what the form accepts and refuses (helpers/signup_risk.py decides,
routes/login.py `sign_up_post` asks). The first part pins the behaviour that came before the signup guard, so the
guard's own tests below can't hide a change to it."""
import hmac
import os
import secrets
import time
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import text

BASE = "http://localhost"
AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15"
HEADERS = {"User-Agent": AGENT, "Accept-Language": "en-GB,en;q=0.9"}


def address():
    return f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"


def name():
    # a name a person might pick: letters only, so no shape rule reads it as made by a machine
    return "ci" + "".join(secrets.choice("abcdefghjkmnoprstuvy") for _ in range(8))


def form_for(client, age=30, agent=AGENT):
    """The form a sign-up page drawn `age` seconds ago would carry for this browser (the key is the route's own
    recipe: the page's time, the session's sign-up token and the browser's User-Agent)."""
    now = str(int(time.time()) - age)
    token = secrets.token_hex(16)
    with client.session_transaction() as session:
        session["signup_token"] = token
    key = hmac.new(key=bytes(os.environ["MASTER_KEY"], "utf-16"), msg=bytes(now + token + agent, "utf-16"),
                   digestmod="md5").hexdigest()
    password = "ci-Password-1"
    return {"formkey": key, "now": now, "username": name(), "password": password, "password_confirm": password}


def sign_up(client, form, ip=None, headers=None):
    return client.post("/signup", base_url=BASE, headers=headers or HEADERS,
                       environ_base={"REMOTE_ADDR": ip or address()}, data=form)


def error_of(answer):
    assert answer.status_code == 302, answer.status_code
    where = urlsplit(answer.headers["Location"])
    return where.path, parse_qs(where.query).get("error", [""])[0]


def account(real_app, username):
    _, db_session = real_app
    db = db_session()
    db.rollback()
    return db.execute(text("SELECT id, email FROM users WHERE username = :u"), {"u": username}).first()


# ---------------------------------------------------------------- before the guard

def test_the_sign_up_page_carries_a_form_key_and_the_time(real_app):
    app, _ = real_app
    page = app.test_client().get("/signup", base_url=BASE, headers=HEADERS)
    assert page.status_code == 200
    assert b'name="formkey"' in page.data and b'name="now"' in page.data


def test_a_sign_up_page_with_no_browser_name_is_refused(real_app):
    app, _ = real_app
    assert app.test_client().get("/signup", base_url=BASE, headers={"User-Agent": ""}).status_code == 403


def test_a_good_form_makes_the_account_and_signs_it_in(real_app):
    app, _ = real_app
    client = app.test_client()
    form = form_for(client)
    answer = sign_up(client, form)
    assert answer.status_code == 302 and urlsplit(answer.headers["Location"]).path == "/"
    assert account(real_app, form["username"]) is not None
    assert client.get("/settings/profile", base_url=BASE, headers=HEADERS).status_code == 200, "signed in"


def test_a_form_sent_without_its_page_is_a_400(real_app):
    app, _ = real_app
    client = app.test_client()
    form = form_for(client)
    with client.session_transaction() as session:
        session.pop("signup_token")
    assert sign_up(client, form).status_code == 400


def test_a_form_with_a_wrong_key_is_refused_and_makes_nothing(real_app):
    app, _ = real_app
    client = app.test_client()
    form = form_for(client)
    form["formkey"] = "0" * 32
    path, error = error_of(sign_up(client, form))
    assert path == "/signup" and error
    assert account(real_app, form["username"]) is None


def test_a_form_sent_within_five_seconds_of_its_page_is_refused(real_app):
    app, _ = real_app
    client = app.test_client()
    form = form_for(client, age=1)
    path, error = error_of(sign_up(client, form))
    assert path == "/signup" and error
    assert account(real_app, form["username"]) is None


def test_a_second_account_from_one_address_within_the_hour_is_a_429(real_app):
    app, _ = real_app
    ip = address()
    first = app.test_client()
    assert sign_up(first, form_for(first), ip=ip).headers["Location"].endswith("/")
    second = app.test_client()
    form = form_for(second)
    assert sign_up(second, form, ip=ip).status_code == 429
    assert account(real_app, form["username"]) is None


def test_a_taken_username_or_email_is_refused(real_app, make_member):
    app, _ = real_app
    member = make_member()
    client = app.test_client()
    form = form_for(client)
    form["username"] = member.username
    assert error_of(sign_up(client, form))[0] == "/signup"

    client = app.test_client()
    form = form_for(client)
    form["email"] = member.user().email.upper()
    assert error_of(sign_up(client, form))[0] == "/signup"
    assert account(real_app, form["username"]) is None


def test_closed_sign_ups_make_nothing(real_app):
    app, _ = real_app
    client = app.test_client()
    form = form_for(client)
    app.config["DISABLE_SIGNUPS"] = 1
    try:
        path, error = error_of(sign_up(client, form))
    finally:
        app.config["DISABLE_SIGNUPS"] = 0
    assert path == "/signup" and error
    assert account(real_app, form["username"]) is None


# ---------------------------------------------------------------- the signup guard

import json                                              # noqa: E402

import pytest                                            # noqa: E402


def made(real_app, username):
    """The new account's row and its review, as the database has them now."""
    _, db_session = real_app
    db = db_session()
    db.rollback()
    return db.execute(text("SELECT u.id, u.quarantined_utc, s.decision, s.reasons, s.score FROM users u "
                           "LEFT JOIN signup_reviews s ON s.user_id = u.id WHERE u.username = :u"),
                      {"u": username}).first()


def held_client(real_app):
    """A browser signed in to a new account the guard held (made during a surge)."""
    from ruqqus.__main__ import r
    from ruqqus.helpers import signup_signals
    app, _ = real_app
    signup_signals.save_settings(r, 50, True, 60)
    try:
        client = app.test_client()
        form = form_for(client)
        sign_up(client, form)
    finally:
        signup_signals.save_settings(r, 50, False, 60)
    row = made(real_app, form["username"])
    assert row.quarantined_utc > 0 and row.decision == ""
    return client, form, row


@pytest.fixture
def surge_off(real_app):
    from ruqqus.__main__ import r
    from ruqqus.helpers import signup_signals
    signup_signals.save_settings(r, 50, False, 60)
    yield
    signup_signals.save_settings(r, 50, False, 60)


def test_an_ordinary_sign_up_is_let_through_and_recorded(real_app, surge_off):
    app, _ = real_app
    client = app.test_client()
    form = form_for(client)
    sign_up(client, form)
    row = made(real_app, form["username"])
    assert row.quarantined_utc == 0 and row.decision == "allowed"


@pytest.mark.parametrize("change, headers", [
    ({"website": "https://example.com"}, None),
    ({}, {"User-Agent": "python-requests/2.32.3", "Accept-Language": "en"}),
])
def test_a_bot_is_refused_with_the_generic_message_and_nothing_is_made(real_app, surge_off, change, headers):
    app, _ = real_app
    client = app.test_client()
    agent = (headers or HEADERS)["User-Agent"]
    form = {**form_for(client, agent=agent), **change}
    path, error = error_of(sign_up(client, form, headers=headers))
    assert path == "/signup" and error == "There was a problem. Please try again."
    assert account(real_app, form["username"]) is None


def test_a_form_kept_for_hours_is_refused(real_app, surge_off):
    app, _ = real_app
    client = app.test_client()
    form = form_for(client, age=3 * 3600)
    assert error_of(sign_up(client, form))[0] == "/signup"
    assert account(real_app, form["username"]) is None


def test_a_throwaway_email_gets_its_own_message(real_app, surge_off):
    app, _ = real_app
    client = app.test_client()
    form = {**form_for(client), "email": "someone@mailinator.com"}
    path, error = error_of(sign_up(client, form))
    assert path == "/signup" and "provider you keep" in error
    assert account(real_app, form["username"]) is None


def test_a_burst_from_one_network_holds_the_fourth_account(real_app, surge_off):
    app, _ = real_app
    network = f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}"
    names = []
    for host in (1, 2, 3, 4):
        client = app.test_client()
        form = form_for(client)
        sign_up(client, form, ip=f"{network}.{host}")
        names.append(form["username"])
    rows = [made(real_app, n) for n in names]
    assert [bool(row.quarantined_utc) for row in rows] == [False, False, False, True]
    assert "prefix_burst" in rows[3].reasons.split(",")


def test_a_held_account_can_read_and_change_settings_but_not_act_on_others(real_app, make_member):
    from tests.pg.conftest import Member
    from ruqqus.classes import User
    app, db_session = real_app
    _, form, row = held_client(real_app)
    member = Member(app, db_session, db_session().query(User).filter_by(id=row.id).first(), form["password"])
    browser = member.browser()
    key = Member.formkey(browser)
    other = make_member()

    home = browser.get("/", base_url=BASE, headers=HEADERS)
    assert home.status_code == 200 and b'id="held-notice"' in home.data
    assert browser.get("/settings/security", base_url=BASE, headers=HEADERS).status_code == 200

    for path, data in ((f"/api/follow/{other.username}", {}), ("/api/comment", {"parent_fullname": "t2_1", "body": "hi"}),
                       ("/api/vue/submit", {"title": "hello", "body": "spam"}), ("/api/vote/post/1/1", {})):
        answer = browser.post(path, base_url=BASE, headers=HEADERS, data={**data, "formkey": key},
                              environ_base={"REMOTE_ADDR": address()})
        assert answer.status_code == 403, path
        assert "waiting for a quick check" in json.loads(answer.data)["error"], path

    from ruqqus.__main__ import r
    from ruqqus.helpers import signup_signals
    tried = signup_signals.attempts(r, row.id)
    assert tried == {"follow": 1, "comment": 1, "post": 1, "vote": 1}

    # a plain form the browser navigated with gets the message page; an /api/ path stays JSON either way
    page = browser.post("/gift_post/1", base_url=BASE, data={"formkey": key},
                        headers={**HEADERS, "Sec-Fetch-Mode": "navigate"}, environ_base={"REMOTE_ADDR": address()})
    assert page.status_code == 403 and not page.is_json and b'href="/settings/security"' in page.data
    api = browser.post(f"/api/follow/{other.username}", base_url=BASE, data={"formkey": key},
                       headers={**HEADERS, "Sec-Fetch-Mode": "navigate"}, environ_base={"REMOTE_ADDR": address()})
    assert api.status_code == 403 and api.is_json


def test_verifying_an_email_releases_a_held_account(real_app):
    from ruqqus.helpers.security import generate_hash
    client, _, row = held_client(real_app)
    email = f"held{secrets.token_hex(3)}@example.com"
    now = int(time.time())
    link = f"/activate?email={email}&id={row.id}&time={now}&token={generate_hash(f'{email}+{row.id}+{now}')}"
    assert client.get(link, base_url=BASE, headers=HEADERS).status_code == 200
    after = made(real_app, account_name(real_app, row.id))
    assert after.quarantined_utc == 0 and after.decision == "verified"


def test_a_throwaway_address_does_not_release_it(real_app):
    from ruqqus.helpers.security import generate_hash
    client, _, row = held_client(real_app)
    email = f"held{secrets.token_hex(3)}@mailinator.com"
    now = int(time.time())
    link = f"/activate?email={email}&id={row.id}&time={now}&token={generate_hash(f'{email}+{row.id}+{now}')}"
    client.get(link, base_url=BASE, headers=HEADERS)
    assert made(real_app, account_name(real_app, row.id)).quarantined_utc > 0


def account_name(real_app, uid):
    _, db_session = real_app
    db = db_session()
    db.rollback()
    return db.execute(text("SELECT username FROM users WHERE id = :i"), {"i": uid}).scalar()


def admin(make_member, level=3):
    member = make_member()
    db = member.db_session()
    db.execute(text("UPDATE users SET admin_level = :l WHERE id = :i"), {"l": level, "i": member.id})
    db.commit()
    return member


def test_the_queue_is_for_admins_only(real_app, make_member):
    assert make_member().browser().get("/admin/signups", base_url=BASE, headers=HEADERS).status_code in (403, 404)
    visitor = real_app[0].test_client().get("/admin/signups", base_url=BASE, headers=HEADERS)
    assert visitor.status_code in (401, 403, 404) or urlsplit(visitor.headers["Location"]).path == "/login"


def test_an_admin_sees_the_held_account_and_approves_it(real_app, make_member):
    from tests.pg.conftest import Member
    _, form, row = held_client(real_app)
    boss = admin(make_member)
    browser = boss.browser()
    page = browser.get("/admin/signups", base_url=BASE, headers=HEADERS)
    assert page.status_code == 200 and f'id="signup-{row.id}"'.encode() in page.data
    assert b"Signed up during a surge" in page.data
    answer = browser.post("/admin/signups/decide", base_url=BASE, headers=HEADERS,
                          data={"formkey": Member.formkey(browser), "uid": [str(row.id)], "decision": "approve"})
    assert answer.status_code == 302
    after = made(real_app, form["username"])
    assert after.quarantined_utc == 0 and after.decision == "approved"


def test_an_admin_bans_a_whole_network_cluster_of_held_accounts_only(real_app, make_member, surge_off):
    from tests.pg.conftest import Member
    from ruqqus.__main__ import r
    from ruqqus.helpers import signup_signals
    app, _ = real_app
    network = f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}"
    names = []
    for host in (1, 2, 3, 4, 5):                       # three let through, then two held
        client = app.test_client()
        form = form_for(client)
        sign_up(client, form, ip=f"{network}.{host}")
        names.append(form["username"])
    rows = [made(real_app, n) for n in names]
    _, db_session = real_app
    network_hash = db_session().execute(text("SELECT network FROM signup_reviews WHERE user_id = :i"),
                                        {"i": rows[4].id}).scalar()
    boss = admin(make_member)
    browser = boss.browser()
    browser.post("/admin/signups/cluster", base_url=BASE, headers=HEADERS,
                 data={"formkey": Member.formkey(browser), "cluster": f"ban|network:{network_hash}"})
    banned = dict(db_session().execute(text("SELECT username, is_banned FROM users WHERE username IN :n").bindparams(
        __import__("sqlalchemy").bindparam("n", expanding=True)), {"n": names}).all())
    assert [bool(banned[n]) for n in names] == [False, False, False, True, True]
    assert signup_signals.tallies(r, 1).get("quarantine", 0) >= 2


def test_settings_are_for_level_four(real_app, make_member):
    from tests.pg.conftest import Member
    from ruqqus.__main__ import r
    from ruqqus.helpers import signup_signals
    three = admin(make_member, 3).browser()
    assert three.post("/admin/signups/settings", base_url=BASE, headers=HEADERS,
                      data={"formkey": Member.formkey(three), "quarantine_at": "5", "surge_ceiling": "60"}
                      ).status_code in (403, 404)
    four = admin(make_member, 4).browser()
    try:
        assert four.post("/admin/signups/settings", base_url=BASE, headers=HEADERS,
                         data={"formkey": Member.formkey(four), "quarantine_at": "70", "surge_ceiling": "90"}
                         ).status_code == 302
        assert signup_signals.settings(r) == {"quarantine_at": 70, "surge": False, "surge_ceiling": 90}
    finally:
        signup_signals.save_settings(r, 50, False, 60)


# ---------------------------------------------------------------- with a captcha provider

@pytest.fixture
def turnstile(real_app, monkeypatch, surge_off):
    import requests
    app, _ = real_app
    for key, value in (("CAPTCHA_PROVIDER", "turnstile"), ("TURNSTILE_SITEKEY", "site"), ("TURNSTILE_SECRET", "secret")):
        monkeypatch.setitem(app.config, key, value)
    answers = {}

    def fake_post(url, data=None, timeout=None):
        answers["asked"] = (url, data)
        if answers.get("down"):
            raise requests.ConnectionError("down")

        class Answer:
            status_code = 200

            @staticmethod
            def json():
                return {"success": data["response"] == "good"}
        return Answer()
    monkeypatch.setattr(requests, "post", fake_post)
    return answers


def test_the_page_draws_turnstile(real_app, turnstile):
    page = real_app[0].test_client().get("/signup", base_url=BASE, headers=HEADERS)
    assert b'class="cf-turnstile mt-3" data-sitekey="site"' in page.data
    assert b"challenges.cloudflare.com/turnstile/v0/api.js" in page.data


@pytest.mark.parametrize("answer, outcome", [(None, "refused"), ("bad", "refused"), ("good", "allowed")])
def test_the_captcha_answer_decides(real_app, turnstile, answer, outcome):
    app, _ = real_app
    client = app.test_client()
    form = form_for(client)
    if answer:
        form["cf-turnstile-response"] = answer
    sign_up(client, form)
    row = made(real_app, form["username"])
    assert (row is None) == (outcome == "refused")
    if answer:
        assert turnstile["asked"][1]["remoteip"].startswith("10.")


def test_the_captcha_service_being_down_holds_the_account(real_app, turnstile):
    turnstile["down"] = True
    app, _ = real_app
    client = app.test_client()
    form = {**form_for(client), "cf-turnstile-response": "good"}
    sign_up(client, form)
    row = made(real_app, form["username"])
    assert row.quarantined_utc > 0 and "captcha_unavailable" in row.reasons
