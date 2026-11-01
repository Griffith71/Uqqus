"""The groundwork of QR sign-in on the real app and a real Postgres: the sign-in function login_post shares still does what the
route did, how a sign-in was made is recorded and shown, and the setting in Settings > Security (helpers/qr_login.py)."""
import secrets
import sys
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import text

from .test_age_declaration import sign_up_form

BASE = "http://localhost"
AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15"
HEADERS = {"User-Agent": AGENT, "Accept-Language": "en-GB,en;q=0.9"}


def address():
    return f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"


def get(client, path):
    return client.get(path, base_url=BASE, headers=HEADERS, environ_base={"REMOTE_ADDR": address()})


def post(client, path, **form):
    return client.post(path, base_url=BASE, headers=HEADERS, environ_base={"REMOTE_ADDR": address()}, data=form)


def sign_in(member):
    client = member.app.test_client()
    answer = post(client, "/login", username=member.username, password=member.password)
    assert answer.status_code == 302, answer.get_data(as_text=True)[:300]
    return client


def session_of(client):
    with client.session_transaction() as session:
        return dict(session)


def methods(member):
    db = member.db_session()
    db.rollback()
    return [row[0] for row in db.execute(text("SELECT method FROM login_events WHERE user_id = :u ORDER BY id"), {"u": member.id})]


def is_off(member):
    return member.user().qr_login_off


def said(answer, key):
    return parse_qs(urlparse(answer.headers["Location"]).query).get(key, [""])[0]


def notices(member):
    db = member.db_session()
    db.rollback()
    return db.execute(text("SELECT count(*) FROM notifications n JOIN comments_aux a ON a.id = n.comment_id "
                           "WHERE n.user_id = :u AND a.body LIKE 'QR sign-in was turned on%'"), {"u": member.id}).scalar()


@pytest.fixture
def offered(monkeypatch):
    monkeypatch.setenv("QR_LOGIN_ENABLED", "1")


@pytest.fixture
def mail(real_app, monkeypatch):
    sent = []
    # by the module itself: `ruqqus.routes.settings` as a name is the route function of that name (the routes' star import)
    monkeypatch.setattr(sys.modules["ruqqus.routes.settings"], "send_mail_quietly", lambda to, subject, html: sent.append((to, subject, html)))
    return sent


def wait_for_greenlets():
    import gevent
    gevent.sleep(0.2)


# ------------------------------------------------------- the shared sign-in

def test_a_password_sign_in_leaves_the_browser_as_it_always_did_and_says_how(make_member):
    member = make_member()
    client = sign_in(member)
    session = session_of(client)
    assert session["user_id"] == member.id and session["login_nonce"] == member.user().login_nonce and session["session_id"]
    assert session["_permanent"] is True
    assert member.id in session["history"], "check_for_alts ran"
    assert methods(member) == ["password"]
    assert get(client, "/settings/security").status_code == 200


def test_a_sign_in_through_two_factor_is_a_password_sign_in_too(make_member):
    import pyotp
    member = make_member()
    db = member.db_session()
    secret = pyotp.random_base32()
    db.execute(text("UPDATE users SET mfa_secret = :s WHERE id = :u"), {"s": secret, "u": member.id})
    db.commit()
    client = member.app.test_client()
    page = post(client, "/login", username=member.username, password=member.password)
    assert page.status_code == 200 and methods(member) == [], "not signed in until the code is given"
    html = page.get_data(as_text=True)
    now = html.split('name="time" value="', 1)[1].split('"', 1)[0]
    token = html.split('name="hash" value="', 1)[1].split('"', 1)[0]
    answer = post(client, "/login", username=member.username, time=now, hash=token, **{"2fa_token": pyotp.TOTP(secret).now()})
    assert answer.status_code == 302 and session_of(client)["user_id"] == member.id
    assert methods(member) == ["password"]


def test_two_accounts_on_one_browser_are_still_linked(make_member, real_app):
    first, second = make_member(), make_member()
    client = first.app.test_client()
    post(client, "/login", username=first.username, password=first.password)
    post(client, "/login", username=second.username, password=second.password)
    db = first.db_session()
    db.rollback()
    assert db.execute(text("SELECT count(*) FROM alts WHERE (user1 = :a AND user2 = :b) OR (user1 = :b AND user2 = :a)"),
                      {"a": first.id, "b": second.id}).scalar() == 1


def test_the_ad_hold_is_set_by_the_password_route_with_ads_on(make_member, monkeypatch):
    from ruqqus.helpers import ads
    monkeypatch.setattr(ads, "live", lambda cfg: True)
    session = session_of(sign_in(make_member()))
    assert session["no_ads_until"] > 0


def test_the_shared_function_alone_holds_no_ads(make_member, monkeypatch):
    """What a QR sign-in will call: nothing about it writes a pre-key, so nothing about it holds the ads back."""
    from flask import g, session as flask_session
    from ruqqus.helpers import ads
    from ruqqus.routes.login import complete_sign_in
    monkeypatch.setattr(ads, "live", lambda cfg: True)
    member = make_member()
    with member.app.test_request_context("/", base_url=BASE, headers=HEADERS, environ_base={"REMOTE_ADDR": address()}):
        g.db = member.db_session()
        complete_sign_in(member.user(), "qr")
        assert flask_session["user_id"] == member.id and "no_ads_until" not in flask_session
        assert "history" not in flask_session, "no alts link until a password is typed on this browser (test_qr_login.py)"
    assert methods(member) == ["qr"]


def test_a_sign_up_is_recorded_as_a_password(real_app):
    app, db_session = real_app
    client = app.test_client()
    form = sign_up_form(client, age_declaration="1")
    answer = post(client, "/signup", **form)
    assert answer.status_code == 302, answer.get_data(as_text=True)[:300]
    db = db_session()
    db.rollback()
    assert db.execute(text("SELECT e.method FROM login_events e JOIN users u ON u.id = e.user_id WHERE u.username = :n"),
                      {"n": form["username"]}).scalar() == "password"


# ------------------------------------------------------- what Recent sign-ins says

def test_recent_sign_ins_says_how_and_nothing_for_an_older_row(make_member):
    member = make_member()
    db = member.db_session()
    for when, method in ((100, None), (200, "qr")):
        db.execute(text("INSERT INTO login_events (user_id, created_utc, cf_country, user_agent, method) VALUES (:u, :t, 'GB', :a, :m)"),
                   {"u": member.id, "t": when, "a": AGENT, "m": method})
    db.commit()
    rows = get(member.browser(), "/settings/security").get_data(as_text=True).split('class="sign-in-row"')[1:]
    assert "QR code" in rows[0] and "Password" not in rows[0].split("</li>")[0]
    older = rows[1].split("</li>")[0]
    assert "QR code" not in older and "Password" not in older


# ------------------------------------------------------- the setting

def test_with_the_switch_off_the_setting_isnt_drawn_and_a_request_changes_nothing(make_member, monkeypatch):
    monkeypatch.setenv("QR_LOGIN_ENABLED", "0")
    member = make_member()
    client = member.browser()
    page = get(client, "/settings/security").get_data(as_text=True)
    assert 'id="qr-login"' not in page and "qr_login_form" not in page
    answer = post(client, "/settings/security", formkey=member.formkey(client), qr_login_form="1", qr_login="off")
    assert answer.status_code == 302 and said(answer, "error") == "Nothing to change."
    assert is_off(member) is False


def test_read_only_mode_hides_it_too(make_member, offered, real_app, monkeypatch):
    app, _ = real_app
    member = make_member()
    client = member.browser()
    assert 'id="qr-login"' in get(client, "/settings/security").get_data(as_text=True)
    monkeypatch.setitem(app.config, "READ_ONLY", True)
    assert 'id="qr-login"' not in get(client, "/settings/security").get_data(as_text=True)


def test_turning_it_off_asks_nothing_and_tells_nobody(make_member, offered, mail):
    member = make_member()
    client = member.browser()
    page = get(client, "/settings/security").get_data(as_text=True).split('id="qr-login"')[1].split("</form>")[0]
    assert "QR sign-in is on." in page and 'name="password"' not in page
    answer = post(client, "/settings/security", formkey=member.formkey(client), qr_login_form="1", qr_login="off")
    assert answer.status_code == 302 and said(answer, "msg") == "QR sign-in is off."
    assert is_off(member) is True
    wait_for_greenlets()
    assert mail == [] and notices(member) == 0


def test_the_form_key_is_still_asked(make_member, offered):
    member = make_member()
    client = member.browser()
    answer = post(client, "/settings/security", qr_login_form="1", qr_login="off")
    assert answer.status_code == 302 and "/login" in answer.headers["Location"], "a 401, which sends a page to sign in"
    assert is_off(member) is False


def switched_off(member):
    db = member.db_session()
    db.execute(text("UPDATE users SET qr_login_off = true WHERE id = :u"), {"u": member.id})
    db.commit()


def test_turning_it_back_on_needs_the_password(make_member, offered, mail):
    member = make_member()
    switched_off(member)
    client = member.browser()
    page = get(client, "/settings/security").get_data(as_text=True).split('id="qr-login"')[1].split("</form>")[0]
    assert "QR sign-in is off." in page and 'name="password"' in page
    for form in ({"password": "wrong"}, {"password": ""}, {}):
        answer = post(client, "/settings/security", formkey=member.formkey(client), qr_login_form="1", qr_login="on", **form)
        assert answer.status_code == 302 and said(answer, "error") == "Invalid password."
        assert is_off(member) is True
    wait_for_greenlets()
    assert mail == [] and notices(member) == 0


def test_turning_it_back_on_tells_the_member_by_notification_and_email(make_member, offered, mail):
    member = make_member()
    switched_off(member)
    db = member.db_session()
    db.execute(text("UPDATE users SET is_activated = true WHERE id = :u"), {"u": member.id})
    db.commit()
    client = member.browser()
    ip = "198.51.100.77"
    answer = client.post("/settings/security", base_url=BASE, environ_base={"REMOTE_ADDR": ip},
                         headers={**HEADERS, "cf-ipcountry": "DE"},
                         data={"formkey": member.formkey(client), "qr_login_form": "1", "qr_login": "on", "password": member.password})
    assert answer.status_code == 302 and said(answer, "msg") == "QR sign-in is on."
    assert is_off(member) is False
    assert notices(member) == 1
    assert "QR sign-in was turned on for your account." in get(client, "/notifications/system").get_data(as_text=True)
    wait_for_greenlets()
    assert len(mail) == 1
    to, subject, html = mail[0]
    assert to == member.user().email and "QR sign-in was turned on" in subject
    assert "Germany" in html and "Safari on macOS" in html and ip not in html


def test_the_email_goes_by_the_sign_in_emails_rule_but_the_notification_always(make_member, offered, mail):
    unverified = make_member()
    switched_off(unverified)
    client = unverified.browser()
    post(client, "/settings/security", formkey=unverified.formkey(client), qr_login_form="1", qr_login="on", password=unverified.password)
    assert is_off(unverified) is False and notices(unverified) == 1

    quiet = make_member()
    switched_off(quiet)
    db = quiet.db_session()
    db.execute(text("UPDATE users SET is_activated = true, signin_alerts = false WHERE id = :u"), {"u": quiet.id})
    db.commit()
    client = quiet.browser()
    post(client, "/settings/security", formkey=quiet.formkey(client), qr_login_form="1", qr_login="on", password=quiet.password)
    assert is_off(quiet) is False and notices(quiet) == 1
    wait_for_greenlets()
    assert mail == []


def test_on_when_it_is_already_on_asks_nothing_and_tells_nobody(make_member, offered, mail):
    member = make_member()
    client = member.browser()
    answer = post(client, "/settings/security", formkey=member.formkey(client), qr_login_form="1", qr_login="on")
    assert answer.status_code == 302 and said(answer, "msg") == "QR sign-in is on."
    wait_for_greenlets()
    assert notices(member) == 0 and mail == []


# ------------------------------------------------------- the export and closing the account

def test_the_export_has_both(make_member):
    from ruqqus.helpers import data_export
    member = make_member()
    sign_in(member)
    switched_off(member)
    db = member.db_session()
    exported = data_export.build(db, member.id)
    assert exported["account"][0]["qr_login_off"] is True
    assert exported["sign_ins"][0]["method"] == "password"


def test_closing_an_account_takes_how_it_signed_in_with_the_rows(make_member):
    """The row in `users` stays (a closed account is marked, not deleted), so the switch stays on it as `signin_alerts` does: it
    is a setting of an account nobody can sign in to, and says nothing about the person."""
    from ruqqus.helpers import account_cleanup
    member = make_member()
    sign_in(member)
    db = member.db_session()
    assert account_cleanup.close_account(db, member.id, 1_900_000_000) == []
    db.commit()
    assert methods(member) == []
