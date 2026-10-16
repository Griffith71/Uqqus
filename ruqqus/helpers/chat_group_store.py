"""Group chats in the database (rules in helpers/chat_groups.py): `chat_groups` and `chat_group_members`.

Plain `text()` SQL with bound values only, tested on SQLite (`tests/test_chat_group_store.py`). Nothing here
commits: the routes do, after the Matrix side has gone through.

A member row is `invited` (the group is in their Requests) or `joined` (in their Inbox); only joined members count
unread messages. Leaving, being removed and deleting an invitation all delete the row, so the same person can be
added again later.
"""

from sqlalchemy import text

from ruqqus.helpers import chat_groups as rules

_GROUP = "id, matrix_room_id, name, name_severity, created_by_id, created_utc, last_activity_utc, deleted_utc"
_MEMBER = "user_id, role, status, invited_by_id, unread_count, last_read_utc, joined_utc, invited_utc"


def create(db, room_id, name, severity, owner_id, now):
    db.execute(text("""INSERT INTO chat_groups (matrix_room_id, name, name_severity, created_by_id, created_utc,
                                                last_activity_utc, deleted_utc)
                       VALUES (:room, :name, :sev, :owner, :now, :now, 0)"""),
               {"room": room_id, "name": name, "sev": severity, "owner": owner_id, "now": now})
    gid = db.execute(text("SELECT id FROM chat_groups WHERE matrix_room_id = :room"), {"room": room_id}).scalar()
    add_member(db, gid, owner_id, rules.OWNER, rules.JOINED, owner_id, now)
    return gid


def add_member(db, gid, user_id, role, status, invited_by_id, now):
    db.execute(text("""INSERT INTO chat_group_members (group_id, user_id, role, status, invited_by_id, unread_count,
                                                       last_read_utc, joined_utc, invited_utc)
                       VALUES (:g, :u, :role, :status, :by, 0, 0, :joined, :now)"""),
               {"g": gid, "u": user_id, "role": role, "status": status, "by": invited_by_id, "now": now,
                "joined": now if status == rules.JOINED else 0})


def group(db, gid):
    return db.execute(text(f"SELECT {_GROUP} FROM chat_groups WHERE id = :g AND deleted_utc = 0"), {"g": gid}).fetchone()


def group_by_room(db, room_id):
    return db.execute(text(f"SELECT {_GROUP} FROM chat_groups WHERE matrix_room_id = :r AND deleted_utc = 0"),
                      {"r": room_id}).fetchone()


def member(db, gid, user_id):
    return db.execute(text(f"SELECT {_MEMBER} FROM chat_group_members WHERE group_id = :g AND user_id = :u"),
                      {"g": gid, "u": user_id}).fetchone()


def members(db, gid):
    """Everyone in the group, invited or joined, the owner first, then by when they were added."""
    return db.execute(text(f"""SELECT {_MEMBER} FROM chat_group_members WHERE group_id = :g
                               ORDER BY CASE role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END,
                                        invited_utc, user_id"""), {"g": gid}).fetchall()


def size(db, gid):
    return db.execute(text("SELECT COUNT(*) FROM chat_group_members WHERE group_id = :g"), {"g": gid}).scalar() or 0


def remove_member(db, gid, user_id):
    db.execute(text("DELETE FROM chat_group_members WHERE group_id = :g AND user_id = :u"), {"g": gid, "u": user_id})


def set_role(db, gid, user_id, role):
    db.execute(text("UPDATE chat_group_members SET role = :role WHERE group_id = :g AND user_id = :u"),
               {"g": gid, "u": user_id, "role": role})


def accept(db, gid, user_id, now):
    """An invitation accepted: the member is in, from now. False when there was no invitation to accept."""
    done = db.execute(text("""UPDATE chat_group_members SET status = 'joined', joined_utc = :now
                              WHERE group_id = :g AND user_id = :u AND status = 'invited'"""),
                      {"g": gid, "u": user_id, "now": now})
    return done.rowcount == 1


def rename(db, gid, name, severity):
    db.execute(text("UPDATE chat_groups SET name = :name, name_severity = :sev WHERE id = :g"),
               {"g": gid, "name": name, "sev": severity})


def close(db, gid, now):
    db.execute(text("UPDATE chat_groups SET deleted_utc = :now WHERE id = :g"), {"g": gid, "now": now})


def created_since(db, user_id, since):
    return db.execute(text("SELECT COUNT(*) FROM chat_groups WHERE created_by_id = :u AND created_utc >= :since"),
                      {"u": user_id, "since": since}).scalar() or 0


def added_since(db, user_id, since):
    return db.execute(text("""SELECT COUNT(*) FROM chat_group_members
                              WHERE invited_by_id = :u AND user_id <> :u AND invited_utc >= :since"""),
                      {"u": user_id, "since": since}).scalar() or 0


def groups_for(db, user_id, status, limit=100):
    """The groups in one of a member's tabs (joined: Inbox, invited: Requests), the most recently active first:
    (group row..., role, unread_count, invited_by_id)."""
    return db.execute(text(f"""SELECT g.id, g.matrix_room_id, g.name, g.name_severity, g.created_by_id, g.created_utc,
                                      g.last_activity_utc, g.deleted_utc, m.role, m.unread_count, m.invited_by_id
                               FROM chat_groups g JOIN chat_group_members m ON m.group_id = g.id
                               WHERE m.user_id = :u AND m.status = :status AND g.deleted_utc = 0
                               ORDER BY g.last_activity_utc DESC, g.id DESC LIMIT :n"""),
                      {"u": user_id, "status": status, "n": limit}).fetchall()


def note_message(db, room_id, sender_id, now):
    """A new message in a group's room: one more unread for every joined member but its sender. False when the
    room is not a group's, or the sender is not one of its joined members (nothing is counted then)."""
    g = group_by_room(db, room_id)
    if g is None:
        return False
    sender = member(db, g[0], sender_id)
    if sender is None or sender[2] != rules.JOINED:
        return False
    db.execute(text("""UPDATE chat_group_members SET unread_count = unread_count + 1
                       WHERE group_id = :g AND status = 'joined' AND user_id <> :u"""), {"g": g[0], "u": sender_id})
    db.execute(text("UPDATE chat_groups SET last_activity_utc = :now WHERE id = :g"), {"g": g[0], "now": now})
    return True


def mark_read(db, gid, user_id, now):
    db.execute(text("""UPDATE chat_group_members SET unread_count = 0, last_read_utc = :now
                       WHERE group_id = :g AND user_id = :u AND status = 'joined'"""),
               {"g": gid, "u": user_id, "now": now})


def unread_total(db, user_id):
    """Unread messages across a member's groups (added to the navbar chat badge)."""
    return int(db.execute(text("""SELECT COALESCE(SUM(m.unread_count), 0) FROM chat_group_members m
                                  JOIN chat_groups g ON g.id = m.group_id
                                  WHERE m.user_id = :u AND m.status = 'joined' AND g.deleted_utc = 0"""),
                          {"u": user_id}).scalar() or 0)


def unread_groups(db, user_id):
    return int(db.execute(text("""SELECT COUNT(*) FROM chat_group_members m JOIN chat_groups g ON g.id = m.group_id
                                  WHERE m.user_id = :u AND m.status = 'joined' AND m.unread_count > 0
                                        AND g.deleted_utc = 0"""), {"u": user_id}).scalar() or 0)


def invitations(db, user_id):
    return int(db.execute(text("""SELECT COUNT(*) FROM chat_group_members m JOIN chat_groups g ON g.id = m.group_id
                                  WHERE m.user_id = :u AND m.status = 'invited' AND g.deleted_utc = 0"""),
                          {"u": user_id}).scalar() or 0)
