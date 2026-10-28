"""What the "What's happening" box and page draw (routes/happening_pages.py, templates/partials/happening_box.html).

`present(event, now)` turns a `site_events` row, or a calendar rule's item (helpers/calendar_rules.py, the same shape), into
what a template needs: translated lines, a badge, a link, and the times the script keeps current. `box(...)` picks the
rows of the three sections:

    Trending   topics, or busy posts when none trend (routes/happening_pages.py hands the topics in)
    Live       live, starting-soon and open items, at most ROWS, "+N more" for the rest
    Today      a window of calendar rows around UTC today. EVERY reader gets the same window; the script
               (assets/js/happening.js) keeps the rows that fall on the reader's own today, so the box never needs the
               reader's time zone (nothing about it travels or is logged).

Nothing here names a member. `signature(model)` is a short number for the parts that matter (not the "in 5 hours"
text, which the script keeps ticking): the poll sends it back and gets 204 when nothing changed.
"""
import json
import zlib
from datetime import timedelta

from ruqqus.helpers import calendar_rules
from ruqqus.helpers import site_event_store as store
from ruqqus.helpers import site_events as ev
from ruqqus.helpers.base36 import base36encode
from ruqqus.helpers.i18n_runtime import date_text, datetime_text, msg, tr, trn

ROWS = 3                   # rows a section shows (the box keeps the height of three)
TODAY_WINDOW_ROWS = 12     # rows of the window sent for the script to choose from
POLL_SECONDS = 300         # how often an idle box asks again; the server may say otherwise (data-next)
POLL_LIVE_SECONDS = 60     # while something is live


def until_phrase(seconds):
    """"in 40 minutes", "in 5 hours", "in 3 days" (the script writes the same words, assets/js/time_phrases.js)."""
    seconds = int(seconds)
    if seconds < 60:
        return tr("now")
    if seconds < 120 * 60:
        return trn("in %(num)s minute", "in %(num)s minutes", seconds // 60)
    if seconds < 48 * 3600:
        return trn("in %(num)s hour", "in %(num)s hours", seconds // 3600)
    return trn("in %(num)s day", "in %(num)s days", seconds // 86400)


def _day_text(day):
    return date_text(ev.midnight(day), "%a %d %b", "EEE d MMM")


def _range_text(first, last):
    """"Sun 8 Nov" or "Mon 9 Nov - Fri 20 Nov" for a civil event."""
    first, last = ev.as_date(first), ev.as_date(last)
    if first is None:
        return ""
    if last is None or last == first:
        return _day_text(first)
    return f"{_day_text(first)} \u2013 {_day_text(last)}"


def _iso(value):
    d = ev.as_date(value)
    return d.isoformat() if d else ""


def _title(event):
    """Rule-made names are marked in helpers/calendar_names.py and drawn in the reader's language; everything else
    (an admin's or a source's words) is shown as written."""
    if event.get("source") == "rules":
        return msg(event["title"])
    return event["title"]


def _score_line(event, status, state):
    home, away = state.get("home", ""), state.get("away", "")
    if not (home and away):
        return event["title"], ""
    if status in ("live", "final") and "home_score" in state and "away_score" in state:
        main = f"{home} {state['home_score']}–{state['away_score']} {away}"
    else:
        main = f"{home} v {away}"
    extra = [state.get(k) for k in ("period", "clock") if state.get(k)]
    return main, " · ".join([event["title"]] + extra)


def present(event, now):
    """One row for a template. Keys: id, key, kind, status, badge, line1, line2 (fixed text), until_utc / until_text (a
    countdown the script keeps ticking), at_text (an instant in UTC, which the script shows in the reader's zone),
    link, external, starts_utc / ends_utc / date / end_date (for the script), tentative, major."""
    now = int(now)
    state = ev.state_of(event)
    status = ev.effective_status(event, now)
    kind = event["kind"]
    start, civil = ev.start_ts(event), event.get("date_kind") == "civil"
    until = start - now if start > now else None
    link, external = event.get("link_url") or "", bool(event.get("link_url"))
    line1, line2, badge = _title(event), "", ""
    tick = False                                   # whether `until` is shown as a countdown

    if kind == "score":
        line1, line2 = _score_line(event, status, state)
        badge = {"live": tr("Live"), "final": tr("Final"), "postponed": tr("Postponed")}.get(status, tr("Soon") if until else "")
        tick = status == "scheduled"
    elif kind in ("poll", "prediction"):
        post = event.get("poll_post_id")
        link, external = (f"/post/{base36encode(post)}" if post else ""), False
        closes = ev.end_ts(event) - now
        if kind == "prediction" and status == "final" and state.get("outcome"):
            badge, line2 = tr("Result"), tr("Result: %(outcome)s", outcome=state["outcome"])
        elif kind == "prediction":
            badge = tr("Prediction")
            line2 = tr("Prediction \u00b7 closes %(when)s", when=until_phrase(closes)) if closes > 0 else tr("Closed")
        else:
            badge = tr("Poll")
            line2 = tr("Vote \u00b7 closes %(when)s", when=until_phrase(closes)) if closes > 0 else tr("Closed")
    elif kind == "election":
        badge = tr("Election")
        phase = state.get("status", "upcoming")
        if phase == "results" or status == "final":
            line2 = tr("Results: see %(source)s", source=state.get("source") or tr("the source"))
        elif phase == "polls_open" or status == "live":
            line2 = tr("Polls open")
        else:
            tick = True
    elif kind == "alert":
        badge = tr("Alert")
        line2 = " \u00b7 ".join(x for x in (state.get("source"), tr("Automatic, not an official warning")) if x)
    elif kind == "news":
        badge = tr("News")
        line2 = state.get("source") or ""
    elif kind in ("countdown", "event"):
        if status == "live":
            badge = tr("Live")                       # a whole-day row under way: its days are the second line (below)
        else:
            tick = True
    if civil and kind in ("countdown", "event") and status == "live":
        line2 = _range_text(event.get("starts_date"), event.get("ends_date"))
    if state.get("evening_before"):
        line2 = line2 or tr("Begins the evening before")
    if event.get("tentative"):
        badge = badge or tr("Expected")
    # holiday, observance, astronomy: the title, and when

    # the time of day (in UTC; the script writes it in the reader's zone) only where it tells something: a coming match or an
    # equinox, not a match already under way
    at_text = when_text = ""
    if civil:
        when_text = _range_text(event.get("starts_date"), event.get("ends_date"))
    elif event.get("starts_utc") is not None:
        when_text = datetime_text(event["starts_utc"], "%a %d %b %H:%M UTC", "EEE d MMM HH:mm 'UTC'")
        if status == "scheduled" or kind == "astronomy":
            at_text = datetime_text(event["starts_utc"], "%H:%M UTC", "HH:mm 'UTC'")
    return {
        "id": event.get("id"), "key": event.get("key") or f"event-{event.get('id')}", "kind": kind, "status": status,
        "badge": badge, "line1": line1, "line2": line2, "link": link, "external": external,
        "until_utc": start if tick and until else None, "until_text": until_phrase(until) if tick and until else "",
        "at_text": at_text, "when_text": when_text,
        "starts_utc": None if civil else event.get("starts_utc"), "ends_utc": None if civil else event.get("ends_utc"),
        "date": _iso(event.get("starts_date")), "end_date": _iso(event.get("ends_date")),
        "tentative": bool(event.get("tentative")), "major": bool(event.get("major")),
        "source": state.get("source", ""), "outcome": state.get("outcome", ""),
        "civil": civil, "alert": kind == "alert",
        # what a reader WITHOUT script sees as today: the UTC day (the script then picks the reader's own)
        "today_utc": ev.on_day(event, ev.utc_date(now)),
        # words an admin or a source wrote carry their language and read in their own direction; ours do neither
        "foreign": event.get("source") != "rules", "lang": (event.get("title_lang") or "") if event.get("source") != "rules" else "",
    }


def merged_calendar(db, first, last):
    """(rows, cut): stored rows and rule-made items touching the UTC dates `first` to `last` inclusive."""
    stored, cut = store.calendar_rows(db, ev.midnight(first), ev.midnight(last) + ev.DAY)
    return calendar_rules.occurrences(first, last) + stored, cut


def box(db, v_level, show_sensitive, now, topics, hot, rows=ROWS):
    """The model of the box: `topics` (Trending rows for the viewer), `hot` (their busy posts when no topic trends)."""
    today = ev.utc_date(now)
    live_all = ev.live_rows(store.live_candidates(db, now), now, v_level, show_sensitive)
    live = [present(e, now) for e in live_all[:rows]]

    items, _ = merged_calendar(db, today - timedelta(days=1), today + timedelta(days=2))
    # what the Live section already shows is not repeated under Today
    in_live = {r["key"] for r in live}
    window = [e for e in ev.window_rows(items, now, v_level, show_sensitive)
              if (e.get("key") or f"event-{e.get('id')}") not in in_live]
    # major first within the window so the script's three rows are the ones people know
    window.sort(key=lambda e: (0 if e.get("major") else 1, ev.start_ts(e)))
    today_rows = [present(e, now) for e in window[:TODAY_WINDOW_ROWS]]

    model = {
        "topics": list(topics)[:rows],
        "hot": [] if topics else list(hot)[:rows],
        "live": live, "live_more": max(0, len(live_all) - rows),
        "live_on": any(r["status"] == "live" for r in live),         # the Live heading gets its dot
        "today": today_rows, "day": today.isoformat(),
        # a match or a headline changes within the minute; a conference under way, a day-long event does not
        "next": POLL_LIVE_SECONDS if any(r["status"] == "live" and not r["civil"] for r in live) else POLL_SECONDS,
    }
    model["rev"] = signature(model)
    return model


def signature(model):
    """A short number for what is in the box (not the ticking text): ids, statuses, scores, the UTC day."""
    parts = {
        "t": [getattr(t, "id", None) for t in model["topics"]],
        "h": [getattr(p, "id", None) for p in model["hot"]],
        # (a poll's or prediction's "closes in 90 minutes" is the clock moving, not a change: it is left out)
        "l": [(r["id"], r["key"], r["status"], r["line1"], "" if r["kind"] in ("poll", "prediction") and r["status"] == "live" else r["line2"],
               r["badge"]) for r in model["live"]],
        "m": model["live_more"],
        "d": model["day"],
        "y": [(r["key"], r["status"], r["line1"]) for r in model["today"]],
    }
    return zlib.crc32(json.dumps(parts, sort_keys=True, default=str).encode("utf-8")) & 0x7FFFFFFF
