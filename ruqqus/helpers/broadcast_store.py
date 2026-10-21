"""Broadcast channels in the database (rules in helpers/broadcasts.py): `broadcasts`, `broadcast_circles`,
`broadcast_members`, `broadcast_posts` (a public channel's messages, kept for the word filter, the daily count and
reports; a channel for circles keeps none: the site can't read it) and `broadcast_reports`.

Plain `text()` SQL with bound values only, tested on SQLite (`tests/test_broadcasts.py`). Nothing here commits.
"""

from sqlalchemy import bindparam, text

from ruqqus.helpers import broadcasts as rules

_CHANNEL = "id, owner_id, name, name_severity, audience, matrix_room_id, created_utc, deleted_utc"


def create(db, owner_id, name, severity, audience, room_id, circle_ids, now):
    db.execute(text("""INSERT INTO broadcasts (owner_id, name, name_severity, audience, matrix_room_id, created_utc, deleted_utc)
                       VALUES (:o, :name, :sev, :aud, :room, :now, 0)"""),
               {"o": owner_id, "name": name, "sev": severity, "aud": audience, "room": room_id, "now": now})
    bid = db.execute(text("SELECT id FROM broadcasts WHERE matrix_room_id = :room"), {"room": room_id}).scalar()
    for cid in circle_ids:
        db.execute(text("INSERT INTO broadcast_circles (broadcast_id, circle_id) VALUES (:b, :c)"), {"b": bid, "c": cid})
    add_member(db, bid, owner_id, rules.OWNER, now)
    return bid


def get(db, bid):
    return db.execute(text(f"SELECT {_CHANNEL} FROM broadcasts WHERE id = :b AND deleted_utc = 0"), {"b": bid}).fetchone()


def by_room(db, room_id):
    return db.execute(text(f"SELECT {_CHANNEL} FROM broadcasts WHERE matrix_room_id = :r AND deleted_utc = 0"),
                      {"r": room_id}).fetchone()


def owned_count(db, owner_id):
    return db.execute(text("SELECT COUNT(*) FROM broadcasts WHERE owner_id = :o AND deleted_utc = 0"), {"o": owner_id}).scalar() or 0


def of_owner(db, owner_id):
    return db.execute(text(f"SELECT {_CHANNEL} FROM broadcasts WHERE owner_id = :o AND deleted_utc = 0 ORDER BY id"),
                      {"o": owner_id}).fetchall()


def public_of_owners(db, owner_ids, viewer_id, limit=30):
    """Public channels of these accounts the viewer is not in yet, newest first (the Channels tab's "From people you
    follow"). The caller leaves out anyone in a block with the viewer."""
    ids = sorted({int(i) for i in owner_ids})
    if not ids:
        return []
    query = text(f"""SELECT {_CHANNEL} FROM broadcasts WHERE owner_id IN :ids AND audience = 0 AND deleted_utc = 0
                     AND id NOT IN (SELECT broadcast_id FROM broadcast_members WHERE user_id = :me)
                     ORDER BY created_utc DESC LIMIT :n""").bindparams(bindparam("ids", expanding=True))
    return db.execute(query, {"ids": ids, "me": viewer_id, "n": int(limit)}).fetchall()


def like_pattern(q):
    """`q` as a LIKE pattern (on LOWER of both sides) that matches it anywhere, its own wildcards taken literally."""
    escaped = str(q or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def search_public(db, q, viewer_id, limit=40):
    """Public channels whose name holds `q`, that the viewer is not in, newest first (the chat page's search). The
    caller leaves out an owner in a block with the viewer and a name their filter hides."""
    return db.execute(text(f"""SELECT {_CHANNEL} FROM broadcasts WHERE audience = 0 AND deleted_utc = 0
                               AND LOWER(name) LIKE LOWER(:q) ESCAPE '\\'
                               AND id NOT IN (SELECT broadcast_id FROM broadcast_members WHERE user_id = :me)
                               ORDER BY created_utc DESC LIMIT :n"""),
                      {"q": like_pattern(q), "me": viewer_id, "n": int(limit)}).fetchall()


def circle_ids(db, bid):
    return tuple(row[0] for row in db.execute(text("SELECT circle_id FROM broadcast_circles WHERE broadcast_id = :b"), {"b": bid}))


def close(db, bid, now):
    db.execute(text("UPDATE broadcasts SET deleted_utc = :now WHERE id = :b"), {"b": bid, "now": now})


# --- members ----------------------------------------------------------------------------------------------------

def add_member(db, bid, user_id, role, now):
    db.execute(text("""INSERT INTO broadcast_members (broadcast_id, user_id, role, joined_utc, unread_count, last_read_utc)
                       VALUES (:b, :u, :role, :now, 0, :now) ON CONFLICT (broadcast_id, user_id) DO NOTHING"""),
               {"b": bid, "u": user_id, "role": role, "now": now})


def member(db, bid, user_id):
    """(role, unread_count, joined_utc) or None."""
    return db.execute(text("SELECT role, unread_count, joined_utc FROM broadcast_members WHERE broadcast_id = :b AND user_id = :u"),
                      {"b": bid, "u": user_id}).fetchone()


def remove_member(db, bid, user_id):
    db.execute(text("DELETE FROM broadcast_members WHERE broadcast_id = :b AND user_id = :u"), {"b": bid, "u": user_id})


def set_role(db, bid, user_id, role):
    db.execute(text("UPDATE broadcast_members SET role = :role WHERE broadcast_id = :b AND user_id = :u"),
               {"b": bid, "u": user_id, "role": role})


def size(db, bid):
    return db.execute(text("SELECT COUNT(*) FROM broadcast_members WHERE broadcast_id = :b"), {"b": bid}).scalar() or 0


def helpers(db, bid):
    return [row[0] for row in db.execute(text("SELECT user_id FROM broadcast_members WHERE broadcast_id = :b AND role = 'helper'"),
                                         {"b": bid})]


def member_ids(db, bid):
    return [row[0] for row in db.execute(text("SELECT user_id FROM broadcast_members WHERE broadcast_id = :b"), {"b": bid})]


def joined_by(db, user_id, limit=100):
    """The channels a member is in, the most recently written first: (channel row..., role, unread_count)."""
    return db.execute(text("""SELECT b.id, b.owner_id, b.name, b.name_severity, b.audience, b.matrix_room_id, b.created_utc,
                                     b.deleted_utc, m.role, m.unread_count,
                                     (SELECT MAX(p.created_utc) FROM broadcast_posts p WHERE p.broadcast_id = b.id) AS last_post
                              FROM broadcasts b JOIN broadcast_members m ON m.broadcast_id = b.id
                              WHERE m.user_id = :u AND b.deleted_utc = 0
                              ORDER BY COALESCE((SELECT MAX(p.created_utc) FROM broadcast_posts p WHERE p.broadcast_id = b.id),
                                                b.created_utc) DESC LIMIT :n"""), {"u": user_id, "n": limit}).fetchall()


def note_message(db, bid, sender_id, muted_owner_ids=()):
    """A new message: one more unread for every member but its writer (and anyone who muted the owner)."""
    if muted_owner_ids:
        query = text("""UPDATE broadcast_members SET unread_count = unread_count + 1
                        WHERE broadcast_id = :b AND user_id <> :u AND user_id NOT IN :muted""").bindparams(
            bindparam("muted", expanding=True))
        db.execute(query, {"b": bid, "u": sender_id, "muted": list(muted_owner_ids)})
    else:
        db.execute(text("UPDATE broadcast_members SET unread_count = unread_count + 1 WHERE broadcast_id = :b AND user_id <> :u"),
                   {"b": bid, "u": sender_id})


def mark_read(db, bid, user_id, now):
    db.execute(text("UPDATE broadcast_members SET unread_count = 0, last_read_utc = :now WHERE broadcast_id = :b AND user_id = :u"),
               {"b": bid, "u": user_id, "now": now})


def unread_total(db, user_id):
    return int(db.execute(text("""SELECT COALESCE(SUM(m.unread_count), 0) FROM broadcast_members m
                                  JOIN broadcasts b ON b.id = m.broadcast_id WHERE m.user_id = :u AND b.deleted_utc = 0"""),
                          {"u": user_id}).scalar() or 0)


# --- a public channel's messages ---------------------------------------------------------------------------------

def record_post(db, bid, author_id, event_id, body, severity, now):
    db.execute(text("""INSERT INTO broadcast_posts (broadcast_id, author_id, event_id, body, word_severity, created_utc, removed_utc)
                       VALUES (:b, :a, :e, :body, :sev, :now, 0)"""),
               {"b": bid, "a": author_id, "e": event_id, "body": body, "sev": severity, "now": now})


def posts_since(db, bid, since):
    return db.execute(text("SELECT COUNT(*) FROM broadcast_posts WHERE broadcast_id = :b AND created_utc >= :since"),
                      {"b": bid, "since": since}).scalar() or 0


def post_by_event(db, bid, event_id):
    """(id, author_id, body, removed_utc) of one message, or None."""
    return db.execute(text("""SELECT id, author_id, body, removed_utc FROM broadcast_posts
                              WHERE broadcast_id = :b AND event_id = :e"""), {"b": bid, "e": event_id}).fetchone()


def remove_post(db, post_id, now):
    db.execute(text("UPDATE broadcast_posts SET removed_utc = :now WHERE id = :p AND removed_utc = 0"), {"p": post_id, "now": now})


def report(db, bid, post_id, reporter_id, reason, now):
    """False when this person had already reported this message."""
    if db.execute(text("SELECT 1 FROM broadcast_reports WHERE post_id = :p AND reporter_id = :u"),
                  {"p": post_id, "u": reporter_id}).fetchone():
        return False
    db.execute(text("""INSERT INTO broadcast_reports (broadcast_id, post_id, reporter_id, reason, created_utc, resolved_utc)
                       VALUES (:b, :p, :u, :reason, :now, 0)"""),
               {"b": bid, "p": post_id, "u": reporter_id, "reason": reason, "now": now})
    return True


def open_reports(db, limit=50):
    """(channel id, message id, its text, event id, how many reports, the latest reasons), the most reported first.
    Never who reported."""
    rows = db.execute(text("""SELECT r.broadcast_id, r.post_id, p.body, p.event_id, COUNT(*) AS n, MAX(r.created_utc) AS latest
                              FROM broadcast_reports r JOIN broadcast_posts p ON p.id = r.post_id
                              WHERE r.resolved_utc = 0 GROUP BY r.broadcast_id, r.post_id, p.body, p.event_id
                              ORDER BY n DESC, latest DESC LIMIT :n"""), {"n": limit}).fetchall()
    out = []
    for bid, pid, body, event_id, n, _latest in rows:
        reasons = [r[0] for r in db.execute(text("""SELECT reason FROM broadcast_reports WHERE post_id = :p AND resolved_utc = 0
                                                    AND reason <> '' ORDER BY created_utc DESC LIMIT 5"""), {"p": pid})]
        out.append((bid, pid, body, event_id, n, reasons))
    return out


def resolve_reports(db, post_id, now):
    db.execute(text("UPDATE broadcast_reports SET resolved_utc = :now WHERE post_id = :p AND resolved_utc = 0"),
               {"p": post_id, "now": now})
