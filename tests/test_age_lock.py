"""The child lock and the regional rule (ruqqus/helpers/age_declaration.py, helpers/age_assurance.py, helpers/visibility.py): which
countries are strict, the youngest age to sign up alone, who is locked and who is merely in a strict country without a confirmed age,
what each does to what a reader sees, and the vendor side with none configured. The routes run on the real app in tests/pg/test_age_lock.py."""
from types import SimpleNamespace as NS

import pytest
from flask import Flask

from ruqqus.helpers import age_assurance, age_declaration as age
from ruqqus.helpers.visibility import CHILD, STANDARD, stored_level, viewer_level

NOW = 2_000_000_000


def member(**kw):
    base = dict(age_declared=1, age_lock=None, age_assured_utc=0, age_under_utc=0, filter_level=None, private_posting=False)
    base.update(kw)
    return NS(**base)


def on(country):
    """A request from this country (None: no header), for code that reads the request."""
    app = Flask(__name__)
    headers = {"CF-IPCountry": country} if country else {}
    return app.test_request_context("/", headers=headers)


# ---------------------------------------------------------------- where it applies

@pytest.mark.parametrize("country", ["GB", "UK", "DE", "FR", "IE", "NL", "SE", "NO", "IS", "LI", "AU", "AX", "GG", "JE", "GI", "RE"])
def test_the_uk_the_eu_eea_and_australia_are_strict(country):
    assert age.is_strict(country)


@pytest.mark.parametrize("country", ["US", "CA", "NZ", "BR", "IN", "JP", "CH", "T1", "XX", "ZZ", "", None, "gbr", "G", "1A", 5, "AU "[:1]])
def test_everywhere_else_and_anything_unknown_is_not(country):
    assert not age.is_strict(country)


def test_a_lowercase_header_is_still_a_country_and_whitespace_is_trimmed():
    assert age.is_strict("gb") and age.is_strict(" DE ")


def test_the_list_can_be_set_by_the_deployment(monkeypatch):
    monkeypatch.setenv("AGE_LOCK_COUNTRIES", "US, nz ,xx,zzz")
    assert age.is_strict("US") and age.is_strict("NZ") and not age.is_strict("GB") and not age.is_strict("XX")
    monkeypatch.setenv("AGE_LOCK_COUNTRIES", "   ")
    assert age.is_strict("GB")                      # nothing usable: the default stands


# ---------------------------------------------------------------- the youngest age to sign up alone

@pytest.mark.parametrize("country, expected", [("US", 13), (None, 13), ("GB", 13), ("KR", 14), ("ES", 14), ("AU", 16), ("DE", 16),
                                                ("FR", 15), ("NL", 16), ("IE", 16), ("BE", 13), ("AT", 14), ("T1", 13)])
def test_the_youngest_age_is_the_highest_of_the_platforms_and_the_national_age_of_consent(country, expected):
    assert age.min_age(country) == expected


def test_the_minimum_can_be_overridden(monkeypatch):
    monkeypatch.setenv("AGE_MIN_AGES", "DE=14, FR=16 ,bad,XX=9,US=x")
    assert age.min_age("DE") == 14 and age.min_age("FR") == 16 and age.min_age("US") == 13


def test_every_listed_age_is_a_sane_age():
    assert all(13 <= n <= 16 for n in list(age.CONSENT_AGE.values()) + list(age.FACEBOOK_MIN.values()))


# ---------------------------------------------------------------- the sign-up answer

def test_adult_cannot_be_chosen_from_a_strict_country_and_is_stored_as_the_locked_answer():
    assert age.signup_answer(age.ADULT, "GB") == age.CHILD
    assert age.signup_answer(age.ADULT, "AU") == age.CHILD
    assert age.signup_answer(age.ADULT, "US") == age.ADULT
    assert age.signup_answer(age.ADULT, None) == age.ADULT
    assert age.signup_answer(age.CHILD, "GB") == age.CHILD and age.signup_answer(age.CHILD, "US") == age.CHILD


def test_a_locked_sign_up_writes_the_lock_the_filter_and_private_posting_in_one_statement():
    fields = age.fields_for(age.signup_answer(age.ADULT, "DE"), NOW)
    assert fields["age_declared"] == age.CHILD and fields["age_lock"] == age.REGION_LOCK and fields["private_posting"] is True
    assert fields["filter_level"] == CHILD and fields["all_shows_sensitive"] is False


# ---------------------------------------------------------------- who is locked

def test_the_stored_lock_is_a_child_account_whichever_way_it_came_to_be_one():
    assert age.is_locked(member(age_declared=2, age_lock=1)) and age.is_locked(member(age_declared=2, age_lock=2))
    assert age.is_locked(member(age_declared=2, age_lock=None)), "a child account from before the lock existed"
    assert not age.is_locked(member()) and not age.is_locked(member(age_declared=None)) and not age.is_locked(None)
    assert not age.is_locked(NS())


def test_assured_means_a_check_or_an_admin_said_so():
    assert age.assured(member(age_assured_utc=NOW)) and not age.assured(member()) and not age.assured(None)
    assert not age.assured(member(age_assured_utc=-1)), "nothing negative ever counts"


def test_the_regional_rule_locks_what_is_seen_not_what_is_stored():
    assert age.region_locked(None, "GB") and age.region_locked(member(), "DE") and age.region_locked(member(age_declared=None), "AU")
    assert not age.region_locked(member(age_assured_utc=NOW), "GB"), "a confirmed age is not held to it"
    assert not age.region_locked(member(), "US") and not age.region_locked(None, None) and not age.region_locked(None, "T1")


# ---------------------------------------------------------------- the one place it bites

def test_a_visitor_in_a_strict_country_browses_at_the_child_level_and_elsewhere_at_standard():
    with on("GB"):
        assert viewer_level(None) == CHILD
    with on("US"), on(None):
        assert viewer_level(None) == STANDARD


def test_a_member_keeps_their_own_level_unless_the_law_asks_for_more():
    off = member(filter_level=0)
    with on("US"):
        assert viewer_level(off) == 0
    with on("DE"):
        assert viewer_level(off) == CHILD and stored_level(off) == 0, "the account's own choice is untouched"
        assert viewer_level(member(age_assured_utc=NOW, filter_level=0)) == 0
        assert viewer_level(member(filter_level=CHILD)) == CHILD


def test_outside_a_request_the_account_alone_decides():
    """A job, a script or a scheduled post has no request: it follows the account, never a country."""
    assert viewer_level(None) == STANDARD and viewer_level(member(filter_level=0)) == 0
    assert age.request_country() is None and age.current_region_locked(None) is False


# ---------------------------------------------------------------- lifting it

def test_declaring_18_lifts_the_lock_turns_private_posting_off_and_returns_the_filter_to_standard():
    lifted = age.lift_fields(NOW, False)
    assert lifted == {"age_declared": 1, "age_declared_utc": NOW, "age_lock": None, "private_posting": False, "filter_level": 1}
    assert "age_assured_utc" not in lifted, "a tick is not a check"


def test_a_check_also_records_that_it_happened_and_an_admins_leaves_the_filter_alone():
    assert age.lift_fields(NOW, True)["age_assured_utc"] == NOW
    assert "filter_level" not in age.lift_fields(NOW, True, reset_filter=False)


def test_a_check_on_an_account_that_was_never_locked_only_records_it():
    plain = age.assure_fields(member(), NOW)
    assert plain == {"age_assured_utc": NOW}, "its word filter and Private posting stay as the member chose"
    first = age.assure_fields(member(age_declared=None), NOW)
    assert first == {"age_assured_utc": NOW, "age_declared": 1, "age_declared_utc": NOW}
    locked = age.assure_fields(member(age_declared=2, age_lock=1), NOW)
    assert locked["age_lock"] is None and locked["age_assured_utc"] == NOW and locked["private_posting"] is False


# ---------------------------------------------------------------- what a locked account can't do

def test_a_locked_account_is_refused_the_features_and_nobody_may_start_one_with_it():
    locked, free = member(age_declared=2, age_lock=1), member()
    assert age.feature_refusal(locked) and age.feature_refusal(free) is None and age.feature_refusal(None) is None
    assert age.target_refusal(locked) and age.target_refusal(free) is None
    assert age.target_refusal(locked) == age.TARGET_LOCKED, "one sentence that never says why"


def test_sensitive_and_anonymous_posting_are_refused_to_a_locked_account_only():
    locked, free = member(age_declared=2, age_lock=1), member()
    assert age.post_refusal(locked, True, False) and age.post_refusal(locked, False, True)
    assert age.post_refusal(locked, False, False) is None
    assert age.post_refusal(free, True, True) is None


def test_the_word_filter_and_the_sensitive_choice_of_a_locked_account_stay_locked():
    locked = member(age_declared=2, age_lock=2)
    assert age.locked_filter(locked, 0) and age.locked_filter(locked, CHILD) is None
    assert age.locked_sensitive(locked, True) and age.locked_sensitive(locked, False) is None
    assert age.locked_filter(member(), 0) is None


# ---------------------------------------------------------------- the vendor, with none chosen

def test_with_no_provider_nothing_is_offered(monkeypatch):
    monkeypatch.delenv("AGE_ASSURANCE_PROVIDER", raising=False)
    monkeypatch.delenv("AGE_ASSURANCE_DEV", raising=False)
    assert not age_assurance.configured() and age_assurance.provider() is None


def test_the_stand_in_exists_only_with_its_own_switch(monkeypatch):
    monkeypatch.setenv("AGE_ASSURANCE_PROVIDER", "dev")
    monkeypatch.delenv("AGE_ASSURANCE_DEV", raising=False)
    assert not age_assurance.configured(), "naming it is not enough: it is never on a live site"
    monkeypatch.setenv("AGE_ASSURANCE_DEV", "1")
    assert age_assurance.configured()
    for result in age_assurance.STATUSES:
        monkeypatch.setenv("AGE_ASSURANCE_DEV_RESULT", result)
        url = age_assurance.provider().start(member(), "https://site/cb", "state123")
        assert url == f"https://site/cb?state=state123&result={result}"
        assert age_assurance.provider().finish(member(), {"result": result}) == result
    assert age_assurance.provider().finish(member(), {"result": "adult; drop table"}) == age_assurance.FAILED
    assert age_assurance.provider().finish(member(), {}) == age_assurance.FAILED


class FakeRedis:
    def __init__(self):
        self.data = {}

    def set(self, key, value, ex=None):
        self.data[key] = str(value).encode()

    def get(self, key):
        return self.data.get(key)

    def delete(self, key):
        return 1 if self.data.pop(key, None) is not None else 0


def test_a_state_is_one_time_and_belongs_to_the_account_that_started_it():
    r = FakeRedis()
    state = age_assurance.new_state(r, 7)
    assert len(state) >= 8
    assert age_assurance.take_state(r, state, 8) is False, "someone else's can't finish it"
    assert age_assurance.take_state(r, state, 7) is True
    assert age_assurance.take_state(r, state, 7) is False, "once"
    for bad in (None, "", "short", "x" * 100, 5, ["a"]):
        assert age_assurance.take_state(r, bad, 7) is False
