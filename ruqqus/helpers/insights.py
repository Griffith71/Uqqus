"""Post insights for Premium authors: the rules, without a database (the storage and the report are
helpers/insights_store.py, the pages routes/insights.py).

What is measured is only ever a count: how many times a post page was opened, and how many votes,
comments, bookmarks, forwards and reposts it got. Nothing names a viewer, a voter or a poll voter.
A "view" is a page open by someone other than the author, that is not a robot or a prefetch, and not a
repeat from the same viewer within half an hour."""
import hashlib
import re
import time

RANGES = (7, 28)
DEFAULT_DAYS = 7
SECONDS_PER_DAY = 86400

# the same viewer opening the same post again within this long is one view
DEBOUNCE_SECONDS = 30 * 60

# who is never counted: robots and link-preview fetchers say so in their user agent, and a request with no
# user agent at all is not a browser
_ROBOT = re.compile(
    r"bot\b|crawl|spider|slurp|scrape|fetch|preview|facebookexternalhit|embedly|whatsapp|"
    r"headless|phantom|python-requests|python-urllib|aiohttp|httpclient|okhttp|curl/|wget/|libwww|go-http-client|"
    r"java/|monitor|uptime|lighthouse|pingdom",
    re.I,
)


def day_of(timestamp):
    """The day number (days since the epoch, UTC) a timestamp falls on."""
    return int(timestamp) // SECONDS_PER_DAY


def day_start(day):
    return int(day) * SECONDS_PER_DAY


def parse_days(raw):
    """The `?days=` of the report: 7 or 28 (anything else is the default)."""
    value = str(raw if raw is not None else "").strip()
    return int(value) if value.isascii() and value.isdigit() and int(value) in RANGES else DEFAULT_DAYS


def window(days, today):
    """The `days` day numbers ending today, oldest first."""
    return [int(today) - days + 1 + i for i in range(days)]


def series(counts, days, today):
    """[(day, count), ...] for each day of the window, zero where there were none. `counts` maps a day number to
    a count (days outside the window are left out)."""
    return [(day, int(counts.get(day, 0))) for day in window(days, today)]


def bar_heights(values, floor=4):
    """Bar heights as whole percentages of the biggest value: 0 for nothing, never under `floor` for something (a
    single view must still be seen), 100 for the biggest."""
    top = max(values) if values else 0
    if top <= 0:
        return [0 for _ in values]
    return [0 if value <= 0 else max(floor, round(value * 100 / top)) for value in values]


def label(day):
    """A day number as "8 Oct" (UTC)."""
    stamp = time.gmtime(day_start(day))
    return f"{stamp.tm_mday} {time.strftime('%b', stamp)}"


def is_robot(user_agent):
    """A robot, a link-preview fetcher or something that is not a browser (no user agent at all)."""
    agent = (user_agent or "").strip()
    return not agent or bool(_ROBOT.search(agent))


def is_prefetch(headers):
    """A request the browser made ahead of the visitor opening the page."""
    purpose = f"{headers.get('Purpose', '')} {headers.get('Sec-Purpose', '')} {headers.get('X-Moz', '')}".lower()
    return "prefetch" in purpose or "prerender" in purpose


def viewer_key(user_id, address, user_agent, secret):
    """The key that tells "the same viewer" within DEBOUNCE_SECONDS: the account id, or for a visitor a salted
    hash of address and user agent. It lives only in Redis, for half an hour, and is never written to the
    database."""
    if user_id:
        return f"u{int(user_id)}"
    seed = f"{secret}|{address or ''}|{user_agent or ''}".encode("utf-8", "replace")
    return "a" + hashlib.sha256(seed).hexdigest()[:20]


def percent(part, whole):
    """A whole percentage, 0 when there is no whole."""
    return round(part * 100 / whole) if whole else 0


def net(ups, downs):
    return int(ups) - int(downs)
