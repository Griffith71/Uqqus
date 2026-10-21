"""The SQL of two-factor backup codes (rules: `mfa_backup.py`). Plain `text()` SQL, tested on SQLite
(tests/test_mfa_backup.py). Nothing here commits; the caller's request does.

A code is spent with one conditional UPDATE (`used_utc = 0` in its WHERE), so two requests presenting the same code
at once cannot both sign in: the second changes no row.
"""
from sqlalchemy import text

from ruqqus.helpers import mfa_backup


def replace(db, user_id, codes, now):
    """Throws away every code the account had (used or not) and stores `codes`, which are shown to the member once."""
    db.execute(text("DELETE FROM mfa_backup_codes WHERE user_id = :u"), {"u": user_id})
    for code in codes:
        salt = mfa_backup.new_salt()
        db.execute(text("INSERT INTO mfa_backup_codes (user_id, code_hash, salt, created_utc, used_utc) "
                        "VALUES (:u, :h, :s, :now, 0)"),
                   {"u": user_id, "h": mfa_backup.digest(salt, code), "s": salt, "now": now})


def has_codes(db, user_id):
    """Whether the account ever made codes (a used one still counts: the older recovery code stays off)."""
    return db.execute(text("SELECT 1 FROM mfa_backup_codes WHERE user_id = :u LIMIT 1"), {"u": user_id}).first() is not None


def unused(db, user_id):
    return db.execute(text("SELECT count(*) FROM mfa_backup_codes WHERE user_id = :u AND used_utc = 0"),
                      {"u": user_id}).scalar() or 0


def spend(db, user_id, typed, now):
    """True when `typed` is one of the account's unused codes, which is then used up. Every unused row is compared
    (at most ten, constant time each), so how long this takes says nothing about which row was close."""
    code = mfa_backup.normalise(typed)
    if code is None:
        return False
    rows = db.execute(text("SELECT code_hash, salt FROM mfa_backup_codes WHERE user_id = :u AND used_utc = 0"),
                      {"u": user_id}).all()
    found = None
    for stored_hash, salt in rows:
        if mfa_backup.matches(salt, stored_hash, code):
            found = stored_hash
    if found is None:
        return False
    done = db.execute(text("UPDATE mfa_backup_codes SET used_utc = :now "
                           "WHERE user_id = :u AND code_hash = :h AND used_utc = 0"),
                      {"now": now, "u": user_id, "h": found})
    return done.rowcount == 1


def delete_all(db, user_id):
    db.execute(text("DELETE FROM mfa_backup_codes WHERE user_id = :u"), {"u": user_id})


def page_context(db, user):
    """What the Security page says about backup codes (only an account with two-factor on has any)."""
    if not user.mfa_secret:
        return {"backup_line": "", "backup_low": False, "has_backup_codes": False}
    has = has_codes(db, user.id)
    line, low = mfa_backup.status_line(unused(db, user.id), has)
    return {"backup_line": line, "backup_low": low, "has_backup_codes": has}
