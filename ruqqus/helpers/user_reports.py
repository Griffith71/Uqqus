"""Reporting an account ("Report @name" in a profile's "⋯" menu), and the admins' queue (/admin/user_reports).

A report is a request to look, like a story report (helpers/story_store.py): one row per reporter per account
(`user_reports`, UNIQUE), a short reason, worked by admins of level 3 who dismiss it or act from the account's own
admin tools. Rules (pure) and plain text() SQL, tested on SQLite (tests/test_user_reports.py):
* never your own account; never a deleted account; once per person per account; at most DAILY_LIMIT a day;
* a reason is required (1 to REASON_MAX characters of plain text, drawn escaped);
* nothing ever tells the reported account who reported it, and the queue does not name reporters either.
"""
import re
import time

from sqlalchemy import text

REASON_MAX = 200
DAILY_LIMIT = 20
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def parse_reason(raw):
    """(reason, error): plain text, control characters dropped, spaces collapsed."""
    reason = re.sub(r"\s+", " ", _CONTROL.sub("", str(raw or ""))).strip()
    if not reason:
        return "", "Say what is wrong with this account."
    return reason[:REASON_MAX], None


def refusal(reporter_id, target_id, target_deleted):
    """(message, status) when a report can't be filed, else None."""
    if target_deleted:
        return "That account has been deactivated.", 410
    if reporter_id == target_id:
        return "You can't report your own account.", 409
    return None


def _now(now):
    return int(time.time()) if now is None else int(now)


def filed_today(db, reporter_id, now=None):
    return db.execute(text("SELECT COUNT(*) FROM user_reports WHERE reporter_id = :r AND created_utc > :since"),
                      {"r": reporter_id, "since": _now(now) - 86400}).scalar() or 0


def report(db, target_id, reporter_id, reason, now=None):
    """File it. False when this person had already reported this account."""
    return db.execute(text("INSERT INTO user_reports (user_id, reporter_id, reason, created_utc) "
                           "VALUES (:u, :r, :x, :t) ON CONFLICT DO NOTHING"),
                      {"u": target_id, "r": reporter_id, "x": reason, "t": _now(now)}).rowcount == 1


def open_reports(db, limit=50, reasons_each=5):
    """[{user_id, username, reports, newest, reasons: [str]}] most reported first. No reporter is named."""
    rows = db.execute(text("""
        SELECT r.user_id, u.username, COUNT(r.id) AS reports, MAX(r.created_utc) AS newest
        FROM user_reports r JOIN users u ON u.id = r.user_id
        WHERE r.resolved_utc = 0
        GROUP BY r.user_id, u.username
        ORDER BY COUNT(r.id) DESC, MAX(r.created_utc) DESC
        LIMIT :l"""), {"l": limit}).fetchall()
    out = []
    for row in rows:
        reasons = [x[0] for x in db.execute(text(
            "SELECT reason FROM user_reports WHERE user_id = :u AND resolved_utc = 0 ORDER BY id DESC LIMIT :n"),
            {"u": row.user_id, "n": reasons_each}).fetchall()]
        out.append({"user_id": row.user_id, "username": row.username, "reports": row.reports,
                    "newest": row.newest, "reasons": reasons})
    return out


def resolve(db, target_id, now=None):
    """Close every open report of this account. How many were open."""
    return db.execute(text("UPDATE user_reports SET resolved_utc = :t WHERE user_id = :u AND resolved_utc = 0"),
                      {"t": _now(now), "u": target_id}).rowcount
