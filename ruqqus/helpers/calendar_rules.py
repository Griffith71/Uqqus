"""The calendar's items that need no data source and no network (pure, no I/O except reading the committed data file).
`occurrences(first, last)` gives every item between two UTC dates; helpers/happening_box.py and routes/happening_pages.py
merge them with the rows of `site_events` (admin and connector items) and draw them the same way.

Two kinds of time, because a day is not the same instant everywhere (CLAUDE.md "What's happening"):
- **civil** items fall on a calendar day that is the same in every time zone: a fixed observance (8 March), a day that
  floats (the last Saturday of March), Easter (computed), and the festivals in the reviewed table
  (`helpers/calendar_data.json`: Lunar New Year, Eid, Diwali...), whose dates are the table's, flagged `tentative` when
  they depend on a moon sighting ("expected, may differ by a day").
- **instant** items happen at one moment: the equinoxes, the solstices and the moon's four principal phases (computed
  with `ephem`, accurate to a few seconds), and the eclipses in the table (greatest eclipse, UTC). The reader's own day
  is worked out in the browser (assets/js/happening.js), because the September 2026 equinox is 00:05 UTC on the 23rd and
  still the 22nd in the Americas.

Names are the English ones, marked in helpers/calendar_names.py and translated where they are drawn (msg_text). No
national holidays: the site has no country (the owner's choice, 2026-10-26); the `holidays` package is the planned
follow-up. Every item has the same shape as a `site_events` row, so one set of rules selects and sorts them all.
"""
import calendar
import json
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path

DATA_FILE = Path(__file__).with_name("calendar_data.json")
MAJOR_ECLIPSES = ("total_solar", "annular_solar", "total_lunar")
ASTRONOMY_YEARS = (1950, 2100)       # ephem is exact well beyond this; the site's tables are not asked for more

try:                                 # the unit tests of the rules run without them installed
    from dateutil.easter import EASTER_ORTHODOX, EASTER_WESTERN, easter
except ImportError:                  # pragma: no cover
    easter = None


def _utc(value):
    """Unix seconds of an `ephem.Date` (it is UTC), to the nearest minute as the published tables give it."""
    return int(round(value.datetime().replace(tzinfo=timezone.utc).timestamp() / 60.0)) * 60


def _item(key, kind, name, *, day=None, end_day=None, utc=None, major=False, tentative=False, link="", state=None):
    civil = day is not None
    return {
        "id": None, "key": key, "kind": kind, "title": name,
        "date_kind": "civil" if civil else "instant", "starts_date": day, "ends_date": end_day,
        "starts_utc": None if civil else utc, "ends_utc": None, "expires_utc": None, "show_from_utc": None,
        "status": "scheduled", "status_changed_utc": 0, "state": state or {}, "priority": 0, "featured": False,
        "tentative": tentative, "link_url": link, "source": "rules", "source_key": key, "hidden_utc": 0,
        "is_sensitive": False, "word_severity": 0, "major": major, "poll_post_id": None,
    }


@lru_cache(maxsize=1)
def data():
    """The committed tables (observances, festivals, eclipses, meteor showers)."""
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


def names():
    from ruqqus.helpers.calendar_names import NAMES
    return NAMES


def nth_weekday(year, month, weekday, nth):
    """The `nth` (1 to 4, or -1 for the last) given weekday (Monday 0) of a month, as a `date`."""
    weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(year, month)
    days = [d for week in weeks for d in week if d.month == month and d.weekday() == weekday]
    return days[nth - 1] if nth > 0 else days[nth]


def _fixed_day(year, month, day):
    try:
        return date(year, month, day)
    except ValueError:                # 29 February in a common year
        return None


def _observances(year):
    out = []
    for entry in data()["observances"]:
        rule = entry.get("rule", "fixed")
        if rule == "fixed":
            day = _fixed_day(year, entry["month"], entry["day"])
        elif rule == "nth_weekday":
            day = nth_weekday(year, entry["month"], entry["weekday"], entry["nth"])
        elif rule == "easter_western" and easter is not None:
            day = easter(year, EASTER_WESTERN)
        elif rule == "easter_orthodox" and easter is not None:
            day = easter(year, EASTER_ORTHODOX)
        else:
            day = None
        if day is not None:
            out.append(_item(f"{entry['key']}:{year}", "observance", names()[entry["key"]], day=day,
                             major=bool(entry.get("major")), link=entry.get("url", "")))
    return out


def _festivals(year):
    out = []
    for f in data()["festivals"]:
        start = date.fromisoformat(f["date"])
        if start.year != year:
            continue
        end = date.fromisoformat(f["end_date"]) if f.get("end_date") else None
        # no link: the review's source (source_url in the data file) is the record, not something to send readers to
        out.append(_item(f"{f['key']}:{year}", "holiday", names()[f["key"]], day=start, end_day=end,
                         major=bool(f.get("major")), tentative=bool(f.get("tentative")),
                         state={"evening_before": True} if f.get("evening_before") else None))
    return out


def _season_items(year):
    import ephem
    start = f"{year - 1}/12/31"
    return [
        _item(f"march-equinox:{year}", "astronomy", names()["march-equinox"], utc=_utc(ephem.next_vernal_equinox(start)), major=True),
        _item(f"june-solstice:{year}", "astronomy", names()["june-solstice"], utc=_utc(ephem.next_summer_solstice(start)), major=True),
        _item(f"september-equinox:{year}", "astronomy", names()["september-equinox"], utc=_utc(ephem.next_autumn_equinox(start)), major=True),
        _item(f"december-solstice:{year}", "astronomy", names()["december-solstice"], utc=_utc(ephem.next_winter_solstice(start)), major=True),
    ]


_PHASES = (("new-moon", "next_new_moon"), ("first-quarter-moon", "next_first_quarter_moon"),
           ("full-moon", "next_full_moon"), ("last-quarter-moon", "next_last_quarter_moon"))


def _moon_items(year):
    import ephem
    out = []
    for key, fn in _PHASES:
        cursor = ephem.Date(f"{year}/1/1") - 2          # a phase early on 1 January is found from the day before
        while True:
            when = getattr(ephem, fn)(cursor)
            if when.datetime().year > year:
                break
            if when.datetime().year == year:            # (one just before the new year is skipped, not the end of the search)
                out.append(_item(f"{key}:{_utc(when)}", "astronomy", names()[key], utc=_utc(when), major=(key == "full-moon")))
            cursor = ephem.Date(when + 1)
    return out


def _sky_table(year):
    """Eclipses and meteor showers from the committed table (greatest eclipse in UTC; a shower's peak night)."""
    out = []
    for e in data().get("eclipses", []):
        moment = datetime.fromisoformat(e["greatest_utc"].replace("Z", "+00:00"))
        if moment.year == year:
            out.append(_item(f"{e['key']}", "astronomy", names()[e["type"]], utc=int(moment.timestamp()),
                             major=e["type"] in MAJOR_ECLIPSES, link=e.get("source_url", "")))
    for m in data().get("meteor_showers", []):
        peak = date.fromisoformat(m["peak_date"])
        if peak.year == year:
            out.append(_item(f"{m['key']}:{year}", "astronomy", names()[m["key"]], day=peak, tentative=True,
                             link=m.get("source_url", "")))
    return out


@lru_cache(maxsize=64)
def _year(year):
    items = _observances(year) + _festivals(year) + _sky_table(year)
    if ASTRONOMY_YEARS[0] <= year <= ASTRONOMY_YEARS[1]:
        try:
            items += _season_items(year) + _moon_items(year)
        except ImportError:        # ephem is in requirements.txt; a machine without it still has every other item
            pass
    return tuple(items)


def occurrences(first, last):
    """Every rule-made item overlapping the UTC dates `first` to `last` inclusive, soonest first."""
    from ruqqus.helpers import site_events as ev
    low, high = ev.midnight(first), ev.midnight(last) + ev.DAY
    found = []
    for year in range(first.year - (1 if first.month == 1 else 0), last.year + (1 if last.month == 12 else 0) + 1):
        for item in _year(year):
            copy = dict(item)
            if ev.touches(copy, low, high):
                found.append(copy)
    found.sort(key=lambda i: (ev.start_ts(i), 0 if i.get("major") else 1, i["key"]))
    return found
