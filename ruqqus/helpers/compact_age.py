"""A short age for a row of the notifications page ("now", "5m", "2h", "3d"), as x.com writes it. Pure; the Jinja global that
words and translates it is `compact_age` in helpers/jinja2.py (tests/test_compact_age.py)."""

MINUTE, HOUR, DAY = 60, 3600, 86400
WEEK = 7 * DAY


def parts(seconds):
    """(unit, number) for an age in seconds: "now" under a minute, then minutes ("m"), hours ("h") and days ("d") up to a
    week; None from a week on, where a date reads better. A negative age (a clock a little ahead) is "now"."""
    seconds = max(0, int(seconds))
    if seconds < MINUTE:
        return "now", 0
    if seconds < HOUR:
        return "m", seconds // MINUTE
    if seconds < DAY:
        return "h", seconds // HOUR
    if seconds < WEEK:
        return "d", seconds // DAY
    return None
