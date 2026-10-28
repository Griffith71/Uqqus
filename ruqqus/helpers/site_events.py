"""The site's "What's happening" events: the rules (pure, no I/O). The SQL is helpers/site_event_store.py, the box
helpers/happening_box.py, the calendar's rule-made items helpers/calendar_rules.py, the pages routes/happening_pages.py.

An event is one row of `site_events` (migration 2026-12-03_site_events.sql) of one of these kinds:

    score, poll, prediction, countdown, election, alert, news, event   shown in the Live section while they matter
    holiday, observance, astronomy                                      calendar items (most are made by calendar_rules)

and of ONE of two kinds of time (`date_kind`): a **civil** date (a holiday is the same day in every time zone:
starts_date / ends_date, inclusive) or an **instant** (a kick-off, an equinox: starts_utc / ends_utc, Unix seconds).

- **Link-out only** for alerts, news and elections: the row must carry an https link to the source, an alert always
  expires, and nothing here rewrites what a source said. KIND_POLICY is the one table of those rules; the migration's
  CHECKs repeat the hard ones (a link on https, an alert with an expiry).
- **Status is worked out when read** from the times (effective_status); a stored `final`, `cancelled`, `ended` or
  `postponed` wins, a stored `live` keeps a row live until its end plus LIVE_GRACE (an admin forgetting to finish a
  match does not leave it live for ever).
- **Counts and people**: nothing in an event names a member. A poll or prediction only points at an ordinary poll
  post (`poll_post_id`); its results follow helpers/polls.py (voters, the author, or everyone after it closes).
- Admin text is English and untranslated (CLAUDE.md "Stays English"); what a reader sees is drawn by the templates.
"""
import json
import re
import unicodedata
from collections import namedtuple
from datetime import date, datetime, timedelta, timezone

from ruqqus.helpers import permalinks
from ruqqus.helpers.base36 import base36decode, base36encode
from ruqqus.helpers.wordfilter import is_hidden

KINDS = ("score", "poll", "prediction", "countdown", "election", "alert", "news", "event",
         "holiday", "observance", "astronomy")
STATUSES = ("scheduled", "live", "final", "postponed", "cancelled", "ended")
TERMINAL = ("final", "cancelled", "ended", "postponed")
DATE_KINDS = ("civil", "instant")

MAX_POST_ID = 2**31 - 1
MAX_YEAR = 2200                      # the last year an admin may enter and a calendar page may show
TITLE_MAX = 200
DETAILS_MAX = 600
LINK_MAX = 300
STATE_MAX = 600
SOON = 6 * 3600                      # an instant event is in the Live section from this long before it starts
SOON_CIVIL_DAYS = 2                  # a whole-day event from this many days before
FINAL_SHOW = 24 * 3600               # a finished match, or a postponed one, stays this long
OUTCOME_SHOW = 48 * 3600             # a resolved prediction
LIVE_GRACE = 6 * 3600                # a stored `live` past its end
ALERT_MAX_SECONDS = 7 * 86400
DAY = 86400

Policy = namedtuple("Policy", "link_required expires_required default_expiry default_seconds child_hidden live calendar "
                              "needs_poll")
#                 link   expires default_expiry  default_seconds child  live   calendar needs_poll
KIND_POLICY = {
    "score":       Policy(False, False, None,       3 * 3600,     False, True,  True,    False),
    "poll":        Policy(False, False, None,       0,            False, True,  False,   True),
    "prediction":  Policy(False, False, None,       0,            False, True,  False,   True),
    "countdown":   Policy(False, False, None,       3600,         False, True,  True,    False),
    "election":    Policy(True,  False, None,       3600,         True,  True,  True,    False),
    "alert":       Policy(True,  True,  DAY,        0,            True,  True,  False,   False),
    "news":        Policy(True,  False, DAY,        0,            True,  True,  False,   False),
    "event":       Policy(False, False, None,       3600,         False, True,  True,    False),
    "holiday":     Policy(False, False, None,       0,            False, False, True,    False),
    "observance":  Policy(False, False, None,       0,            False, False, True,    False),
    "astronomy":   Policy(False, False, None,       0,            False, False, True,    False),
}
LIVE_KINDS = tuple(k for k in KINDS if KIND_POLICY[k].live)
CALENDAR_KINDS = tuple(k for k in KINDS if KIND_POLICY[k].calendar)

ELECTION_STATUSES = ("upcoming", "polls_open", "results")
SEVERITIES = ("minor", "moderate", "severe", "extreme")

_SPACE = re.compile(r"\s+")
_WHEN = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{1,2}):(\d{2}))?\s*(?:UTC|Z)?\s*$", re.I)


class Refused(ValueError):
    """Shown to the admin as it is."""


# --- small helpers -----------------------------------------------------------------------------------------------------

def as_date(value):
    """A `date` from what a database hands back (a date, or ISO text in SQLite), or None."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def utc_date(timestamp):
    return datetime.fromtimestamp(int(timestamp), tz=timezone.utc).date()


def midnight(day):
    """Unix time of 00:00 UTC on a `date`."""
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())


def state_of(event):
    """The `state` JSON of a row as a dict ({} when it is not an object)."""
    raw = event.get("state")
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


_BIDI = set("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")      # a title read in another order than it was written


def _plain(value, limit, what):
    text = _SPACE.sub(" ", str(value or "")).strip()
    if len(text) > limit:
        raise Refused(f"{what} can be at most {limit} characters.")
    if any(c in text for c in "<>") or any(unicodedata.category(c) in ("Cc", "Zl", "Zp") or c in _BIDI for c in text):
        raise Refused(f"{what} is plain text.")
    return text


def clean_link(value, required=False):
    """An https address, or "". A missing scheme means https; http is refused (the table's CHECK says the same)."""
    raw = str(value or "").strip()
    if not raw:
        if required:
            raise Refused("A link to the source is required for this kind (https).")
        return ""
    if raw.lower().startswith("http://"):
        raise Refused("The link must use https.")
    from ruqqus.helpers import profile_details
    try:
        cleaned = profile_details.clean_website(raw, link=True)
    except profile_details.Refused as e:
        raise Refused(str(e))
    if not cleaned.startswith("https://") or len(cleaned) > LINK_MAX:
        raise Refused("The link must use https and be at most %d characters." % LINK_MAX)
    return cleaned


def parse_when(value, civil=False):
    """Unix seconds (or a `date` for a civil event) from `YYYY-MM-DD HH:MM` (UTC) or `YYYY-MM-DD`; None for empty."""
    raw = str(value or "").strip()
    if not raw:
        return None
    match = _WHEN.match(raw)
    if not match:
        raise Refused(f"Write the time as YYYY-MM-DD HH:MM in UTC (got {raw[:40]!r}).")
    year, month, day, hour, minute = match.groups()
    if not 1970 <= int(year) <= MAX_YEAR:
        raise Refused(f"The year must be between 1970 and {MAX_YEAR}.")
    try:
        if civil:
            return date(int(year), int(month), int(day))
        moment = datetime(int(year), int(month), int(day), int(hour or 0), int(minute or 0), tzinfo=timezone.utc)
    except ValueError:
        raise Refused(f"{raw[:40]!r} is not a real date.")
    return int(moment.timestamp())


def poll_post_id(value):
    """The id of the poll post an admin pasted: a post address of this site, or its base-36 id."""
    raw = str(value or "").strip()
    if not raw:
        return None
    found = permalinks.parse(raw)
    if found and found[0] == "post":
        post = base36decode(found[1])
    elif re.fullmatch(r"[0-9a-z]{1,10}", raw.lower()):
        post = base36decode(raw.lower())
    else:
        raise Refused("Paste the address of the poll post.")
    if not 1 <= post <= MAX_POST_ID:                       # the column is a 32-bit integer
        raise Refused("That is not the address of a post on this site.")
    return post


def _int(value, low, high, what):
    raw = str(value if value is not None else "").strip()
    if raw == "":
        return None
    if not re.fullmatch(r"-?\d{1,6}", raw) or not low <= int(raw) <= high:
        raise Refused(f"{what} must be a whole number from {low} to {high}.")
    return int(raw)


def clean_state(kind, form):
    """The kind's `state` from the admin form, only its own keys, each bounded.

    A key is in the answer only when its field was SUBMITTED, so the quick edit that posts the goals and the clock never
    touches the team names. A submitted but empty field answers None: "clear this" (`site_event_store.set_state` removes
    the key, `clean_event` leaves it out)."""
    out = {}

    def text(key, limit, what):
        name = f"state_{key}"
        if name in form:
            out[key] = _plain(form.get(name), limit, what) or None

    def number(key):
        name = f"state_{key}"
        if name in form:
            out[key] = _int(form.get(name), 0, 999, "A score")

    if kind == "score":
        text("home", 40, "The home team")
        text("away", 40, "The away team")
        number("home_score")
        number("away_score")
        text("period", 20, "The period")
        text("clock", 10, "The clock")
    elif kind == "election":
        if "state_status" in form:
            status = str(form.get("state_status") or "upcoming").strip()
            if status not in ELECTION_STATUSES:
                raise Refused("Election status is upcoming, polls_open or results.")
            out["status"] = status
        text("source", 80, "The source name")
    elif kind == "prediction":
        text("outcome", 80, "The outcome")
    elif kind == "alert":
        if "state_severity" in form:
            severity = str(form.get("state_severity") or "").strip().lower()
            if severity and severity not in SEVERITIES:
                raise Refused("Alert severity is minor, moderate, severe or extreme.")
            out["severity"] = severity or None
        text("source", 80, "The source name")
    elif kind == "news":
        text("source", 80, "The source name")
    if len(json.dumps({k: v for k, v in out.items() if v is not None}, sort_keys=True, separators=(",", ":"))) > STATE_MAX:
        raise Refused("That is too much detail.")
    return out


def clean_event(form, now):
    """The fields to store from an admin's form (a dict of strings), or Refused. Never trusts `kind`'s needs: the
    policy table decides what is required, defaults an expiry and bounds an alert's life."""
    kind = str(form.get("kind") or "").strip()
    if kind not in KINDS:
        raise Refused("Choose a kind.")
    policy = KIND_POLICY[kind]
    title = _plain(form.get("title"), TITLE_MAX, "A title")
    if not title:
        raise Refused("A title is required.")
    details = _plain(form.get("details"), DETAILS_MAX, "The details")
    link = clean_link(form.get("link_url"), policy.link_required)

    civil = (form.get("date_kind") or ("civil" if kind in ("holiday", "observance") else "instant")) == "civil"
    fields = {"kind": kind, "title": title, "details": details, "link_url": link,
              "title_lang": _plain(form.get("title_lang"), 12, "The language"),
              "date_kind": "civil" if civil else "instant",
              "starts_date": None, "ends_date": None, "starts_utc": None, "ends_utc": None,
              "expires_utc": None, "show_from_utc": None}
    if civil:
        start, end = parse_when(form.get("starts"), civil=True), parse_when(form.get("ends"), civil=True)
        if start is None:
            raise Refused("A start date is required.")
        if end is not None and end < start:
            raise Refused("The end can't be before the start.")
        fields["starts_date"], fields["ends_date"] = start, end
    else:
        start, end = parse_when(form.get("starts")), parse_when(form.get("ends"))
        if start is None:
            raise Refused("A start time is required (UTC).")
        if end is not None and end < start:
            raise Refused("The end can't be before the start.")
        fields["starts_utc"], fields["ends_utc"] = start, end

    expires = parse_when(form.get("expires"))
    if expires is None and policy.default_expiry:
        base = start if not civil else midnight(start)
        expires = max(int(now), base) + policy.default_expiry
    if policy.expires_required and expires is None:
        raise Refused("An alert needs an expiry.")
    if kind == "alert" and expires is not None and expires - int(now) > ALERT_MAX_SECONDS:
        raise Refused("An alert can't last longer than 7 days.")
    fields["expires_utc"] = expires
    fields["show_from_utc"] = parse_when(form.get("show_from"))

    status = str(form.get("status") or "scheduled").strip()
    if status not in STATUSES:
        raise Refused("Choose a status.")
    fields["status"] = status

    if policy.needs_poll and fields["ends_utc"] is None:
        raise Refused("A poll or a prediction needs the time it closes (Ends).")
    post_id = poll_post_id(form.get("poll_post")) if policy.needs_poll else None
    if policy.needs_poll and post_id is None:
        raise Refused("Paste the address of the poll post this points at.")
    fields["poll_post_id"] = post_id

    priority = _int(form.get("priority"), -10, 10, "Priority")
    fields["priority"] = priority or 0
    fields["featured"] = str(form.get("featured") or "") in ("1", "on", "true", "yes")
    fields["tentative"] = str(form.get("tentative") or "") in ("1", "on", "true", "yes")
    fields["is_sensitive"] = str(form.get("is_sensitive") or "") in ("1", "on", "true", "yes")
    fields["state"] = {k: v for k, v in clean_state(kind, form).items() if v is not None}
    return fields


# --- what a row is at a moment -----------------------------------------------------------------------------------------

def start_ts(event):
    """The moment an event starts (midnight UTC of its first day for a civil one)."""
    if event.get("date_kind") == "civil":
        return midnight(as_date(event["starts_date"]))
    return int(event["starts_utc"])


def end_ts(event):
    """The moment it ends; with no end, the kind's default length after the start (an alert or a headline: its expiry).
    A civil event ends after its last day."""
    if event.get("date_kind") == "civil":
        last = as_date(event.get("ends_date")) or as_date(event["starts_date"])
        return midnight(last) + DAY
    start = int(event["starts_utc"])
    if event.get("ends_utc") is not None:
        return int(event["ends_utc"])
    policy = KIND_POLICY[event["kind"]]
    if (policy.expires_required or policy.default_expiry) and event.get("expires_utc") is not None:
        return max(start, int(event["expires_utc"]))          # an alert or a headline is live until it expires
    return start + policy.default_seconds


# a calendar day is on the clock somewhere from 14 hours before UTC midnight (Kiritimati, UTC+14) to 12 hours after it ends
# (Baker Island, UTC-12): a whole-day row is "on" for that long, not for a UTC day (the US midterms are still going on in
# California when UTC has moved on to the next day)
CIVIL_EARLY = 14 * 3600
CIVIL_LATE = 12 * 3600


def live_span(event):
    """(from, to) Unix times between which a row counts as under way (instants: their own start and end)."""
    if event.get("date_kind") == "civil":
        return start_ts(event) - CIVIL_EARLY, end_ts(event) + CIVIL_LATE
    return start_ts(event), end_ts(event)


def effective_status(event, now):
    """`scheduled`, `live`, `final`, `postponed`, `cancelled` or `ended` at `now`."""
    now = int(now)
    stored = event.get("status") or "scheduled"
    if stored in TERMINAL:
        return stored
    expires = event.get("expires_utc")
    if expires is not None and now >= int(expires):
        return "ended"
    start, end = live_span(event)
    if now < start:
        return "live" if stored == "live" and event.get("date_kind") != "civil" else "scheduled"
    if now < end:
        return "live"
    if stored == "live" and now < end + LIVE_GRACE:
        return "live"
    return "ended"


_RANK = {"live": 0, "scheduled": 1, "final": 2, "postponed": 3}


def event_visible(event, level=1, show_sensitive=False):
    """Whether a viewer at word-filter `level` may see this row at all: not hidden by an admin, not a kind the Child
    level leaves out (alerts, news, elections), not sensitive unless they chose to see it (never at Child: the All
    rule, helpers/sensitive.py), and its text not rated past their level (helpers/wordfilter.is_hidden)."""
    policy = KIND_POLICY.get(event.get("kind"))
    if policy is None or event.get("hidden_utc"):
        return False
    if level >= 2 and policy.child_hidden:
        return False
    if event.get("is_sensitive") and (level >= 2 or not show_sensitive):
        return False
    return not is_hidden(level, event.get("word_severity") or 0)


def in_live_section(event, now, level=1, show_sensitive=False):
    """Whether the Live section shows this row now (status, the soon window, `show_from_utc`, visibility)."""
    policy = KIND_POLICY.get(event.get("kind"))
    if policy is None or not policy.live or not event_visible(event, level, show_sensitive):
        return False
    show_from = event.get("show_from_utc")
    if show_from is not None and int(now) < int(show_from):
        return False
    status = effective_status(event, now)
    if status in ("ended", "cancelled"):
        return False
    if status == "postponed":                       # for a day after it was said, not until the original date
        return int(now) <= (int(event.get("status_changed_utc") or 0) or end_ts(event)) + FINAL_SHOW
    if status == "final":
        anchor = max(int(event.get("status_changed_utc") or 0), end_ts(event))
        return int(now) <= anchor + (OUTCOME_SHOW if event["kind"] == "prediction" else FINAL_SHOW)
    if status == "live":
        return True
    # scheduled: shown early when the admin set a date to start showing it, or when it is about to start
    if show_from is not None:
        return True
    if event["kind"] in ("poll", "prediction"):
        return False
    soon = SOON_CIVIL_DAYS * DAY if event.get("date_kind") == "civil" else SOON
    return live_span(event)[0] - int(now) <= soon


PINNED = 5          # a priority this high, or a row marked featured, goes above everything; below it priority only orders one status


def live_rows(events, now, level=1, show_sensitive=False):
    """The rows for the Live section, best first: pinned (priority 5 or more), then live > soon > finished, then priority,
    then the nearest start. What is happening now outranks a far-off countdown unless an admin pinned the countdown."""
    chosen = [e for e in events if in_live_section(e, now, level, show_sensitive)]
    chosen.sort(key=lambda e: (0 if int(e.get("priority") or 0) >= PINNED or e.get("featured") else 1,
                               _RANK.get(effective_status(e, now), 9), -int(e.get("priority") or 0), start_ts(e),
                               int(e.get("id") or 0)))
    return chosen


def touches(event, first, last):
    """Whether an event overlaps the half-open span [first, last) of Unix times (an instant with no length counts)."""
    start, end = start_ts(event), end_ts(event)
    return start < last and (end > first or (end == start and start >= first))


def window_rows(events, now, level=1, show_sensitive=False, days_before=1, days_after=2):
    """Calendar rows touching the days from `days_before` before UTC today to `days_after` after it (inclusive): the
    box's Today section sends this window to every reader and the script keeps the rows on the reader's own today."""
    today = utc_date(now)
    first = midnight(today - timedelta(days=days_before))
    last = midnight(today + timedelta(days=days_after + 1))
    out = []
    for e in events:
        policy = KIND_POLICY.get(e.get("kind"))
        if policy is None or not policy.calendar or not event_visible(e, level, show_sensitive):
            continue
        if effective_status(e, now) == "cancelled":
            continue
        if touches(e, first, last):
            out.append(e)
    out.sort(key=lambda e: (start_ts(e), -int(e.get("priority") or 0), 0 if e.get("major") else 1, str(e.get("title"))))
    return out


def on_day(event, day):
    """Whether an event covers a UTC `date` (a month grid cell)."""
    return touches(event, midnight(day), midnight(day) + DAY)


def form_values(event):
    """A stored row as the admin form's strings, for editing it (the same names `clean_event` reads)."""
    def when(ts):
        return "" if ts is None else datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")

    civil = event.get("date_kind") == "civil"
    out = {
        "kind": event["kind"], "title": event["title"], "details": event.get("details") or "",
        "link_url": event.get("link_url") or "", "title_lang": event.get("title_lang") or "",
        "date_kind": event.get("date_kind") or "instant",
        "starts": (as_date(event.get("starts_date")).isoformat() if civil else when(event.get("starts_utc"))),
        "ends": ((as_date(event.get("ends_date")).isoformat() if event.get("ends_date") else "") if civil else when(event.get("ends_utc"))),
        "expires": when(event.get("expires_utc")), "show_from": when(event.get("show_from_utc")),
        "status": event.get("status") or "scheduled",
        "poll_post": base36encode(event["poll_post_id"]) if event.get("poll_post_id") else "",
        "priority": str(event.get("priority") or 0),
        "featured": "1" if event.get("featured") else "", "tentative": "1" if event.get("tentative") else "",
        "is_sensitive": "1" if event.get("is_sensitive") else "",
    }
    for key, value in state_of(event).items():
        out[f"state_{key}"] = str(value)
    return out
