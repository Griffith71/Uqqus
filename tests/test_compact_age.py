"""The short age on a notifications row (ruqqus/helpers/compact_age.py)."""
from ruqqus.helpers import compact_age as ca


def test_now_minutes_hours_days_and_then_a_date():
    assert ca.parts(0) == ("now", 0) and ca.parts(59) == ("now", 0)
    assert ca.parts(60) == ("m", 1) and ca.parts(3599) == ("m", 59)
    assert ca.parts(3600) == ("h", 1) and ca.parts(86399) == ("h", 23)
    assert ca.parts(86400) == ("d", 1) and ca.parts(6 * 86400 + 86399) == ("d", 6)
    assert ca.parts(7 * 86400) is None and ca.parts(400 * 86400) is None


def test_a_clock_a_little_ahead_reads_as_now():
    assert ca.parts(-5) == ("now", 0)
