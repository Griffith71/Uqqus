"""QR sign-in's rules (helpers/qr_login.py). The flow on the real app and a real Redis is tests/pg/test_qr_login.py."""
from types import SimpleNamespace

import pytest

from ruqqus.helpers import oidc_provider, qr_login, session_cookie
from tests.fake_redis import FakeRedis

REDIS = object()


def test_it_is_on_unless_the_site_says_otherwise():
    assert qr_login.DEFAULT == "1"
    assert qr_login.switched_on({})
    assert qr_login.offered({}, REDIS, False)


@pytest.mark.parametrize("value", ["1", "yes", "on", " TRUE ", "true"])
def test_a_switched_on_value(value):
    assert qr_login.switched_on({"QR_LOGIN_ENABLED": value})


@pytest.mark.parametrize("value", ["", " ", "0", "false", "No", "OFF"])
def test_a_switched_off_value(value):
    assert not qr_login.switched_on({"QR_LOGIN_ENABLED": value})


def test_no_redis_or_read_only_hides_it_too():
    on = {"QR_LOGIN_ENABLED": "1"}
    assert qr_login.offered(on, REDIS, False)
    assert not qr_login.offered(on, None, False)
    assert not qr_login.offered(on, REDIS, True)


def test_a_code_is_long_random_and_only_its_digest_is_kept():
    code, digest = qr_login.new_code()
    other, _ = qr_login.new_code()
    assert code != other and len(code) >= 43
    assert digest == oidc_provider.digest(code) and code not in digest
    assert qr_login.digest_of(code) == digest


@pytest.mark.parametrize("value", [None, "", 12, "x" * 129, ["a"]])
def test_what_is_not_a_code_has_no_digest(value):
    assert qr_login.digest_of(value) is None


def test_the_address_carries_the_code_only_in_its_fragment():
    address = qr_login.approve_address("example.com", True, "CODE")
    assert address == "https://example.com/login/qr/approve#c=CODE"
    assert qr_login.approve_address("localhost", 0, "CODE").startswith("http://localhost/")


def account(**values):
    fields = dict(is_deleted=False, is_bot=False, is_suspended=False, qr_login_off=False, login_nonce=3)
    return SimpleNamespace(**{**fields, **values})


def test_an_ordinary_account_may_and_a_held_one_too():
    assert qr_login.refusal(account()) is None
    assert qr_login.refusal(account(is_quarantined=True)) is None
    assert qr_login.refusal(account(), 3) is None


@pytest.mark.parametrize("values, why", [
    ({"is_deleted": True}, qr_login.DELETED), ({"is_bot": True}, qr_login.BOT), ({"is_suspended": True}, qr_login.SUSPENDED),
    ({"qr_login_off": True}, qr_login.SWITCHED_OFF),
])
def test_who_may_not(values, why):
    assert qr_login.refusal(account(**values)) == why


def test_no_account_and_a_nonce_that_moved_are_refused():
    assert qr_login.refusal(None) == qr_login.DELETED
    assert qr_login.refusal(account(), 2) == qr_login.NONCE_MOVED


def test_a_limited_session_ends_at_its_time_and_a_whole_one_never():
    now = 1_000_000
    assert not qr_login.session_over({}, now)
    assert not qr_login.session_over({"user_id": 1}, now)
    session = {}
    qr_login.begin_limited(session, now)
    assert qr_login.is_limited(session) and session[qr_login.EXPIRES_KEY] == now + 12 * 3600
    assert not qr_login.session_over(session, now + 12 * 3600 - 1)
    assert qr_login.session_over(session, now + 12 * 3600)


@pytest.mark.parametrize("session", [{"qr_limited": True}, {"qr_expires_utc": "soon"}, {"qr_expires_utc": True}, {"qr_expires_utc": 1.5}])
def test_an_end_that_cant_be_read_is_over(session):
    assert qr_login.session_over(session, 10)


def test_giving_the_password_ends_both():
    session = {}
    qr_login.begin_limited(session, 5)
    qr_login.end_limited(session)
    assert session == {}
    assert not qr_login.session_over(session, 10 ** 10)


def test_the_cookies_digest_counts_only_for_a_few_minutes():
    session = {}
    qr_login.remember_pending(session, "d", 100)
    assert qr_login.pending_digest(session, 100 + qr_login.PENDING_SECONDS) == "d"
    assert qr_login.pending_digest(session, 101 + qr_login.PENDING_SECONDS) is None
    assert qr_login.pending_digest({"qr_pending": "just-a-string"}, 100) is None


def test_a_passing_status_reads_what_redis_holds():
    r = FakeRedis()
    assert qr_login.status_of(r, None) == ("expired", None)
    assert qr_login.status_of(r, "d") == ("expired", None)
    r.set(qr_login.wait_key("d"), "{}")
    assert qr_login.status_of(r, "d") == ("waiting", None)
    r.set(qr_login.no_key("d"), 1)
    assert qr_login.status_of(r, "d")[0] == "denied"
    r.delete(qr_login.no_key("d"))
    r.set(qr_login.ok_key("d"), '{"uid": 7, "nonce": 1, "t": 2}')
    assert qr_login.status_of(r, "d") == ("approved", {"uid": 7, "nonce": 1, "t": 2})


def test_the_fifth_wrong_answer_deletes_the_code():
    r = FakeRedis()
    r.set(qr_login.wait_key("d"), "{}")
    for _ in range(4):
        assert qr_login.wrong_answer(r, "d") is True
        assert r.get(qr_login.wait_key("d"))
    assert qr_login.wrong_answer(r, "d") is False
    assert r.get(qr_login.wait_key("d")) is None


@pytest.mark.parametrize("raw", [None, "", "{", "[1]", "3", b"\xff"])
def test_a_stored_value_that_isnt_ours_reads_as_nothing(raw):
    assert qr_login.read_json(raw) is None


def new_id():
    return "made-here"


def test_settle_keeps_a_qr_sign_in_until_its_end_and_never_moves_it():
    session = {"user_id": 7, "session_id": "s", "login_nonce": 0, "_permanent": True}
    qr_login.begin_limited(session, 1000)
    end = session[qr_login.EXPIRES_KEY]
    session_cookie.settle(session, new_id, now=1001)
    assert session["user_id"] == 7 and session[qr_login.EXPIRES_KEY] == end and session[qr_login.LIMITED_KEY] is True


def test_settle_signs_it_out_at_its_end_and_a_cookie_with_nothing_else_goes():
    session = {"user_id": 7, "session_id": "s", "login_nonce": 0, "_permanent": True}
    qr_login.begin_limited(session, 1000)
    session_cookie.settle(session, new_id, now=1000 + qr_login.SESSION_SECONDS)
    assert session == {}


def test_settle_at_its_end_keeps_history_as_any_sign_out_does():
    session = {"user_id": 7, "session_id": "s", "login_nonce": 0, "_permanent": True, "history": [7]}
    qr_login.begin_limited(session, 1000)
    session_cookie.settle(session, new_id, now=1000 + qr_login.SESSION_SECONDS)
    assert session == {"_permanent": True, "history": [7]}


def test_log_out_clears_it_with_the_other_sign_in_keys():
    assert {qr_login.LIMITED_KEY, qr_login.EXPIRES_KEY} <= set(session_cookie.SIGN_IN_KEYS)
    session = {"user_id": None, "session_id": None, "qr_limited": True, "qr_expires_utc": 10 ** 11}
    session_cookie.settle(session, new_id, now=5)
    assert session == {}


def test_settle_makes_nothing_year_long_for_a_visitor_waiting_on_a_code():
    session = {}
    qr_login.remember_pending(session, "d", 100)
    session_cookie.settle(session, new_id, now=101)
    assert session == {"qr_pending": {"d": "d", "t": 100}}


def test_settle_drops_the_digest_once_it_is_useless_or_someone_signs_in():
    session = {"history": [1], "_permanent": True}
    qr_login.remember_pending(session, "d", 100)
    session_cookie.settle(session, new_id, now=100 + qr_login.PENDING_SECONDS + 1)
    assert "qr_pending" not in session
    session = {"user_id": 3, "session_id": "s", "_permanent": True}
    qr_login.remember_pending(session, "d", 100)
    session_cookie.settle(session, new_id, now=101)
    assert "qr_pending" not in session


def test_a_sign_in_found_to_be_over_lets_go_of_the_cookie():
    from ruqqus.helpers import session_cookie
    session = {"user_id": 1, "session_id": "x", "login_nonce": 3, qr_login.LIMITED_KEY: True, qr_login.EXPIRES_KEY: 99,
               qr_login.PENDING_KEY: {"d": "a", "t": 1}, "history": [1], "base_feed": "all", "catids": [2]}
    session_cookie.forget_sign_in(session)
    # the filters were the account's to take with it (session_cookie.FILTER_KEYS); what is left is the browser's
    assert session == {qr_login.PENDING_KEY: {"d": "a", "t": 1}, "history": [1], "base_feed": "all"}
