"""The database side of display names (the rules are helpers/display_names.py): plain SQL, tested on SQLite."""
from sqlalchemy import text

from ruqqus.helpers import display_names


def takes_a_handle(db, user_id, name):
    """Does `name` look like another account's @handle (or the handle that account started with)? Compared by
    `display_names.skeleton`: case, separators, accents, other scripts' look-alike letters and 0/o, 1/l/i, rn/m, vv/w
    don't tell them apart (audit I-04: "аudit_friend" with a Cyrillic а used to pass). Nobody can wear someone else's
    handle as a display name. `user_id` is the account setting the name: its own handle is fine (None for an account
    that doesn't exist yet)."""
    wanted = display_names.skeleton(name)
    if not wanted:
        return False
    # the same look-alike swaps, done in SQL on the ASCII handles (nested replace works on Postgres and SQLite)
    def look(column):
        expr = f"lower(replace({column}, '_', ''))"
        for a, b in display_names._ASCII_LOOKALIKES:
            expr = f"replace({expr}, '{a}', '{b}')"
        return expr
    row = db.execute(text(
        f"SELECT 1 FROM users WHERE id != :me AND ({look('username')} = :k OR {look('original_username')} = :k) LIMIT 1"),
        {"me": user_id if user_id is not None else -1, "k": wanted}).fetchone()
    return row is not None
