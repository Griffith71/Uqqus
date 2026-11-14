"""The database side of History's Deleted list (the Log plan's slice D3c): what a member deleted themselves, from their own private copies. Plain
`text()` SQL, tested on SQLite (tests/test_mass_edit.py). Nothing here commits, and nothing here names anyone but the member whose id is asked."""
from sqlalchemy import text

PER_PAGE = 25


def deleted_by(db, user_id, page=1):
    """`(rows, next_exists)`: the member's own deleted posts and comments, newest first, from the copies kept for them: the `delete` copy that stays
    in the public Log as Anonymous (it has no end) and the `grace` copy of an erase or a child's delete (kept 90 days). Only text still kept: an admin's
    scrub, a purge and the 90 days empty the copy, and an emptied one isn't listed. A legal hold isn't theirs to read."""
    page = max(int(page or 1), 1)
    rows = db.execute(text(
        "SELECT id, kind, entry_id, title, url, body, created_utc, expires_utc, target_submission_id, target_comment_id FROM log_snapshots "
        "WHERE author_id = :u AND kind IN ('delete', 'grace') AND scrubbed_utc = 0 AND NOT legal_hold "
        "AND (target_submission_id IS NOT NULL OR target_comment_id IS NOT NULL) "
        "ORDER BY created_utc DESC, id DESC LIMIT :limit OFFSET :offset"),
        {"u": int(user_id), "limit": PER_PAGE + 1, "offset": PER_PAGE * (page - 1)}).fetchall()
    wanted = [dict(r._mapping) for r in rows]
    return wanted[:PER_PAGE], len(wanted) > PER_PAGE
