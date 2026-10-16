"""Spaces in the database (rules in helpers/spaces.py): `spaces`, `space_roles`, `space_hands`, `space_reminders`,
`space_removed` and `space_reports`. Who is listening right now is kept in Redis by helpers/call_store.py under the
room name `space:<id>`, like a call.

Plain `text()` SQL with bound values only, tested on SQLite (`tests/test_spaces.py`). Nothing here commits.
"""

from sqlalchemy import bindparam, text

from ruqqus.helpers import spaces as rules

_SPACE = ("id, post_id, host_id, status, scheduled_utc, started_utc, ended_utc, record, replay_asset_id, "
          "listener_peak, ended_by_admin, matrix_room_id")


def room(space_id):
    """Its name in call_store (Redis): who is in it now, its heartbeats."""
    return f"space:{int(space_id)}"


def create(db, post_id, host_id, start_utc, record, now):
    db.execute(text("""INSERT INTO spaces (post_id, host_id, status, scheduled_utc, started_utc, ended_utc, record,
                                           listener_peak, ended_by_admin, created_utc)
                       VALUES (:post, :host, 'scheduled', :start, 0, 0, :record, 0, :no, :now)"""),
               {"post": post_id, "host": host_id, "start": start_utc, "record": bool(record), "now": now, "no": False})
    return db.execute(text("SELECT id FROM spaces WHERE post_id = :post"), {"post": post_id}).scalar()


def get(db, space_id):
    return db.execute(text(f"SELECT {_SPACE} FROM spaces WHERE id = :s"), {"s": space_id}).fetchone()


def for_posts(db, post_ids):
    """{post id: space row} for a page of posts."""
    if not post_ids:
        return {}
    query = text(f"SELECT {_SPACE} FROM spaces WHERE post_id IN :ids").bindparams(bindparam("ids", expanding=True))
    return {row[1]: row for row in db.execute(query, {"ids": list(post_ids)}).fetchall()}


def made_since(db, host_id, since):
    return db.execute(text("SELECT COUNT(*) FROM spaces WHERE host_id = :h AND created_utc >= :since"),
                      {"h": host_id, "since": since}).scalar() or 0


def start(db, space_id, now):
    """Scheduled -> live, once. False when it was not waiting to start."""
    done = db.execute(text("UPDATE spaces SET status = 'live', started_utc = :now WHERE id = :s AND status = 'scheduled'"),
                      {"s": space_id, "now": now})
    return done.rowcount == 1


def end(db, space_id, now, by_admin=False):
    """Live (or scheduled) -> ended, once. False when it had already ended."""
    done = db.execute(text("""UPDATE spaces SET status = 'ended', ended_utc = :now, ended_by_admin = :admin
                              WHERE id = :s AND status <> 'ended'"""),
                      {"s": space_id, "now": now, "admin": bool(by_admin)})
    return done.rowcount == 1


def set_room(db, space_id, room_id):
    db.execute(text("UPDATE spaces SET matrix_room_id = :room WHERE id = :s"), {"s": space_id, "room": room_id})


def note_listeners(db, space_id, count):
    db.execute(text("UPDATE spaces SET listener_peak = :n WHERE id = :s AND listener_peak < :n"), {"s": space_id, "n": count})


def set_replay(db, space_id, asset_id):
    db.execute(text("UPDATE spaces SET replay_asset_id = :a WHERE id = :s"), {"s": space_id, "a": asset_id})


# --- roles ------------------------------------------------------------------------------------------------------

def roles(db, space_id):
    """{user id: (role, status)} of the co-hosts and speakers, invited or accepted."""
    rows = db.execute(text("SELECT user_id, role, status FROM space_roles WHERE space_id = :s"), {"s": space_id}).fetchall()
    return {row[0]: (row[1], row[2]) for row in rows}


def invite_speaker(db, space_id, user_id, now):
    db.execute(text("""INSERT INTO space_roles (space_id, user_id, role, status, updated_utc)
                       VALUES (:s, :u, 'speaker', 'invited', :now)
                       ON CONFLICT (space_id, user_id) DO UPDATE SET role = 'speaker', status = 'invited', updated_utc = :now"""),
               {"s": space_id, "u": user_id, "now": now})


def accept_speaker(db, space_id, user_id, now):
    done = db.execute(text("""UPDATE space_roles SET status = 'accepted', updated_utc = :now
                              WHERE space_id = :s AND user_id = :u AND status = 'invited'"""),
                      {"s": space_id, "u": user_id, "now": now})
    return done.rowcount == 1


def set_role(db, space_id, user_id, role, now):
    db.execute(text("UPDATE space_roles SET role = :role, updated_utc = :now WHERE space_id = :s AND user_id = :u"),
               {"s": space_id, "u": user_id, "role": role, "now": now})


def drop_role(db, space_id, user_id):
    """Back to listening (or an invitation withdrawn)."""
    db.execute(text("DELETE FROM space_roles WHERE space_id = :s AND user_id = :u"), {"s": space_id, "u": user_id})


# --- hands, reminders, removals, reports -------------------------------------------------------------------------

def raise_hand(db, space_id, user_id, now):
    db.execute(text("""INSERT INTO space_hands (space_id, user_id, raised_utc) VALUES (:s, :u, :now)
                       ON CONFLICT (space_id, user_id) DO NOTHING"""), {"s": space_id, "u": user_id, "now": now})


def lower_hand(db, space_id, user_id):
    db.execute(text("DELETE FROM space_hands WHERE space_id = :s AND user_id = :u"), {"s": space_id, "u": user_id})


def hands(db, space_id):
    rows = db.execute(text("SELECT user_id FROM space_hands WHERE space_id = :s ORDER BY raised_utc, user_id"),
                      {"s": space_id}).fetchall()
    return [row[0] for row in rows]


def remind(db, space_id, user_id, now):
    db.execute(text("""INSERT INTO space_reminders (space_id, user_id, created_utc) VALUES (:s, :u, :now)
                       ON CONFLICT (space_id, user_id) DO NOTHING"""), {"s": space_id, "u": user_id, "now": now})


def forget_reminder(db, space_id, user_id):
    db.execute(text("DELETE FROM space_reminders WHERE space_id = :s AND user_id = :u"), {"s": space_id, "u": user_id})


def reminded(db, space_id, user_id):
    return db.execute(text("SELECT 1 FROM space_reminders WHERE space_id = :s AND user_id = :u"),
                      {"s": space_id, "u": user_id}).fetchone() is not None


def reminder_ids(db, space_id):
    return [row[0] for row in db.execute(text("SELECT user_id FROM space_reminders WHERE space_id = :s"), {"s": space_id})]


def remove(db, space_id, user_id, by_id, now):
    db.execute(text("""INSERT INTO space_removed (space_id, user_id, removed_by_id, created_utc) VALUES (:s, :u, :by, :now)
                       ON CONFLICT (space_id, user_id) DO NOTHING"""), {"s": space_id, "u": user_id, "by": by_id, "now": now})
    drop_role(db, space_id, user_id)
    lower_hand(db, space_id, user_id)


def removed(db, space_id, user_id):
    return db.execute(text("SELECT 1 FROM space_removed WHERE space_id = :s AND user_id = :u"),
                      {"s": space_id, "u": user_id}).fetchone() is not None


def report(db, space_id, reporter_id, reason, now):
    """False when this person had already reported this Space."""
    exists = db.execute(text("SELECT 1 FROM space_reports WHERE space_id = :s AND reporter_id = :u"),
                        {"s": space_id, "u": reporter_id}).fetchone()
    if exists:
        return False
    db.execute(text("""INSERT INTO space_reports (space_id, reporter_id, reason, created_utc, resolved_utc)
                       VALUES (:s, :u, :reason, :now, 0)"""), {"s": space_id, "u": reporter_id, "reason": reason, "now": now})
    return True


def open_reports(db, limit=50):
    """(space id, how many reports, the latest reasons) for the admins' queue, the most reported first. Never who."""
    rows = db.execute(text("""SELECT space_id, COUNT(*) AS n, MAX(created_utc) AS latest FROM space_reports
                              WHERE resolved_utc = 0 GROUP BY space_id ORDER BY n DESC, latest DESC LIMIT :n"""),
                      {"n": limit}).fetchall()
    out = []
    for space_id, n, _latest in rows:
        reasons = [r[0] for r in db.execute(text("""SELECT reason FROM space_reports WHERE space_id = :s AND resolved_utc = 0
                                                    AND reason <> '' ORDER BY created_utc DESC LIMIT 5"""), {"s": space_id})]
        out.append((space_id, n, reasons))
    return out


def resolve_reports(db, space_id, now):
    db.execute(text("UPDATE space_reports SET resolved_utc = :now WHERE space_id = :s AND resolved_utc = 0"),
               {"s": space_id, "now": now})
