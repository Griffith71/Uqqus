"""How long the site's own cookie lasts and what identifies whom in it (ruqqus/helpers/session_cookie.py), on Flask's real
cookie session, so that "sets no cookie" means what a browser would get. tests/pg/test_visitor_cookie.py has the real app."""
import re
from pathlib import Path

from flask import Flask, session

from ruqqus.helpers import session_cookie


def new_id():
    return "made-here"


def answer_for(before, *then):
    """The Set-Cookie header a request carrying the session `before` answers with, once settled. The browser's cookie is
    built by the same interface the site's own subclasses."""
    app = Flask(__name__)
    app.secret_key = "test"
    app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 60 * 24 * 365
    app.config["SESSION_REFRESH_EACH_REQUEST"] = True      # as ruqqus/__main__.py

    @app.route("/")
    def page():
        session_cookie.settle(session, new_id)
        for step in then:
            step(session)
        return "ok"

    client = app.test_client()
    if before:
        with client.session_transaction() as held:
            held.update(before)
    return client.get("/").headers.get("Set-Cookie")


def test_a_visitor_with_no_cookie_is_given_none():
    assert answer_for({}) is None


def test_a_visitor_who_chose_something_gets_a_cookie_that_ends_with_the_browser_and_holds_only_that():
    cookie = answer_for({}, lambda held: held.__setitem__("catids", [1]))
    assert cookie.startswith("session=") and "Expires" not in cookie and "Max-Age" not in cookie


def test_a_visitors_cookie_from_before_loses_its_identifier_and_its_year_and_is_deleted_when_nothing_is_left():
    cookie = answer_for({"_permanent": True, "session_id": "old"})
    assert "session=;" in cookie and "1970" in cookie         # Flask's way of deleting a cookie


def test_a_visitors_filters_from_before_stay_but_only_until_the_browser_closes():
    cookie = answer_for({"_permanent": True, "session_id": "old", "catids": [1]})
    assert cookie.startswith("session=e") and "Expires" not in cookie


def settled(held):
    class Session(dict):
        permanent = property(lambda self: self.get("_permanent", False),
                             lambda self, value: self.__setitem__("_permanent", bool(value)))
    session = Session(held)
    session_cookie.settle(session, new_id)
    return dict(session)


def test_a_member_keeps_everything_and_has_a_year():
    assert settled({"user_id": 5, "session_id": "s", "login_nonce": 2}) == {
        "user_id": 5, "session_id": "s", "login_nonce": 2, "_permanent": True}


def test_a_member_with_no_session_id_or_a_cleared_one_is_given_one_or_the_form_key_would_be_made_from_none():
    assert settled({"user_id": 5})["session_id"] == "made-here"
    assert settled({"user_id": 5, "session_id": None})["session_id"] == "made-here"


def test_a_member_with_a_session_id_is_not_given_another_or_their_open_pages_form_keys_would_stop_working():
    assert settled({"user_id": 5, "session_id": "s", "_permanent": True})["session_id"] == "s"


def test_nothing_is_written_for_a_visitor_with_nothing_to_settle():
    # the setter of `permanent` is a write, in either direction: it would make the cookie this exists to avoid
    assert settled({}) == {}


def test_signing_out_takes_the_sign_in_out():
    out = settled({"user_id": None, "session_id": None, "login_nonce": 3, "_permanent": True, "base_feed": "all"})
    assert out == {"base_feed": "all"}


FILTERS = {"selected_regions": ["eu"], "langcodes": ["de"], "catids": [1], "groupids": [2], "feed_post_types": ["poll"],
           "feed_filter": {"from": ["bob"]}}


def test_a_sign_in_ending_takes_whose_filters_they_were_and_the_filters():
    # front._apply_saved_defaults writes the account's id and copies its saved default in on any feed: neither outlives the sign-in
    after_log_out = settled({"user_id": None, "session_id": None, "login_nonce": 3, "_permanent": True, "feed_defaults_for": 7,
                             "base_feed": "all", **FILTERS})
    assert after_log_out == {"base_feed": "all"}
    past_its_end = settled({"user_id": 7, "session_id": "a", "login_nonce": 3, "_permanent": True, "qr_limited": True,
                            "qr_expires_utc": 1, "feed_defaults_for": 7, **FILTERS})
    assert past_its_end == {}


def test_an_old_cookie_that_still_says_whose_filters_they_were_is_put_right():
    assert settled({"feed_defaults_for": 7, **FILTERS}) == {}


def test_a_browser_that_keeps_its_year_keeps_only_the_list_of_accounts():
    out = settled({"user_id": None, "session_id": None, "_permanent": True, "history": [7], "feed_defaults_for": 7, **FILTERS})
    assert out == {"_permanent": True, "history": [7]}


def test_a_visitors_own_filters_are_not_a_sign_in_ending():
    assert settled(dict(FILTERS)) == FILTERS
    assert settled({"_permanent": True, "history": [7], **FILTERS}) == {"_permanent": True, "history": [7], **FILTERS}


def test_a_member_still_signed_in_keeps_them():
    held = {"user_id": 7, "session_id": "a", "_permanent": True, "feed_defaults_for": 7, **FILTERS}
    assert settled(held) == held


def test_what_a_member_alone_can_write_goes_with_the_sign_in():
    assert "media_connect" in session_cookie.SIGN_IN_KEYS
    assert settled({"user_id": None, "media_connect": {"n": "x", "t": 1}}) == {}


def test_every_filter_the_feed_routes_write_is_one_the_sign_in_takes():
    front = (Path(__file__).resolve().parent.parent / "ruqqus" / "routes" / "front.py").read_text(encoding="utf-8")
    written = set(re.findall(r"flask_session\[\s*['\"](\w+)['\"]\s*\]\s*=", front))
    # a preference that names nobody and what says whose the filters were
    assert written - {"base_feed", "feed_defaults_for"} == set(session_cookie.FILTER_KEYS)
    assert "feed_defaults_for" in session_cookie.SIGN_IN_KEYS


def test_a_browser_that_holds_the_accounts_signed_in_on_it_keeps_its_year_after_signing_out():
    # check_for_alts and the signup guard's ban_evade read `history`: a session cookie would end that at the first closed browser
    out = settled({"user_id": None, "session_id": None, "login_nonce": 3, "_permanent": True, "history": [5, 6]})
    assert out == {"_permanent": True, "history": [5, 6]}
