"""The call relay's monthly allowance (ruqqus/helpers/turn_usage.py rules, helpers/turn_usage_store.py on SQLite).

The promises: what coturn sends is counted once (a restart of coturn is a new count, never a negative one or a
recount), video goes off from 80% and calls pause from 100%, and it fails closed on cost: no allowance, or no
recent reading of the relay, means paused."""
import calendar

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import turn_usage as rules
from ruqqus.helpers import turn_usage_store as store

OCT_16 = calendar.timegm((2026, 10, 16, 12, 0, 0))
NOV_1 = calendar.timegm((2026, 11, 1, 0, 0, 0))
GB = rules.GB

METRICS = """# HELP turn_total_traffic_sentb Represents total finished sessions sent bytes
# TYPE turn_total_traffic_sentb counter
turn_total_traffic_sentb 1500
# TYPE turn_total_traffic_peer_sentb counter
turn_total_traffic_peer_sentb{realm="uqqus.local"} 2.5e3
# TYPE turn_total_traffic_rcvb counter
turn_total_traffic_rcvb 999999
# TYPE turn_total_allocations gauge
turn_total_allocations 3
"""

FRESH = """# TYPE turn_total_traffic_sentb counter

# TYPE turn_total_traffic_peer_sentb counter

# TYPE turn_total_allocations gauge
"""


def test_only_what_the_relay_sent_is_counted():
    assert rules.sent_bytes(METRICS) == 4000          # sent to clients and to peers; received bytes are not
    assert rules.sent_bytes(FRESH) == 0               # coturn names its counters before they have a value
    assert rules.sent_bytes("") is None
    assert rules.sent_bytes("<html>not coturn</html>") is None
    assert rules.sent_bytes("go_goroutines 7\n") is None


def test_a_restart_of_coturn_starts_a_new_count():
    assert rules.advance(1000, 1600) == 600
    assert rules.advance(1600, 1600) == 0
    assert rules.advance(5000, 200) == 200            # coturn restarted: its counter began again at 0
    assert rules.advance(None, 700) == 700            # the first reading counts what was sent before
    assert rules.advance(100, None) == 0
    assert rules.advance(100, -5) == 0


def test_months_are_utc_and_the_pause_ends_on_the_first():
    assert rules.month_of(OCT_16) == "2026-10"
    assert rules.month_of(NOV_1 - 1) == "2026-10" and rules.month_of(NOV_1) == "2026-11"
    assert rules.next_month_start(OCT_16) == NOV_1
    assert rules.next_month_start(calendar.timegm((2026, 12, 31, 23, 0, 0))) == calendar.timegm((2027, 1, 1, 0, 0, 0))
    assert rules.paused_message(OCT_16) == "Calls are paused until 1 November. Messages still work."


@pytest.mark.parametrize("value, expected", [
    ("50", 50 * GB), (" 0.5 ", GB // 2), ("", None), (None, None), ("0", None), ("-3", None),
    ("lots", None), ("nan", None), ("inf", None),
])
def test_the_allowance_is_a_positive_number_of_gb(value, expected):
    assert rules.allowance_bytes(value) == expected


def test_video_goes_off_at_80_percent_and_calls_pause_at_100():
    allowance = 100 * GB
    fresh = OCT_16 - 60
    assert rules.state(0, allowance, fresh, OCT_16) == rules.OPEN
    assert rules.state(79 * GB, allowance, fresh, OCT_16) == rules.OPEN
    assert rules.state(80 * GB, allowance, fresh, OCT_16) == rules.AUDIO_ONLY
    assert rules.state(100 * GB, allowance, fresh, OCT_16) == rules.PAUSED
    assert rules.video_allowed(rules.OPEN) and not rules.video_allowed(rules.AUDIO_ONLY)
    assert not rules.video_allowed(rules.PAUSED)


def test_it_fails_closed_on_cost():
    allowance = 100 * GB
    assert rules.state(0, None, OCT_16, OCT_16) == rules.PAUSED                          # no allowance set
    assert rules.state(0, allowance, 0, OCT_16) == rules.PAUSED                          # never read
    assert rules.state(0, allowance, OCT_16 - rules.STALE_SECONDS - 1, OCT_16) == rules.PAUSED   # the job stopped
    assert rules.state(0, allowance, OCT_16 - rules.STALE_SECONDS, OCT_16) == rules.OPEN


def test_what_a_join_is_told():
    allowance = 10 * GB
    assert rules.refusal(rules.OPEN, 0, allowance, OCT_16) is None
    assert rules.refusal(rules.AUDIO_ONLY, 9 * GB, allowance, OCT_16) is None
    assert rules.refusal(rules.PAUSED, 10 * GB, allowance, OCT_16) == rules.paused_message(OCT_16)
    assert rules.refusal(rules.PAUSED, 0, allowance, OCT_16) == rules.UNAVAILABLE         # stale, not spent
    assert rules.refusal(rules.PAUSED, 0, None, OCT_16) == rules.UNAVAILABLE


def _migration_sql():
    from pathlib import Path
    sql = (Path(__file__).resolve().parent.parent / "scripts" / "migrations" / "2026-10-21_turn_usage.sql").read_text(encoding="utf-8")
    body = sql.split("CREATE TABLE IF NOT EXISTS public.turn_usage", 1)[1].split(";", 1)[0]
    return "CREATE TABLE turn_usage" + body


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text(_migration_sql()))
    with Session(engine) as session:
        yield session


def test_readings_add_up_within_a_month(db):
    assert store.used(db, OCT_16) == 0 and store.last_reading(db) == 0
    assert store.record(db, 1000, OCT_16) == 1000
    assert store.record(db, 1600, OCT_16 + 300) == 1600
    assert store.record(db, 1600, OCT_16 + 600) == 1600         # nothing new
    assert store.record(db, 400, OCT_16 + 900) == 2000          # coturn restarted
    assert store.last_reading(db) == OCT_16 + 900


def test_a_new_month_starts_from_what_was_last_read(db):
    store.record(db, 5 * GB, NOV_1 - 300)
    assert store.record(db, 5 * GB + 100, NOV_1 + 60) == 100     # only what was sent since the last reading
    assert store.used(db, NOV_1 - 300) == 5 * GB                 # October keeps its own total
    assert store.used(db, NOV_1 + 60) == 100


def test_the_state_the_routes_read(db):
    allowance = 10 * GB
    assert store.call_state(db, OCT_16, allowance) == (rules.PAUSED, 0)     # never read: fail closed
    store.record(db, 9 * GB, OCT_16)
    assert store.call_state(db, OCT_16 + 60, allowance) == (rules.AUDIO_ONLY, 9 * GB)
    store.record(db, 11 * GB, OCT_16 + 300)
    assert store.call_state(db, OCT_16 + 360, allowance) == (rules.PAUSED, 11 * GB)
    assert store.call_state(db, OCT_16 + 360 + rules.STALE_SECONDS + 1, allowance)[0] == rules.PAUSED
