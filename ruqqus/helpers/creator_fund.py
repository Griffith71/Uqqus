"""The creator fund: the rules (no I/O). The database side is helpers/creator_fund_store.py, the pages
routes/creator_fund.py.

Each month the site sets aside a budget of coins and shares it among the members whose posts and comments other
people valued most. The budget is a number an admin chooses for each month. It is **never** worked out from what
ads earn, and the fund **never** counts views or clicks: Google AdSense forbids passing its earnings on, or paying for
traffic, without its written permission (support.google.com/adsense/answer/1348725). Nothing in the fund may read
helpers/ads.py.

A member's points for a month: one point for each (account, item) pair, where the account upvoted the member's post or
comment in that month, or commented on the member's post in that month. Only established accounts count (at least
ACCOUNT_AGE_DAYS old, not banned or deleted, not the member), only public, live items, and one account gives one
member at most POINTS_PER_ACCOUNT points a month, so a few friends cannot carry anyone. Members who opted in and reach
MIN_POINTS share the budget by points; nobody gets more than MAX_SHARE of it.

Earnings are private: a member sees their own, admins see the payout list, and nothing lists earners publicly. An
anonymous post or comment earns for its author like any other, and nothing shown anywhere ties the two together.
"""
import calendar
import os
import re
from datetime import datetime, timezone

ACCOUNT_AGE_DAYS = 30
POINTS_PER_ACCOUNT = 20
MAX_SHARE = 0.25
MAX_BUDGET = 1000000
DAY = 86400

MONTH = re.compile(r"(\d{4})-(\d{2})")


def min_points(environ=None):
    """Points a member needs to share a month's budget (CREATOR_FUND_MIN_POINTS, default 50, never under 10)."""
    environ = os.environ if environ is None else environ
    raw = (environ.get("CREATOR_FUND_MIN_POINTS") or "").strip()
    value = int(raw) if raw.isascii() and raw.isdigit() else 50
    return max(value, 10)


def parse_month(text):
    """"YYYY-MM" or None."""
    m = MONTH.fullmatch((text or "").strip())
    if not m or not 1 <= int(m.group(2)) <= 12 or int(m.group(1)) < 2000:
        return None
    return m.group(0)


def month_bounds(month):
    """(first second, first second of the next month) of a "YYYY-MM" month, in UTC."""
    year, mon = int(month[:4]), int(month[5:])
    start = int(datetime(year, mon, 1, tzinfo=timezone.utc).timestamp())
    return start, start + calendar.monthrange(year, mon)[1] * DAY


def month_of(utc):
    return datetime.fromtimestamp(int(utc), tz=timezone.utc).strftime("%Y-%m")


def last_month(now):
    """The month before the one `now` is in."""
    start, _ = month_bounds(month_of(now))
    return month_of(start - 1)


def is_over(month, now):
    return month_bounds(month)[1] <= int(now)


def month_name(month):
    start, _ = month_bounds(month)
    return datetime.fromtimestamp(start, tz=timezone.utc).strftime("%B %Y")


def parse_budget(text):
    """A whole number of coins from 1 to MAX_BUDGET, or None."""
    raw = (text or "").strip()
    if not (raw.isascii() and raw.isdigit()):
        return None
    value = int(raw)
    return value if 1 <= value <= MAX_BUDGET else None


def established_before(end):
    """An account counts for a month if it was made at least ACCOUNT_AGE_DAYS before the month ended."""
    return end - ACCOUNT_AGE_DAYS * DAY


def split(budget, points, minimum):
    """{member: coins} for a budget shared by `points` ({member: points}). Members under `minimum` get nothing;
    nobody gets more than MAX_SHARE of the budget; shares are rounded down and a share under 1 coin is dropped.
    What is left over is not paid."""
    qualifying = {who: p for who, p in points.items() if p >= minimum}
    total = sum(qualifying.values())
    if budget <= 0 or total <= 0:
        return {}
    cap = int(budget * MAX_SHARE)
    coins = {}
    for who, p in qualifying.items():
        share = min(budget * p // total, cap)
        if share >= 1:
            coins[who] = int(share)
    return coins


def flags(points, engagers, networks):
    """Reasons for an admin to look closer before paying (never shown to the member)."""
    out = []
    if engagers < 10:
        out.append(f"points from only {engagers} accounts")
    if engagers and networks and networks * 3 <= engagers:
        out.append(f"{engagers} accounts on {networks} networks")
    if engagers and points / engagers >= POINTS_PER_ACCOUNT * 0.75:
        out.append("most accounts gave close to the most points one account can")
    return out
