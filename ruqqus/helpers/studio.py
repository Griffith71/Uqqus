"""Creator Studio (routes/studio.py): what a Premium member earns and how their posts are doing, in one place. The rules,
with no I/O; the counting is helpers/studio_store.py.

* **Only counts.** Coins and numbers of people, never who: no tipper, subscriber, viewer or voter is named here.
* **Never tied to ads.** AdSense forbids passing its earnings on, or paying for traffic, without Google's written
  permission (support.google.com/adsense/answer/1348725), so nothing in the studio reads ad figures (helpers/ads.py)
  and it says nothing about ad money. The ways to earn are tips, paid circles and the creator fund.
* **Premium only**, checked with `has_premium_no_renew` (the `has_premium` property spends a coin to renew).
"""
WINDOW_DAYS = 30
DAY = 24 * 60 * 60

# where coins come from: (key, what the page calls it, what it is)
SOURCES = (
    ("tips", "Tips", "Coins people tipped on your posts and comments"),
    ("circles", "Paid circles", "Subscriptions to your paid circles and Circle guilds you founded"),
    ("fund", "Creator fund", "Monthly creator fund payouts"),
)


def summary(recent, total):
    """(rows, recent total, all-time total) for the earnings table: one row per source, in SOURCES order."""
    rows = [{"key": key, "label": label, "hint": hint, "recent": int(recent.get(key, 0) or 0), "total": int(total.get(key, 0) or 0)}
            for key, label, hint in SOURCES]
    return rows, sum(r["recent"] for r in rows), sum(r["total"] for r in rows)


def coins(n):
    """"1 coin", "1,234 coins"."""
    n = int(n or 0)
    return f"{n:,} coin" + ("" if n == 1 else "s")
