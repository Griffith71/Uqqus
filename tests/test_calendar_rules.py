"""The calendar's rule-made items (helpers/calendar_rules.py, helpers/calendar_data.json, helpers/calendar_names.py).
The promises: an equinox, a solstice or a moon phase is an INSTANT (the September 2026 equinox is the 22nd for half the
world), a holiday is a civil DATE; Easter is computed, not typed; every name in the data has a marked English name and no
name uses a word the terminology rule bans; the reviewed festival table covers the years it says it does."""
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("ephem")
pytest.importorskip("dateutil")

from ruqqus.helpers import calendar_names, calendar_rules as rules
from ruqqus.helpers import site_events as ev

ROOT = Path(__file__).resolve().parent.parent


def at(utc):
    return datetime.fromtimestamp(utc, tz=timezone.utc)


def one(items, prefix):
    found = [i for i in items if i["key"].startswith(prefix)]
    assert len(found) == 1, (prefix, [i["key"] for i in found])
    return found[0]


def test_seasons_are_instants_at_the_right_minute():
    items = rules.occurrences(date(2026, 1, 1), date(2026, 12, 31))
    expected = {"march-equinox": (3, 20, 14, 46), "june-solstice": (6, 21, 8, 25),
                "september-equinox": (9, 23, 0, 5), "december-solstice": (12, 21, 20, 50)}
    for key, (month, day, hour, minute) in expected.items():
        item = one(items, key)
        assert item["date_kind"] == "instant" and item["starts_utc"] is not None and item["starts_date"] is None
        moment = at(item["starts_utc"])
        assert (moment.month, moment.day) == (month, day)
        assert abs((moment.hour * 60 + moment.minute) - (hour * 60 + minute)) <= 2, (key, moment)


def test_the_september_equinox_is_the_22nd_in_the_americas():
    """The reason astronomy is not stored as dates: 00:05 UTC on the 23rd is still the 22nd west of Greenwich."""
    item = one(rules.occurrences(date(2026, 9, 20), date(2026, 9, 25)), "september-equinox")
    assert at(item["starts_utc"]).isoformat().startswith("2026-09-23T00:05")
    assert at(item["starts_utc"] - 5 * 3600).date() == date(2026, 9, 22)


def test_moon_phases_match_known_instants():
    feb = rules.occurrences(date(2026, 2, 1), date(2026, 2, 28))
    new = one(feb, "new-moon")
    assert at(new["starts_utc"]).strftime("%Y-%m-%d %H") == "2026-02-17 12"                 # the annular eclipse's new moon
    jan = rules.occurrences(date(2026, 1, 1), date(2026, 1, 10))
    full = one(jan, "full-moon")
    assert abs(full["starts_utc"] - int(datetime(2026, 1, 3, 10, 3, tzinfo=timezone.utc).timestamp())) <= 300
    # four phases a month, about twenty-nine and a half days apart
    year = [i for i in rules.occurrences(date(2026, 1, 1), date(2026, 12, 31)) if i["key"].startswith("full-moon")]
    assert len(year) in (12, 13)
    gaps = [(b["starts_utc"] - a["starts_utc"]) / 86400 for a, b in zip(year, year[1:])]
    assert all(28.9 < g < 30.1 for g in gaps)


def test_no_moon_phase_goes_missing_in_any_year():
    """A phase in the last hours of 31 December used to end the search for the whole year the next one came in."""
    names = ("new-moon", "first-quarter-moon", "full-moon", "last-quarter-moon")
    for year in list(range(1995, 2060)) + [1970, 1972, 1991, 2067, 2090]:
        found = rules.occurrences(date(year, 1, 1), date(year, 12, 31))
        for name in names:
            count = sum(1 for i in found if i["key"].startswith(name + ":") and at(i["starts_utc"]).year == year)
            assert count in (12, 13), (year, name, count)
    full_2029 = [i for i in rules.occurrences(date(2029, 1, 1), date(2029, 12, 31)) if i["key"].startswith("full-moon")]
    assert len(full_2029) == 12
    last_night = [i for i in rules.occurrences(date(2028, 12, 31), date(2028, 12, 31)) if i["key"].startswith("full-moon")]
    assert last_night and at(last_night[0]["starts_utc"]).strftime("%H:%M") == "16:48"      # the 2028 lunar eclipse's full moon


def test_instants_are_to_the_nearest_minute_as_the_published_tables_give_them():
    items = rules.occurrences(date(2026, 1, 1), date(2026, 12, 31))
    def minute(key):
        return at(one(items, key)["starts_utc"]).strftime("%m-%d %H:%M")
    assert minute("march-equinox") == "03-20 14:46" and minute("december-solstice") == "12-21 20:50"
    assert all(i["starts_utc"] % 60 == 0 for i in items if i["date_kind"] == "instant")


def test_easter_is_computed_for_both_churches():
    expected = {2026: ((4, 5), (4, 12)), 2027: ((3, 28), (5, 2)), 2028: ((4, 16), (4, 16)), 2029: ((4, 1), (4, 8))}
    for year, (west, east) in expected.items():
        items = rules.occurrences(date(year, 3, 1), date(year, 5, 31))
        assert one(items, f"easter:{year}")["starts_date"] == date(year, *west)
        assert one(items, f"orthodox-easter:{year}")["starts_date"] == date(year, *east)


def test_an_observance_is_a_civil_date_and_a_festival_can_span_years():
    womens = one(rules.occurrences(date(2027, 3, 1), date(2027, 3, 31)), "womens-day")
    assert womens["date_kind"] == "civil" and womens["starts_date"] == date(2027, 3, 8) and womens["major"]
    assert womens["starts_utc"] is None and womens["source"] == "rules" and womens["kind"] == "observance"
    # Hanukkah 2027 runs 25 December to 1 January: it is in a query for the new year's day, from the year before's table
    jan = rules.occurrences(date(2028, 1, 1), date(2028, 1, 1))
    hanukkah = one(jan, "hanukkah")
    assert hanukkah["starts_date"] == date(2027, 12, 25) and hanukkah["ends_date"] == date(2028, 1, 1)
    assert hanukkah["state"] == {"evening_before": True} and not hanukkah["tentative"]
    assert one(rules.occurrences(date(2027, 3, 9), date(2027, 3, 9)), "eid-al-fitr")["tentative"]       # sighting-based


def test_occurrences_are_exact_at_the_edges_and_sorted():
    items = rules.occurrences(date(2026, 12, 24), date(2026, 12, 26))
    keys = [i["key"] for i in items]
    assert "christmas-eve:2026" in keys and "christmas-day:2026" in keys and "new-years-eve:2026" not in keys
    assert items == sorted(items, key=lambda i: (ev.start_ts(i), 0 if i.get("major") else 1, i["key"]))
    assert rules.occurrences(date(2026, 6, 10), date(2026, 6, 9)) == []
    # nothing leaks in from the next or the previous day
    for item in rules.occurrences(date(2026, 3, 20), date(2026, 3, 20)):
        assert ev.on_day(item, date(2026, 3, 20))


def test_every_item_has_the_shape_the_event_rules_read():
    for item in rules.occurrences(date(2026, 1, 1), date(2026, 12, 31)):
        assert item["kind"] in ev.KINDS and item["source"] == "rules" and item["hidden_utc"] == 0
        assert item["date_kind"] in ev.DATE_KINDS
        assert ev.effective_status(item, 1_793_000_000) in ev.STATUSES
        assert ev.event_visible(item, 2)                                 # no rule-made item is hidden from a child


def test_the_names_and_the_data_agree():
    data = rules.data()
    wanted = {o["key"] for o in data["observances"]} | {f["key"] for f in data["festivals"]} \
        | {e["type"] for e in data["eclipses"]} | {m["key"] for m in data["meteor_showers"]} \
        | {"march-equinox", "june-solstice", "september-equinox", "december-solstice",
           "new-moon", "first-quarter-moon", "full-moon", "last-quarter-moon"}
    assert wanted == set(calendar_names.NAMES), (wanted ^ set(calendar_names.NAMES))
    banned = re.compile(r"promot|\brepl(y|ies|ied)\b|crosspost|yank", re.I)       # tests/test_terminology.py's words
    assert not [n for n in calendar_names.NAMES.values() if banned.search(n)]
    assert len(set(calendar_names.NAMES.values())) == len(calendar_names.NAMES)    # two keys never share a name


def test_the_data_is_reviewed_and_covers_the_years_it_says():
    data = rules.data()
    assert data["reviewed_on"] >= "2026-10-26"
    # a fixed date, not the wall clock: when the horizon runs out this fails on the day someone extends it, not on a
    # stranger's unrelated commit
    horizon = {}
    for f in data["festivals"]:
        horizon.setdefault(f["key"], set()).add(int(f["date"][:4]))
        assert f["source_url"].startswith("https://")
    for key, years in horizon.items():
        assert years >= {2026, 2027, 2028}, (key, years)
    for e in data["eclipses"]:
        assert e["source_url"].startswith("https://") and e["greatest_utc"].endswith("Z")
    assert {int(m["peak_date"][:4]) for m in data["meteor_showers"]} == {2026, 2027, 2028}
    for o in data["observances"]:
        assert o["rule"] in ("fixed", "easter_western", "easter_orthodox")
        if o["rule"] == "fixed":
            date(2024, o["month"], o["day"])                              # a real day (2024: a leap year)


def test_nth_weekday_rule_works():
    assert rules.nth_weekday(2026, 3, 5, -1) == date(2026, 3, 28)        # the last Saturday of March 2026
    assert rules.nth_weekday(2026, 11, 3, 4) == date(2026, 11, 26)       # the 4th Thursday of November 2026


def test_the_astronomy_is_left_alone_far_from_the_years_ephem_serves():
    assert not [i for i in rules.occurrences(date(2300, 1, 1), date(2300, 12, 31)) if i["kind"] == "astronomy"]
    assert time.gmtime(0).tm_year == 1970
