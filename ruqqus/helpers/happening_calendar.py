"""The calendar tab of /happening: a month as a grid of days (routes/happening_pages.py, templates/happening.html).

The month, its weeks and its prev/next come from helpers/guild_events.py (imported, not changed: Monday first, the same
`?month=YYYY-MM` the guild calendar takes). The rows come from helpers/happening_box.merged_calendar: what the rules make
(helpers/calendar_rules.py) and what an admin or a source stored (`site_events`), with the Child level, the sensitive
rule and the word filter applied (`site_events.event_visible`).

The server puts every item on its UTC day; assets/js/happening_page.js moves an instant to the reader's own day and marks
the reader's today (a civil item, such as a holiday, is the same day for everyone and never moves). Without script the
grid is UTC and says so. A day shows at most CHIPS items and a "+N more" count; the page lists the month's items under
the grid, so nothing is only a chip. A span holds at most `site_event_store.CALENDAR_CAP` stored rows; the page says
when it was cut.
"""
from datetime import date, timedelta

from ruqqus.helpers import guild_events as grid
from ruqqus.helpers import happening_box as box
from ruqqus.helpers import site_events as ev

CHIPS = 3                # chips a day shows (the rest are counted)
CHIP_CAP = 12            # chips a day sends at all (the script may move some in from another day)


def _order(item):
    return (0 if item.get("major") else 1, -int(item.get("priority") or 0), ev.start_ts(item), str(item.get("title")))


def month(db, year, month_number, now, level=1, show_sensitive=False):
    """The model of one month: weeks of days, each day's chips, the month's list, prev/next, and the `spill`: instants just
    outside the grid (the day before and after) that a reader in another zone sees INSIDE it (the script places them)."""
    weeks = grid.month_grid(year, month_number)
    first, last = weeks[0][0], weeks[-1][-1]
    items, cut = box.merged_calendar(db, first - timedelta(days=1), last + timedelta(days=1))
    visible = [i for i in items if ev.event_visible(i, level, show_sensitive) and ev.effective_status(i, now) != "cancelled"]
    visible.sort(key=_order)

    rows = []
    for week in weeks:
        cells = []
        for day in week:
            here = [i for i in visible if ev.on_day(i, day)]
            cells.append({
                "date": day, "iso": day.isoformat(), "other": day.month != month_number,
                "chips": [_chip(i, now, day) for i in here[:CHIP_CAP]], "more": max(0, len(here) - CHIPS),
            })
        rows.append(cells)

    edge = (first - timedelta(days=1), last + timedelta(days=1))
    spill = [_chip(i, now, ev.utc_date(i["starts_utc"])) for i in visible
             if i.get("date_kind") == "instant" and ev.utc_date(i["starts_utc"]) in edge]

    in_month = [i for i in visible if _touches_month(i, year, month_number)]
    in_month.sort(key=lambda i: (ev.start_ts(i), _order(i)))
    prev_year, prev_month = grid.shift_month(year, month_number, -1)
    next_year, next_month = grid.shift_month(year, month_number, 1)
    return {
        "year": year, "month": month_number, "weeks": rows, "spill": spill, "today": ev.utc_date(now).isoformat(),
        "prev": f"{prev_year:04d}-{prev_month:02d}", "next": f"{next_year:04d}-{next_month:02d}",
        "list": [box.present(i, now) for i in in_month], "cut": cut,
    }


def _touches_month(item, year, month_number):
    nxt_year, nxt_month = grid.shift_month(year, month_number, 1)
    return ev.touches(item, ev.midnight(date(year, month_number, 1)), ev.midnight(date(nxt_year, nxt_month, 1)))


def _chip(item, now, day):
    """A chip for `day` (a UTC date). An instant that lasts several days is ONE thing that starts on one day: its later days
    are plain markers that stay where they are, and only the first carries the time the script moves."""
    p = box.present(item, now)
    p["short"] = p["line1"]
    if p["starts_utc"] is not None and ev.utc_date(p["starts_utc"]) != day:
        p["starts_utc"] = None
    return p
