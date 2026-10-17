"""Taking someone out of a guild's channel rooms the moment they lose the right to be in them (helpers/guild_channels.py):
they left the guild, a guildmaster exiled or unapproved them. Best effort and never in the way of the change itself: a
kick that fails leaves their row, and the sync step (scripts/recomputes.py, `guild_channel_store.who_should_leave`)
takes them out within minutes. Nothing here commits.
"""
from ruqqus.helpers import guild_channel_store as store
from ruqqus.helpers import guild_role_store
import ruqqus.helpers.matrix_client as matrix
from ruqqus.__main__ import app


def remove_from_rooms(db, board_id, user_id, drop_roles=False):
    """Kick this person from every channel room of the guild they are in, and forget they were in them. `drop_roles`: they
    left the guild or were exiled, so their roles in it go too (unapproving someone leaves them in the guild)."""
    if drop_roles:
        try:
            with db.begin_nested():
                guild_role_store.drop_roles(db, board_id, user_id)
        except Exception:
            app.logger.warning("guild roles: could not drop a person's roles", exc_info=True)
    try:
        with db.begin_nested():
            rooms = store.rooms_in_board(db, board_id, user_id)
    except Exception:
        app.logger.warning("guild channels: could not list rooms", exc_info=True)
        return
    for cid, room in rooms:
        try:
            matrix.kick_as_bot(room, matrix.mxid_for(user_id), "No longer allowed in the guild's channels")
            with db.begin_nested():
                store.leave(db, cid, user_id)
        except Exception:
            app.logger.warning(f"guild channel {cid}: not kicked yet", exc_info=True)
