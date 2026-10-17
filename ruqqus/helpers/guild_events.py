"""Guild events and their calendar (pure rules, no I/O; the SQL is helpers/guild_event_store.py, the routes
routes/guild_events.py).

As Facebook's and Discord's events: a guild's guildmasters (content permission) create, edit and cancel events; members
answer Going or Interested (one answer, changeable) and are reminded an hour before. **Counts only, never who**: no
page, JSON or feed lists who answered; a reader sees the counts and their own answer.

- **Times** come from the browser as Unix times (worked out from the reader's own clock, as a Space's start): from
  PAST_GRACE seconds ago up to AHEAD_DAYS ahead, at most MAX_DAYS long; no end means MAX_DEFAULT_HOURS.
- **Text**: a title up to TITLE_MAX and details (markdown, rendered and sanitized like a comment) up to DETAILS_MAX,
  both rated by the word filter. **Where**: an online link (checked like a profile's website) or a place in plain text.
- PER_MONTH events a guild in any 30 days. Cancelling keeps the page, marked Cancelled.
- **Who reads** is whoever can see the guild; the .ics feed exists for public guilds only (a feed handed to another app
  would carry a private guild's events out of it).
"""
import calendar
import re
import time

TITLE_MAX = 120
DETAILS_MAX = 5000
PLACE_MAX = 120
PAST_GRACE = 300
AHEAD_DAYS = 365
MAX_DAYS = 7
MAX_DEFAULT_HOURS = 1
PER_MONTH = 20
REMIND_BEFORE = 3600

GOING, INTERESTED = "going", "interested"
STATUSES = (GOING, INTERESTED)

_SPACE = re.compile(r"\s+")
_MONTH = re.compile(r"^(\d{4})-(\d{2})$")


class Refused(ValueError):
    """Shown to the member as it is."""


def _plain(value, limit, what):
    text = _SPACE.sub(" ", str(value or "")).strip()
    if len(text) > limit:
        raise Refused(f"{what} can be at most {limit} characters.")
    if any(c in text for c in "<>") or any(ord(c) < 32 for c in text):
        raise Refused(f"{what} is plain text.")
    return text


def clean_title(value):
    title = _plain(value, TITLE_MAX, "A title")
    if not title:
        raise Refused("Give the event a title.")
    return title


def clean_details(value):
    details = str(value or "").replace("\r\n", "\n").strip()
    if len(details) > DETAILS_MAX:
        raise Refused(f"Details can be at most {DETAILS_MAX:,} characters.")
    return details


def _int(value):
    value = str(value or "").strip()
    if not value or not value.isascii() or not value.lstrip("-").isdigit():
        return None
    return int(value)


def clean_times(start, end, now, *, keep_start=None):
    """(starts_utc, ends_utc). `keep_start`: an edited event's stored start, accepted again even if it has passed."""
    s, e = _int(start), _int(end)
    if s is None:
        raise Refused("Choose when the event starts.")
    if s != keep_start and s < now - PAST_GRACE:
        raise Refused("An event can't start in the past.")
    if s > now + AHEAD_DAYS * 86400:
        raise Refused(f"An event can start at most {AHEAD_DAYS} days ahead.")
    if e is None:
        e = s + MAX_DEFAULT_HOURS * 3600
    if e <= s:
        raise Refused("An event ends after it starts.")
    if e - s > MAX_DAYS * 86400:
        raise Refused(f"An event can last at most {MAX_DAYS} days.")
    return s, e


def clean_location(url, place):
    """(location_url, location_text): either, both or neither."""
    url = str(url or "").strip()
    if url:
        from ruqqus.helpers import profile_details
        try:
            url = profile_details.clean_website(url)
        except profile_details.Refused as e:
            raise Refused(str(e).replace("The website", "The link").replace("website", "link"))
    return url or "", _plain(place, PLACE_MAX, "A place")


def clean_status(value):
    """`going`, `interested`, or None (take the answer back)."""
    value = str(value or "").strip().lower()
    if value in ("", "none", "not_going"):
        return None
    if value not in STATUSES:
        raise Refused("Answer Going or Interested.")
    return value


def make_refusal(made_in_last_30_days):
    if made_in_last_30_days >= PER_MONTH:
        return f"A guild can make {PER_MONTH} events in 30 days. Try again later."
    return None


def rsvp_refusal(*, event, now, signed_in, suspended, bot, exiled):
    """Why this reader can't answer this event (None: they can). `event`: (cancelled_utc, ends_utc)."""
    if not signed_in:
        return "Sign in to answer."
    if suspended or bot or exiled:
        return "You can't answer this event."
    cancelled, ends = event
    if cancelled:
        return "This event was cancelled."
    if ends < now:
        return "This event is over."
    return None


def status_of(event, now):
    """`cancelled`, `over`, `live` or `upcoming`. `event`: (starts_utc, ends_utc, cancelled_utc)."""
    starts, ends, cancelled = event
    if cancelled:
        return "cancelled"
    if ends < now:
        return "over"
    if starts <= now:
        return "live"
    return "upcoming"


# --- the calendar -----------------------------------------------------------------------------------------------------

def parse_month(value, now):
    """(year, month) from `YYYY-MM`; anything else is the month of `now` (UTC)."""
    found = _MONTH.match(str(value or ""))
    if found:
        year, month = int(found.group(1)), int(found.group(2))
        if 1970 <= year <= 9998 and 1 <= month <= 12:
            return year, month
    t = time.gmtime(now)
    return t.tm_year, t.tm_mon


def shift_month(year, month, step):
    month += step
    while month > 12:
        year, month = year + 1, month - 12
    while month < 1:
        year, month = year - 1, month + 12
    return year, month


def month_grid(year, month):
    """The weeks of a month view, Monday first: a list of weeks, each 7 `date`s (days of the months around it too)."""
    return calendar.Calendar(firstweekday=0).monthdatescalendar(year, month)


def grid_span(weeks):
    """(first, last) Unix times the grid shows, a day wider each side: a reader's time zone can move an event a day."""
    first, last = weeks[0][0], weeks[-1][-1]
    start = calendar.timegm(first.timetuple()) - 86400
    end = calendar.timegm(last.timetuple()) + 2 * 86400
    return start, end


def day_key(utc):
    return time.strftime("%Y-%m-%d", time.gmtime(utc))


# --- iCalendar (RFC 5545) ---------------------------------------------------------------------------------------------

def ics_text(value):
    """A TEXT value: backslash, semicolon, comma escaped; newlines as \\n; no other control characters."""
    value = "".join(c for c in str(value or "") if c in "\n" or ord(c) >= 32)
    return (value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\n")
            .replace("\n", "\\n"))


def _fold(line):
    """Lines of at most 75 octets, continued with a space (never splitting a UTF-8 character)."""
    out, current = [], ""
    for ch in line:
        if len((current + ch).encode("utf-8")) > (75 if not out else 74):
            out.append(current)
            current = ch
        else:
            current += ch
    out.append(current)
    return "\r\n ".join(out)


def _stamp(utc):
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(utc))


def ics(events, *, host, name, now):
    """A calendar of `events`: dicts with id, title, details, starts_utc, ends_utc, url, location, cancelled."""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:-//{ics_text(host)}//guild events//EN", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", f"X-WR-CALNAME:{ics_text(name)}"]
    for e in events:
        lines += ["BEGIN:VEVENT", f"UID:guild-event-{int(e['id'])}@{ics_text(host)}", f"DTSTAMP:{_stamp(now)}",
                  f"DTSTART:{_stamp(e['starts_utc'])}", f"DTEND:{_stamp(e['ends_utc'])}",
                  f"SUMMARY:{ics_text(e['title'])}"]
        if e.get("details"):
            lines.append(f"DESCRIPTION:{ics_text(e['details'])}")
        if e.get("location"):
            lines.append(f"LOCATION:{ics_text(e['location'])}")
        if e.get("url"):
            lines.append(f"URL:{ics_text(e['url'])}")
        lines.append("STATUS:CANCELLED" if e.get("cancelled") else "STATUS:CONFIRMED")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
