"""New-sign-in emails (helpers/signin_alerts.py): who is told, when something is new, and how often."""
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import signin_alerts

NOW = 2_000_000_000
DAY = 86400
NAMES = {"GB": "United Kingdom", "DE": "Germany", "FR": "France"}
CHROME_WIN = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
CHROME_WIN_NEWER = CHROME_WIN.replace("126.0", "127.0")
FIREFOX_LINUX = "Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0"


class FakeRedis:
    def __init__(self):
        self.keys = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.keys:
            return None
        self.keys[key] = value
        return True


@pytest.fixture
def db():
    session = Session(create_engine("sqlite://"))
    session.execute(text("CREATE TABLE login_events (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, "
                         "created_utc INTEGER, ip TEXT, cf_country TEXT, region_code TEXT, user_agent TEXT)"))
    return session


def signed_in(db, user=1, when=NOW - DAY, country="GB", agent=CHROME_WIN):
    db.execute(text("INSERT INTO login_events (user_id, created_utc, cf_country, user_agent) VALUES (:u, :t, :c, :a)"),
               {"u": user, "t": when, "c": country, "a": agent})


def member(**kw):
    return SimpleNamespace(**{"id": 1, "signin_alerts": True, "is_activated": True, "email": "a@example.com", **kw})


def check(db, country="GB", agent=CHROME_WIN, redis=None, user=None):
    return signin_alerts.check(db, user or member(), country, agent, NOW, redis or FakeRedis(), NAMES)


def test_the_first_sign_in_has_nothing_to_be_new_against(db):
    assert check(db) is None


def test_the_same_country_and_browser_is_not_new_even_with_a_newer_version(db):
    signed_in(db)
    assert check(db) is None
    assert check(db, agent=CHROME_WIN_NEWER) is None


def test_a_new_country_is_new_and_says_so_in_words(db):
    signed_in(db)
    alert = check(db, country="FR")
    assert alert["why"] == ["country"] and alert["place"] == "France" and alert["browser"] == "Chrome on Windows"
    assert alert["when"].endswith("UTC")


def test_a_new_browser_or_system_is_new(db):
    signed_in(db)
    assert check(db, agent=FIREFOX_LINUX)["why"] == ["browser"]
    assert check(db, country="DE", agent=FIREFOX_LINUX, redis=FakeRedis())["why"] == ["country", "browser"]


def test_country_codes_compare_without_regard_to_case_and_a_missing_one_is_one_value(db):
    signed_in(db, country="gb")
    assert check(db, country="GB") is None
    signed_in(db, user=2, country=None)
    assert check(db, user=member(id=2), country=None) is None


def test_old_sign_ins_without_a_browser_only_judge_the_country(db):
    """Sign-ins from before the browser text was kept: otherwise every account is emailed on its first sign-in after the change."""
    signed_in(db, agent=None)
    assert check(db) is None
    assert check(db, country="DE")["why"] == ["country"]


def test_only_the_last_90_days_count(db):
    signed_in(db, when=NOW - 91 * DAY, country="FR")
    signed_in(db, when=NOW - 5 * DAY, country="GB")
    assert check(db, country="FR")["why"] == ["country"], "a country last used 91 days ago is new again"


def test_another_accounts_sign_ins_say_nothing(db):
    signed_in(db, user=2, country="FR")
    signed_in(db, user=1, country="GB")
    assert check(db, country="FR")["why"] == ["country"]


def test_at_most_one_email_a_day_per_account(db):
    signed_in(db)
    signed_in(db, user=2)
    redis = FakeRedis()
    assert check(db, country="FR", redis=redis) is not None
    assert check(db, country="DE", redis=redis) is None, "a second one the same day is not sent"
    assert check(db, country="DE", redis=redis, user=member(id=2)) is not None, "another account has its own key"
    assert {"signin_alert:1", "signin_alert:2"} <= set(redis.keys)


def test_a_failing_redis_sends_nothing_and_no_redis_still_allows_it():
    class Broken:
        def set(self, *a, **k):
            raise ConnectionError("down")
    assert signin_alerts.claim_today(Broken(), 1) is False
    assert signin_alerts.claim_today(None, 1) is True


@pytest.mark.parametrize("user", [member(signin_alerts=False), member(is_activated=False), member(email=None), member(email="")])
def test_nobody_is_emailed_without_a_verified_address_or_with_the_setting_off(db, user):
    signed_in(db)
    assert signin_alerts.wants_email(user) is False
    assert check(db, country="FR", user=user) is None


def test_the_email_never_carries_the_address(db):
    signed_in(db)
    alert = check(db, country="FR")
    assert set(alert) == {"browser", "place", "when", "why"}


def test_the_subject_names_the_site():
    assert signin_alerts.subject("Laba") == "New sign-in to your Laba account"
