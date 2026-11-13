"""Sign-ins that ended stay ended, on the real app (helpers/sign_in_ends.py, CLAUDE.md "Account security"): a copy of a cookie taken
before Log out, before the QR password or before a later sign-in on the same browser is a visitor's; an answer whose sign-in ended while
it ran writes no cookie; and the paths that open no database are untouched by the answer-time check."""
import secrets

from sqlalchemy import text

from ruqqus.helpers import qr_login

BASE = "http://localhost"
AGENT = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"}


def address():
    return f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"


def call(client, method, path, headers=None, **kw):
    return getattr(client, method)(path, base_url=BASE, headers={**AGENT, **(headers or {})}, environ_base={"REMOTE_ADDR": address()}, **kw)


def cookie(client):
    found = client.get_cookie("session_ruqqus")
    return found.value if found else None


def copy_of(app, client):
    """Another browser holding a copy of this one's cookie (a stolen copy, or this browser's own answer still on its way)."""
    other = app.test_client()
    other.set_cookie("session_ruqqus", cookie(client), domain="localhost")
    return other


def who(client):
    with client.session_transaction() as session:
        return session.get("user_id")


def signed_in(client):
    return call(client, "get", "/settings/security").status_code == 200


def session_cookie_set(answer):
    return any(c.startswith("session_ruqqus=") for c in answer.headers.getlist("Set-Cookie"))


def endpoint_of(app, path, method="GET"):
    adapter = app.url_map.bind("localhost")
    return adapter.match(path, method=method)[0]


def test_a_copy_of_the_cookie_taken_before_log_out_is_a_visitors(real_app, make_member):
    app, _ = real_app
    member = make_member()
    browser = member.browser()
    assert signed_in(browser)
    copy = copy_of(app, browser)
    out = call(browser, "post", "/logout", data={"formkey": member.formkey(browser)})
    assert out.status_code == 302
    assert who(browser) is None                       # the cookie stops naming the account at once
    refused = call(copy, "get", "/settings/security")
    assert refused.status_code != 200
    assert not session_cookie_set(refused)            # and the refused copy's answer writes nothing over what the browser holds now
    db = member.db_session()
    db.rollback()
    assert db.execute(text("SELECT reason FROM ended_sign_ins WHERE user_id = :u"), {"u": member.id}).scalars().all() == ["logout"]


def test_an_answer_held_open_across_log_out_writes_no_cookie(real_app, make_member, monkeypatch):
    """The browser's own page asked something, Log out committed while it ran, and the answer arrived after: it must not put the
    old sign-in back."""
    app, _ = real_app
    member = make_member()
    browser = member.browser()
    endpoint = endpoint_of(app, "/settings/security")
    real = app.view_functions[endpoint]
    from flask import session as flask_session
    from ruqqus.__main__ import _engine
    from ruqqus.helpers import sign_in_ends

    def slow(*a, **kw):
        answer = real(*a, **kw)
        with _engine.begin() as conn:                 # another request's Log out, committed while this one ran
            sign_in_ends.end(conn, flask_session["session_id"], member.id, "logout", 1)
        return answer

    control = call(browser, "get", "/settings/security")
    assert control.status_code == 200 and session_cookie_set(control)      # an answer refreshes the cookie
    monkeypatch.setitem(app.view_functions, endpoint, slow)
    held = call(browser, "get", "/settings/security")
    assert held.status_code == 200
    assert not session_cookie_set(held)
    monkeypatch.setitem(app.view_functions, endpoint, real)
    assert not signed_in(browser)                     # and the next request refuses the cookie it kept


def test_a_sign_in_over_an_older_one_ends_the_older(real_app, make_member):
    app, _ = real_app
    first, second = make_member(), make_member()
    browser = first.browser()
    copy = copy_of(app, browser)
    answer = call(browser, "post", "/login", data={"username": second.username, "password": second.password})
    assert answer.status_code == 302, answer.get_data(as_text=True)[:300]
    assert who(browser) == second.id and signed_in(browser)
    assert not signed_in(copy)


def test_the_qr_password_ends_a_copy_taken_before_it(real_app, make_member):
    app, _ = real_app
    member = make_member()
    browser = member.browser()
    with browser.session_transaction() as session:
        qr_login.begin_limited(session, __import__("time").time())
        before = session["session_id"]
    copy = copy_of(app, browser)
    answer = call(browser, "post", "/login/qr/upgrade", data={"formkey": member.formkey(browser), "password": member.password})
    assert answer.status_code == 302
    with browser.session_transaction() as session:
        assert session["session_id"] != before and not session.get(qr_login.LIMITED_KEY)
    assert signed_in(browser)
    # it used to come back limited; now it is signed out (fail closed). Asked on a page a limited sign-in may open, and its answer
    # writes no cookie over the whole one the browser holds now (a double-pressed password, another tab's poll)
    late = call(copy, "get", "/")
    assert late.status_code == 200 and "data-logout" not in late.get_data(as_text=True)
    assert not session_cookie_set(late)
    assert signed_in(browser)


def test_log_out_everywhere_keeps_this_browser(real_app, make_member):
    app, _ = real_app
    member = make_member()
    browser, other = member.browser(), member.browser()
    answer = call(browser, "post", "/settings/log_out_all_others", data={"formkey": member.formkey(browser), "password": member.password})
    assert answer.status_code in (200, 302)
    assert signed_in(browser) and not signed_in(other)


def test_a_read_only_post_from_a_member_is_answered_as_before(real_app, make_member, monkeypatch):
    """Read-only mode returns before the database is opened: the answer-time check must not turn that into a crash."""
    app, _ = real_app
    member = make_member()
    browser = member.browser()
    monkeypatch.setitem(app.config, "READ_ONLY", 1)
    answer = call(browser, "post", "/settings/log_out_all_others", data={"formkey": member.formkey(browser)})
    assert answer.status_code == 500 and "read-only" in answer.get_json()["error"]
    monkeypatch.setitem(app.config, "READ_ONLY", 0)
    assert signed_in(browser)


def test_a_browser_holding_an_ended_cookie_can_still_sign_up(real_app, make_member):
    """A refused cookie writes nothing of its own, but a route that changes the session (the sign-up form's token, the sign-in) still
    writes it, without the old account in it."""
    app, _ = real_app
    member = make_member()
    browser = member.browser()
    copy = copy_of(app, browser)
    call(browser, "post", "/logout", data={"formkey": member.formkey(browser)})
    form = call(copy, "get", "/signup")
    assert form.status_code == 200 and session_cookie_set(form)
    with copy.session_transaction() as session:
        assert session.get("signup_token") and "user_id" not in session


def test_a_request_that_never_opens_the_database_writes_no_cookie_naming_an_account(real_app, make_member):
    """An address in time out is answered before the database is opened, so the sign-in can't be checked: the answer must not refresh
    the cookie (a copy of an ended one would otherwise outlive its row)."""
    app, _ = real_app
    from ruqqus.__main__ import r
    member = make_member()
    browser = member.browser()
    with browser.session_transaction() as session:
        session.permanent = True                      # as the login route leaves it: refreshed on every answer that writes one
    banned = address()
    r.set(f"ban_ip_{banned}", 1, ex=60)
    try:
        answer = browser.get("/settings/security", base_url=BASE, headers=AGENT, environ_base={"REMOTE_ADDR": banned})
        assert answer.status_code == 429
        assert not session_cookie_set(answer)
    finally:
        r.delete(f"ban_ip_{banned}")
    assert signed_in(browser)


def test_an_error_before_the_database_is_opened_writes_no_cookie_either(real_app, make_member, monkeypatch):
    """Not only the listed early returns: with Redis down the ban-table check in before_request raises (as does the limiter's), before
    `g.db` exists, and the 500 that follows must not refresh the cookie either, or someone polling the site with a copy of an ended
    cookie would keep it alive past the 400 days its row is kept (the review of 2026-11-06, C4)."""
    app, _ = real_app
    import ruqqus.__main__ as main

    class Down:
        def get(self, *a, **k):
            raise ConnectionError("redis is down")

        def __bool__(self):
            return True

    member = make_member()
    browser = member.browser()
    with browser.session_transaction() as session:
        session.permanent = True
    monkeypatch.setitem(app.config, "PROPAGATE_EXCEPTIONS", False)
    monkeypatch.setattr(main, "r", Down())
    answer = call(browser, "get", "/settings/security")
    monkeypatch.undo()
    assert answer.status_code >= 500
    assert not session_cookie_set(answer)
    assert signed_in(browser)                         # and the sign-in itself was never touched


def test_a_route_that_never_asks_who_is_signed_in_still_saves_its_change(real_app, make_member):
    """Every request that opens the database refuses an ended cookie (before_request), so a route with no sign-in wrapper writes what it
    changed (here a feed filter), without the old account in it."""
    app, _ = real_app
    member = make_member()
    browser = member.browser()
    copy = copy_of(app, browser)
    call(browser, "post", "/logout", data={"formkey": member.formkey(browser)})
    answer = call(copy, "get", "/post_type?types=poll")
    assert answer.status_code == 302 and session_cookie_set(answer)
    with copy.session_transaction() as session:
        assert session.get("feed_post_types") and "user_id" not in session


def test_a_minute_after_the_end_the_refused_cookie_is_replaced_by_a_visitors(real_app, make_member):
    """Writing nothing back protects a sign-in that replaced this one, which can only be on its way in the first moments; after that the
    browser is given a visitor's cookie, so it stops sending the ended one."""
    app, _ = real_app
    member = make_member()
    browser = member.browser()
    copy = copy_of(app, browser)
    call(browser, "post", "/logout", data={"formkey": member.formkey(browser)})
    db = member.db_session()
    db.rollback()
    db.execute(text("UPDATE ended_sign_ins SET ended_utc = ended_utc - 120 WHERE user_id = :u"), {"u": member.id})
    db.commit()
    answer = call(copy, "get", "/")
    assert answer.status_code == 200 and session_cookie_set(answer)
    assert who(copy) is None


def test_a_frame_whose_sign_in_ended_says_so_and_is_never_blank(real_app, make_member):
    """The guild Chat tab's frame and the side panels' (`?embed=1` on /chat and /notifications) of a browser whose sign-in ended (here a
    Log out; another browser's Log out everywhere is the same 401): the sign-in page may not be framed (X-Frame-Options deny), so the
    redirect there that a page gets left the frame blank. A frame gets its own "Signed out" page, framable by the site only, whose
    link signs in from the page around it (default.html gives a frame <base target="_top">) and comes back to it."""
    app, _ = real_app
    member = make_member()
    browser = member.browser()
    copy = copy_of(app, browser)
    assert call(browser, "post", "/logout", data={"formkey": member.formkey(browser)}).status_code == 302
    for path in ("/chat?embed=1&single=1&room=!x:s", "/notifications?embed=1"):
        answer = call(copy, "get", path, headers={"Referer": BASE + "/+pets/chat?c=3"})
        assert answer.status_code == 401, path
        page = answer.get_data(as_text=True)
        assert "Signed out" in page and "Sign in again to carry on." in page and '<base target="_top">' in page
        assert 'href="/login?redirect=%2F%2Bpets%2Fchat%3Fc%3D3"' in page
        assert answer.headers.get("X-Frame-Options") == "sameorigin" and "frame-ancestors 'self'" in answer.headers["Content-Security-Policy"]
    # another site's page as the referrer comes back to the front page; a page that isn't a frame still goes to the sign-in page
    away = call(copy, "get", "/chat?embed=1&single=1", headers={"Referer": "https://elsewhere.example/x"})
    assert 'href="/login?redirect=%2F"' in away.get_data(as_text=True)
    page = call(copy, "get", "/chat")
    assert page.status_code == 302 and "/login?redirect=" in page.headers["Location"]
