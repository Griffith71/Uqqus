"""QR sign-in on the real app, a real Postgres and a real Redis: the whole flow once and never again, what ties a code to a browser,
who may approve and finish, what a limited sign-in may not do until its password is given, and its 12-hour end
(helpers/qr_login.py, routes/qr_login_pages.py, CLAUDE.md "QR sign-in")."""
import importlib.util
import json
import re
import secrets
import time
from pathlib import Path

import pyotp
import pytest
from sqlalchemy import text

from ruqqus.helpers import qr_login

from .test_visitor_cookie import site_cookie

BASE = "http://localhost"
PHONE_AGENT = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
DESK_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
DESK = {"User-Agent": DESK_AGENT, "Accept-Language": "en-GB,en;q=0.9", "CF-IPCountry": "DE"}
PHONE = {"User-Agent": PHONE_AGENT, "Accept-Language": "en-GB,en;q=0.9"}
NOT_ALLOWED = "This code can't be used."


def address():
    return f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"


def desk_get(client, path):
    return client.get(path, base_url=BASE, headers=DESK, environ_base={"REMOTE_ADDR": address()})


def desk_post(client, path, **form):
    return client.post(path, base_url=BASE, headers=DESK, environ_base={"REMOTE_ADDR": address()}, data=form)


def phone_post(client, path, **form):
    return client.post(path, base_url=BASE, headers=PHONE, environ_base={"REMOTE_ADDR": address()}, data=form)


def phone_get(client, path, headers=None):
    return client.get(path, base_url=BASE, headers={**PHONE, **(headers or {})}, environ_base={"REMOTE_ADDR": address()})


@pytest.fixture(autouse=True)
def switched_on(monkeypatch):
    monkeypatch.delenv("QR_LOGIN_ENABLED", raising=False)


@pytest.fixture
def codes(monkeypatch):
    """Every code made, in order: a test can't read them from the picture (the browser test does: scripts/ui/qr_login.spec.ts)."""
    made = []
    real = qr_login.new_code

    def recording():
        code, digest = real()
        made.append(code)
        return code, digest
    monkeypatch.setattr(qr_login, "new_code", recording)
    return made


def start(real_app, codes):
    """A computer that pressed the button, and its code."""
    app, _ = real_app
    desk = app.test_client()
    answer = desk_post(desk, "/login/qr/start")
    assert answer.status_code == 200, answer.get_data(as_text=True)[:200]
    assert answer.get_json()["image"].startswith("data:image/png;base64,") and answer.get_json()["seconds"] == 120
    return desk, codes[-1]


def status(desk):
    return desk_get(desk, "/login/qr/status").get_json()


def approve(member, phone, code, answer=None):
    return phone_post(phone, "/login/qr/approve", code=code, formkey=member.formkey(phone), answer=member.password if answer is None else answer)


def session_of(client):
    with client.session_transaction() as session:
        return dict(session)


def signed_in_as(client):
    return session_of(client).get("user_id")


def sign_ins(member):
    db = member.db_session()
    db.rollback()
    return db.execute(text("SELECT method, user_agent, cf_country FROM login_events WHERE user_id = :u ORDER BY id"), {"u": member.id}).all()


def notices(member, like):
    db = member.db_session()
    db.rollback()
    return db.execute(text("SELECT count(*) FROM notifications n JOIN comments_aux a ON a.id = n.comment_id "
                           "WHERE n.user_id = :u AND a.body LIKE :like"), {"u": member.id, "like": like}).scalar()


def set_user(member, **values):
    db = member.db_session()
    db.rollback()
    db.execute(text("UPDATE users SET " + ", ".join(f"{k} = :{k}" for k in values) + " WHERE id = :id"), {**values, "id": member.id})
    db.commit()


def limited_browser(real_app, codes, member):
    """A browser signed in through a code, the way a real one gets there."""
    desk, code = start(real_app, codes)
    phone = member.browser()
    assert approve(member, phone, code).status_code == 200
    answer = desk_post(desk, "/login/qr/finish")
    assert answer.status_code == 200, answer.get_data(as_text=True)[:200]
    return desk


# --- the flow ----------------------------------------------------------------------------------------------------------------

def test_the_whole_flow_once_and_never_again(real_app, codes, make_member):
    member = make_member()
    desk, code = start(real_app, codes)
    phone = member.browser()
    assert status(desk) == {"state": "waiting"}

    info = phone_post(phone, "/login/qr/info", code=code, formkey=member.formkey(phone))
    assert info.status_code == 200 and info.get_json()["browser"] == "Chrome on Windows" and info.get_json()["place"] == "Germany"

    assert approve(member, phone, code).get_json() == {"ok": True}
    assert status(desk) == {"state": "approved", "name": member.username}

    finished = desk_post(desk, "/login/qr/finish", redirect="/settings/content")
    assert finished.get_json() == {"redirect": "/settings/content"}
    session = session_of(desk)
    assert session["user_id"] == member.id and session["qr_limited"] is True and "qr_pending" not in session
    assert abs(session["qr_expires_utc"] - (time.time() + 12 * 3600)) < 30
    assert sign_ins(member)[-1] == ("qr", DESK_AGENT[:len(sign_ins(member)[-1][1])], "DE"), "the row describes the computer"

    # never again, by the same browser or another
    other = real_app[0].test_client()
    assert desk_post(other, "/login/qr/finish").status_code == 410
    assert approve(member, member.browser(), code).status_code == 410


def test_the_approval_goes_only_to_the_browser_that_asked(real_app, codes, make_member):
    member = make_member()
    desk, code = start(real_app, codes)
    approve(member, member.browser(), code)
    stranger = real_app[0].test_client()
    assert status(stranger) == {"state": "expired"}, "no name, no state"
    assert desk_post(stranger, "/login/qr/finish").status_code == 410
    assert signed_in_as(stranger) is None
    assert status(desk)["state"] == "approved"


def test_status_sets_no_cookie_even_on_a_browser_whose_cookie_is_refreshed_on_every_answer(real_app, codes):
    desk, _ = start(real_app, codes)
    assert site_cookie(desk_get(desk, "/login/qr/status")) is None
    with desk.session_transaction() as session:
        session["history"] = [1]
        session.permanent = True
    assert site_cookie(desk_get(desk, "/login/qr/status")) is None, "polling must not write over what another request just set"


def test_nothing_happens_without_confirm(real_app, codes, make_member):
    member = make_member()
    desk, code = start(real_app, codes)
    approve(member, member.browser(), code)
    assert status(desk)["state"] == "approved"
    assert signed_in_as(desk) is None and [row for row in sign_ins(member) if row[0] == "qr"] == []
    assert desk_get(desk, "/login").status_code == 200, "still the sign-in page"


def test_cancel_ends_it(real_app, codes, make_member):
    member = make_member()
    desk, code = start(real_app, codes)
    approve(member, member.browser(), code)
    assert desk_post(desk, "/login/qr/cancel").get_json() == {"state": "cancelled"}
    assert desk_post(desk, "/login/qr/finish").status_code == 410
    assert signed_in_as(desk) is None


def test_a_second_press_replaces_the_first_code(real_app, codes, make_member):
    member = make_member()
    desk, first = start(real_app, codes)
    desk_post(desk, "/login/qr/start")
    second = codes[-1]
    assert first != second
    phone = member.browser()
    assert approve(member, phone, first).status_code == 410
    assert approve(member, phone, second).status_code == 200


def test_not_me_tells_the_computer_and_uses_the_code_up(real_app, codes, make_member):
    member = make_member()
    desk, code = start(real_app, codes)
    phone = member.browser()
    assert phone_post(phone, "/login/qr/deny", code=code, formkey=member.formkey(phone)).get_json() == {"ok": True}
    assert status(desk) == {"state": "denied"}
    assert approve(member, phone, code).status_code == 410
    assert desk_post(desk, "/login/qr/finish").status_code == 410


def test_not_me_after_approving_takes_the_approval_back(real_app, codes, make_member):
    member = make_member()
    desk, code = start(real_app, codes)
    phone = member.browser()
    assert approve(member, phone, code).status_code == 200
    assert status(desk)["state"] == "approved"
    assert phone_post(phone, "/login/qr/deny", code=code, formkey=member.formkey(phone)).get_json() == {"ok": True}
    assert status(desk) == {"state": "denied"}
    finished = desk_post(desk, "/login/qr/finish")
    assert finished.status_code == 410 and finished.get_json() == {"state": "denied"}, "the computer that still shows Confirm is told why"
    assert signed_in_as(desk) is None
    assert phone_post(phone, "/login/qr/deny", code=code, formkey=member.formkey(phone)).get_json() == {"ok": True}, "pressed twice"


def test_not_me_never_says_so_when_it_stops_nothing(real_app, codes, make_member):
    member, other = make_member(), make_member()
    # another account's approval is not this phone's to take back
    desk, code = start(real_app, codes)
    assert approve(member, member.browser(), code).status_code == 200
    stranger = other.browser()
    refused = phone_post(stranger, "/login/qr/deny", code=code, formkey=other.formkey(stranger))
    assert refused.status_code == 410 and refused.get_json()["error"].startswith(NOT_ALLOWED)
    assert status(desk)["state"] == "approved"
    # too late: the computer has signed in
    assert desk_post(desk, "/login/qr/finish").status_code == 200
    phone = member.browser()
    assert phone_post(phone, "/login/qr/deny", code=code, formkey=member.formkey(phone)).status_code == 410
    # no such code
    assert phone_post(phone, "/login/qr/deny", code="never-made", formkey=member.formkey(phone)).status_code == 410
    assert phone_post(phone, "/login/qr/deny", formkey=member.formkey(phone)).status_code == 410


def test_ten_computers_behind_one_address_never_use_up_each_others_questions(real_app, codes):
    """A classroom: each asks every 3 s, so 20 a minute each and 200 between them, and none of them is limited or counted."""
    from ruqqus.__main__ import r
    app, _ = real_app
    where = {"REMOTE_ADDR": address()}
    desks = [app.test_client() for _ in range(10)]
    for desk in desks:
        assert desk.post("/login/qr/start", base_url=BASE, headers=DESK, environ_base=where).status_code == 200
    answers = [desk.get("/login/qr/status", base_url=BASE, headers=DESK, environ_base=where).status_code for _ in range(13) for desk in desks]
    assert answers == [200] * 130
    assert r.get(f"429_count_{where['REMOTE_ADDR']}") is None and r.get(f"ban_ip_{where['REMOTE_ADDR']}") is None


def test_one_computer_past_its_limit_is_refused_and_the_address_isnt_counted(real_app, codes):
    from ruqqus.__main__ import r
    app, _ = real_app
    where = {"REMOTE_ADDR": address()}
    desk = app.test_client()
    assert desk.post("/login/qr/start", base_url=BASE, headers=DESK, environ_base=where).status_code == 200
    answers = [desk.get("/login/qr/status", base_url=BASE, headers=DESK, environ_base=where) for _ in range(125)]
    refused = [a for a in answers if a.status_code == 429]
    assert refused and refused[0].get_data() == b"", "no page is drawn for a script that ignores it"
    assert r.get(f"429_count_{where['REMOTE_ADDR']}") is None


def test_nobody_on_the_address_can_stop_a_confirm(real_app, codes, make_member):
    """Cookieless requests, then someone with codes of their own, from the victim's address: the victim's Confirm still works."""
    app, _ = real_app
    member = make_member()
    where = {"REMOTE_ADDR": address()}
    desk = app.test_client()
    assert desk.post("/login/qr/start", base_url=BASE, headers=DESK, environ_base=where).status_code == 200
    code = codes[-1]
    assert approve(member, member.browser(), code).status_code == 200
    for _ in range(8):
        assert app.test_client().post("/login/qr/finish", base_url=BASE, headers=DESK, environ_base=where).status_code == 410
    rival = app.test_client()
    assert rival.post("/login/qr/start", base_url=BASE, headers=DESK, environ_base=where).status_code == 200
    answers = [rival.post("/login/qr/finish", base_url=BASE, headers=DESK, environ_base=where).status_code for _ in range(8)]
    assert answers[:6] == [410] * 6 and answers[-1] == 429, "the rival's own budget, and only that"
    finished = desk.post("/login/qr/finish", base_url=BASE, headers=DESK, environ_base=where)
    assert finished.status_code == 200 and session_of(desk)["user_id"] == member.id


def test_what_a_press_keeps_is_the_two_words_the_phone_shows(real_app, codes):
    from ruqqus.__main__ import r
    desk = real_app[0].test_client()
    agent = DESK_AGENT + " ExtraTokenXYZ"
    assert desk.post("/login/qr/start", base_url=BASE, headers={**DESK, "User-Agent": agent}, environ_base={"REMOTE_ADDR": address()}).status_code == 200
    stored = r.get(qr_login.wait_key(qr_login.digest_of(codes[-1])))
    stored = stored.decode() if isinstance(stored, bytes) else stored
    assert "ExtraTokenXYZ" not in stored and "Mozilla" not in stored
    assert json.loads(stored)["ua"] == ["Chrome", "Windows"]


def test_the_code_is_taken_in_one_step_on_real_redis(real_app):
    from ruqqus.__main__ import r
    key = qr_login.ok_key("take-" + secrets.token_hex(4))
    r.set(key, "value", ex=30)
    assert qr_login.take(r, key) in ("value", b"value")
    assert qr_login.take(r, key) is None
    assert r.get(key) is None


def test_a_press_is_what_starts_it(real_app):
    from ruqqus.__main__ import r
    before = set(r.scan_iter("qr:wait:*"))
    client = real_app[0].test_client()
    answer = desk_get(client, "/login")
    assert answer.status_code == 200
    assert site_cookie(answer) is None, "no cookie on opening the page"
    assert set(r.scan_iter("qr:wait:*")) == before, "nothing written for someone who never uses it"
    page = answer.get_data(as_text=True)
    assert 'id="qr-start"' in page and "qr-image" in page and "#c=" not in page and "data:image/png" not in page


def test_the_cookie_a_press_writes_ends_with_the_browser(real_app, codes):
    client = real_app[0].test_client()
    data, lasts = site_cookie(desk_post(client, "/login/qr/start"))
    assert set(data) == {"qr_pending"} and not lasts
    assert codes[-1] not in json.dumps(data), "the digest, never the code"


def test_every_answer_is_no_store(real_app, codes, make_member):
    member = make_member()
    desk, code = start(real_app, codes)
    phone = member.browser()
    for answer in (desk_get(desk, "/login/qr/status"), phone_get(real_app[0].test_client(), "/login/qr/approve"), phone_get(phone, "/login/qr/approve"),
                   phone_post(phone, "/login/qr/info", code=code, formkey=member.formkey(phone))):
        assert answer.headers.getlist("Cache-Control") == ["no-store"]


def test_a_request_from_another_site_is_refused(real_app):
    client = real_app[0].test_client()
    for path in ("/login/qr/start", "/login/qr/finish", "/login/qr/cancel"):
        answer = client.post(path, base_url=BASE, headers={**DESK, "Origin": "https://evil.example"}, environ_base={"REMOTE_ADDR": address()})
        assert answer.status_code == 403, path


def test_a_computer_already_signed_in_can_neither_start_nor_finish(real_app, codes, make_member):
    member, other = make_member(), make_member()
    desk, code = start(real_app, codes)
    approve(member, member.browser(), code)
    with desk.session_transaction() as session:
        session["user_id"] = other.id
        session["session_id"] = secrets.token_hex(8)
        session["login_nonce"] = other.user().login_nonce
    assert desk_post(desk, "/login/qr/finish").status_code == 403
    assert desk_post(desk, "/login/qr/start").status_code == 403
    assert session_of(desk)["user_id"] == other.id


# --- who may approve and finish --------------------------------------------------------------------------------------------

def test_a_signed_out_phone_is_told_to_sign_in_with_no_way_back(real_app):
    page = phone_get(real_app[0].test_client(), "/login/qr/approve")
    html = page.get_data(as_text=True)
    assert page.status_code == 200 and "Sign in on this phone first, then scan a new code." in html
    assert 'href="/login"' in html and "/login?redirect" not in html
    assert "qr-approve-form" not in html


def test_the_page_holds_no_code_and_warns_more_if_a_link_opened_it(real_app, make_member):
    member = make_member()
    phone = member.browser()
    plain = phone_get(phone, "/login/qr/approve", {"Sec-Fetch-Site": "none"}).get_data(as_text=True)
    linked = phone_get(phone, "/login/qr/approve", {"Sec-Fetch-Site": "cross-site"}).get_data(as_text=True)
    assert "opened from a link" not in plain and "opened from a link" in linked
    assert "Only continue if you started this yourself" in plain and "Not me" in plain
    assert "/assets/js/qr_login.js" in plain and "adsbygoogle" not in plain and "plausible" not in plain


CHILD = {"age_declared": 2, "age_declared_utc": 5, "filter_level": 2}       # the database's own rule: a child account is on the Child filter


@pytest.mark.parametrize("values", [{"qr_login_off": True}, {"is_banned": 1, "ban_reason": "x"},
                                    CHILD, {**CHILD, "age_lock": 1}, {**CHILD, "age_lock": 2}])     # every locked account (the owner, 2026-11-19)
def test_an_account_that_may_not_is_refused_at_approve_in_one_sentence(real_app, codes, make_member, values):
    member = make_member()
    desk, code = start(real_app, codes)
    phone = member.browser()
    set_user(member, **values)
    answer = approve(member, phone, code)
    assert answer.status_code == 410 and answer.get_json()["error"].startswith(NOT_ALLOWED)
    info = phone_post(phone, "/login/qr/info", code=code, formkey=member.formkey(phone))
    assert info.status_code == 410 and info.get_json()["error"].startswith(NOT_ALLOWED)
    page = phone_get(phone, "/login/qr/approve").get_data(as_text=True)
    assert NOT_ALLOWED in page.replace("&#39;", "'") and "qr-approve-form" not in page
    assert status(desk) == {"state": "waiting"}, "the code stands: nothing was approved"


@pytest.mark.parametrize("change", ["switched_off", "suspended", "nonce", "locked"])
def test_what_changes_after_the_approval_stops_the_finish(real_app, codes, make_member, change):
    member = make_member()
    desk, code = start(real_app, codes)
    assert approve(member, member.browser(), code).status_code == 200
    if change == "switched_off":
        set_user(member, qr_login_off=True)
    elif change == "suspended":
        set_user(member, is_banned=1, ban_reason="x")
    elif change == "locked":
        set_user(member, **CHILD)
    else:
        set_user(member, login_nonce=member.user().login_nonce + 1)
    assert status(desk) == {"state": "expired"}
    assert desk_post(desk, "/login/qr/finish").status_code == 410
    assert signed_in_as(desk) is None and [row for row in sign_ins(member) if row[0] == "qr"] == []


def test_a_wrong_password_five_times_deletes_the_code(real_app, codes, make_member):
    member = make_member()
    desk, code = start(real_app, codes)
    phone = member.browser()
    for attempt in range(4):
        wrong = approve(member, phone, code, answer="not-it")
        assert wrong.status_code == 400 and wrong.get_json()["error"] == "That password isn't right.", attempt
    assert approve(member, phone, code, answer="not-it").status_code == 410
    assert status(desk) == {"state": "expired"}
    assert approve(member, phone, code).status_code == 410, "the right one, too late"


def with_two_factor(member):
    secret = pyotp.random_base32()
    db = member.db_session()
    db.rollback()
    db.execute(text("UPDATE users SET mfa_secret = :s WHERE id = :u"), {"s": secret, "u": member.id})
    db.commit()
    return secret


def test_two_factor_a_code_is_what_approves_and_never_the_password(real_app, codes, make_member):
    member = make_member()
    secret = with_two_factor(member)
    desk, code = start(real_app, codes)
    phone = member.browser()
    assert approve(member, phone, code, answer=member.password).status_code == 400
    assert approve(member, phone, code, answer=pyotp.TOTP(secret).now()).status_code == 200
    assert status(desk)["state"] == "approved"


def test_two_factor_a_backup_code_approves_and_is_spent(real_app, codes, make_member):
    from ruqqus.helpers import mfa_backup, mfa_backup_store
    member = make_member()
    with_two_factor(member)
    db = member.db_session()
    backup = mfa_backup.generate()
    backup = backup[0] if isinstance(backup, list) else backup
    mfa_backup_store.replace(db, member.id, [backup], int(time.time()))
    db.commit()
    desk, code = start(real_app, codes)
    phone = member.browser()
    assert approve(member, phone, code, answer=backup).status_code == 200
    _, again = start(real_app, codes)
    assert approve(member, phone, again, answer=backup).status_code == 400, "spent"


def test_two_factor_the_older_recovery_code_is_refused_and_leaves_two_factor_on(real_app, codes, make_member):
    member = make_member()
    with_two_factor(member)
    desk, code = start(real_app, codes)
    phone = member.browser()
    assert approve(member, phone, code, answer=member.user().mfa_removal_code).status_code == 400
    assert member.user().mfa_secret, "it switches two-factor off where it works, and doesn't work here"


# --- what a QR sign-in is told and tells ------------------------------------------------------------------------------------

def test_the_member_is_always_told_and_the_alert_doesnt_take_todays_email(real_app, codes, make_member, monkeypatch):
    from ruqqus.__main__ import r
    from ruqqus.routes import login as login_routes
    sent = []
    monkeypatch.setattr(login_routes, "send_mail_quietly", lambda to, subject, html: sent.append((to, subject, html)))
    monkeypatch.setattr(login_routes.gevent, "spawn", lambda fn, *args: fn(*args))
    member = make_member()
    set_user(member, is_activated=True)
    limited_browser(real_app, codes, member)
    assert notices(member, "Someone signed in to your account with a QR code%") == 1
    assert sent and sent[0][0] == member.user().email and "with a QR code" in sent[0][2]
    assert "Chrome on Windows" in sent[0][2] and not re.search(r"\b10\.\d+\.\d+\.\d+\b", sent[0][2]), "never the address"
    assert r.get(f"signin_alert:{member.id}") is None


def test_a_member_without_a_verified_address_gets_the_notice_alone(real_app, codes, make_member, monkeypatch):
    from ruqqus.routes import login as login_routes
    sent = []
    monkeypatch.setattr(login_routes, "send_mail_quietly", lambda *args: sent.append(args))
    member = make_member()
    limited_browser(real_app, codes, member)
    assert notices(member, "Someone signed in to your account with a QR code%") == 1 and sent == []


# --- the 12 hours ------------------------------------------------------------------------------------------------------------

def test_a_feed_opened_in_a_qr_sign_in_leaves_nothing_of_the_account_past_its_end(real_app, codes, make_member):
    member = make_member()
    set_user(member, feed_defaults=json.dumps({"regions": [], "languages": ["de"], "categories": [], "types": [], "advanced": {}}))
    desk = limited_browser(real_app, codes, member)
    assert desk_get(desk, "/").status_code == 200
    held = session_of(desk)
    assert held["feed_defaults_for"] == member.id and held["langcodes"] == ["de"]
    with desk.session_transaction() as session:
        session["qr_expires_utc"] = int(time.time()) - 1
    answer = desk_get(desk, "/")
    assert answer.status_code == 200
    found = site_cookie(answer)
    assert found == ({}, False) or found is None
    assert session_of(desk) == {}, "this browser never gave a password: it ends up a visitor's, with no account's number or filters in it"


def test_what_the_privacy_page_says_of_addresses_in_redis(real_app, codes, make_member):
    """A code's record holds no address. The limits are counted under the address, for as long as the page says."""
    from ruqqus.__main__ import r
    app, _ = real_app
    computer, handset = address(), address()
    desk = app.test_client()
    assert desk.post("/login/qr/start", base_url=BASE, headers=DESK, environ_base={"REMOTE_ADDR": computer}).status_code == 200
    code = codes[-1]
    digest = qr_login.digest_of(code)
    assert computer not in str(r.get(qr_login.wait_key(digest)))
    keys = r.keys(f"*{computer}*")
    assert keys and all(0 < r.ttl(key) <= 60 for key in keys), [(key, r.ttl(key)) for key in keys]

    member = make_member()
    phone = member.browser()
    answer = phone.post("/login/qr/approve", base_url=BASE, headers=PHONE, environ_base={"REMOTE_ADDR": handset},
                        data={"code": code, "formkey": member.formkey(phone), "answer": member.password})
    assert answer.status_code == 200, answer.get_data(as_text=True)[:200]
    assert handset not in str(r.get(qr_login.ok_key(digest)))
    keys = r.keys(f"*{handset}*")
    assert any(b"throttle:qr_approve:" in (key if isinstance(key, bytes) else key.encode()) for key in keys)
    assert all(0 < r.ttl(key) <= 10 * 3600 for key in keys), [(key, r.ttl(key)) for key in keys]


def test_it_is_signed_out_at_its_end_and_the_cookie_lets_go(real_app, codes, make_member):
    member = make_member()
    desk = limited_browser(real_app, codes, member)
    with desk.session_transaction() as session:
        session["qr_expires_utc"] = int(time.time()) - 1
    answer = desk_get(desk, "/me")
    assert answer.status_code == 401 or "/login" in answer.headers.get("Location", "")
    session = session_of(desk)
    for key in ("user_id", "session_id", "login_nonce", "qr_expires_utc", "qr_limited"):
        assert key not in session, key
    assert not session.get("history"), "a QR sign-in adds nothing to the browser's list of accounts: only a typed password does"


def test_the_sign_in_check_refuses_it_by_itself_past_its_end(real_app, make_member):
    """Without `settle`, which runs first on a real request: a path that skips it (an error page, a job) still gets nobody."""
    app, db_session = real_app
    from flask import g, session as flask_session
    from ruqqus.helpers.wrappers import get_logged_in_user
    member = make_member()
    with app.test_request_context("/", base_url=BASE):
        g.db = db_session()
        flask_session["user_id"] = member.id
        flask_session["login_nonce"] = member.user().login_nonce
        qr_login.begin_limited(flask_session, time.time())
        assert get_logged_in_user()[0].id == member.id
        flask_session[qr_login.EXPIRES_KEY] = int(time.time()) - 1
        assert get_logged_in_user() == (None, None)
        g.db.close()


def test_nothing_moves_its_end(real_app, codes, make_member):
    member = make_member()
    desk = limited_browser(real_app, codes, member)
    end = session_of(desk)["qr_expires_utc"]
    with desk.session_transaction() as session:
        session["qr_expires_utc"] = end - 100
    for path in ("/", "/settings/content", "/login/qr/upgrade", "/chat"):
        desk_get(desk, path)
    assert session_of(desk)["qr_expires_utc"] == end - 100


def test_a_log_out_clears_it(real_app, codes, make_member):
    member = make_member()
    desk = limited_browser(real_app, codes, member)
    assert desk_post(desk, "/logout", formkey=member.formkey(desk)).status_code == 302
    desk_get(desk, "/")
    session = session_of(desk)
    assert "qr_limited" not in session and "qr_expires_utc" not in session and not session.get("user_id")


def test_log_out_everywhere_from_the_phone_ends_it(real_app, codes, make_member):
    member = make_member()
    desk = limited_browser(real_app, codes, member)
    set_user(member, login_nonce=member.user().login_nonce + 1)
    answer = desk_get(desk, "/settings/content")
    assert answer.status_code == 302 and "/login" in answer.headers["Location"]


# --- the limited session: every listed route --------------------------------------------------------------------------------

def _listed():
    path = Path(__file__).resolve().parent.parent / "test_qr_login_assets.py"
    spec = importlib.util.spec_from_file_location("qr_login_assets_list", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.LISTED


def _urls_of(app, endpoint):
    from werkzeug.routing.converters import AnyConverter, IntegerConverter
    for rule in app.url_map.iter_rules(endpoint):
        values = {}
        for name, converter in (rule._converters or {}).items():
            if isinstance(converter, AnyConverter):
                values[name] = list(converter.items)[0]
            elif isinstance(converter, IntegerConverter):
                values[name] = 1
            else:
                values[name] = "x"
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            yield method, rule.build(values)[1]        # this rule's own address: building by endpoint gives one address for all of them


def test_every_listed_route_is_refused_until_the_password(real_app, codes, make_member):
    LISTED = _listed()
    app, _ = real_app
    member = make_member()
    desk = limited_browser(real_app, codes, member)
    key = member.formkey(desk)
    checked = set()
    for _module, name in sorted(LISTED):
        endpoints = [e for e in app.view_functions if e == name or e.endswith("." + name)]
        assert endpoints, name
        for method, url in _urls_of(app, endpoints[0]):
            headers = {**DESK, "Accept": "application/json"}
            got = desk.open(url, method=method, base_url=BASE, headers=headers, data={"formkey": key} if method != "GET" else None,
                            environ_base={"REMOTE_ADDR": address()})
            # /api/v1 only ever takes a token, and /api/v2 asks for one to write: a session never gets through either
            if url.startswith("/api/v1/") or (url.startswith("/api/v2/") and method != "GET"):
                assert got.status_code == 401, (name, method, url, got.status_code)
                continue
            assert got.status_code == 403, (name, method, url, got.status_code)
            if method == "GET" and not url.startswith("/api/"):
                assert 'action="/login/qr/upgrade"' in got.get_data(as_text=True), (name, url)
            else:
                assert got.get_json().get("need_password") is True, (name, method, url)
            checked.add(name)
    assert {name for _module, name in LISTED} == checked


def rows(member, sql, **values):
    db = member.db_session()
    db.rollback()
    return db.execute(text(sql), values).all()


def test_a_limited_sign_in_saves_a_draft_but_never_schedules_one(real_app, codes, make_member):
    """A scheduled post goes out as the member after log out everywhere, and for up to a year."""
    member = make_member()
    desk = limited_browser(real_app, codes, member)
    key = member.formkey(desk)
    soon = int(time.time()) + 3600
    refused = desk_post(desk, "/api/drafts", title="posted by whoever held the code", body="x", formkey=key, publish_utc=str(soon))
    assert refused.status_code == 403 and refused.get_json()["need_password"] is True
    assert rows(member, "SELECT count(*) FROM post_drafts WHERE user_id = :u", u=member.id)[0][0] == 0
    saved = desk_post(desk, "/api/drafts", title="a draft", body="x", formkey=key)
    assert saved.status_code == 200 and saved.get_json()["status"] == "draft"
    # one the member scheduled can't be rescheduled either, and saving over it unschedules it, as for anyone
    again = desk_post(desk, "/api/drafts", title="a draft", body="x", formkey=key, publish_utc=str(soon), draft_id=str(saved.get_json()["id"]))
    assert again.status_code == 403
    assert desk_post(desk, "/login/qr/upgrade", formkey=key, password=member.password).status_code == 302
    # the password gave this browser a new session id (helpers/sign_in_ends.py), so the pages drawn after it carry a new form key
    assert member.formkey(desk) != key
    assert desk_post(desk, "/api/drafts", title="a draft", body="x", formkey=member.formkey(desk), publish_utc=str(soon)).get_json()["status"] == "scheduled"
    # never left scheduled: an hour from now it would be due, and the scheduler's own tests (test_bughunt_fixes.py) would publish it
    db = member.db_session()
    db.execute(text("DELETE FROM post_drafts WHERE user_id = :u"), {"u": member.id})
    db.commit()


def test_a_limited_sign_in_never_starts_a_video_on_the_members_own_channel(real_app, codes, make_member):
    member = make_member()
    desk = limited_browser(real_app, codes, member)
    key = member.formkey(desk)
    video = desk_post(desk, "/api/media/uploads", kind="video", filename="scam.mp4", size="1000", title="Buy my coin", visibility="public", formkey=key)
    assert video.status_code == 403 and video.get_json()["need_password"] is True
    picture = desk_post(desk, "/api/media/uploads", kind="image", filename="a.png", size="1000", formkey=key)
    assert picture.get_json().get("need_password") is None, "pictures aren't held: this one only has nowhere to go"


def _paid_circle(owner, subscriber, renews_in=30 * 86400):
    db = owner.db_session()
    db.rollback()
    now = int(time.time())
    circle = db.execute(text("INSERT INTO circles (user_id, name, kind, price_coins, created_utc) VALUES (:o, :n, 'paid', 5, :t) RETURNING id"),
                        {"o": owner.id, "n": "paid " + secrets.token_hex(3), "t": now}).scalar()
    db.execute(text("INSERT INTO circle_members (circle_id, user_id, tier, status, price_coins, renews_utc, created_utc) "
                    "VALUES (:c, :u, 'subscriber', 'active', 5, :r, :t)"), {"c": circle, "u": subscriber.id, "r": now + renews_in, "t": now})
    db.commit()
    return circle


@pytest.mark.parametrize("who_pays", ["the member", "the other"])
def test_a_limited_block_never_ends_paid_days_but_a_plain_one_works(real_app, codes, make_member, who_pays):
    member, other, plain = make_member(), make_member(), make_member()
    if who_pays == "the member":
        circle = _paid_circle(other, member)
    else:
        circle = _paid_circle(member, other)
    desk = limited_browser(real_app, codes, member)
    key = member.formkey(desk)
    refused = desk_post(desk, "/settings/block", username=other.username, formkey=key)
    assert refused.status_code == 403 and refused.get_json()["need_password"] is True
    assert rows(member, "SELECT status FROM circle_members WHERE circle_id = :c", c=circle) == [("active",)]
    assert rows(member, "SELECT count(*) FROM userblocks WHERE user_id = :u", u=member.id)[0][0] == 0
    assert desk_post(desk, "/settings/block", username=plain.username, formkey=key).status_code == 200


def test_a_limited_sign_in_names_no_co_author_when_posting(real_app, codes, make_member):
    member, other = make_member(), make_member()
    desk = limited_browser(real_app, codes, member)
    answer = desk.post("/api/vue/submit", base_url=BASE, headers={**DESK, "Accept": "application/json"}, environ_base={"REMOTE_ADDR": address()},
                       data={"title": "a title " + secrets.token_hex(4), "body": "x", "coauthors": other.username, "formkey": member.formkey(desk)})
    assert answer.status_code == 403 and answer.get_json()["need_password"] is True
    assert rows(member, "SELECT count(*) FROM post_coauthors WHERE user_id = :u", u=other.id)[0][0] == 0


def test_a_limited_sign_in_never_makes_a_private_curation_public(real_app, codes, make_member):
    member = make_member()
    db = member.db_session()
    db.rollback()
    slug = "c" + secrets.token_hex(4)
    db.execute(text("INSERT INTO curations (owner_id, name, slug, description, is_private, created_utc) VALUES (:u, 'mine', :s, '', true, :t)"),
               {"u": member.id, "s": slug, "t": int(time.time())})
    db.commit()
    desk = limited_browser(real_app, codes, member)
    key = member.formkey(desk)
    refused = desk_post(desk, f"/&{slug}/edit", name="mine", description="", is_private="false", formkey=key)
    assert refused.status_code == 403 and refused.get_json()["need_password"] is True
    assert rows(member, "SELECT is_private FROM curations WHERE slug = :s", s=slug) == [(True,)]
    assert desk_post(desk, f"/&{slug}/edit", name="renamed", description="", is_private="true", formkey=key).status_code == 302


def test_the_email_address_is_kept_from_a_limited_sign_in(real_app, codes, make_member):
    member = make_member()
    address_on_file = f"only-{secrets.token_hex(6)}@example.org"
    set_user(member, is_activated=True, email=address_on_file)
    desk = limited_browser(real_app, codes, member)
    pages = ("/legal", "/help/contact", "/help/press", "/help/dmca", "/help/counter_dmca", "/settings/security", "/settings/profile", "/", "/me")
    for path in pages:
        answer = desk.get(path, base_url=BASE, headers=DESK, environ_base={"REMOTE_ADDR": address()}, follow_redirects=True)
        assert answer.status_code < 500, path
        assert address_on_file not in answer.get_data(as_text=True), path
    assert "Enter your password" in desk_get(desk, "/help/contact").get_data(as_text=True)
    key = member.formkey(desk)
    for path in ("/help/submit_contact", "/help/dmca", "/help/counter_dmca", "/legal/2", "/legal/final"):
        assert desk_post(desk, path, formkey=key).get_json().get("need_password") is True, path
    assert desk_post(desk, "/login/qr/upgrade", formkey=key, password=member.password).status_code == 302
    for path in ("/legal", "/help/contact", "/help/press", "/help/dmca", "/help/counter_dmca"):
        assert address_on_file in desk_get(desk, path).get_data(as_text=True), path


def test_opening_notifications_leaves_them_new_for_the_member(real_app, codes, make_member):
    member = make_member()
    desk = limited_browser(real_app, codes, member)
    def unread():
        return rows(member, "SELECT count(*) FROM notifications WHERE user_id = :u AND read = false", u=member.id)[0][0]
    assert unread() == 1
    for path in ("/notifications", "/notifications/system", "/notifications/all"):
        assert desk_get(desk, path).status_code == 200, path
    assert unread() == 1, "the one alert a member without a verified address gets"
    assert desk_post(desk, "/login/qr/upgrade", formkey=member.formkey(desk), password=member.password).status_code == 302
    assert desk_get(desk, "/notifications/system").status_code == 200
    assert unread() == 0, "the member has it now"


def test_a_qr_sign_in_links_no_accounts_until_a_password_is_typed(real_app, codes, make_member):
    """The computer's cookie is whoever's holds the code: its `history` must not write a lasting row beside the member, which the spam
    guard and an admin's ban act on."""
    member, stranger = make_member(), make_member()
    desk, code = start(real_app, codes)
    with desk.session_transaction() as session:
        session["history"] = [stranger.id]
    assert approve(member, member.browser(), code).status_code == 200
    assert desk_post(desk, "/login/qr/finish").status_code == 200
    def links():
        return rows(member, "SELECT count(*) FROM alts WHERE (user1 = :a AND user2 = :b) OR (user1 = :b AND user2 = :a)", a=member.id, b=stranger.id)[0][0]
    assert links() == 0 and session_of(desk)["history"] == [stranger.id], "and the member's id isn't added to the cookie"
    assert desk_post(desk, "/login/qr/upgrade", formkey=member.formkey(desk), password=member.password).status_code == 302
    assert links() == 1, "the password makes it what any sign-in is"
    assert member.id in session_of(desk)["history"]


def test_in_a_frame_the_password_form_is_only_a_way_out(real_app, codes, make_member):
    """The page around a frame may hold Google's tag, and the frame shares the tab's sessionStorage, where the form writes its key."""
    member = make_member()
    desk = limited_browser(real_app, codes, member)
    for path, back in (("/chat?embed=1", "%2Fchat"), ("/chat?embed=1&single=1&room=abc", "%2Fchat%3Froom%3Dabc")):
        page = desk_get(desk, path)
        html = page.get_data(as_text=True)
        assert page.status_code == 403 and "data-chat-prekey" not in html and 'name="password"' not in html, path
        assert f'target="_top" href="/login/qr/upgrade?redirect={back}"' in html, path
        assert '<base target="_top">' in html, "the page's other links can't be drawn in a frame either"
    top = desk_get(desk, "/login/qr/upgrade?redirect=/chat").get_data(as_text=True)
    assert "data-chat-prekey" in top and '<base target="_top">' not in top


def test_a_form_the_browser_navigated_with_gets_the_password_form_coming_back_to_its_page(real_app, codes, make_member):
    member = make_member()
    desk = limited_browser(real_app, codes, member)
    answer = desk.post("/settings/premium/redeem", base_url=BASE, data={"formkey": member.formkey(desk)},
                       headers={**DESK, "Sec-Fetch-Mode": "navigate", "Referer": f"{BASE}/settings/premium"},
                       environ_base={"REMOTE_ADDR": address()})
    html = answer.get_data(as_text=True)
    assert answer.status_code == 403 and 'action="/login/qr/upgrade"' in html
    assert 'name="redirect" value="/settings/premium"' in html and "data-chat-prekey" in html
    assert member.formkey(desk) in html


def test_everyday_things_still_work(real_app, codes, make_member):
    member = make_member()
    desk = limited_browser(real_app, codes, member)
    for path in ("/", "/settings/content", "/settings/profile"):
        answer = desk_get(desk, path)
        assert answer.status_code == 200, path
    assert 'id="qr-limited-notice"' in desk_get(desk, "/").get_data(as_text=True)
    assert 'id="rss_feed"' not in desk_get(desk, "/settings/profile").get_data(as_text=True), "that address outlives a log out"
    assert desk_post(desk, "/settings/score_display", formkey=member.formkey(desk), score_display="1").status_code < 400


def test_a_limited_sign_in_never_approves_another(real_app, codes, make_member):
    member = make_member()
    desk = limited_browser(real_app, codes, member)
    _, code = start(real_app, codes)
    answer = desk_post(desk, "/login/qr/approve", code=code, formkey=member.formkey(desk), answer=member.password)
    assert answer.status_code == 403 and answer.get_json()["need_password"] is True
    assert desk_post(desk, "/login/qr/info", code=code, formkey=member.formkey(desk)).status_code == 403
    page = desk_get(desk, "/login/qr/approve").get_data(as_text=True)
    assert "can't approve another" in page.replace("&#39;", "'") and "qr-approve-form" not in page


def test_chat_gets_the_password_form_and_a_clear_refusal(real_app, codes, make_member):
    member = make_member()
    desk = limited_browser(real_app, codes, member)
    page = desk_get(desk, "/chat")
    html = page.get_data(as_text=True)
    assert page.status_code == 403 and "data-chat-prekey" in html and "chat_bundle" not in html
    token = desk_post(desk, "/api/chat/token", formkey=member.formkey(desk))
    assert token.status_code == 403 and token.get_json()["need_password"] is True
    assert "qr-limited" not in html


# --- the password ----------------------------------------------------------------------------------------------------------------

def test_the_password_ends_the_limit_and_the_end(real_app, codes, make_member):
    member = make_member()
    desk = limited_browser(real_app, codes, member)
    wrong = desk_post(desk, "/login/qr/upgrade", formkey=member.formkey(desk), password="nope", redirect="/chat")
    assert wrong.status_code == 400 and session_of(desk)["qr_limited"] is True
    right = desk_post(desk, "/login/qr/upgrade", formkey=member.formkey(desk), password=member.password, redirect="/settings/security")
    assert right.status_code == 302 and right.headers["Location"].endswith("/settings/security")
    session = session_of(desk)
    assert "qr_limited" not in session and "qr_expires_utc" not in session and session["user_id"] == member.id
    assert desk_get(desk, "/settings/security").status_code == 200


def test_the_password_goes_with_two_factor_where_it_is_on(real_app, codes, make_member):
    from ruqqus.helpers import mfa_backup, mfa_backup_store
    member = make_member()
    secret = with_two_factor(member)
    backup = mfa_backup.generate()[0]
    db = member.db_session()
    mfa_backup_store.replace(db, member.id, [backup], int(time.time()))
    db.commit()
    desk, code = start(real_app, codes)
    assert approve(member, member.browser(), code, answer=pyotp.TOTP(secret).now()).status_code == 200
    assert desk_post(desk, "/login/qr/finish").status_code == 200
    alone = desk_post(desk, "/login/qr/upgrade", formkey=member.formkey(desk), password=member.password)
    assert alone.status_code == 400 and session_of(desk)["qr_limited"] is True
    assert 'name="2fa_token"' in alone.get_data(as_text=True)
    wrong = desk_post(desk, "/login/qr/upgrade", formkey=member.formkey(desk), password="nope", **{"2fa_token": backup})
    assert wrong.status_code == 400
    both = desk_post(desk, "/login/qr/upgrade", formkey=member.formkey(desk), password=member.password, **{"2fa_token": backup})
    assert both.status_code == 302 and "qr_limited" not in session_of(desk)


def test_it_goes_nowhere_but_this_site(real_app, codes, make_member):
    member = make_member()
    desk = limited_browser(real_app, codes, member)
    answer = desk_post(desk, "/login/qr/upgrade", formkey=member.formkey(desk), password=member.password, redirect="https://evil.example/")
    assert answer.status_code == 302 and "evil.example" not in answer.headers["Location"]


def test_signing_in_with_the_password_on_that_browser_makes_it_whole(real_app, codes, make_member):
    member = make_member()
    desk = limited_browser(real_app, codes, member)
    assert desk_post(desk, "/login", username=member.username, password=member.password).status_code == 302
    session = session_of(desk)
    assert "qr_limited" not in session and "qr_expires_utc" not in session


# --- admins and guildmasters ---------------------------------------------------------------------------------------------------

def test_a_limited_admin_is_asked_for_the_password_and_holds_no_powers(real_app, codes, make_member):
    app, _ = real_app
    member = make_member()
    set_user(member, admin_level=4)
    desk = limited_browser(real_app, codes, member)
    page = desk_get(desk, "/admin/feedback")
    assert page.status_code == 403 and 'action="/login/qr/upgrade"' in page.get_data(as_text=True)
    answer = desk_post(desk, "/api/ban_user/1", formkey=member.formkey(desk), reason="x")
    assert answer.status_code == 403 and answer.get_json()["need_password"] is True
    assert member.user().admin_level == 4, "the account keeps its level: only this sign-in reads as none"
    upgrade = desk_post(desk, "/login/qr/upgrade", formkey=member.formkey(desk), password=member.password)
    assert upgrade.status_code == 302
    assert desk_get(desk, "/admin/feedback").status_code == 200


def test_an_ordinary_member_limited_gets_the_ordinary_refusal_from_admin_pages(real_app, codes, make_member):
    member = make_member()
    desk = limited_browser(real_app, codes, member)
    assert desk_get(desk, "/admin/feedback").status_code == 403
    assert 'action="/login/qr/upgrade"' not in desk_get(desk, "/admin/feedback").get_data(as_text=True)


def test_every_check_of_the_level_outside_the_wrapper_reads_nothing_and_nothing_is_saved(real_app, codes, make_member):
    """The object's level reads 0 through a commit and a reload, and neither ever writes it."""
    app, db_session = real_app
    from flask import g, session as flask_session
    from ruqqus.helpers.wrappers import get_logged_in_user
    member = make_member()
    set_user(member, admin_level=4)
    with app.test_request_context("/", base_url=BASE):
        g.db = db_session()
        flask_session["user_id"] = member.id
        flask_session["login_nonce"] = member.user().login_nonce
        qr_login.begin_limited(flask_session, time.time())
        v, _ = get_logged_in_user()
        assert v.admin_level == 0 and v.qr_limited and v.qr_held_level == 4
        v.last_active_utc = 5
        g.db.commit()
        assert v.admin_level == 0, "a commit expires it, and the reload doesn't bring the level back"
        g.db.commit()
        g.db.close()
    assert member.user().admin_level == 4


def _lead_a_guild(member, private=False):
    from ruqqus.classes import Board, ModRelationship
    # on an address of its own: every account here shares 127.0.0.1, and a network leads five guilds at most (helpers/guild_limits.py)
    set_user(member, creation_ip=address())
    db = member.db_session()
    db.rollback()
    name = "qr" + secrets.token_hex(4)
    board = Board(name=name, description="a guild", description_html="<p>a guild</p>", creator_id=member.id, subcat_id=0, is_private=private)
    db.add(board)
    db.flush()
    db.add(ModRelationship(user_id=member.id, board_id=board.id, accepted=True, perm_full=True, perm_content=True, perm_appearance=True,
                           perm_config=True, perm_access=True))
    db.commit()
    return name


def test_a_limited_guildmaster_is_asked_for_the_password_and_can_still_read_a_private_guild(real_app, codes, make_member):
    member = make_member()
    name = _lead_a_guild(member, private=True)
    desk = limited_browser(real_app, codes, member)
    settings = desk_get(desk, f"/+{name}/mod/settings")
    assert settings.status_code == 403 and 'action="/login/qr/upgrade"' in settings.get_data(as_text=True)
    action = desk_post(desk, f"/mod/exile/{name}", formkey=member.formkey(desk), username="x")
    assert action.status_code == 403 and action.get_json()["need_password"] is True
    assert desk_get(desk, f"/+{name}").status_code == 200, "it is still theirs to read"
    # a power checked inside its route, not by the wrapper: `has_mod` says no there too, and the page answers as for anyone else
    assert desk_get(desk, f"/+{name}/events/new").status_code in (403, 404)
    desk_post(desk, "/login/qr/upgrade", formkey=member.formkey(desk), password=member.password)
    assert desk_get(desk, f"/+{name}/mod/settings").status_code == 200
    assert desk_get(desk, f"/+{name}/events/new").status_code == 200


# --- switched off ------------------------------------------------------------------------------------------------------------------

def test_with_the_switch_off_there_is_nothing_at_all(real_app, make_member, monkeypatch):
    monkeypatch.setenv("QR_LOGIN_ENABLED", "0")
    member = make_member()
    client = real_app[0].test_client()
    assert 'id="qr-start"' not in desk_get(client, "/login").get_data(as_text=True)
    assert "qr_login.js" not in desk_get(client, "/login").get_data(as_text=True)
    for path in ("/login/qr/start", "/login/qr/finish", "/login/qr/cancel"):
        assert desk_post(client, path).status_code == 404, path
    assert desk_get(client, "/login/qr/status").status_code == 404
    assert phone_get(client, "/login/qr/approve").status_code == 404
    phone = member.browser()
    assert phone_post(phone, "/login/qr/approve", code="x", formkey=member.formkey(phone), answer="x").status_code == 404


def test_with_the_switch_off_a_sign_in_made_before_can_still_give_its_password(real_app, codes, make_member, monkeypatch):
    member = make_member()
    desk = limited_browser(real_app, codes, member)
    monkeypatch.setenv("QR_LOGIN_ENABLED", "0")
    assert desk_get(desk, "/login/qr/upgrade").status_code == 200
    assert desk_post(desk, "/login/qr/upgrade", formkey=member.formkey(desk), password=member.password).status_code == 302
    assert "qr_limited" not in session_of(desk)


def test_the_privacy_page_says_it_only_while_it_is_on(real_app, monkeypatch):
    client = real_app[0].test_client()
    assert "qr_pending" in desk_get(client, "/help/privacy").get_data(as_text=True)
    monkeypatch.setenv("QR_LOGIN_ENABLED", "0")
    assert "qr_pending" not in desk_get(client, "/help/privacy").get_data(as_text=True)


# --- a limited sign-in leaves nothing behind that a log out can't reach, and is told nothing it can't un-know --------------------

def on_site(client, member, path, **form):
    return client.post(path, base_url=BASE, headers={**DESK, "Accept": "application/json"}, environ_base={"REMOTE_ADDR": address()},
                       data={"formkey": member.formkey(client), **form})


def write_post(client, member, **form):
    """(id, base 36 id, title) of a post made through the real route."""
    from ruqqus.helpers.base36 import base36encode
    title = form.pop("title", "title " + secrets.token_hex(8))
    body = form.pop("body", "text " + secrets.token_hex(8))
    answer = on_site(client, member, "/api/vue/submit", title=title, body=body, **form)
    assert answer.status_code < 400, answer.get_data(as_text=True)[:300]
    found = rows(member, "SELECT id FROM submissions_aux WHERE title = :t ORDER BY id DESC", t=title)
    assert found, "the post wasn't made"
    return found[0][0], base36encode(found[0][0]), title


def write_comment(client, member, post36, **form):
    from ruqqus.helpers.base36 import base36decode, base36encode
    body = form.pop("body", "words " + secrets.token_hex(8))
    answer = on_site(client, member, "/api/comment", parent_fullname=f"t2_{post36}", submission=post36, body=body, **form)
    assert answer.status_code < 400, answer.get_data(as_text=True)[:300]
    found = rows(member, "SELECT id FROM comments_aux WHERE body = :b ORDER BY id DESC", b=body)
    assert found, "the comment wasn't made"
    assert base36decode(base36encode(found[0][0])) == found[0][0]
    return found[0][0], base36encode(found[0][0]), body


def member_state(member):
    """(1 when the account is banned OR held by a check, else 0; unban_utc; ban_reason; quarantined_utc; quarantine_kind; is_banned). The site's own checks put an
    account on hold now and never ban it (helpers/auto_quarantine.py), so [0] is what a limited sign-in must leave at 0 and [4] says which kind of hold a whole one earned."""
    return rows(member, "SELECT CASE WHEN is_banned <> 0 OR quarantined_utc > 0 THEN 1 ELSE 0 END, unban_utc, ban_reason, quarantined_utc, quarantine_kind, is_banned "
                        "FROM users WHERE id = :u", u=member.id)[0]


def card_of(page, post36):
    start = page.index(f'id="post-{post36}" class="card')
    next_card = re.compile(r'id="post-[0-9a-z]+" class="card').search(page, start + 20)
    return page[start:next_card.start() if next_card else None]


CONTROLS = re.compile(r'\b(?:href|onclick|data-[a-z-]+)="([^"]*)"')


def controls(html, *names):
    """What a piece of a page lets its reader do: every link, press and data attribute, with the readers' own names taken out."""
    for name in names:
        html = html.replace(name, "NAME")
    return set(CONTROLS.findall(html))


def test_a_limited_sign_in_finds_none_of_the_members_anonymous_items(real_app, codes, make_member):
    """Which items are theirs is what anonymity exists to keep, and telling a relayed code once can't be taken back."""
    member = make_member()
    whole = member.browser()
    pid, post36, title = write_post(whole, member, anonymous="1")
    _, comment36, body = write_comment(whole, member, write_post(whole, member)[1], anonymous="1")
    desk = limited_browser(real_app, codes, member)
    outsider = make_member().browser()
    by_name = f"/search?q=from:@{member.username}"

    for client, sees in ((whole, True), (desk, False), (outsider, False)):
        assert (title in client.get(by_name, base_url=BASE, headers=DESK).get_data(as_text=True)) is sees
        assert (title in client.get(by_name + "&format=json", base_url=BASE, headers=DESK).get_data(as_text=True)) is sees
        assert (body in client.get(by_name + "&type=comments", base_url=BASE, headers=DESK).get_data(as_text=True)) is sees
    assert desk_get(desk, "/history/anonymous").status_code == 403

    assert desk_post(desk, "/login/qr/upgrade", formkey=member.formkey(desk), password=member.password).status_code == 302
    assert title in desk.get(by_name, base_url=BASE, headers=DESK).get_data(as_text=True), "the password shows them"
    assert body in desk.get(by_name + "&type=comments", base_url=BASE, headers=DESK).get_data(as_text=True)


def test_a_card_and_a_post_page_read_to_a_limited_sign_in_as_to_a_stranger(real_app, codes, make_member):
    member, other = make_member(), make_member()
    whole = member.browser()
    pid, post36, title = write_post(whole, member, anonymous="1")
    desk = limited_browser(real_app, codes, member)
    outsider = other.browser()

    def card(client):
        answer = client.get("/all?sort=new", base_url=BASE, headers=DESK)
        assert answer.status_code == 200
        return card_of(answer.get_data(as_text=True), post36)

    limited, stranger, own = card(desk), card(outsider), card(whole)
    assert f"/@{member.username}" not in limited and "Anonymous" in limited, "no name in the byline"
    assert f"/@{member.username}" in own, "the member's own card, with the password, says whose it is"
    names = (member.username, other.username)
    assert controls(limited, *names) == controls(stranger, *names), "nothing to press that a stranger doesn't have"
    assert controls(own, *names) != controls(stranger, *names), "this test can tell the difference"

    def page(client):
        html = client.get(f"/post/{post36}", base_url=BASE, headers=DESK).get_data(as_text=True)
        return html[html.index(f'id="post-{post36}" class="card'):html.index('class="bottom-tab')]     # the post, before the phone's bar

    limited, stranger, own = page(desk), page(outsider), page(whole)
    assert f"/@{member.username}" not in limited
    assert controls(limited, *names) == controls(stranger, *names)
    assert controls(own, *names) != controls(stranger, *names)


def test_nothing_about_a_limited_sign_ins_own_anonymous_item_answers_differently(real_app, codes, make_member):
    member = make_member()
    whole = member.browser()
    pid, post36, title = write_post(whole, member, anonymous="1")
    cid, comment36, body = write_comment(whole, member, post36, anonymous="1")
    desk = limited_browser(real_app, codes, member)
    outsider = make_member()
    stranger = outsider.browser()

    for client, who in ((desk, member), (stranger, outsider)):
        assert on_site(client, who, f"/edit_post/{post36}", title=title, body="changed").status_code == 403
        assert on_site(client, who, f"/delete_post/{post36}").status_code == 403
        assert on_site(client, who, f"/api/toggle_post_sensitive/{post36}").status_code == 403
        assert on_site(client, who, f"/edit_comment/{comment36}", body="changed").status_code == 403
        assert on_site(client, who, f"/delete/comment/{comment36}").status_code == 403
        assert client.get(f"/api/post/{post36}/voters", base_url=BASE, headers=DESK).status_code == 403
        assert client.get(f"/api/comment/{comment36}/voters", base_url=BASE, headers=DESK).status_code == 403
        assert client.get(f"/post/{post36}/insights", base_url=BASE, headers=DESK).status_code == 404
        assert client.get(f"/inpage/post_card/{post36}", base_url=BASE, headers=DESK).status_code == 403
        follow = on_site(client, who, f"/api/post/{post36}/follow")
        assert follow.status_code == 200 and "notified" in follow.get_json()["message"]
        fewer = on_site(client, who, f"/api/post/{post36}/fewer")
        assert fewer.status_code == 200 and "fewer" in fewer.get_json()["message"]
    assert rows(member, "SELECT deleted_utc FROM submissions WHERE id = :p", p=pid) == [(0,)]
    assert rows(member, "SELECT deleted_utc FROM comments WHERE id = :c", c=cid) == [(0,)]
    assert rows(member, "SELECT count(*) FROM post_follows WHERE user_id = :u", u=member.id)[0][0] == 0
    assert rows(member, "SELECT count(*) FROM post_feedback WHERE user_id = :u", u=member.id)[0][0] == 0
    # a repost would put it on the profile, and the refusal says "your own": any anonymous item waits for the password
    assert on_site(desk, member, f"/post/{post36}/repost").get_json().get("need_password") is True
    assert on_site(desk, member, f"/comment/{comment36}/repost").get_json().get("need_password") is True

    assert desk_post(desk, "/login/qr/upgrade", formkey=member.formkey(desk), password=member.password).status_code == 302
    assert desk.get(f"/api/post/{post36}/voters", base_url=BASE, headers=DESK).status_code == 200, "the password makes it theirs again"
    assert on_site(desk, member, f"/api/post/{post36}/follow").status_code == 409


def test_a_rated_anonymous_post_isnt_kept_on_a_limited_sign_ins_feed_for_being_its_own(real_app, codes, make_member):
    """Your own posts are never filtered from you, so one that shows when a stranger's wouldn't says whose it is."""
    member = make_member()
    whole = member.browser()
    pid, post36, title = write_post(whole, member, anonymous="1")
    db = member.db_session()
    db.rollback()
    db.execute(text("UPDATE submissions SET word_severity = 2 WHERE id = :p"), {"p": pid})
    db.commit()
    desk = limited_browser(real_app, codes, member)
    assert title not in desk_get(desk, "/all?sort=new").get_data(as_text=True)
    assert title in desk_get(whole, "/all?sort=new").get_data(as_text=True)


def test_a_limited_sign_in_still_runs_the_members_named_posts(real_app, codes, make_member):
    member = make_member()
    desk = limited_browser(real_app, codes, member)
    _, post36, title = write_post(desk, member)
    assert on_site(desk, member, f"/edit_post/{post36}", title=title, body="changed").status_code < 400
    assert on_site(desk, member, f"/delete_post/{post36}").status_code < 400


def test_comments_on_the_members_anonymous_items_wait_for_the_password(real_app, codes, make_member):
    """"Comment on your post" would say whose it is."""
    member, other = make_member(), make_member()
    whole, theirs = member.browser(), other.browser()
    _, post36, _ = write_post(whole, member, anonymous="1")
    open_post = write_post(theirs, other)[1]
    _, comment36, _ = write_comment(whole, member, open_post, anonymous="1")
    _, _, on_post = write_comment(theirs, other, post36)
    under_comment = "under it " + secrets.token_hex(8)
    assert on_site(theirs, other, "/api/comment", parent_fullname=f"t3_{comment36}", submission=open_post, body=under_comment).status_code < 400
    desk = limited_browser(real_app, codes, member)
    limited = desk_get(desk, "/notifications/all").get_data(as_text=True)
    assert on_post not in limited and under_comment not in limited
    assert "signed in to your account with a QR code" in limited, "the site's own notice is still there"
    assert desk_post(desk, "/login/qr/upgrade", formkey=member.formkey(desk), password=member.password).status_code == 302
    whole_list = desk_get(desk, "/notifications/all").get_data(as_text=True)
    assert on_post in whole_list and under_comment in whole_list


def _banned_domain(member, domain, reason):
    db = member.db_session()
    db.rollback()
    db.execute(text("INSERT INTO domains (domain, can_submit, can_comment, reason) VALUES (:d, false, false, :r)"), {"d": domain, "r": reason})
    db.commit()


@pytest.mark.parametrize("reason", [4, 7, 9])
def test_a_banned_link_on_a_limited_sign_in_is_refused_and_bans_nobody(real_app, codes, make_member, reason):
    member = make_member()
    desk = limited_browser(real_app, codes, member)
    domain = f"bad{secrets.token_hex(4)}.example"
    _banned_domain(member, domain, reason)

    # the post's link, its text and a comment's text
    assert on_site(desk, member, "/api/vue/submit", title="a title " + secrets.token_hex(4), url=f"https://{domain}/x").status_code == 400
    assert member_state(member)[0] == 0, "the link of a post"
    assert on_site(desk, member, "/api/vue/submit", title="a title " + secrets.token_hex(4), body=f"[x](https://{domain}/y)").status_code in (400, 403)
    assert member_state(member)[0] == 0, "the text of a post"
    _, post36, title = write_post(desk, member)
    answer = on_site(desk, member, "/api/comment", parent_fullname=f"t2_{post36}", submission=post36, body=f"[x](https://{domain}/z)")
    assert answer.status_code == 400 and member_state(member)[0] == 0, "the text of a comment"
    # and when editing
    assert on_site(desk, member, f"/edit_post/{post36}", title=title, body=f"[x](https://{domain}/e)").status_code == 403
    assert member_state(member)[0] == 0, "an edit of a post"
    _, comment36, _ = write_comment(desk, member, post36)
    assert on_site(desk, member, f"/edit_comment/{comment36}", body=f"[x](https://{domain}/e)").status_code == 400
    assert member_state(member)[0] == 0, "an edit of a comment"
    assert on_site(desk, member, "/settings/profile/edit", bio=f"[x](https://{domain}/b)").status_code == 400
    assert member_state(member)[0] == 0, "the pop-up's bio"

    # the rule itself stands: the same link from a whole sign-in
    whole = member.browser()
    on_site(whole, member, "/api/vue/submit", title="a title " + secrets.token_hex(4), url=f"https://{domain}/x")
    assert member_state(member)[0] != 0 and member_state(member)[4] == 1 and member_state(member)[5] == 0


def test_a_link_that_bans_on_sight_on_a_limited_sign_in_is_refused(real_app, codes, make_member):
    member = make_member()
    desk = limited_browser(real_app, codes, member)
    link = f"bad{secrets.token_hex(4)}.example/spam"
    db = member.db_session()
    db.rollback()
    db.execute(text("INSERT INTO badlinks (reason, link, autoban) VALUES (1, :l, true)"), {"l": link})
    db.commit()
    assert on_site(desk, member, "/api/vue/submit", title="a title " + secrets.token_hex(4), url=f"https://{link}").status_code == 400
    assert member_state(member)[0] == 0
    _, post36, title = write_post(desk, member)
    assert on_site(desk, member, f"/edit_post/{post36}", title=title, body=f"[x](https://{link})").status_code == 403
    assert member_state(member)[0] == 0


def test_the_spam_guard_refuses_a_limited_sign_in_and_touches_nobody(real_app, codes, make_member, monkeypatch):
    """It suspends the member and every account linked to them: 16 similar posts took 1.3 s, and the row outlived a log out everywhere."""
    app, _ = real_app
    monkeypatch.setitem(app.config, "SPAM_SIMILAR_COUNT_THRESHOLD", 1)     # 3 for an account over a week old
    member, alt = make_member(), make_member()
    set_user(member, created_utc=int(time.time()) - 90 * 86400)              # the model stamps "now" over what make_member gives it
    db = member.db_session()
    db.rollback()
    db.execute(text("INSERT INTO alts (user1, user2) VALUES (:a, :b)"), {"a": member.id, "b": alt.id})
    db.commit()
    desk = limited_browser(real_app, codes, member)
    for i in range(8):
        answer = on_site(desk, member, "/api/vue/submit", title=f"the very same words again and again {i}", body="the very same body again and again")
        if answer.status_code == 403:
            break
        assert answer.status_code < 400, answer.get_data(as_text=True)[:200]
    assert answer.status_code == 403 and answer.get_json()["need_password"] is True
    made = rows(member, "SELECT id FROM submissions WHERE author_id = :u", u=member.id)
    assert len(made) >= 2, "there is something for it to have taken down"
    assert member_state(member)[0] == 0 and member_state(alt)[0] == 0
    assert rows(member, "SELECT count(*) FROM submissions WHERE author_id = :u AND is_banned = true", u=member.id)[0][0] == 0
    log_of = "SELECT count(*) FROM log_entries WHERE action = 'quarantine_user' AND target_user_id IN (:a, :b)"
    assert rows(member, log_of, a=member.id, b=alt.id)[0][0] == 0, "nothing was written about it either"

    # with the password the rule is as it was
    assert desk_post(desk, "/login/qr/upgrade", formkey=member.formkey(desk), password=member.password).status_code == 302
    assert on_site(desk, member, "/api/vue/submit", title="the very same words again and again 4", body="the very same body again and again").status_code < 500
    assert member_state(member)[0] != 0 and member_state(alt)[0] != 0
    assert member_state(member)[4] == 1 and member_state(alt)[4] == 1, "the site's own check puts both on hold"
    assert member_state(member)[5] == 0 and member_state(alt)[5] == 0, "and bans neither"


def test_the_comment_spam_guards_refuse_a_limited_sign_in(real_app, codes, make_member, monkeypatch):
    app, _ = real_app
    member = make_member()
    desk = limited_browser(real_app, codes, member)
    _, post36, _ = write_post(desk, member)
    made = [write_comment(desk, member, post36, body=f"the very same comment over and over {i}") for i in range(3)]
    monkeypatch.setitem(app.config, "COMMENT_SPAM_COUNT_THRESHOLD", 0)
    answer = on_site(desk, member, "/api/comment", parent_fullname=f"t2_{post36}", submission=post36, body="the very same comment over and over 3")
    assert answer.status_code == 403 and (answer.get_json() or {}).get("need_password") is True, answer.get_data(as_text=True)[:400]
    assert member_state(member)[0] == 0
    monkeypatch.setitem(app.config, "SPAM_SIMILAR_COUNT_THRESHOLD", 0)
    answer = on_site(desk, member, f"/edit_comment/{made[0][1]}", body="the very same comment over and over 4")
    assert answer.status_code == 403 and (answer.get_json() or {}).get("need_password") is True, answer.get_data(as_text=True)[:400]
    assert member_state(member)[0] == 0
    assert rows(member, "SELECT count(*) FROM comments WHERE author_id = :u AND is_banned = true", u=member.id)[0][0] == 0


def test_the_pop_ups_new_account_rule_refuses_a_limited_sign_in_and_saves_nothing(real_app, codes, make_member):
    member = make_member()
    set_user(member, created_utc=int(time.time()))
    desk = limited_browser(real_app, codes, member)
    answer = on_site(desk, member, "/settings/profile/edit", bio="[a link](https://example.com/x)")
    assert answer.status_code == 403 and answer.get_json()["need_password"] is True
    assert member_state(member)[0] == 0 and rows(member, "SELECT bio FROM users WHERE id = :u", u=member.id)[0][0] in (None, "")
    assert desk_post(desk, "/login/qr/upgrade", formkey=member.formkey(desk), password=member.password).status_code == 302
    on_site(desk, member, "/settings/profile/edit", bio="[a link](https://example.com/x)")
    assert member_state(member)[4] == 1 and member_state(member)[5] == 0, "the rule stands for a whole sign-in: the account is put on hold, never banned"


def test_the_banned_picture_list_holds_nobody_on_a_limited_sign_ins_word(real_app, make_member, monkeypatch):
    """The site's own list, read after the upload. The picture goes either way, and nobody is ever banned or suspended by it (the owner, 2026-11-17: the site's own
    checks only quarantine). A limited sign-in's word holds nobody: the check could be wrong and the real member would carry it. (The child-abuse match is CLAUDE.md's
    named exception, and holds whoever uploaded: tests/pg/test_scan_bans.py.)"""
    from types import SimpleNamespace

    from ruqqus.helpers import aws
    monkeypatch.setattr(aws, "BUCKET", "bucket.example")
    monkeypatch.setattr(aws.requests, "get", lambda *a, **k: SimpleNamespace(status_code=200, iter_content=lambda n: [b"x"]))
    monkeypatch.setattr(aws, "check_phash", lambda db, name: SimpleNamespace(ban_time=0, ban_reason="on the list"))
    monkeypatch.setattr(aws, "delete_file", lambda name: None)
    for limited in (True, False):
        member = make_member()
        user = member.user()
        user.qr_limited = limited
        removed = []
        aws.check_csam_url(f"https://bucket.example/{secrets.token_hex(4)}.png", user, lambda: removed.append(1))
        assert removed == [1]
        state = member_state(member)
        assert state[5] == 0 and state[1] in (0, None), "never banned or suspended, whoever uploaded"
        if limited:
            assert state[0] == 0 and state[3] == 0 and state[4] == 0, "a limited sign-in's word holds nobody"
        else:
            assert state[3] > 0 and state[4] == 1, "an ordinary sign-in's word puts the account on hold, for an admin to decide"


def test_a_qr_row_says_nothing_about_the_member_until_the_password(real_app, codes, make_member):
    """Its address counted against the member's guild limit for 90 days and its region towards the warning on their profile for 400,
    past every log out."""
    from ruqqus.helpers import guild_limits, regions, signin_alerts
    from ruqqus.helpers.regions import compute_region_state
    member = make_member()
    db = member.db_session()
    db.rollback()
    now = int(time.time())
    db.execute(text("INSERT INTO login_events (user_id, created_utc, ip, cf_country, region_code, user_agent, method) "
                    "VALUES (:u, :t, '127.0.0.1', 'GB', :r, 'a browser', 'password')"),
               {"u": member.id, "t": now - 3600, "r": regions.resolve_region("GB")})
    db.commit()
    before = guild_limits.addresses_of(db, member.id)

    far, app_client = "203.0.113.77", real_app[0].test_client()
    away = {**DESK, "CF-IPCountry": "RU"}
    assert app_client.post("/login/qr/start", base_url=BASE, headers=away, environ_base={"REMOTE_ADDR": far}).status_code == 200
    assert approve(member, member.browser(), codes[-1]).status_code == 200
    assert app_client.post("/login/qr/finish", base_url=BASE, headers=away, environ_base={"REMOTE_ADDR": far}).status_code == 200

    row = rows(member, "SELECT ip, region_code, cf_country, method, user_agent FROM login_events WHERE user_id = :u ORDER BY id DESC LIMIT 1", u=member.id)[0]
    assert row[0] is None and row[1] is None and row[2] == "RU" and row[3] == "qr" and row[4], "Recent sign-ins still has what it shows"
    assert far not in guild_limits.addresses_of(db, member.id) and guild_limits.addresses_of(db, member.id) == before
    assert compute_region_state(member.user(), db)["suspicious"] is False
    assert "RU" not in {country for country, _ in signin_alerts.previous(db, member.id, now + 60)}, "its country isn't old news for the alerts"

    # with the password it is what any sign-in is, from the address the password came from
    assert app_client.post("/login/qr/upgrade", base_url=BASE, headers=away, environ_base={"REMOTE_ADDR": far},
                           data={"formkey": member.formkey(app_client), "password": member.password}).status_code == 302
    db.rollback()
    row = rows(member, "SELECT ip, region_code FROM login_events WHERE user_id = :u ORDER BY id DESC LIMIT 1", u=member.id)[0]
    assert row == (far, regions.resolve_region("RU"))
    assert far in guild_limits.addresses_of(db, member.id)
    assert compute_region_state(member.user(), db)["suspicious"] is True


def test_a_qr_sign_in_from_tor_marks_nothing_until_the_password(real_app, codes, make_member):
    from ruqqus.helpers.regions import compute_region_state
    member = make_member()
    client = real_app[0].test_client()
    tor = {**DESK, "CF-IPCountry": "T1"}
    assert client.post("/login/qr/start", base_url=BASE, headers=tor, environ_base={"REMOTE_ADDR": address()}).status_code == 200
    assert approve(member, member.browser(), codes[-1]).status_code == 200
    assert client.post("/login/qr/finish", base_url=BASE, headers=tor, environ_base={"REMOTE_ADDR": address()}).status_code == 200
    assert compute_region_state(member.user(), member.db_session())["suspicious"] is False


def _sign_in_ended_elsewhere(member, client):
    set_user(member, login_nonce=member.user().login_nonce + 1)
    assert signed_in_as(client) == member.id, "the cookie doesn't know yet"


def test_a_browser_whose_sign_in_was_ended_elsewhere_can_use_a_code(real_app, codes, make_member):
    """`settle` reads no database, so it took the code's digest out of a cookie that still named an account, on every request."""
    member = make_member()
    browser = member.browser()
    _sign_in_ended_elsewhere(member, browser)
    assert desk_post(browser, "/login/qr/start").status_code == 200
    assert signed_in_as(browser) is None and qr_login.PENDING_KEY in session_of(browser)
    assert status(browser) == {"state": "waiting"}
    assert approve(member, member.browser(), codes[-1]).status_code == 200
    assert status(browser) == {"state": "approved", "name": member.username}
    assert desk_post(browser, "/login/qr/finish").status_code == 200
    assert signed_in_as(browser) == member.id


def test_any_page_lets_go_of_a_sign_in_that_was_ended_elsewhere(real_app, make_member):
    member = make_member()
    browser = member.browser()
    assert "user_id" in session_of(browser)
    _sign_in_ended_elsewhere(member, browser)
    assert desk_get(browser, "/login").status_code == 200
    session = session_of(browser)
    assert not {"user_id", "session_id", "login_nonce"} & set(session)


def test_a_new_account_never_takes_on_a_limited_sign_ins_marks(real_app, codes, make_member):
    from .test_signup_guard import form_for
    member = make_member()
    desk = limited_browser(real_app, codes, member)
    _sign_in_ended_elsewhere(member, desk)
    form = form_for(desk, agent=DESK_AGENT)
    answer = desk_post(desk, "/signup", **form)
    assert answer.status_code == 302, answer.get_data(as_text=True)[:200]
    session = session_of(desk)
    assert session.get("user_id") and session["user_id"] != member.id
    assert qr_login.LIMITED_KEY not in session and qr_login.EXPIRES_KEY not in session
    assert "qr-limited-notice" not in desk_get(desk, "/all").get_data(as_text=True)
