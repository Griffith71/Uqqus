"""Guild roles in the database (rules in helpers/guild_roles.py): `guild_roles`, `guild_role_members` and
`guild_channel_overwrites`. Plain `text()` SQL with bound values only, tested on SQLite (tests/test_guild_roles.py).
Every function that changes a role or an overwrite takes the guild's id, so another guild's roles are never reached.
Nothing here commits. The roles a viewer holds come with the channel rows (guild_channel_store `_STANDING`).
"""
from sqlalchemy import bindparam, text

_ROLE = "id, board_id, name, word_severity, colour, position, perm_slow_exempt, perm_remove_messages, perm_pin"


def _chunks(items, size=1000):
    items = list(items)
    for start in range(0, len(items), size):
        yield items[start:start + size]


# --- roles ---------------------------------------------------------------------------------------------------------------

def roles_of(db, board_id):
    return db.execute(text(f"SELECT {_ROLE} FROM guild_roles WHERE board_id = :b ORDER BY position, id"), {"b": board_id}).fetchall()


def role(db, board_id, role_id):
    if not role_id:
        return None
    return db.execute(text(f"SELECT {_ROLE} FROM guild_roles WHERE id = :r AND board_id = :b"), {"r": role_id, "b": board_id}).fetchone()


def role_count(db, board_id):
    return db.execute(text("SELECT COUNT(*) FROM guild_roles WHERE board_id = :b"), {"b": board_id}).scalar() or 0


def name_taken(db, board_id, name, except_id=0):
    wanted = name.casefold()
    return any(row[1].casefold() == wanted for row in
               db.execute(text("SELECT id, name FROM guild_roles WHERE board_id = :b AND id <> :x"), {"b": board_id, "x": except_id or 0}))


def make_role(db, board_id, name, severity, colour, perms):
    position = db.execute(text("SELECT COALESCE(MAX(position), 0) + 1 FROM guild_roles WHERE board_id = :b"), {"b": board_id}).scalar()
    db.execute(text("""INSERT INTO guild_roles (board_id, name, word_severity, colour, position, perm_slow_exempt, perm_remove_messages, perm_pin)
                       VALUES (:b, :n, :s, :c, :p, :a, :r, :i)"""),
               {"b": board_id, "n": name, "s": severity, "c": colour, "p": position, "a": bool(perms.get("slow_exempt")),
                "r": bool(perms.get("remove_messages")), "i": bool(perms.get("pin"))})
    return db.execute(text("SELECT MAX(id) FROM guild_roles WHERE board_id = :b"), {"b": board_id}).scalar()


def update_role(db, board_id, role_id, name, severity, colour, perms):
    db.execute(text("""UPDATE guild_roles SET name = :n, word_severity = :s, colour = :c, perm_slow_exempt = :a,
                              perm_remove_messages = :r, perm_pin = :i WHERE id = :id AND board_id = :b"""),
               {"id": role_id, "b": board_id, "n": name, "s": severity, "c": colour, "a": bool(perms.get("slow_exempt")),
                "r": bool(perms.get("remove_messages")), "i": bool(perms.get("pin"))})


def delete_role(db, board_id, role_id):
    """The role, its people and its overwrites go (the foreign keys cascade; the SQLite tests have none, so they go here)."""
    if role(db, board_id, role_id) is None:
        return
    db.execute(text("DELETE FROM guild_role_members WHERE role_id = :r"), {"r": role_id})
    db.execute(text("DELETE FROM guild_channel_overwrites WHERE role_id = :r"), {"r": role_id})
    db.execute(text("DELETE FROM guild_roles WHERE id = :r AND board_id = :b"), {"r": role_id, "b": board_id})


def move_roles(db, board_id, ordered_ids):
    for index, role_id in enumerate(ordered_ids, start=1):
        db.execute(text("UPDATE guild_roles SET position = :p WHERE id = :r AND board_id = :b"), {"p": index, "r": role_id, "b": board_id})


def rescan_severities(db, rate):
    for role_id, name in db.execute(text("SELECT id, name FROM guild_roles")).fetchall():
        db.execute(text("UPDATE guild_roles SET word_severity = :s WHERE id = :r"), {"s": rate(name), "r": role_id})


# --- who has one -------------------------------------------------------------------------------------------------------------

def members_of(db, role_id, limit=50):
    return [row[0] for row in db.execute(text("SELECT user_id FROM guild_role_members WHERE role_id = :r ORDER BY created_utc, user_id LIMIT :n"),
                                         {"r": role_id, "n": limit})]


def member_count(db, role_id):
    return db.execute(text("SELECT COUNT(*) FROM guild_role_members WHERE role_id = :r"), {"r": role_id}).scalar() or 0


def has_role(db, role_id, user_id):
    return db.execute(text("SELECT 1 FROM guild_role_members WHERE role_id = :r AND user_id = :u"), {"r": role_id, "u": user_id}).fetchone() is not None


def add_member(db, role_id, user_id, by, now):
    db.execute(text("""INSERT INTO guild_role_members (role_id, user_id, assigned_by, created_utc) VALUES (:r, :u, :by, :now)
                       ON CONFLICT (role_id, user_id) DO NOTHING"""), {"r": role_id, "u": user_id, "by": by, "now": now})


def remove_member(db, role_id, user_id):
    return bool(db.execute(text("DELETE FROM guild_role_members WHERE role_id = :r AND user_id = :u"), {"r": role_id, "u": user_id}).rowcount)


def drop_roles(db, board_id, user_id):
    """Someone left the guild or was exiled: their roles in it go."""
    db.execute(text("""DELETE FROM guild_role_members WHERE user_id = :u
                       AND role_id IN (SELECT id FROM guild_roles WHERE board_id = :b)"""), {"u": user_id, "b": board_id})


def top_roles(db, board_id, user_ids):
    """{user id: (name, colour, severity)}: each person's first role by the guildmasters' order, for the line over their
    messages."""
    out = {}
    for chunk in _chunks(user_ids):
        query = text("""SELECT rm.user_id, ro.name, ro.colour, ro.word_severity FROM guild_role_members rm
                        JOIN guild_roles ro ON ro.id = rm.role_id WHERE ro.board_id = :b AND rm.user_id IN :ids
                        ORDER BY ro.position, ro.id""").bindparams(bindparam("ids", expanding=True))
        for uid, name, colour, severity in db.execute(query, {"b": board_id, "ids": chunk}):
            out.setdefault(uid, (name, colour, severity))
    return out


# --- what a role says about a channel -------------------------------------------------------------------------------------------

def overwrites_of(db, channel_id):
    """{role id: (read, write)}: INHERIT, ALLOW or DENY."""
    return {row[0]: (row[1], row[2]) for row in db.execute(
        text("SELECT role_id, read_ow, write_ow FROM guild_channel_overwrites WHERE channel_id = :c"), {"c": channel_id})}


def set_overwrites(db, board_id, channel_id, mapping):
    """Replaces the channel's overwrites with {role id: (read, write)}; roles that aren't this guild's are ignored and
    a role left as "the channel's own rules" for both has no row."""
    db.execute(text("DELETE FROM guild_channel_overwrites WHERE channel_id = :c"), {"c": channel_id})
    own = {row[0] for row in db.execute(text("SELECT id FROM guild_roles WHERE board_id = :b"), {"b": board_id})}
    for role_id, (read, write) in mapping.items():
        if role_id in own and (read or write):
            db.execute(text("INSERT INTO guild_channel_overwrites (channel_id, role_id, read_ow, write_ow) VALUES (:c, :r, :rd, :wr)"),
                       {"c": channel_id, "r": role_id, "rd": read, "wr": write})


def overwrites_for_users(db, channel_id, user_ids):
    """{user id: (allow read, deny read, allow write, deny write)} of the people in these roles' overwrites on this
    channel (for the sync step; the viewer's own come with their channel row). Allowing writing allows reading."""
    out = {}
    for chunk in _chunks(user_ids):
        query = text("""SELECT rm.user_id,
                               MAX(CASE WHEN o.read_ow = 1 OR o.write_ow = 1 THEN 1 ELSE 0 END),
                               MAX(CASE WHEN o.read_ow = 2 THEN 1 ELSE 0 END),
                               MAX(CASE WHEN o.write_ow = 1 THEN 1 ELSE 0 END),
                               MAX(CASE WHEN o.write_ow = 2 THEN 1 ELSE 0 END)
                        FROM guild_channel_overwrites o JOIN guild_role_members rm ON rm.role_id = o.role_id
                        WHERE o.channel_id = :c AND rm.user_id IN :ids GROUP BY rm.user_id""").bindparams(bindparam("ids", expanding=True))
        for uid, ar, dr, aw, dw in db.execute(query, {"c": channel_id, "ids": chunk}):
            out[uid] = (bool(ar), bool(dr), bool(aw), bool(dw))
    return out
