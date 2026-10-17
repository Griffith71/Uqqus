"""The upkeep of guild channel rooms (helpers/guild_channels.py), one step of scripts/recomputes.py every cycle:

- takes out of a room everyone who should not be in it (`guild_channel_store.who_should_leave`: left the guild, exiled,
  unapproved, the guild went private or was banned, the channel was tightened or closed, the account is gone, or the
  room unopened for IDLE_DAYS: they come back by opening it), at most MAX_KICKS a cycle, the rest next time;
- empties a voice channel's room of anyone who is not in its call (they closed the window): the room holds only the
  people in the call;
- keeps an announcement room's writers (power level 50) to the guildmasters with the content permission who are in it,
  each room about once an hour;
- blanks the text of messages older than KEEP_TEXT_DAYS (unless a report about them is open).

The Matrix calls are passed in, so it is tested with stand-ins (tests/test_guild_channels.py).
"""
from ruqqus.helpers import guild_channel_store as store
from ruqqus.helpers import guild_channels as rules

MAX_KICKS = 300
WRITER_CYCLES = 12                 # a cycle is about 5 minutes: each announcement room is reconciled about hourly


VOICE_GRACE = 600                 # someone just in a voice channel's room may not be in its call yet


def run(db, now, kick, set_writers, mxid_for, log=None, roster_of=None):
    """Returns how many people were taken out of rooms. `roster_of(room id)`: who is in that voice channel's call now."""
    kicked = 0
    for channel in store.all_for_sync(db):
        if channel.kind in rules.ROOMLESS:
            continue
        closed = bool(channel.deleted_utc)
        board = (bool(channel.guild_private), bool(channel.guild_banned) or closed)
        leaving = store.who_should_leave(db, channel, board, now)
        if channel.kind == rules.VOICE and roster_of is not None and not closed:
            leaving = list(dict.fromkeys(leaving + store.voice_stale(db, channel.id, roster_of(channel.matrix_room_id), now - VOICE_GRACE)))
        for uid in leaving:
            if kicked >= MAX_KICKS:
                break
            try:
                kick(channel.matrix_room_id, mxid_for(uid), "No longer allowed in the channel")
            except Exception as e:                 # Matrix is slow or down: they stay on the list for the next cycle
                if log:
                    log(f"guild channel {channel.id}: not kicked: {e}")
                continue
            store.leave(db, channel.id, uid)
            kicked += 1
        if not closed and channel.kind == rules.ANNOUNCEMENT and (now // 300) % WRITER_CYCLES == channel.id % WRITER_CYCLES:
            try:
                set_writers(channel.matrix_room_id, [mxid_for(uid) for uid in store.writer_ids(db, channel.id, channel.board_id)])
            except Exception as e:
                if log:
                    log(f"guild channel {channel.id}: writers not synced: {e}")
    store.blank_old_text(db, now - rules.KEEP_TEXT_DAYS * 86400)
    return kicked
