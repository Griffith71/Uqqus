"""On the real app: a visitor's first page carries no identifier and no year-long cookie, and everything that needs the cookie still
works (helpers/session_cookie.py, CLAUDE.md "Cookies and other sites"). Nothing here reads the database's rows: what counts is the answer."""
import re
import secrets
import sys
from http.cookies import SimpleCookie

import pyotp
import pytest

BASE = "http://localhost"
AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15"
HEADERS = {"User-Agent": AGENT, "Accept-Language": "en-GB,en;q=0.9"}


def addr():
    return f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"


def call(client, method, path, **kw):
    return getattr(client, method)(path, base_url=BASE, headers=HEADERS, environ_base={"REMOTE_ADDR": addr()}, **kw)


def site_cookie(answer):
    """(what the signed cookie holds, whether it outlives the browser), or None when the answer sets none."""
    from ruqqus.__main__ import app
    for header in answer.headers.getlist("Set-Cookie"):
        cookie = SimpleCookie(header)
        if "session_ruqqus" in cookie:
            morsel = cookie["session_ruqqus"]
            if not morsel.value:
                return {}, False                          # deleted
            data = app.session_interface.get_signing_serializer(app).loads(morsel.value)
            return data, bool(morsel["expires"] or morsel["max-age"])
    return None


@pytest.mark.parametrize("path", ["/", "/login", "/help/privacy", "/browse", "/trending", "/happening"])
def test_a_visitors_first_get_sets_no_cookie_at_all(real_app, path):
    app, _ = real_app
    answer = call(app.test_client(), "get", path)
    assert answer.status_code == 200, path
    assert "session_ruqqus" not in " ".join(answer.headers.getlist("Set-Cookie")), path


@pytest.mark.parametrize("path, key", [("/all", "base_feed"), ("/signup", "signup_token")])
def test_a_page_that_has_something_to_remember_gets_a_cookie_that_ends_with_the_browser_and_no_identifier(real_app, path, key):
    app, _ = real_app
    data, lasts = site_cookie(call(app.test_client(), "get", path))
    assert key in data and not lasts
    assert "session_id" not in data and "user_id" not in data and "_permanent" not in data


def test_a_visitors_filter_is_kept_for_the_visit_without_an_identifier(real_app):
    app, _ = real_app
    region = sorted(sys.modules["ruqqus.routes.front"].REGION_CENTROIDS)[0]
    client = app.test_client()
    data, lasts = site_cookie(call(client, "get", f"/regional?region={region}"))
    assert data.get("selected_regions") == [region] and not lasts and "session_id" not in data
    # the next page of the same visit still knows it, and does not rewrite it
    answer = call(client, "get", "/")
    assert answer.status_code == 200 and site_cookie(answer) is None
    with client.session_transaction() as session:
        assert session["selected_regions"] == [region]


def test_a_visitors_year_long_cookie_from_before_is_put_right(real_app):
    app, _ = real_app
    bare = app.test_client()
    with bare.session_transaction() as session:
        session["_permanent"], session["session_id"] = True, "old"
    assert site_cookie(call(bare, "get", "/")) == ({}, False)

    filtered = app.test_client()
    with filtered.session_transaction() as session:
        session["_permanent"], session["session_id"], session["catids"] = True, "old", [1]
    data, lasts = site_cookie(call(filtered, "get", "/"))
    assert data == {"catids": [1]} and not lasts


def sign_in(client, member):
    return call(client, "post", "/login", data={"username": member.username, "password": member.password})


def test_signing_in_makes_the_cookie_year_long_and_form_keys_work(make_member):
    member = make_member()
    client = member.app.test_client()
    answer = sign_in(client, member)
    assert answer.status_code == 302
    data, lasts = site_cookie(answer)
    assert lasts and data["user_id"] == member.id and data["session_id"] and data["history"] == [member.id]
    # a form key a page drew works, and stays the same from one request to the next
    first = member.formkey(client)
    assert call(client, "get", "/settings/security").status_code == 200
    assert member.formkey(client) == first
    assert call(client, "post", "/settings/dark_mode/1", data={"formkey": first}).status_code == 204
    assert call(client, "post", "/settings/dark_mode/1", data={"formkey": "wrong"}).status_code != 204


def test_a_session_the_login_route_did_not_make_is_still_given_its_id_and_its_year(make_member):
    member = make_member()
    browser = member.browser()
    with browser.session_transaction() as session:
        del session["session_id"]
    data, lasts = site_cookie(call(browser, "get", "/settings/security"))
    assert lasts and data["session_id"]


def test_signing_out_keeps_only_the_browsers_history_for_the_alts_check(make_member):
    member = make_member()
    client = member.app.test_client()
    sign_in(client, member)
    assert call(client, "post", "/logout", data={"formkey": member.formkey(client)}).status_code == 302
    answer = call(client, "get", "/")
    data, lasts = site_cookie(answer)
    assert lasts and data.get("history") == [member.id]
    for key in ("user_id", "session_id", "login_nonce"):
        assert key not in data, key
    assert answer.status_code == 200


def saved_default(member, client):
    """The member, on this browser, saves a filter as their default and opens a feed. Returns the filter."""
    region = sorted(sys.modules["ruqqus.routes.front"].REGION_CENTROIDS)[0]
    assert call(client, "get", "/").status_code == 200
    assert call(client, "get", f"/regional?region={region}").status_code < 400
    assert call(client, "post", "/settings/feed_defaults", data={"action": "save", "formkey": member.formkey(client)}).status_code < 400
    return region


def test_a_feed_opened_while_signed_in_leaves_no_account_number_or_filters_after_signing_out(make_member):
    member = make_member()
    client = member.app.test_client()
    sign_in(client, member)
    region = saved_default(member, client)
    with client.session_transaction() as session:
        assert session["feed_defaults_for"] == member.id and session["selected_regions"] == [region]
    assert call(client, "post", "/logout", data={"formkey": member.formkey(client)}).status_code == 302
    data, lasts = site_cookie(call(client, "get", "/"))
    assert lasts and data == {"_permanent": True, "history": [member.id]}, data


def test_a_browser_that_only_ever_signed_up_is_a_visitors_again(make_member):
    # no `history`: only a typed password at /login writes it. So nothing with an account's number stays, and nothing lasts past the browser.
    member = make_member()
    client = member.browser()
    with client.session_transaction() as session:
        session.pop("history", None)
    saved_default(member, client)
    assert call(client, "post", "/logout", data={"formkey": member.formkey(client)}).status_code == 302
    answer = call(client, "get", "/")
    found = site_cookie(answer)
    assert found == ({}, False) or (found is None and not session_keys(client)), found


def session_keys(client):
    with client.session_transaction() as session:
        return dict(session)


def test_the_saved_default_comes_back_with_the_next_sign_in(make_member):
    member = make_member()
    client = member.app.test_client()
    sign_in(client, member)
    region = saved_default(member, client)
    call(client, "post", "/logout", data={"formkey": member.formkey(client)})
    call(client, "get", "/")
    assert "selected_regions" not in session_keys(client)
    sign_in(client, member)
    assert call(client, "get", "/").status_code == 200
    assert session_keys(client).get("selected_regions") == [region]


def test_the_browsers_history_still_links_a_second_account_signed_in_later(make_member):
    from sqlalchemy import text
    first, second = make_member(), make_member()
    client = first.app.test_client()
    sign_in(client, first)
    call(client, "post", "/logout", data={"formkey": first.formkey(client)})
    call(client, "get", "/")
    sign_in(client, second)
    db = first.db_session()
    db.rollback()
    linked = db.execute(text("SELECT count(*) FROM alts WHERE (user1 = :a AND user2 = :b) OR (user1 = :b AND user2 = :a)"),
                        {"a": first.id, "b": second.id}).scalar()
    assert linked == 1


def test_dismissing_the_install_tip_works_for_a_visitor_and_lasts_for_the_visit(real_app):
    app, _ = real_app
    client = app.test_client()
    answer = call(client, "post", "/dismiss_mobile_tip")
    assert answer.status_code == 204
    data, lasts = site_cookie(answer)
    assert "tooltip_last_dismissed" in data and not lasts and "session_id" not in data


def test_the_sign_up_form_works_without_a_cookie_before_it(real_app):
    """The token the form is checked against is made by the page, from nothing: no identifier had to be there first."""
    app, _ = real_app
    client = app.test_client()
    page = call(client, "get", "/signup")
    assert page.status_code == 200 and b'name="formkey"' in page.data
    with client.session_transaction() as session:
        assert session["signup_token"] and "session_id" not in session


def test_an_api_call_with_no_session_gets_no_cookie(real_app):
    app, _ = real_app
    answer = app.test_client().get("/api/v1/all/listing", base_url=BASE, headers=HEADERS, environ_base={"REMOTE_ADDR": addr()})
    assert answer.status_code < 500
    assert "session_ruqqus" not in " ".join(answer.headers.getlist("Set-Cookie"))


def test_the_privacy_page_says_it_on_the_real_app(real_app):
    app, _ = real_app
    def privacy(country):
        return app.test_client().get("/help/privacy", base_url=BASE, headers={**HEADERS, "CF-IPCountry": country},
                                     environ_base={"REMOTE_ADDR": addr()}).get_data(as_text=True)
    # where nobody is asked (the US) the players load on scroll, and the page says so; where the reader is asked (the UK) it says they wait
    page = privacy("US")
    assert "signed, not encrypted" in page and "Video players that load on scroll" in page and "late_box_h:" in page
    assert "##" not in page
    asked = privacy("GB")
    assert "signed, not encrypted" in asked and "Video players that load on scroll" not in asked and "late_box_h:" in asked
    # the sentences a switch decides are drawn where the switch is on
    assert "when you enter your password after signing in with a QR code" in page and "That record holds no address." in page
    assert '<a href="/info/image_hosts">' in page


def test_the_privacy_page_names_a_members_own_storage_only_where_it_is_offered(real_app, monkeypatch):
    app, _ = real_app
    def privacy():
        return call(app.test_client(), "get", "/help/privacy").get_data(as_text=True)
    monkeypatch.delenv("MEDIA_DEV_PROVIDER", raising=False)
    from ruqqus.helpers.media import registry
    monkeypatch.setattr(registry, "account_kinds", lambda: [])
    assert "Your own storage account" not in privacy()
    monkeypatch.setattr(registry, "account_kinds", lambda: ["google"])
    assert "your browser sends each file straight to it, not through us" in privacy()


# the key chat_key.js leaves in the browser when a sign-in form is sent (partials/forget_prekey.html)
FORGETS_THE_KEY = 'localStorage.removeItem("uqqus_chat_prekey")'


def page(answer):
    return answer.get_data(as_text=True)


@pytest.mark.parametrize("form", [{"username": "ci_nobody_here", "password": "whatever-1"}, {"username": "", "password": "whatever-1"}])
def test_a_refused_sign_in_takes_the_key_it_left(real_app, form):
    app, _ = real_app
    answer = call(app.test_client(), "post", "/login", data=form)
    assert answer.status_code in (200, 400) and 'id="login"' in page(answer)
    assert FORGETS_THE_KEY in page(answer)


def test_a_wrong_password_takes_the_key_too(real_app, make_member):
    app, _ = real_app
    member = make_member()
    answer = call(app.test_client(), "post", "/login", data={"username": member.username, "password": "not-the-password"})
    assert FORGETS_THE_KEY in page(answer)


@pytest.mark.parametrize("path", ["/", "/login", "/signup", "/forgot", "/help/privacy", "/help/about"])
def test_every_page_a_visitor_lands_on_takes_the_key(real_app, path):
    app, _ = real_app
    answer = call(app.test_client(), "get", path)
    assert answer.status_code == 200, path
    assert FORGETS_THE_KEY in page(answer), path


def test_an_error_page_for_a_visitor_takes_the_key(real_app):
    app, _ = real_app
    answer = call(app.test_client(), "get", "/this-page-is-not-here")
    assert answer.status_code == 404 and FORGETS_THE_KEY in page(answer)


@pytest.mark.parametrize("path", ["/", "/help/privacy", "/settings/security", "/this-page-is-not-here"])
def test_a_members_pages_leave_the_key_for_chat(make_member, path):
    member = make_member()
    answer = call(member.browser(), "get", path)
    assert FORGETS_THE_KEY not in page(answer), path


def test_the_redirect_after_a_real_sign_in_leaves_the_key_for_chat(real_app, make_member):
    app, _ = real_app
    member = make_member()
    client = app.test_client()
    answer = call(client, "post", "/login", data={"username": member.username, "password": member.password, "redirect": "/"})
    assert answer.status_code == 302
    assert FORGETS_THE_KEY not in page(call(client, "get", "/"))


def test_the_two_factor_step_keeps_the_key_because_the_sign_in_is_still_going_on(real_app, make_member):
    app, db_session = real_app
    member = make_member()
    db = db_session()
    user = member.user()
    user.mfa_secret = pyotp.random_base32()
    db.add(user)
    db.commit()
    client = app.test_client()
    first = call(client, "post", "/login", data={"username": member.username, "password": member.password})
    assert 'name="hash"' in page(first) and FORGETS_THE_KEY not in page(first)
    wrong = call(client, "post", "/login", data={
        "username": member.username, "2fa_token": "000000",
        "time": re.search(r'name="time" value="(\d+)"', page(first)).group(1),
        "hash": re.search(r'name="hash" value="([^"]+)"', page(first)).group(1)})
    assert "Invalid verification code" in page(wrong) and FORGETS_THE_KEY not in page(wrong)


def test_a_sign_in_ended_from_another_browser_takes_the_key_at_the_sign_in_form(make_member):
    member = make_member()
    client = member.browser()
    with client.session_transaction() as session:
        session["login_nonce"] = member.user().login_nonce - 1
    # the cookie still names the account, so an ordinary page can't tell and leaves the key; the form it is sent to takes it
    assert FORGETS_THE_KEY in page(call(client, "get", "/login"))
