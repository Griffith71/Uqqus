"""Spaces in the database (rules in helpers/spaces.py): `spaces`, `space_roles`, `space_hands`, `space_reminders`,
`space_removed`, `space_reports`, and the board: `space_messages` and `space_message_reports`. Who is listening right now is kept in Redis by helpers/call_store.py under the
room name `space:<id>`, like a call.

Plain `text()` SQL with bound values only, tested on SQLite (`tests/test_spaces.py`). Nothing here commits.
"""

from sqlalchemy import bindparam, text

from ruqqus.helpers import spaces as rules

# the columns of a Space row, in this order (routes/spaces.py `_Space` reads them by position)
_SPACE = ("id, post_id, host_id, status, scheduled_utc, started_utc, ended_utc, record, replay_asset_id, "
          "listener_peak, ended_by_admin, matrix_room_id, video, board_slow_seconds, kind, ends_utc, replay_link")
KIND, ENDS, REPLAY_LINK = 14, 15, 16


def room(space_id):
    """Its name in call_store (Redis): who is in it now, its heartbeats."""
    return f"space:{int(space_id)}"


def create(db, post_id, host_id, start_utc, record, now, video=False, kind=rules.CALL, ends_utc=0):
    """A new Space, waiting to start. `ends_utc` is a board's or a live thread's planned end, counted from its planned
    start (`start` moves it when it opens late)."""
    db.execute(text("""INSERT INTO spaces (post_id, host_id, status, scheduled_utc, started_utc, ended_utc, record,
                                           listener_peak, ended_by_admin, created_utc, video, board_slow_seconds,
                                           kind, ends_utc)
                       VALUES (:post, :host, 'scheduled', :start, 0, 0, :record, 0, :no, :now, :video, 0, :kind, :ends)"""),
               {"post": post_id, "host": host_id, "start": start_utc, "record": bool(record), "now": now, "no": False,
                "video": bool(video) and kind == rules.CALL, "kind": kind, "ends": int(ends_utc or 0)})
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
    """Scheduled -> live, once. A board or a live thread keeps the length it was given, from now. False when it was not
    waiting to start."""
    done = db.execute(text("""UPDATE spaces SET status = 'live', started_utc = :now,
                                  ends_utc = CASE WHEN ends_utc > 0 THEN :now + (ends_utc - scheduled_utc) ELSE 0 END
                              WHERE id = :s AND status = 'scheduled'"""),
                      {"s": space_id, "now": now})
    return done.rowcount == 1


def due(db, now, limit=100):
    """Live Spaces that have run their time (helpers/spaces.py `is_due`): a board or a live thread past its end, a call
    past MAX_SECONDS. For the background cycle (helpers/space_ending.py)."""
    return db.execute(text(f"""SELECT {_SPACE} FROM spaces WHERE status = 'live'
                               AND ((ends_utc > 0 AND ends_utc <= :now)
                                    OR (kind = 'call' AND started_utc > 0 AND started_utc < :oldest))
                               ORDER BY id LIMIT :n"""),
                      {"now": now, "oldest": now - rules.MAX_SECONDS, "n": int(limit)}).fetchall()


def end(db, space_id, now, by_admin=False):
    """Live (or scheduled) -> ended, once. False when it had already ended."""
    done = db.execute(text("""UPDATE spaces SET status = 'ended', ended_utc = :now, ended_by_admin = :admin
                              WHERE id = :s AND status <> 'ended'"""),
                      {"s": space_id, "now": now, "admin": bool(by_admin)})
    return done.rowcount == 1


def set_ends(db, post_id, ends_utc):
    """A live-thread Space's new end, when its host extends the thread from its page (routes/live_threads.py)."""
    db.execute(text("UPDATE spaces SET ends_utc = :ends WHERE post_id = :p AND status = 'live' AND ends_utc > 0"),
               {"p": post_id, "ends": int(ends_utc)})


def for_post(db, post_id):
    """The Space row whose backing row is this post id, or None."""
    return db.execute(text(f"SELECT {_SPACE} FROM spaces WHERE post_id = :p"), {"p": post_id}).fetchone()


def set_room(db, space_id, room_id):
    db.execute(text("UPDATE spaces SET matrix_room_id = :room WHERE id = :s"), {"s": space_id, "room": room_id})


def note_listeners(db, space_id, count):
    db.execute(text("UPDATE spaces SET listener_peak = :n WHERE id = :s AND listener_peak < :n"), {"s": space_id, "n": count})


def set_replay(db, space_id, asset_id, link=None):
    """The replay: an asset on the host's linked storage (the sound, served at /media/...) and, for a video replay, its
    address on the host's own video channel."""
    db.execute(text("UPDATE spaces SET replay_asset_id = :a, replay_link = :link WHERE id = :s"),
               {"s": space_id, "a": asset_id, "link": link})


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


# --- the Spaces area (routes/spaces.py `spaces_home`) --------------------------------------------------------------

LIST_MAX = 60


# what counts as a recording: a call with its replay saved; a recorded board or live thread is its own record
RECORDING = "(kind <> 'call' OR replay_asset_id IS NOT NULL OR replay_link IS NOT NULL)"


def listing(db, tab, viewer_id=None, limit=LIST_MAX, kind=None):
    """Space rows for a tab of the Spaces area, newest or soonest first. Who may see each one is the caller's (through
    its post); this only picks the rows. `live`: running now; `upcoming`: scheduled; `recordings`: ended with a replay;
    `mine`: the viewer's own, any state. `kind` narrows it to one kind of Space."""
    where = " AND kind = :kind" if kind in rules.KINDS else ""
    if tab == "live":
        sql = f"SELECT {_SPACE} FROM spaces WHERE status = 'live'{where} ORDER BY started_utc DESC LIMIT :n"
    elif tab == "upcoming":
        sql = f"SELECT {_SPACE} FROM spaces WHERE status = 'scheduled'{where} ORDER BY scheduled_utc ASC LIMIT :n"
    elif tab == "recordings":
        sql = (f"SELECT {_SPACE} FROM spaces WHERE status = 'ended' AND record = :yes AND {RECORDING}{where} "
               "ORDER BY ended_utc DESC LIMIT :n")
    elif tab == "mine":
        sql = f"SELECT {_SPACE} FROM spaces WHERE host_id = :me{where} ORDER BY created_utc DESC LIMIT :n"
    else:
        return []
    return db.execute(text(sql), {"n": int(limit), "me": viewer_id or 0, "yes": True, "kind": kind}).fetchall()


def host_listing(db, host_id, limit=LIST_MAX):
    """A profile's Spaces tab: the host's Spaces, newest first (the caller asks who may see each, through its row)."""
    return db.execute(text(f"SELECT {_SPACE} FROM spaces WHERE host_id = :h ORDER BY created_utc DESC LIMIT :n"),
                      {"h": host_id, "n": int(limit)}).fetchall()


def live_for_host(db, host_id):
    """The Space this account is hosting right now, or None (the live banner on its profile)."""
    return db.execute(text(f"""SELECT {_SPACE} FROM spaces WHERE host_id = :h AND status = 'live'
                               ORDER BY started_utc DESC LIMIT 1"""), {"h": host_id}).fetchone()


def clear_reminders(db, space_id):
    db.execute(text("DELETE FROM space_reminders WHERE space_id = :s"), {"s": space_id})


# --- the board (`space_messages`, `space_message_reports`) -----------------------------------------------------------

_MESSAGE = "id, space_id, author_id, body, word_severity, quote_id, created_utc, deleted_utc, removed_utc"
BOARD_PAGE = 100


def set_slow(db, space_id, seconds):
    db.execute(text("UPDATE spaces SET board_slow_seconds = :n WHERE id = :s"), {"s": space_id, "n": int(seconds)})


def add_message(db, space_id, author_id, body, severity, quote_id, now):
    db.execute(text("""INSERT INTO space_messages (space_id, author_id, body, word_severity, quote_id, created_utc,
                                                   deleted_utc, removed_utc)
                       VALUES (:s, :a, :body, :sev, :q, :now, 0, 0)"""),
               {"s": space_id, "a": author_id, "body": body, "sev": int(severity or 0), "q": quote_id, "now": now})
    return db.execute(text("SELECT MAX(id) FROM space_messages WHERE space_id = :s AND author_id = :a"),
                      {"s": space_id, "a": author_id}).scalar()


def message(db, message_id):
    return db.execute(text(f"SELECT {_MESSAGE} FROM space_messages WHERE id = :m"), {"m": message_id}).fetchone()


def quotable(db, space_id, message_id):
    """A message of this Space that can be quoted (not deleted or removed)."""
    row = message(db, message_id)
    return row is not None and row[1] == space_id and not row[7] and not row[8]


def messages(db, space_id, after_id=0, limit=BOARD_PAGE):
    """The board's messages: the newest page, oldest first (or, after an id, the newer ones). Never deleted or removed
    ones."""
    if after_id:
        return db.execute(text(f"""SELECT {_MESSAGE} FROM space_messages WHERE space_id = :s AND id > :after
                                   AND deleted_utc = 0 AND removed_utc = 0 ORDER BY id ASC LIMIT :n"""),
                          {"s": space_id, "after": after_id, "n": limit}).fetchall()
    rows = db.execute(text(f"""SELECT {_MESSAGE} FROM space_messages WHERE space_id = :s
                               AND deleted_utc = 0 AND removed_utc = 0 ORDER BY id DESC LIMIT :n"""),
                      {"s": space_id, "n": limit}).fetchall()
    return list(reversed(rows))


def messages_by(db, ids):
    """{id: row} for the quoted messages of a page (deleted and removed ones too: the quote says so)."""
    if not ids:
        return {}
    query = text(f"SELECT {_MESSAGE} FROM space_messages WHERE id IN :ids").bindparams(bindparam("ids", expanding=True))
    return {row[0]: row for row in db.execute(query, {"ids": list(ids)}).fetchall()}


def messages_since(db, author_id, since):
    return db.execute(text("SELECT COUNT(*) FROM space_messages WHERE author_id = :a AND created_utc >= :since"),
                      {"a": author_id, "since": since}).scalar() or 0


def delete_message(db, message_id, now):
    """Its writer takes it back: the text goes at once."""
    done = db.execute(text("""UPDATE space_messages SET deleted_utc = :now, body = '' WHERE id = :m AND deleted_utc = 0
                              AND removed_utc = 0"""), {"m": message_id, "now": now})
    return done.rowcount == 1


def remove_message(db, message_id, by_id, now):
    """The host, a co-host or an admin takes it down. The text stays only while a report about it is open."""
    done = db.execute(text("""UPDATE space_messages SET removed_utc = :now, removed_by_id = :by WHERE id = :m
                              AND deleted_utc = 0 AND removed_utc = 0"""), {"m": message_id, "by": by_id, "now": now})
    db.execute(text("""UPDATE space_messages SET body = '' WHERE id = :m AND NOT EXISTS
                       (SELECT 1 FROM space_message_reports WHERE message_id = :m AND resolved_utc = 0)"""), {"m": message_id})
    return done.rowcount == 1


def report_message(db, message_id, reporter_id, reason, now):
    """False when this person had already reported this message."""
    exists = db.execute(text("SELECT 1 FROM space_message_reports WHERE message_id = :m AND reporter_id = :u"),
                        {"m": message_id, "u": reporter_id}).fetchone()
    if exists:
        return False
    db.execute(text("""INSERT INTO space_message_reports (message_id, reporter_id, reason, created_utc, resolved_utc)
                       VALUES (:m, :u, :reason, :now, 0)"""), {"m": message_id, "u": reporter_id, "reason": reason, "now": now})
    return True


def open_message_reports(db, limit=50):
    """(message row, how many reports, the latest reasons) for the admins' queue, the most reported first. Never who."""
    rows = db.execute(text("""SELECT message_id, COUNT(*) AS n, MAX(created_utc) AS latest FROM space_message_reports
                              WHERE resolved_utc = 0 GROUP BY message_id ORDER BY n DESC, latest DESC LIMIT :n"""),
                      {"n": limit}).fetchall()
    out = []
    for message_id, n, _latest in rows:
        row = message(db, message_id)
        if row is None:
            continue
        reasons = [r[0] for r in db.execute(text("""SELECT reason FROM space_message_reports WHERE message_id = :m
                                                    AND resolved_utc = 0 AND reason <> '' ORDER BY created_utc DESC LIMIT 5"""),
                                            {"m": message_id})]
        out.append((row, n, reasons))
    return out


def resolve_message_reports(db, message_id, now):
    """Closes the reports; a removed message, or one whose Space left nothing behind, loses its text with them."""
    db.execute(text("UPDATE space_message_reports SET resolved_utc = :now WHERE message_id = :m AND resolved_utc = 0"),
               {"m": message_id, "now": now})
    db.execute(text("UPDATE space_messages SET body = '' WHERE id = :m AND removed_utc > 0"), {"m": message_id})
    db.execute(text("""DELETE FROM space_messages WHERE id = :m AND space_id IN
                       (SELECT id FROM spaces WHERE status = 'ended' AND record = :no)"""), {"m": message_id, "no": False})


def clear_board(db, space_id):
    """A Space that was not recorded ended: its messages go, except a reported one, which waits for the admins."""
    db.execute(text("""DELETE FROM space_messages WHERE space_id = :s AND id NOT IN
                       (SELECT message_id FROM space_message_reports WHERE resolved_utc = 0)"""), {"s": space_id})
