"""Adding an account or a guild to a curation, from a form or from a post's menu ("Add to curation").

The rules are pure (`author_refusal`, `guild_refusal`); the inserts are plain SQL so the same statements run
on SQLite in the tests. The owner check is the route's (`_owned_curation_or_404`): only a curation's owner
changes its members.

- **An anonymous post never lets its author be added**, whoever asks (even an admin or the author): a public
  curation lists its accounts, so adding the author would publish who wrote it. The guild can still be added.
- A deleted, banned or suspended account can't be added.
- A private or Circle guild only goes into a PRIVATE curation: a public curation lists its guilds, and a
  guild's name is not public to people who cannot see the guild. A banned guild never.
"""
import time

from sqlalchemy import text

WHAT = ("author", "guild")


class MemberError(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.message = message
        self.status = status


def author_refusal(post_is_anonymous, account):
    """Why the post's author can't be added, or None. `account` is the author's User."""
    if post_is_anonymous:
        return "The author of an anonymous post can't be added."
    if account is None or account.is_deleted or account.is_suspended:
        return "That account can't be added."
    return None


def guild_refusal(is_profile_post, board, curation_is_private):
    """Why the post's guild can't be added, or None."""
    if is_profile_post:
        return "That post is on a profile, not in a guild."
    if board.is_banned:
        return "That guild can't be added."
    if (board.is_private or getattr(board, "is_circle", False)) and not curation_is_private:
        return "A private guild can only go in a private curation."
    return None


# --- the database ----------------------------------------------------------------------------------------

_HAS_USER = text("SELECT 1 FROM curation_users WHERE curation_id = :c AND target_user_id = :t")
_ADD_USER = text("INSERT INTO curation_users (curation_id, target_user_id, created_utc) VALUES (:c, :t, :now)")
_HAS_GUILD = text("SELECT 1 FROM curation_guilds WHERE curation_id = :c AND board_id = :b")
_ADD_GUILD = text("INSERT INTO curation_guilds (curation_id, board_id, created_utc) VALUES (:c, :b, :now)")


def has_user(db, curation_id, user_id):
    return db.execute(_HAS_USER, {"c": curation_id, "t": user_id}).first() is not None


def has_guild(db, curation_id, board_id):
    return db.execute(_HAS_GUILD, {"c": curation_id, "b": board_id}).first() is not None


def add_user(db, curation_id, user_id, now=None):
    """Add the account; False when it was already a member. Does not commit."""
    if has_user(db, curation_id, user_id):
        return False
    db.execute(_ADD_USER, {"c": curation_id, "t": user_id, "now": int(now if now is not None else time.time())})
    return True


def add_guild(db, curation_id, board_id, now=None):
    """Add the guild; False when it was already a member. Does not commit."""
    if has_guild(db, curation_id, board_id):
        return False
    db.execute(_ADD_GUILD, {"c": curation_id, "b": board_id, "now": int(now if now is not None else time.time())})
    return True
