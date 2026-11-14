"""The Report button on a Log entry that shows a person's words (the Log, slice D3a; the owner's decision 5 and the plan's correction 10).

What a member deleted stays in the Log as Anonymous, and a reason is a person's words too, so the Log needs a way to take words down that should not
stay: harassment, personal details, copyrighted text, an erasure request. A signed-in member reports an entry once, with a reason of up to
`REASON_MAX` characters; **nobody is ever shown who reported**. The site's admins decide: **Purge** (the words are erased through the existing purge
path, so the Log's private and public copies are emptied too: `action_log.scrub_for`), **Redact** (the reason is hidden from everyone below staff;
level 4) or **Dismiss**. Admins only; guildmasters have no tool for deleted items.

Plain `text()` SQL, tested on SQLite (tests/test_log_reports.py). Nothing here commits."""
from sqlalchemy import text

from ruqqus.helpers import action_log

REASON_MAX = 200
PER_DAY = 20
OPEN, PURGED, REDACTED, DISMISSED = "open", "purged", "redacted", "dismissed"
DECISIONS = (PURGED, REDACTED, DISMISSED)


def parse_reason(raw):
    """One line of plain words, at most `REASON_MAX` characters (it is escaped where it is drawn)."""
    return action_log.clean_reason(raw, REASON_MAX)


def file_report(db, entry_id, reporter_id, reason, now):
    """Record a report: "ok", "again" (this person already reported this entry) or "limit" (too many today). The unique index settles a race."""
    since = int(now) - action_log.DAY
    if (db.execute(text("SELECT count(*) FROM log_reports WHERE reporter_id = :r AND created_utc >= :s"),
                   {"r": int(reporter_id), "s": since}).scalar() or 0) >= PER_DAY:
        return "limit"
    made = db.execute(text(
        "INSERT INTO log_reports (entry_id, reporter_id, reason, created_utc) VALUES (:e, :r, :why, :now) "
        "ON CONFLICT (entry_id, reporter_id) DO NOTHING"),
        {"e": int(entry_id), "r": int(reporter_id), "why": parse_reason(reason), "now": int(now)})
    return "ok" if made.rowcount else "again"


def waiting(db, limit=100):
    """The entries with an open report, most reported first: [{entry_id, reports, latest_utc, reasons: [..]}]. Never who reported."""
    rows = db.execute(text(
        "SELECT entry_id, count(*) AS reports, max(created_utc) AS latest FROM log_reports WHERE status = 'open' "
        "GROUP BY entry_id ORDER BY count(*) DESC, max(created_utc) DESC LIMIT :n"), {"n": int(limit)}).all()
    out = []
    for entry_id, reports, latest in rows:
        reasons = [r[0] for r in db.execute(text(
            "SELECT reason FROM log_reports WHERE entry_id = :e AND status = 'open' AND reason <> '' ORDER BY id LIMIT 5"), {"e": entry_id})]
        out.append({"entry_id": entry_id, "reports": reports, "latest_utc": latest, "reasons": reasons})
    return out


def waiting_count(db):
    """How many entries have an open report (for /admin)."""
    return db.execute(text("SELECT count(DISTINCT entry_id) FROM log_reports WHERE status = 'open'")).scalar() or 0


def decide(db, entry_id, status, admin_id, now):
    """Close every open report of an entry with one outcome (they are one question). Returns how many were closed; 0 when none was open (another admin
    decided it a moment ago: a conditional UPDATE, so one decision is made once)."""
    if status not in DECISIONS:
        raise ValueError(status)
    return db.execute(text(
        "UPDATE log_reports SET status = :s, decided_by = :a, decided_utc = :now WHERE entry_id = :e AND status = 'open'"),
        {"s": status, "a": int(admin_id), "now": int(now), "e": int(entry_id)}).rowcount or 0
