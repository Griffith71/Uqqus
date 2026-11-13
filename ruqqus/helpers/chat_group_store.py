"""Group chats in the database (rules in helpers/chat_groups.py): `chat_groups` and `chat_group_members`.

Plain `text()` SQL with bound values only, tested on SQLite (`tests/test_chat_group_store.py`). Nothing here
commits: the routes do, after the Matrix side has gone through.

A member row is `invited` (the group is in their Requests) or `joined` (in their Inbox); only joined members count
unread messages. Leaving, being removed and deleting an invitation all delete the row, so the same person can be
added again later.

Unread is a count of messages, not a row per member per message (groups hold up to 500 people): a group counts its
messages (`chat_groups.message_seq`, one UPDATE per message whatever its size) and each member's row holds the count
they last read (`last_read_seq`, which the writer's own message also moves), as guild channels do. A member's unread
is the difference. The old per-member `unread_count` column is no longer written or read.
"""

from sqlalchemy import text

from ruqqus.helpers import chat_groups as rules

_GROUP = "id, matrix_room_id, name, name_severity, created_by_id, created_utc, last_activity_utc, deleted_utc"
# a member's unread messages: joined members only (an invitation reads nothing yet)
_UNREAD = "CASE WHEN m.status = 'joined' THEN g.message_seq - m.last_read_seq ELSE 0 END"
_MEMBER = f"m.user_id, m.role, m.status, m.invited_by_id, {_UNREAD}, m.last_read_utc, m.joined_utc, m.invited_utc"
_MEMBERS = "chat_group_members m JOIN chat_groups g ON g.id = m.group_id"
_ORDER = "CASE m.role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END, m.invited_utc, m.user_id"
# what a member has read when they come in: everything said before (they read from their invitation on, and nothing
# before it counts as unread)
_SEQ_NOW = "(SELECT message_seq FROM chat_groups WHERE id = :g)"
# a last-read number only moves forward: a read that waited on the webhook's update of the same row (the member's own
# message, counted meanwhile) would otherwise write the count from before it (seen by the end-to-end check, 2026-11-13)
_SEQ_KEPT = f"CASE WHEN {_SEQ_NOW} > last_read_seq THEN {_SEQ_NOW} ELSE last_read_seq END"


def create(db, room_id, name, severity, owner_id, now):
    db.execute(text("""INSERT INTO chat_groups (matrix_room_id, name, name_severity, created_by_id, created_utc,
                                                last_activity_utc, deleted_utc)
                       VALUES (:room, :name, :sev, :owner, :now, :now, 0)"""),
               {"room": room_id, "name": name, "sev": severity, "owner": owner_id, "now": now})
    gid = db.execute(text("SELECT id FROM chat_groups WHERE matrix_room_id = :room"), {"room": room_id}).scalar()
    add_member(db, gid, owner_id, rules.OWNER, rules.JOINED, owner_id, now)
    return gid


def add_member(db, gid, user_id, role, status, invited_by_id, now):
    db.execute(text(f"""INSERT INTO chat_group_members (group_id, user_id, role, status, invited_by_id, last_read_seq,
                                                        last_read_utc, joined_utc, invited_utc)
                        VALUES (:g, :u, :role, :status, :by, {_SEQ_NOW}, 0, :joined, :now)"""),
               {"g": gid, "u": user_id, "role": role, "status": status, "by": invited_by_id, "now": now,
                "joined": now if status == rules.JOINED else 0})
    if invited_by_id and invited_by_id != user_id:
        # every add is logged, so removing someone and adding them again still counts against the daily cap (M-08)
        db.execute(text("INSERT INTO chat_group_adds (adder_id, group_id, user_id, created_utc) VALUES (:a, :g, :u, :now)"),
                   {"a": invited_by_id, "g": gid, "u": user_id, "now": now})


def group(db, gid):
    return db.execute(text(f"SELECT {_GROUP} FROM chat_groups WHERE id = :g AND deleted_utc = 0"), {"g": gid}).fetchone()


def group_by_room(db, room_id):
    return db.execute(text(f"SELECT {_GROUP} FROM chat_groups WHERE matrix_room_id = :r AND deleted_utc = 0"),
                      {"r": room_id}).fetchone()


def member(db, gid, user_id):
    return db.execute(text(f"SELECT {_MEMBER} FROM {_MEMBERS} WHERE m.group_id = :g AND m.user_id = :u"),
                      {"g": gid, "u": user_id}).fetchone()


def members(db, gid):
    """Everyone in the group, invited or joined, the owner first, then by when they were added."""
    return db.execute(text(f"SELECT {_MEMBER} FROM {_MEMBERS} WHERE m.group_id = :g ORDER BY {_ORDER}"), {"g": gid}).fetchall()


def preview(db, gid, limit=8):
    """The first `limit` members in the same order, each with the group's counts (everyone, and those who joined), for the
    chat list: a list of groups never loads every member of every group."""
    return db.execute(text(f"""SELECT {_MEMBER},
                                      COUNT(*) OVER () AS total,
                                      SUM(CASE WHEN m.status = 'joined' THEN 1 ELSE 0 END) OVER () AS joined
                               FROM {_MEMBERS} WHERE m.group_id = :g ORDER BY {_ORDER} LIMIT :n"""),
                      {"g": gid, "n": limit}).fetchall()


def member_ids(db, gid, joined_only=False):
    """The members' ids alone (who may be in a call, who a new member must not be in a block with)."""
    sql = "SELECT user_id FROM chat_group_members WHERE group_id = :g" + (" AND status = 'joined'" if joined_only else "")
    return [row[0] for row in db.execute(text(sql), {"g": gid}).fetchall()]


def size(db, gid):
    return db.execute(text("SELECT COUNT(*) FROM chat_group_members WHERE group_id = :g"), {"g": gid}).scalar() or 0


def remove_member(db, gid, user_id):
    db.execute(text("DELETE FROM chat_group_members WHERE group_id = :g AND user_id = :u"), {"g": gid, "u": user_id})


def set_role(db, gid, user_id, role):
    db.execute(text("UPDATE chat_group_members SET role = :role WHERE group_id = :g AND user_id = :u"),
               {"g": gid, "u": user_id, "role": role})


def accept(db, gid, user_id, now):
    """An invitation accepted: the member is in, from now. False when there was no invitation to accept."""
    done = db.execute(text(f"""UPDATE chat_group_members SET status = 'joined', joined_utc = :now, last_read_seq = {_SEQ_NOW}
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
    """People this account added to groups since `since`, from the log (a removed member still counts: M-08)."""
    return db.execute(text("SELECT COUNT(*) FROM chat_group_adds WHERE adder_id = :u AND created_utc >= :since"),
                      {"u": user_id, "since": since}).scalar() or 0


def groups_for(db, user_id, status, limit=100):
    """The groups in one of a member's tabs (joined: Inbox, invited: Requests), the most recently active first:
    (group row..., role, unread_count, invited_by_id)."""
    return db.execute(text(f"""SELECT g.id, g.matrix_room_id, g.name, g.name_severity, g.created_by_id, g.created_utc,
                                      g.last_activity_utc, g.deleted_utc, m.role, {_UNREAD}, m.invited_by_id
                               FROM {_MEMBERS}
                               WHERE m.user_id = :u AND m.status = :status AND g.deleted_utc = 0
                               ORDER BY g.last_activity_utc DESC, g.id DESC LIMIT :n"""),
                      {"u": user_id, "status": status, "n": limit}).fetchall()


def note_message(db, room_id, sender_id, now):
    """A new message in a group's room: one more unread for every joined member but its sender (the group's count goes
    up and the sender's own last-read number with it). False when the room is not a group's, or the sender is not one
    of its joined members (nothing is counted then)."""
    g = group_by_room(db, room_id)
    if g is None:
        return False
    sender = member(db, g[0], sender_id)
    if sender is None or sender[2] != rules.JOINED:
        return False
    db.execute(text("UPDATE chat_groups SET message_seq = message_seq + 1, last_activity_utc = :now WHERE id = :g"),
               {"g": g[0], "now": now})
    db.execute(text(f"UPDATE chat_group_members SET last_read_seq = {_SEQ_KEPT} WHERE group_id = :g AND user_id = :u"),
               {"g": g[0], "u": sender_id})
    return True


def mark_read(db, gid, user_id, now):
    db.execute(text(f"""UPDATE chat_group_members SET last_read_seq = {_SEQ_KEPT}, last_read_utc = :now
                        WHERE group_id = :g AND user_id = :u AND status = 'joined'"""),
               {"g": gid, "u": user_id, "now": now})


def unread_total(db, user_id):
    """Unread messages across a member's groups (added to the navbar chat badge)."""
    return int(db.execute(text(f"""SELECT COALESCE(SUM({_UNREAD}), 0) FROM {_MEMBERS}
                                   WHERE m.user_id = :u AND m.status = 'joined' AND g.deleted_utc = 0"""),
                          {"u": user_id}).scalar() or 0)


def unread_groups(db, user_id):
    return int(db.execute(text(f"""SELECT COUNT(*) FROM {_MEMBERS}
                                   WHERE m.user_id = :u AND m.status = 'joined' AND g.message_seq > m.last_read_seq
                                         AND g.deleted_utc = 0"""), {"u": user_id}).scalar() or 0)


def invitations(db, user_id):
    return int(db.execute(text("""SELECT COUNT(*) FROM chat_group_members m JOIN chat_groups g ON g.id = m.group_id
                                  WHERE m.user_id = :u AND m.status = 'invited' AND g.deleted_utc = 0"""),
                          {"u": user_id}).scalar() or 0)


def push_targets(db, room_id, sender_id, kinds):
    """The joined members of the group in this room, but the sender, whose push choice is one of `kinds`
    (helpers/push_rules.py: who a message or a call is told to)."""
    marks = ",".join(str(int(k)) for k in kinds) or "-1"
    return [row[0] for row in db.execute(text(f"""SELECT m.user_id FROM {_MEMBERS}
                                                   WHERE g.matrix_room_id = :r AND g.deleted_utc = 0 AND m.status = 'joined'
                                                         AND m.user_id <> :u AND m.push_mode IN ({marks})"""),
                                         {"r": room_id, "u": sender_id}).fetchall()]


def set_push_mode(db, gid, user_id, mode):
    return db.execute(text("UPDATE chat_group_members SET push_mode = :m WHERE group_id = :g AND user_id = :u"),
                      {"m": mode, "g": gid, "u": user_id}).rowcount == 1


def push_mode_of(db, gid, user_id):
    return db.execute(text("SELECT push_mode FROM chat_group_members WHERE group_id = :g AND user_id = :u"),
                      {"g": gid, "u": user_id}).scalar() or 0
