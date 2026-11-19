"""A held account (ruqqus/helpers/quarantine.py): who is refused, and that each refusal counts what was tried."""
from types import SimpleNamespace

from ruqqus.helpers import quarantine, signup_signals
from tests.fake_redis import FakeRedis


def test_a_held_account_is_refused_and_the_attempt_counted():
    r = FakeRedis()
    held = SimpleNamespace(id=5, is_quarantined=True)
    assert quarantine.refusal(held, "vote", r) == quarantine.HELD
    assert quarantine.refusal(held, "vote", r)
    assert quarantine.refusal(held, "made-up", r)
    assert signup_signals.attempts(r, 5) == {"vote": 2, "other": 1}


def test_anyone_else_passes_and_nothing_is_counted():
    r = FakeRedis()
    for v in (None, SimpleNamespace(id=6, is_quarantined=False), SimpleNamespace(id=7)):
        assert quarantine.refusal(v, "post", r) is None
    assert r.data == {}


def test_no_redis_still_refuses():
    assert quarantine.refusal(SimpleNamespace(id=5, is_quarantined=True), "post", None) == quarantine.HELD


def test_every_action_has_an_admin_label():
    from pathlib import Path
    pages = (Path(__file__).resolve().parents[1] / "ruqqus/routes/signup_guard_pages.py").read_text()
    for action in quarantine.ACTIONS + ("other",):
        assert f'"{action}":' in pages, action


def test_an_automatic_hold_gets_its_own_message_and_the_review_link_and_the_guards_hold_keeps_the_email_one():
    automatic = SimpleNamespace(id=5, is_quarantined=True, quarantine_kind=1)
    guard = SimpleNamespace(id=6, is_quarantined=True, quarantine_kind=0)
    unsaid = SimpleNamespace(id=7, is_quarantined=True)
    assert quarantine.refusal(automatic, "post") == quarantine.HELD_AUTOMATIC
    assert quarantine.refusal(guard, "post") == quarantine.HELD and quarantine.refusal(unsaid, "post") == quarantine.HELD
    assert quarantine.is_automatic(automatic) and not quarantine.is_automatic(guard) and not quarantine.is_automatic(unsaid) and not quarantine.is_automatic(None)
    assert "email" not in quarantine.HELD_AUTOMATIC.lower() and "email" in quarantine.HELD.lower()           # no email releases it, so none is offered
    assert quarantine.APPEAL_LINK == "/appeal/quarantine"


def test_the_attempts_of_an_automatically_held_account_are_counted_too():
    r = FakeRedis()
    assert quarantine.refusal(SimpleNamespace(id=5, is_quarantined=True, quarantine_kind=1), "comment", r) == quarantine.HELD_AUTOMATIC
    assert signup_signals.attempts(r, 5) == {"comment": 1}
