"""Guild flair in the database (rules in helpers/guild_flair.py): `guild_flairs` and `submissions.guild_flair_id`.
Plain `text()` SQL with bound values only, tested on SQLite (tests/test_guild_flair.py). Every function that changes a
flair takes the guild's id, so one guild's guildmasters can never touch another's. Nothing here commits.
"""
from sqlalchemy import bindparam, text

_FLAIR = "id, board_id, name, colour, position, name_severity"


def flairs_of(db, board_id):
    """A guild's flairs in their order: (id, board_id, name, colour, position, name_severity)."""
    return db.execute(text(f"SELECT {_FLAIR} FROM guild_flairs WHERE board_id = :b ORDER BY position, id"),
                      {"b": board_id}).fetchall()


def flair(db, board_id, flair_id):
    return db.execute(text(f"SELECT {_FLAIR} FROM guild_flairs WHERE board_id = :b AND id = :f"),
                      {"b": board_id, "f": flair_id}).fetchone()


def ids_of(db, board_id):
    return [row[0] for row in db.execute(text("SELECT id FROM guild_flairs WHERE board_id = :b"), {"b": board_id})]


def _name_taken(db, board_id, name, except_id=None):
    # compared in Python: SQLite's lower() only folds ASCII (the unique index does the same work in Postgres)
    wanted = name.casefold()
    return any(row[2].casefold() == wanted and row[0] != except_id for row in flairs_of(db, board_id))


def add(db, board_id, name, colour, severity, now):
    """The new flair's id, or None when the guild has one by that name already."""
    if _name_taken(db, board_id, name):
        return None
    position = db.execute(text("SELECT COALESCE(MAX(position), 0) + 1 FROM guild_flairs WHERE board_id = :b"),
                          {"b": board_id}).scalar()
    db.execute(text("""INSERT INTO guild_flairs (board_id, name, colour, position, name_severity, created_utc)
                       VALUES (:b, :name, :colour, :pos, :sev, :now)"""),
               {"b": board_id, "name": name, "colour": colour, "pos": position, "sev": severity, "now": now})
    return db.execute(text("SELECT MAX(id) FROM guild_flairs WHERE board_id = :b"), {"b": board_id}).scalar()


def edit(db, board_id, flair_id, name, colour, severity):
    """False when another of the guild's flairs has that name."""
    if _name_taken(db, board_id, name, except_id=flair_id):
        return False
    db.execute(text("""UPDATE guild_flairs SET name = :name, colour = :colour, name_severity = :sev
                       WHERE board_id = :b AND id = :f"""),
               {"b": board_id, "f": flair_id, "name": name, "colour": colour, "sev": severity})
    return True


def move(db, board_id, flair_id, step):
    """One place up (step -1) or down (+1) in the guild's list."""
    rows = flairs_of(db, board_id)
    ids = [row[0] for row in rows]
    if flair_id not in ids:
        return
    i = ids.index(flair_id)
    j = i + (1 if step > 0 else -1)
    if not 0 <= j < len(ids):
        return
    ids[i], ids[j] = ids[j], ids[i]
    for position, fid in enumerate(ids, start=1):
        db.execute(text("UPDATE guild_flairs SET position = :p WHERE board_id = :b AND id = :f"),
                   {"p": position, "b": board_id, "f": fid})


def delete(db, board_id, flair_id):
    """The flair goes; the posts that had it lose it (they are never deleted with it)."""
    db.execute(text("UPDATE submissions SET guild_flair_id = NULL WHERE board_id = :b AND guild_flair_id = :f"),
               {"b": board_id, "f": flair_id})
    db.execute(text("DELETE FROM guild_flairs WHERE board_id = :b AND id = :f"), {"b": board_id, "f": flair_id})


def set_post_flair(db, post_id, flair_id):
    db.execute(text("UPDATE submissions SET guild_flair_id = :f WHERE id = :p"), {"p": post_id, "f": flair_id})


def flairs_by_id(db, flair_ids):
    """{id: (id, board_id, name, colour, position, name_severity)} for a page of posts' flairs."""
    if not flair_ids:
        return {}
    query = text(f"SELECT {_FLAIR} FROM guild_flairs WHERE id IN :ids").bindparams(bindparam("ids", expanding=True))
    return {row[0]: row for row in db.execute(query, {"ids": list(flair_ids)})}


def attach(db, posts):
    """Each post's flair (`post.guild_flair`: {"id", "name", "colour", "severity"} or None), one query for a page.
    A flair of another guild than the post's (a guild copy moved or a stale id) is not shown."""
    posts = list(posts)
    ids = {p.guild_flair_id for p in posts if getattr(p, "guild_flair_id", None)}
    rows = flairs_by_id(db, ids)
    for p in posts:
        row = rows.get(getattr(p, "guild_flair_id", None))
        p.__dict__["guild_flair"] = None if row is None or row[1] != p.board_id else {
            "id": row[0], "name": row[2], "colour": row[3], "severity": row[5]}
