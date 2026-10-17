"""Who forwarded each post of a page, in three queries for the whole page (audit P-06).

The byline ("X forwarded Y's post"), the menus and the flair rule each asked `forwardrels` / `comment_forwardrels` once
per card, and every one of those loaded the relationship's posts and guild with it (`lazy="subquery"`): about four
`SELECT submissions` per card. `attach` reads only the columns it needs and fills the same per-object caches the
`@lazy` properties on Submission use (`is_forward_copy`, `forwarded_by`, `is_comment_forward`, `comment_forwarded_by`),
plus `_forwarder_id` for routes/guild_flair.py. A post loaded another way keeps working: its properties ask as before.
"""
from sqlalchemy import bindparam, text

_FORWARDS = text("SELECT forward_submission_id, forwarded_by_id FROM forwardrels "
                 "WHERE forward_submission_id IN :ids").bindparams(bindparam("ids", expanding=True))
_COMMENT_FORWARDS = text("SELECT promoted_submission_id, promoted_by_id FROM comment_forwardrels "
                         "WHERE promoted_submission_id IN :ids").bindparams(bindparam("ids", expanding=True))


def _users(db, ids):
    from ruqqus.classes.user import User
    return {u.id: u for u in db.query(User).filter(User.id.in_(ids)).all()} if ids else {}


def attach(db, posts):
    """Fills the forwarder caches of `posts` (only guild copies can be forwards, so the rest are settled at once)."""
    posts = [p for p in posts if "_forwarder_id" not in p.__dict__]
    if not posts:
        return
    ids = [p.id for p in posts]
    forwards = {row[0]: row[1] for row in db.execute(_FORWARDS, {"ids": ids})}
    comment_forwards = {row[0]: row[1] for row in db.execute(_COMMENT_FORWARDS, {"ids": ids})}

    wanted = set()
    for p in posts:
        for table in (forwards, comment_forwards):
            by = table.get(p.id)
            if by is not None and by != p.author_id:
                wanted.add(by)
    users = _users(db, wanted)

    for p in posts:
        cache = p.__dict__.setdefault("_lazy", {})
        by = forwards.get(p.id)
        cache["is_forward_copy"] = p.id in forwards
        cache["forwarded_by"] = users.get(by) if by is not None and by != p.author_id else None
        comment_by = comment_forwards.get(p.id)
        cache["is_comment_forward"] = p.id in comment_forwards
        cache["comment_forwarded_by"] = (users.get(comment_by)
                                         if comment_by is not None and comment_by != p.author_id else None)
        p.__dict__["_forwarder_id"] = by
