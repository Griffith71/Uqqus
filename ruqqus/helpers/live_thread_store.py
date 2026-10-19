"""Live threads in the database (rules in helpers/live_threads.py): `live_threads` (one row per primary post that has
gone live) and `live_updates`. Plain `text()` SQL with bound values only, tested on SQLite (`tests/test_live_threads.py`).
Nothing here commits.
"""

from sqlalchemy import bindparam, text

_UPDATE = "id, post_id, author_id, body, body_html, word_severity, created_utc, edited_utc, deleted_utc, struck_utc"
_THREAD = ("started_utc, ended_utc, last_update_utc, started_by_id, ends_utc, summary, summary_html, summary_severity, "
           "summary_edited_utc")


def thread(db, post_id):
    """(started_utc, ended_utc, last_update_utc, started_by_id, ends_utc, summary, summary_html, summary_severity,
    summary_edited_utc) or None."""
    return db.execute(text(f"SELECT {_THREAD} FROM live_threads WHERE post_id = :p"), {"p": post_id}).fetchone()


def start(db, post_id, user_id, now, ends_utc=0):
    """Live from now until `ends_utc` (again, if it had ended: its updates and summary stay)."""
    if thread(db, post_id) is None:
        db.execute(text("""INSERT INTO live_threads (post_id, started_utc, ended_utc, last_update_utc, started_by_id, ends_utc)
                           VALUES (:p, :now, 0, :now, :u, :ends)"""), {"p": post_id, "now": now, "u": user_id, "ends": ends_utc})
    else:
        db.execute(text("""UPDATE live_threads SET started_utc = :now, ended_utc = 0, last_update_utc = :now, started_by_id = :u,
                           ends_utc = :ends WHERE post_id = :p"""), {"p": post_id, "now": now, "u": user_id, "ends": ends_utc})


def extend(db, post_id, ends_utc):
    """A running thread's new end."""
    db.execute(text("UPDATE live_threads SET ends_utc = :ends WHERE post_id = :p AND ended_utc = 0"),
               {"p": post_id, "ends": ends_utc})


def set_summary(db, post_id, body, body_html, severity, now):
    db.execute(text("""UPDATE live_threads SET summary = :body, summary_html = :html, summary_severity = :sev,
                       summary_edited_utc = :now WHERE post_id = :p"""),
               {"p": post_id, "body": body, "html": body_html, "sev": severity, "now": now})


def strike(db, update_id, on, now):
    """Cross an update out ("Corrected"), or undo it."""
    db.execute(text("UPDATE live_updates SET struck_utc = :at WHERE id = :u AND deleted_utc = 0"),
               {"u": update_id, "at": now if on else 0})


def end(db, post_id, now):
    db.execute(text("UPDATE live_threads SET ended_utc = :now WHERE post_id = :p AND ended_utc = 0"), {"p": post_id, "now": now})


def threads_for(db, post_ids):
    """{post id: (started_utc, ended_utc, last_update_utc, ends_utc)} for a page of (primary) posts."""
    if not post_ids:
        return {}
    query = text("SELECT post_id, started_utc, ended_utc, last_update_utc, ends_utc FROM live_threads WHERE post_id IN :ids").bindparams(
        bindparam("ids", expanding=True))
    return {row[0]: tuple(row[1:]) for row in db.execute(query, {"ids": list(post_ids)})}


def add_update(db, post_id, author_id, body, body_html, severity, now):
    db.execute(text("""INSERT INTO live_updates (post_id, author_id, body, body_html, word_severity, created_utc, edited_utc, deleted_utc)
                       VALUES (:p, :a, :body, :html, :sev, :now, 0, 0)"""),
               {"p": post_id, "a": author_id, "body": body, "html": body_html, "sev": severity, "now": now})
    db.execute(text("UPDATE live_threads SET last_update_utc = :now WHERE post_id = :p"), {"p": post_id, "now": now})
    return db.execute(text("SELECT MAX(id) FROM live_updates WHERE post_id = :p AND author_id = :a"),
                      {"p": post_id, "a": author_id}).scalar()


def update(db, update_id):
    return db.execute(text(f"SELECT {_UPDATE} FROM live_updates WHERE id = :u"), {"u": update_id}).fetchone()


def updates(db, post_id, after_id=0, limit=50):
    """The post's live updates, newest first (or, after an id, the newer ones, oldest first). Never deleted ones."""
    if after_id:
        return db.execute(text(f"""SELECT {_UPDATE} FROM live_updates WHERE post_id = :p AND id > :after AND deleted_utc = 0
                                   ORDER BY id ASC LIMIT :n"""), {"p": post_id, "after": after_id, "n": limit}).fetchall()
    return db.execute(text(f"""SELECT {_UPDATE} FROM live_updates WHERE post_id = :p AND deleted_utc = 0
                               ORDER BY id DESC LIMIT :n"""), {"p": post_id, "n": limit}).fetchall()


def delete_update(db, update_id, now):
    db.execute(text("UPDATE live_updates SET deleted_utc = :now WHERE id = :u AND deleted_utc = 0"), {"u": update_id, "now": now})


def updates_since(db, post_id, since):
    return db.execute(text("SELECT COUNT(*) FROM live_updates WHERE post_id = :p AND created_utc >= :since"),
                      {"p": post_id, "since": since}).scalar() or 0


def erase(db, post_id, now):
    """A Space that was not recorded ended: its updates and summary go, text and all."""
    db.execute(text("""UPDATE live_updates SET body = '', body_html = '',
                       deleted_utc = CASE WHEN deleted_utc = 0 THEN :now ELSE deleted_utc END WHERE post_id = :p"""),
               {"p": post_id, "now": now})
    db.execute(text("""UPDATE live_threads SET summary = '', summary_html = '',
                       ended_utc = CASE WHEN ended_utc = 0 THEN :now ELSE ended_utc END WHERE post_id = :p"""),
               {"p": post_id, "now": now})
