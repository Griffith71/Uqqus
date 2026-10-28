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
