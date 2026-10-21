"""Guild analytics for guildmasters: the rules (no I/O; the SQL is `guild_insights_store.py`).

Modelled on post insights and the Creator Studio: **counts only, never who**. A guildmaster sees how many people
joined and left, how many posts, comments and upvotes the guild got, how often its page was opened, and what its
moderators did, for the last 7 or 28 days. Nothing names a member, a poster, a voter or a viewer; there is no "top
contributors" (it would single out an anonymous author); nothing is split by person.

**Small guilds show window totals only.** A day-by-day chart of joins and leaves in a guild of ten members would point
at one person, so the per-day figures are drawn only for a guild of at least `MIN_CHART_MEMBERS`.

**The numbers start the day this shipped.** The database never recorded when someone left a guild, nor who looked at a
guild's page, and a re-join keeps the original join date, so growth cannot be rebuilt from history. The page says so.
"""
import time

WINDOWS = (7, 28)
DEFAULT_DAYS = 28
MIN_CHART_MEMBERS = 25
KEEP_DAYS = 400                 # helpers/retention.py
DEBOUNCE_SECONDS = 1800         # the same viewer opening the page again within half an hour counts once
DAY = 86400

STARTED_NOTE = ("These counts start from the day guild insights were switched on: before that the site did not record "
                "when people left a guild or how often its page was opened.")
PRIVACY_NOTE = ("Only counts are shown, never who. Day-by-day figures appear once a guild has "
                f"{MIN_CHART_MEMBERS} members, so a small guild's numbers can't point at one person.")


def parse_days(raw):
    """7 or 28; anything else is the default."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_DAYS
    return value if value in WINDOWS else DEFAULT_DAYS


def day_of(seconds):
    """The day number (days since 1970) of a Unix time."""
    return int(seconds) // DAY


def bounds(now, days):
    """(first second, one past the last second) of the window: the last `days` whole days and today."""
    today = day_of(now)
    return (today - days + 1) * DAY, (today + 1) * DAY


def chart_allowed(members):
    return int(members or 0) >= MIN_CHART_MEMBERS


def day_label(day):
    return time.strftime("%d %b", time.gmtime(day * DAY)).lstrip("0")


def fill_days(rows, now, days):
    """[{day, label, joins, leaves, views, posts, comments}] for every day of the window, oldest first, zero where
    nothing was counted. `rows` maps a day number to a dict of the counts of that day."""
    today = day_of(now)
    out = []
    for day in range(today - days + 1, today + 1):
        counts = rows.get(day, {})
        out.append({"day": day, "label": day_label(day), **{k: int(counts.get(k, 0) or 0) for k in
                    ("joins", "leaves", "views", "posts", "comments")}})
    return out


def net(joined, left):
    return int(joined or 0) - int(left or 0)


def signed(n):
    return f"+{n}" if n > 0 else str(n)


def may_read(mod):
    """Whether a guildmaster's permissions allow reading the insights: configuration or content, or full."""
    return bool(mod and (getattr(mod, "perm_full", False) or getattr(mod, "perm_config", False)
                         or getattr(mod, "perm_content", False)))
