"""Settings > Security's list of recent sign-ins (helpers/sign_in_history.py) on SQLite, and the retention rules for the
rows it reads (helpers/retention.py)."""
import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import retention, sign_in_history

ROOT = Path(__file__).resolve().parent.parent
NOW = 2_000_000_000
DAY = 86400
NAMES = {"GB": "United Kingdom", "DE": "Germany"}


@pytest.fixture
def db():
    session = Session(create_engine("sqlite://"))
    session.execute(text("CREATE TABLE login_events (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, "
                         "created_utc INTEGER, ip TEXT, cf_country TEXT, region_code TEXT, user_agent TEXT)"))
    return session


def add(db, user, when, country="GB", agent="Mozilla/5.0 (Windows NT 10.0) Chrome/126.0 Safari/537.36", ip="203.0.113.5"):
    db.execute(text("INSERT INTO login_events (user_id, created_utc, ip, cf_country, user_agent) VALUES (:u, :t, :ip, :c, :a)"),
               {"u": user, "t": when, "ip": ip, "c": country, "a": agent})


def test_it_lists_only_the_accounts_own_sign_ins_newest_first_and_at_most_ten(db):
    for i in range(12):
        add(db, 1, NOW - i * 1000)
    add(db, 2, NOW + 5, country="DE")                                   # someone else's
    rows = sign_in_history.recent(db, 1)
    assert len(rows) == 10
    assert [r[0] for r in rows] == sorted((r[0] for r in rows), reverse=True)
    assert all(r[1] == "GB" for r in rows)


def test_the_page_gets_words_not_addresses(db):
    add(db, 1, NOW, country="DE", agent="Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0")
    add(db, 1, NOW - 100, country="T1", agent=None)
    add(db, 1, NOW - 200, country=None)
    add(db, 1, NOW - 300, country="ZZ")
    shown = sign_in_history.entries(sign_in_history.recent(db, 1), NAMES)
    assert [(e["place"], e["browser"]) for e in shown] == [
        ("Germany", "Firefox on Linux"), ("Tor network", "Browser not recorded"),
        ("Unknown location", "Chrome on Windows"), ("Unknown location", "Chrome on Windows")]
    assert shown[0]["iso"].endswith("Z") and "UTC" in shown[0]["when"]
    assert "203.0.113.5" not in repr(shown), "the address must never reach the page"


def test_the_query_never_selects_the_address():
    source = (ROOT / "ruqqus/helpers/sign_in_history.py").read_text(encoding="utf-8")
    sql = re.search(r'text\("SELECT (.*?) FROM login_events', source, re.S).group(1)
    assert "ip" not in [c.strip() for c in sql.split(",")]


def test_the_address_and_browser_go_after_90_days_but_the_country_stays_for_the_region_logic(db):
    add(db, 1, NOW - 91 * DAY)
    add(db, 1, NOW - 89 * DAY)
    add(db, 1, NOW - 411 * DAY)
    retention.prune(db, NOW)
    rows = db.execute(text("SELECT created_utc, ip, cf_country, user_agent FROM login_events ORDER BY created_utc")).all()
    assert len(rows) == 2, "a row older than 410 days is gone"
    old, new = rows
    assert old[1] is None and old[3] is None and old[2] == "GB", "past 90 days: no address, no browser, country kept"
    assert new[1] == "203.0.113.5" and new[3] is not None


def test_login_events_are_kept_long_enough_for_the_region_logic():
    """helpers/regions.py reads LOOKBACK_DAYS of country and region to decide whether an account has moved: pruning
    earlier would stop regions ever migrating."""
    regions = (ROOT / "ruqqus/helpers/regions.py").read_text(encoding="utf-8")
    lookback = int(re.search(r"^LOOKBACK_DAYS = (\d+)", regions, re.M).group(1))
    assert retention.LOGIN_EVENT_DAYS > lookback
    limits = (ROOT / "ruqqus/helpers/guild_limits.py").read_text(encoding="utf-8")
    assert "WINDOW = 90 * 24 * 60 * 60" in limits and retention.LOGIN_ADDRESS_DAYS == 90
