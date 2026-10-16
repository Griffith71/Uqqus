"""Which posts of a page are live threads (the LIVE mark on a card, the panel on the post page): attached in one query
by `get_posts` (`attach`), or looked up on first use (`live_of`). A guild copy shows its primary's live thread."""
import time

from ruqqus.helpers.base36 import base36encode
from ruqqus.helpers import live_thread_store as store
from ruqqus.helpers import live_threads as rules


def attach(db, posts):
    posts = list(posts)
    if not posts:
        return
    primary = {p.id: (p.repost_id or p.id) for p in posts}
    rows = store.threads_for(db, set(primary.values()))
    now = int(time.time())
    for p in posts:
        row = rows.get(primary[p.id])
        p.live_data = None if row is None else {
            "live": rules.is_live(row[0], row[1], row[2], now), "started_utc": row[0], "ended_utc": row[1],
            "last_update_utc": row[2], "post_id": primary[p.id], "post36": base36encode(primary[p.id]),
        }


def live_of(p):
    from flask import g
    data = p.__dict__.get("live_data", False)
    if data is False:
        attach(g.db, [p])
        data = p.__dict__.get("live_data")
    return data
