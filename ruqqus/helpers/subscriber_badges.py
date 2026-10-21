"""The Subscriber badge (the owner's choice, 2026-10-20, as on X): next to the name of someone who pays for a person's
paid circle, on that person's profile and in the comments under that person's posts.

A badge says exactly what one row of the person's Subscribers list says, so it follows the same two rules
(helpers/follow_lists.py): the person's own setting for who may open their Subscribers list, and the subscriber's own
setting for who may see the circles they pay for (`subscriptions_visibility`, "Circles you subscribe to"). Blocks
either way and names the viewer's word filter hides show no badge either.

* **Paid circles only.** A custom circle is never revealed: nobody is told they were put in one.
* **Never on an anonymous item, and never under an anonymous post**: the badge names whom the subscriber pays, so on
  an anonymous post it would name the post's author, and on an anonymous comment it would hint at its writer.

`badges` is the SQL (tested on SQLite); `attach` sets each item's badge for a page in one query per owner, and the
templates read it with `subscriber_badge(item)`.
"""
import time

from sqlalchemy import bindparam, text

from ruqqus.helpers import follow_lists


def _statement():
    return text(f"""
        SELECT o.id, MIN(c.name) FROM circle_members m JOIN circles c ON c.id = m.circle_id JOIN users o ON o.id = m.user_id
        WHERE c.user_id = :owner AND c.kind = 'paid' AND m.tier = 'subscriber' AND m.status = 'active'
          AND m.renews_utc > :now AND o.id IN :ids{follow_lists._LIVE}{follow_lists._shown("subscribers")}
        GROUP BY o.id""").bindparams(bindparam("ids", expanding=True))


def badges(db, owner_id, author_ids, viewer_id, cut, now, owner_setting, viewer_follows_owner):
    """{author id: the name of an owner's paid circle they subscribe to} that this viewer may be shown. `cut` is the
    lowest name severity the viewer's word filter hides (None: Off); `owner_setting` is the owner's
    `subscribers_visibility`."""
    is_owner = viewer_id is not None and viewer_id == owner_id
    if not follow_lists.may_open(owner_setting, is_owner=is_owner, follows_owner=viewer_follows_owner):
        return {}
    ids = sorted({int(i) for i in author_ids if i and int(i) != owner_id})
    if not ids or not owner_id:
        return {}
    params = follow_lists._params(owner_id, viewer_id, cut, now)
    params["ids"] = ids
    return {row[0]: row[1] for row in db.execute(_statement(), params).fetchall()}


def attach(owner, items, v):
    """Set `_subscriber_badge` on each item (post or comment) written by someone subscribed to `owner`'s paid circles,
    as this viewer may be shown it. Anonymous items are left out before anything is asked."""
    from flask import g
    from ruqqus.helpers.visibility import _cut, viewer_level

    items = [x for x in items if x is not None]
    for item in items:
        item.__dict__["_subscriber_badge"] = None
    if owner is None or not items:
        return
    named = [x for x in items if not x.is_anonymous and x.author_id and x.author_id != owner.id]
    if not named:
        return
    level = viewer_level(v)
    found = badges(g.db, owner.id, [x.author_id for x in named], v.id if v else None,
                   _cut(level) if level > 0 else None, int(time.time()),
                   getattr(owner, follow_lists.SETTING["subscribers"], follow_lists.ONLY_ME),
                   follow_lists.follows(g.db, v.id if v else None, owner.id))
    if not found:
        return
    for item in named:
        name = found.get(item.author_id)
        if name:
            item.__dict__["_subscriber_badge"] = {"circle": name, "owner": owner.username}


def badge_of(item):
    """For templates: {circle, owner} or None."""
    return getattr(item, "__dict__", {}).get("_subscriber_badge")
