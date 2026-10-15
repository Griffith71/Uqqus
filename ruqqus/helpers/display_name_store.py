"""The database side of display names (the rules are helpers/display_names.py): plain SQL, tested on SQLite."""
from sqlalchemy import text

from ruqqus.helpers import display_names


def takes_a_handle(db, user_id, name):
    """Is `name`, ignoring case, spaces, dots, dashes and underscores, another account's @handle (or the handle that
    account started with)? Nobody can wear someone else's handle as a display name. `user_id` is the account setting
    the name: its own handle is fine (None for an account that doesn't exist yet)."""
    wanted = display_names.key(name)
    if not wanted:
        return False
    row = db.execute(text(
        "SELECT 1 FROM users WHERE id != :me AND (lower(replace(username, '_', '')) = :k "
        "OR lower(replace(original_username, '_', '')) = :k) LIMIT 1"), {"me": user_id if user_id is not None else -1, "k": wanted}).fetchone()
    return row is not None
