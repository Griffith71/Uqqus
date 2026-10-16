"""What a post's card says about its Space (partials/space_card.html, the Jinja global `space_of`): attached to a
page of posts in one query by `get_posts` (`attach`), or looked up on first use. A guild copy shows its primary
post's Space. The number of people in it now is read from Redis (helpers/call_store.py); never who they are.
"""
import time

from ruqqus.helpers import call_store, space_store
from ruqqus.helpers import spaces as rules


def _redis():
    from ruqqus.__main__ import r
    return r


def attach(db, posts, v):
    posts = list(posts)
    if not posts:
        return
    primary = {p.id: (p.repost_id or p.id) for p in posts}
    rows = space_store.for_posts(db, set(primary.values()))
    r = _redis()
    now = int(time.time())
    for p in posts:
        row = rows.get(primary[p.id])
        if row is None:
            p.space_data = None
            continue
        sid, _post_id, host_id, status, scheduled_utc = row[0], row[1], row[2], row[3], row[4]
        people = 0
        if status == rules.LIVE and r is not None:
            people = len({e["user_id"] for e in call_store.roster(r, space_store.room(sid), now)})
        p.space_data = {
            "id": sid, "status": status, "label": rules.state_label(status, scheduled_utc, now),
            "scheduled_utc": scheduled_utc, "record": bool(row[7]), "people": people, "peak": row[9],
            "is_host": v is not None and v.id == host_id,
            "reminded": v is not None and status == rules.SCHEDULED and space_store.reminded(db, sid, v.id),
        }


def space_of(p, v):
    from flask import g
    data = p.__dict__.get("space_data", False)
    if data is False:
        attach(g.db, [p], v)
        data = p.__dict__.get("space_data")
    return data
