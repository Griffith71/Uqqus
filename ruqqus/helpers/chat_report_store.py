"""Reports of messages in private chats, in the database (rules in helpers/chat_reports.py): `chat_reports`, one per
reporter and reported message, and `chat_report_messages`, the copy the reporter's browser sent (the reported message
and the few before it), each with who sent it and when as the chat server said. Plain `text()` SQL with bound values,
tested on SQLite. Nothing here commits."""

from sqlalchemy import text

_REPORT = ("id, reporter_id, room_id, room_kind, event_id, reported_id, reason, created_utc, status, resolved_utc, "
           "resolved_by_id")


def already(db, reporter_id, event_id):
    return db.execute(text("SELECT 1 FROM chat_reports WHERE reporter_id = :u AND event_id = :e"),
                      {"u": reporter_id, "e": event_id}).first() is not None


def sent_since(db, reporter_id, since):
    return db.execute(text("SELECT COUNT(*) FROM chat_reports WHERE reporter_id = :u AND created_utc >= :s"),
                      {"u": reporter_id, "s": since}).scalar() or 0


def create(db, reporter_id, room_id, room_kind, event_id, reported_id, reason, messages, now):
    """`messages`: [(event_id, sender_id or None, sender mxid, sent_ms, words)], oldest first, the reported one last."""
    rid = db.execute(text("""INSERT INTO chat_reports (reporter_id, room_id, room_kind, event_id, reported_id, reason, created_utc,
                                                       status, resolved_utc, resolved_by_id)
                             VALUES (:u, :r, :k, :e, :t, :why, :now, 'open', 0, NULL) RETURNING id"""),
                     {"u": reporter_id, "r": room_id, "k": room_kind, "e": event_id, "t": reported_id, "why": reason,
                      "now": now}).scalar()
    for position, (eid, sender_id, sender_mxid, sent_ms, words) in enumerate(messages):
        db.execute(text("""INSERT INTO chat_report_messages (report_id, position, event_id, sender_id, sender_mxid, sent_ms, body)
                           VALUES (:r, :p, :e, :s, :m, :t, :b)"""),
                   {"r": rid, "p": position, "e": eid, "s": sender_id, "m": sender_mxid, "t": sent_ms, "b": words})
    return rid


def report(db, rid):
    return db.execute(text(f"SELECT {_REPORT} FROM chat_reports WHERE id = :r"), {"r": rid}).fetchone()


def open_reports(db, limit=100):
    return db.execute(text(f"SELECT {_REPORT} FROM chat_reports WHERE status = 'open' ORDER BY created_utc LIMIT :n"),
                      {"n": limit}).fetchall()


def messages_of(db, rid):
    return db.execute(text("""SELECT position, event_id, sender_id, sender_mxid, sent_ms, body FROM chat_report_messages
                              WHERE report_id = :r ORDER BY position"""), {"r": rid}).fetchall()


def resolve(db, rid, status, admin_id, now):
    """Closed once: True when this call closed it."""
    done = db.execute(text("""UPDATE chat_reports SET status = :s, resolved_utc = :now, resolved_by_id = :a
                              WHERE id = :r AND status = 'open'"""), {"r": rid, "s": status, "a": admin_id, "now": now})
    return done.rowcount == 1

