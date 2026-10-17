"""Guild channels in the database (rules in helpers/guild_channels.py): `guild_channels`, `guild_channel_reads` (a row
for each person who opened a channel: who is in its Matrix room, and when they last read it), `guild_channel_posts`
(messages, kept for reports, the daily count and removals; their text is blanked after KEEP_TEXT_DAYS) and
`guild_channel_reports`. A guild's chat mute is the old `chatbans` table.

Plain `text()` SQL with bound values only, tested on SQLite (`tests/test_guild_channels.py`). Every function that
changes a channel takes the guild's id, so a channel of another guild is never reached. Nothing here commits.

The Redis half (slow mode, the burst limit, a signature's nonce) takes the Redis client as an argument, so it is tested
with tests/fake_redis.py.
"""
import secrets
import time

from sqlalchemy import bindparam, text

from ruqqus.helpers import guild_channels as rules

_COLUMNS = ("c.id, c.board_id, c.name, c.topic, c.rules, c.word_severity, c.kind, c.read_level, c.write_level, "
            "c.slow_mode_seconds, c.position, c.matrix_room_id, c.last_activity_utc, c.created_utc, c.deleted_utc, "
            "c.category_id, c.rules_gate, c.activity_seq, c.space_post_id, c.encrypted")
_PLAIN = _COLUMNS.replace("c.", "")

# the viewer's own standing in the channel's guild, worked out by the database in the same query
_STANDING = """
    EXISTS (SELECT 1 FROM subscriptions s WHERE s.user_id = :u AND s.board_id = c.board_id AND s.is_active) AS joined,
    EXISTS (SELECT 1 FROM contributors k WHERE k.user_id = :u AND k.board_id = c.board_id AND k.is_active) AS contributor,
    EXISTS (SELECT 1 FROM mods m WHERE m.user_id = :u AND m.board_id = c.board_id AND m.accepted
            AND m.invite_rescinded IS NOT TRUE) AS mod,
    EXISTS (SELECT 1 FROM mods m WHERE m.user_id = :u AND m.board_id = c.board_id AND m.accepted
            AND m.invite_rescinded IS NOT TRUE AND (m.perm_content OR m.perm_full)) AS content_mod,
    EXISTS (SELECT 1 FROM bans x WHERE x.user_id = :u AND x.board_id = c.board_id AND x.is_active) AS exiled,
    EXISTS (SELECT 1 FROM chatbans z WHERE z.user_id = :u AND z.board_id = c.board_id) AS muted,
    EXISTS (SELECT 1 FROM guild_channel_overwrites o JOIN guild_role_members rm ON rm.role_id = o.role_id
            WHERE o.channel_id = c.id AND rm.user_id = :u AND (o.read_ow = 1 OR o.write_ow = 1)) AS role_allow_read,
    EXISTS (SELECT 1 FROM guild_channel_overwrites o JOIN guild_role_members rm ON rm.role_id = o.role_id
            WHERE o.channel_id = c.id AND rm.user_id = :u AND o.read_ow = 2) AS role_deny_read,
    EXISTS (SELECT 1 FROM guild_channel_overwrites o JOIN guild_role_members rm ON rm.role_id = o.role_id
            WHERE o.channel_id = c.id AND rm.user_id = :u AND o.write_ow = 1) AS role_allow_write,
    EXISTS (SELECT 1 FROM guild_channel_overwrites o JOIN guild_role_members rm ON rm.role_id = o.role_id
            WHERE o.channel_id = c.id AND rm.user_id = :u AND o.write_ow = 2) AS role_deny_write,
    EXISTS (SELECT 1 FROM guild_role_members rm JOIN guild_roles ro ON ro.id = rm.role_id
            WHERE rm.user_id = :u AND ro.board_id = c.board_id AND ro.perm_slow_exempt) AS role_slow_exempt,
    EXISTS (SELECT 1 FROM guild_role_members rm JOIN guild_roles ro ON ro.id = rm.role_id
            WHERE rm.user_id = :u AND ro.board_id = c.board_id AND ro.perm_remove_messages) AS role_remove,
    EXISTS (SELECT 1 FROM guild_role_members rm JOIN guild_roles ro ON ro.id = rm.role_id
            WHERE rm.user_id = :u AND ro.board_id = c.board_id AND ro.perm_pin) AS role_pin,
    r.joined_utc AS in_room_since, r.last_read_utc AS last_read_utc, r.last_read_seq AS last_read_seq, b.name AS board_name,
    b.is_private AS is_private,
    COALESCE(pf.muted_until, 0) AS muted_until, ak.rules_key AS acked_key,
    (SELECT COUNT(*) FROM guild_channel_mentions mn WHERE mn.channel_id = c.id AND mn.user_id = :u AND mn.read_utc = 0) AS mentions,
    cat.name AS category_name, cat.word_severity AS category_severity, cat.position AS category_position
"""
_FROM_USER = """FROM guild_channels c JOIN boards b ON b.id = c.board_id
                LEFT JOIN guild_channel_reads r ON r.channel_id = c.id AND r.user_id = :u
                LEFT JOIN guild_channel_prefs pf ON pf.channel_id = c.id AND pf.user_id = :u
                LEFT JOIN guild_channel_rules_ack ak ON ak.channel_id = c.id AND ak.user_id = :u
                LEFT JOIN guild_channel_categories cat ON cat.id = c.category_id"""


def _now():
    return int(time.time())


# --- channels -----------------------------------------------------------------------------------------------------------

def create(db, board_id, kind, name, topic, channel_rules, severity, read_level, write_level, slow, room_id, now,
           category_id=None, rules_gate=False, space_post_id=None, encrypted=False):
    position = db.execute(text("SELECT COALESCE(MAX(position), 0) + 1 FROM guild_channels WHERE board_id = :b AND deleted_utc = 0"),
                          {"b": board_id}).scalar()
    db.execute(text("""INSERT INTO guild_channels (board_id, name, topic, rules, word_severity, kind, read_level, write_level,
                                                  slow_mode_seconds, position, matrix_room_id, last_activity_utc, created_utc, deleted_utc,
                                                  category_id, rules_gate, space_post_id, encrypted)
                       VALUES (:b, :name, :topic, :rules, :sev, :kind, :read, :write, :slow, :pos, :room, :now, :now, 0, :cat, :gate, :space, :enc)"""),
               {"b": board_id, "name": name, "topic": topic, "rules": channel_rules, "sev": severity, "kind": kind,
                "read": read_level, "write": write_level, "slow": slow, "pos": position, "room": room_id, "now": now,
                "cat": category_id, "gate": bool(rules_gate), "space": space_post_id, "enc": bool(encrypted)})
    return db.execute(text("SELECT id FROM guild_channels WHERE matrix_room_id = :r"), {"r": room_id}).scalar()


def get(db, cid):
    return db.execute(text(f"SELECT {_PLAIN} FROM guild_channels WHERE id = :c AND deleted_utc = 0"), {"c": cid}).fetchone()


def get_in_board(db, board_id, cid):
    return db.execute(text(f"SELECT {_PLAIN} FROM guild_channels WHERE id = :c AND board_id = :b AND deleted_utc = 0"),
                      {"c": cid, "b": board_id}).fetchone()


def by_room(db, room_id):
    return db.execute(text(f"SELECT {_PLAIN} FROM guild_channels WHERE matrix_room_id = :r AND deleted_utc = 0"),
                      {"r": room_id}).fetchone()


def all_for_sync(db):
    """Every channel with its guild's (is_private, is_banned), closed ones that still have people in their rooms
    included: the sync step empties them."""
    return db.execute(text(f"""SELECT {_COLUMNS}, b.is_private AS guild_private, b.is_banned AS guild_banned
                              FROM guild_channels c JOIN boards b ON b.id = c.board_id
                              WHERE c.deleted_utc = 0 OR EXISTS (SELECT 1 FROM guild_channel_reads r WHERE r.channel_id = c.id)
                              ORDER BY c.id""")).fetchall()


def of_board(db, board_id):
    return db.execute(text(f"SELECT {_PLAIN} FROM guild_channels WHERE board_id = :b AND deleted_utc = 0 ORDER BY position, id"),
                      {"b": board_id}).fetchall()


def count(db, board_id):
    return db.execute(text("SELECT COUNT(*) FROM guild_channels WHERE board_id = :b AND deleted_utc = 0"), {"b": board_id}).scalar() or 0


def name_taken(db, board_id, name, except_id=0):
    """Names are unique per guild ignoring case (compared here: SQLite's lower() is ASCII only)."""
    wanted = name.casefold()
    return any(row[1].casefold() == wanted for row in
               db.execute(text("SELECT id, name FROM guild_channels WHERE board_id = :b AND deleted_utc = 0 AND id <> :x"),
                          {"b": board_id, "x": except_id or 0}))


def update(db, board_id, cid, name, topic, channel_rules, severity, read_level, write_level, slow, category_id=None,
           rules_gate=False, space_post_id=None):
    db.execute(text("""UPDATE guild_channels SET name = :name, topic = :topic, rules = :rules, word_severity = :sev,
                              read_level = :read, write_level = :write, slow_mode_seconds = :slow,
                              category_id = :cat, rules_gate = :gate, space_post_id = :space
                       WHERE id = :c AND board_id = :b AND deleted_utc = 0"""),
               {"c": cid, "b": board_id, "name": name, "topic": topic, "rules": channel_rules, "sev": severity,
                "read": read_level, "write": write_level, "slow": slow, "cat": category_id, "gate": bool(rules_gate),
                "space": space_post_id})


def close(db, board_id, cid, now):
    db.execute(text("UPDATE guild_channels SET deleted_utc = :now WHERE id = :c AND board_id = :b AND deleted_utc = 0"),
               {"c": cid, "b": board_id, "now": now})


def close_board(db, board_id, now):
    db.execute(text("UPDATE guild_channels SET deleted_utc = :now WHERE board_id = :b AND deleted_utc = 0"), {"b": board_id, "now": now})


def move(db, board_id, ordered_ids):
    """Positions 1.. in this order; ids that are not this guild's channels are ignored."""
    for index, cid in enumerate(ordered_ids, start=1):
        db.execute(text("UPDATE guild_channels SET position = :p WHERE id = :c AND board_id = :b AND deleted_utc = 0"),
                   {"p": index, "c": cid, "b": board_id})


def touch(db, room_id, now, sender_id=None):
    """A message in this room: one more on the channel's count, however many people are in the guild; its writer has
    read it. False when it is not a channel's room."""
    done = bool(db.execute(text("""UPDATE guild_channels SET last_activity_utc = :now, activity_seq = activity_seq + 1
                                   WHERE matrix_room_id = :r AND deleted_utc = 0"""), {"r": room_id, "now": now}).rowcount)
    if done and sender_id:
        db.execute(text("""UPDATE guild_channel_reads SET last_read_utc = :now,
                                  last_read_seq = (SELECT c.activity_seq FROM guild_channels c WHERE c.id = guild_channel_reads.channel_id)
                           WHERE user_id = :u AND channel_id IN (SELECT id FROM guild_channels WHERE matrix_room_id = :r)"""),
                   {"now": now, "u": sender_id, "r": room_id})
    return done


def rescan_severities(db, rate):
    """Re-rates every channel's name, topic and rules with `rate(text)` (after the word list changed)."""
    for cid, name, topic, channel_rules in db.execute(text("SELECT id, name, topic, rules FROM guild_channels")).fetchall():
        db.execute(text("UPDATE guild_channels SET word_severity = :s WHERE id = :c"),
                   {"s": rate(f"{name} {topic} {channel_rules}"), "c": cid})


# --- the viewer's channels ---------------------------------------------------------------------------------------------

def of_board_for(db, board_id, user_id):
    """The guild's channels with the viewer's standing and place: (channel columns..., joined, contributor, mod,
    content_mod, exiled, muted, in_room_since, last_read_utc, board_name, is_private)."""
    return db.execute(text(f"SELECT {_COLUMNS}, {_STANDING} {_FROM_USER} WHERE c.board_id = :b AND c.deleted_utc = 0 "
                           "ORDER BY COALESCE(cat.position, 0), c.position, c.id"), {"b": board_id, "u": user_id}).fetchall()


def one_for(db, cid, user_id):
    return db.execute(text(f"SELECT {_COLUMNS}, {_STANDING} {_FROM_USER} WHERE c.id = :c AND c.deleted_utc = 0"),
                      {"c": cid, "u": user_id}).fetchone()


def joined_guilds_for(db, user_id, limit=400):
    """Channels of the guilds this person is in (joined, approved or a guildmaster), a guild at a time: the list in
    the chat page. The rules decide which of them they may read."""
    return db.execute(text(f"""SELECT {_COLUMNS}, {_STANDING} {_FROM_USER}
                              WHERE c.deleted_utc = 0 AND b.is_banned IS NOT TRUE
                                AND (EXISTS (SELECT 1 FROM subscriptions s WHERE s.user_id = :u AND s.board_id = c.board_id AND s.is_active)
                                  OR EXISTS (SELECT 1 FROM contributors k WHERE k.user_id = :u AND k.board_id = c.board_id AND k.is_active)
                                  OR EXISTS (SELECT 1 FROM mods m WHERE m.user_id = :u AND m.board_id = c.board_id AND m.accepted
                                             AND m.invite_rescinded IS NOT TRUE))
                              ORDER BY b.name, COALESCE(cat.position, 0), c.position, c.id LIMIT :n"""), {"u": user_id, "n": limit}).fetchall()


def unread(row, now=None):
    """Has this person not read everything written in the channel? Counted in messages (`activity_seq` against what they
    last read), never in seconds. Never opened: unread once anyone has written. A channel they muted (`now` given) is
    never unread."""
    if now is not None and rules.is_muted(row.muted_until, now):
        return False
    seen = (row.last_read_seq or 0) if row.in_room_since is not None else 0
    return (row.activity_seq or 0) > seen


# --- who is in a channel's room -----------------------------------------------------------------------------------------

def join(db, cid, user_id, now):
    db.execute(text("""INSERT INTO guild_channel_reads (channel_id, user_id, joined_utc, last_read_utc, last_read_seq)
                       SELECT :c, :u, :now, :now, activity_seq FROM guild_channels WHERE id = :c
                       ON CONFLICT (channel_id, user_id) DO NOTHING"""),
               {"c": cid, "u": user_id, "now": now})


def in_room(db, cid, user_id):
    return db.execute(text("SELECT 1 FROM guild_channel_reads WHERE channel_id = :c AND user_id = :u"),
                      {"c": cid, "u": user_id}).fetchone() is not None


def leave(db, cid, user_id):
    db.execute(text("DELETE FROM guild_channel_reads WHERE channel_id = :c AND user_id = :u"), {"c": cid, "u": user_id})


def rooms_in_board(db, board_id, user_id):
    """[(channel id, Matrix room id)] of the guild's channels this person is in."""
    return [(row[0], row[1]) for row in db.execute(text("""SELECT c.id, c.matrix_room_id FROM guild_channel_reads r
                                                           JOIN guild_channels c ON c.id = r.channel_id
                                                           WHERE c.board_id = :b AND r.user_id = :u"""), {"b": board_id, "u": user_id})]


def leave_board(db, board_id, user_id):
    """Out of every channel of a guild (the person left it, was exiled from it or unapproved): the rows of their rooms."""
    ids = [row[0] for row in db.execute(text("""SELECT r.channel_id FROM guild_channel_reads r JOIN guild_channels c ON c.id = r.channel_id
                                                WHERE c.board_id = :b AND r.user_id = :u"""), {"b": board_id, "u": user_id})]
    db.execute(text("""DELETE FROM guild_channel_reads WHERE user_id = :u
                       AND channel_id IN (SELECT id FROM guild_channels WHERE board_id = :b)"""), {"b": board_id, "u": user_id})
    return ids


def mark_read(db, cid, user_id, now):
    db.execute(text("""UPDATE guild_channel_reads SET last_read_utc = :now,
                              last_read_seq = (SELECT activity_seq FROM guild_channels WHERE id = :c)
                       WHERE channel_id = :c AND user_id = :u"""), {"c": cid, "u": user_id, "now": now})


def size(db, cid):
    return db.execute(text("SELECT COUNT(*) FROM guild_channel_reads WHERE channel_id = :c"), {"c": cid}).scalar() or 0


def readers(db, cid, limit=20000):
    return [row[0] for row in db.execute(text("SELECT user_id FROM guild_channel_reads WHERE channel_id = :c LIMIT :n"),
                                         {"c": cid, "n": limit})]


def writer_ids(db, cid, board_id):
    """Who an announcement room lets write (power level 50): the guildmasters with the content permission, and the
    people in a role that is allowed to write in this channel, who are in the channel's room."""
    ids = [row[0] for row in db.execute(text("""SELECT m.user_id FROM mods m JOIN guild_channel_reads r
                                                ON r.user_id = m.user_id AND r.channel_id = :c
                                                WHERE m.board_id = :b AND m.accepted AND m.invite_rescinded IS NOT TRUE
                                                  AND (m.perm_content OR m.perm_full)"""), {"c": cid, "b": board_id})]
    for (uid,) in db.execute(text("""SELECT DISTINCT rm.user_id FROM guild_channel_overwrites o
                                     JOIN guild_role_members rm ON rm.role_id = o.role_id
                                     JOIN guild_channel_reads r ON r.user_id = rm.user_id AND r.channel_id = o.channel_id
                                     WHERE o.channel_id = :c AND o.write_ow = 1"""), {"c": cid}):
        if uid not in ids:
            ids.append(uid)
    for (uid,) in db.execute(text("""SELECT f.bot_user_id FROM guild_channel_feeds f JOIN guild_channel_reads r
                                     ON r.user_id = f.bot_user_id AND r.channel_id = f.channel_id WHERE f.channel_id = :c"""), {"c": cid}):
        if uid not in ids:
            ids.append(uid)
    return ids


def idle_readers(db, cid, before):
    return [row[0] for row in db.execute(text("SELECT user_id FROM guild_channel_reads WHERE channel_id = :c AND last_read_utc < :t"),
                                         {"c": cid, "t": before})]


def _chunks(items, size=1000):
    items = list(items)
    for start in range(0, len(items), size):
        yield items[start:start + size]


def voice_stale(db, cid, in_call_ids, before):
    """People in a voice channel's room who are not in its call and joined before `before`: they left without being
    taken out (a closed window). A voice room holds only the people in the call, so the devices it tells browsers about
    stay few."""
    present = set(in_call_ids)
    return [row[0] for row in db.execute(text("SELECT user_id FROM guild_channel_reads WHERE channel_id = :c AND joined_utc < :t"),
                                         {"c": cid, "t": before}) if row[0] not in present]


def standing_sets(db, board_id, user_ids):
    """The standing of these people in a guild, for the sync step: sets of user ids (a thousand at a time, so a big
    guild never makes a huge query)."""
    queries = {
        "joined": "SELECT user_id FROM subscriptions WHERE board_id = :b AND is_active AND user_id IN :ids",
        "contributor": "SELECT user_id FROM contributors WHERE board_id = :b AND is_active AND user_id IN :ids",
        "mod": "SELECT user_id FROM mods WHERE board_id = :b AND accepted AND invite_rescinded IS NOT TRUE AND user_id IN :ids",
        "content_mod": "SELECT user_id FROM mods WHERE board_id = :b AND accepted AND invite_rescinded IS NOT TRUE "
                       "AND (perm_content OR perm_full) AND user_id IN :ids",
        "exiled": "SELECT user_id FROM bans WHERE board_id = :b AND is_active AND user_id IN :ids",
        "muted": "SELECT user_id FROM chatbans WHERE board_id = :b AND user_id IN :ids",
    }
    out = {name: set() for name in queries}
    for chunk in _chunks(user_ids):
        for name, sql in queries.items():
            query = text(sql).bindparams(bindparam("ids", expanding=True))
            out[name].update(row[0] for row in db.execute(query, {"b": board_id, "ids": chunk}))
    return out


def accounts(db, user_ids):
    """{id: (is_bot, gone, admin_level)} for these accounts; gone: deleted or banned."""
    out = {}
    for chunk in _chunks(user_ids):
        query = text("SELECT id, is_bot, is_deleted, is_banned, admin_level FROM users WHERE id IN :ids").bindparams(
            bindparam("ids", expanding=True))
        out.update({row[0]: (bool(row[1]), bool(row[2]) or bool(row[3]), row[4] or 0) for row in db.execute(query, {"ids": chunk})})
    return out


def who_should_leave(db, channel, board, now):
    """The people in a channel's room who should not be: no longer allowed to read it (left the guild, exiled,
    unapproved, the guild went private, the channel was tightened, the account is gone) or unopened for IDLE_DAYS.
    `board`: (is_private, is_banned). Returns user ids."""
    in_room = readers(db, channel.id)
    if not in_room:
        return []
    if board[1]:
        return in_room
    stand = standing_sets(db, channel.board_id, in_room)
    people = accounts(db, in_room)
    from ruqqus.helpers import guild_role_store, guild_roles
    role_words = guild_role_store.overwrites_for_users(db, channel.id, in_room)
    idle = set(idle_readers(db, channel.id, now - rules.IDLE_DAYS * 86400))
    out = []
    for uid in in_room:
        bot, gone, admin_level = people.get(uid, (False, True, 0))
        if gone or uid in idle:
            out.append(uid)
            continue
        contributor, mod = uid in stand["contributor"], uid in stand["mod"]
        refusal = rules.read_refusal(
            channel.read_level, can_view=rules.guild_visible(bool(board[0]), contributor, mod, admin_level >= 4), hidden=False,
            bot=bot, exiled=uid in stand["exiled"], joined=uid in stand["joined"], contributor=contributor, mod=mod,
            admin=admin_level >= 4, role_read=guild_roles.resolve(*role_words.get(uid, (False, False, False, False))[:2]))
        if refusal:
            out.append(uid)
        elif channel.encrypted and uid in stand["muted"] and uid not in stand["mod"]:
            out.append(uid)             # the site can't stop a muted person writing in an encrypted room, so they are not in it
    return out


# --- messages ------------------------------------------------------------------------------------------------------------

def record_post(db, cid, author_id, event_id, body, severity, now, thread_event_id=""):
    db.execute(text("""INSERT INTO guild_channel_posts (channel_id, author_id, event_id, body, word_severity, created_utc, removed_utc,
                                                       thread_event_id)
                       VALUES (:c, :a, :e, :body, :sev, :now, 0, :thread) ON CONFLICT (channel_id, event_id) DO NOTHING"""),
               {"c": cid, "a": author_id, "e": event_id, "body": body, "sev": severity, "now": now, "thread": thread_event_id})
    return db.execute(text("SELECT id FROM guild_channel_posts WHERE channel_id = :c AND event_id = :e"),
                      {"c": cid, "e": event_id}).scalar()


def posts_since(db, cid, author_id, since):
    return db.execute(text("SELECT COUNT(*) FROM guild_channel_posts WHERE channel_id = :c AND author_id = :a AND created_utc >= :t"),
                      {"c": cid, "a": author_id, "t": since}).scalar() or 0


def post_by_event(db, cid, event_id):
    """(id, author_id, body, removed_utc, thread_event_id, word_severity) of one message, or None."""
    return db.execute(text("""SELECT id, author_id, body, removed_utc, thread_event_id, word_severity FROM guild_channel_posts
                              WHERE channel_id = :c AND event_id = :e"""), {"c": cid, "e": event_id}).fetchone()


def remove_post(db, cid, event_id, now):
    """The message is gone (its writer took it back, or a guildmaster removed it): its text goes with it."""
    db.execute(text("""UPDATE guild_channel_posts SET removed_utc = :now, body = ''
                       WHERE channel_id = :c AND event_id = :e AND removed_utc = 0"""), {"c": cid, "e": event_id, "now": now})
    db.execute(text("""DELETE FROM guild_channel_pins WHERE post_id IN
                       (SELECT id FROM guild_channel_posts WHERE channel_id = :c AND event_id = :e)"""), {"c": cid, "e": event_id})
    db.execute(text("""DELETE FROM guild_channel_mentions WHERE post_id IN
                       (SELECT id FROM guild_channel_posts WHERE channel_id = :c AND event_id = :e)"""), {"c": cid, "e": event_id})


def authors_among(db, cid, user_ids):
    """Which of these accounts have written in this channel (so a lookup of names reaches nobody else)."""
    found = set()
    for chunk in _chunks(user_ids):
        query = text("SELECT DISTINCT author_id FROM guild_channel_posts WHERE channel_id = :c AND author_id IN :ids").bindparams(
            bindparam("ids", expanding=True))
        found.update(row[0] for row in db.execute(query, {"c": cid, "ids": chunk}))
    return found


def members_among(db, cid, user_ids):
    """Which of these accounts are in the channel's room (an encrypted channel keeps no messages, so this is who may be
    named in it)."""
    found = set()
    for chunk in _chunks(user_ids):
        query = text("SELECT user_id FROM guild_channel_reads WHERE channel_id = :c AND user_id IN :ids").bindparams(
            bindparam("ids", expanding=True))
        found.update(row[0] for row in db.execute(query, {"c": cid, "ids": chunk}))
    return found


def encrypted_rooms_of(db, board_id, user_id):
    """[(channel id, room id)] of the guild's encrypted channels this person is in."""
    return [(row[0], row[1]) for row in db.execute(text("""SELECT c.id, c.matrix_room_id FROM guild_channel_reads r
                                                           JOIN guild_channels c ON c.id = r.channel_id
                                                           WHERE c.board_id = :b AND r.user_id = :u AND c.encrypted"""), {"b": board_id, "u": user_id})]


def blank_old_text(db, before):
    """Messages older than this keep no text, unless a report about them is still open."""
    db.execute(text("""UPDATE guild_channel_posts SET body = '' WHERE created_utc < :t AND body <> ''
                       AND id NOT IN (SELECT post_id FROM guild_channel_reports WHERE resolved_utc = 0)
                       AND id NOT IN (SELECT post_id FROM guild_channel_pins)"""), {"t": before})


# --- reports ---------------------------------------------------------------------------------------------------------------

def report(db, cid, post_id, reporter_id, reason, now):
    """False when this person had already reported this message."""
    if db.execute(text("SELECT 1 FROM guild_channel_reports WHERE post_id = :p AND reporter_id = :u"),
                  {"p": post_id, "u": reporter_id}).fetchone():
        return False
    db.execute(text("""INSERT INTO guild_channel_reports (channel_id, post_id, reporter_id, reason, created_utc, resolved_utc)
                       VALUES (:c, :p, :u, :reason, :now, 0)"""), {"c": cid, "p": post_id, "u": reporter_id, "reason": reason, "now": now})
    return True


def open_reports(db, board_id, limit=50):
    """(channel id, channel name, message id, its text, event id, how many reports, the latest reasons), the most
    reported first. Never who reported."""
    rows = db.execute(text("""SELECT c.id, c.name, p.id, p.body, p.event_id, COUNT(*) AS n, MAX(r.created_utc) AS latest
                              FROM guild_channel_reports r JOIN guild_channel_posts p ON p.id = r.post_id
                              JOIN guild_channels c ON c.id = r.channel_id
                              WHERE c.board_id = :b AND r.resolved_utc = 0 AND p.removed_utc = 0
                              GROUP BY c.id, c.name, p.id, p.body, p.event_id ORDER BY n DESC, latest DESC LIMIT :n"""),
                      {"b": board_id, "n": limit}).fetchall()
    out = []
    for cid, name, pid, body, event_id, n, _latest in rows:
        reasons = [r[0] for r in db.execute(text("""SELECT reason FROM guild_channel_reports WHERE post_id = :p AND resolved_utc = 0
                                                    AND reason <> '' ORDER BY created_utc DESC LIMIT 5"""), {"p": pid})]
        out.append((cid, name, pid, body, event_id, n, reasons))
    return out


def resolve_reports(db, post_id, now):
    db.execute(text("UPDATE guild_channel_reports SET resolved_utc = :now WHERE post_id = :p AND resolved_utc = 0"),
               {"p": post_id, "now": now})


def post_in_board(db, board_id, post_id):
    """(post id, channel id, event id, author id, removed_utc) when the message is in one of this guild's channels."""
    return db.execute(text("""SELECT p.id, p.channel_id, p.event_id, p.author_id, p.removed_utc FROM guild_channel_posts p
                              JOIN guild_channels c ON c.id = p.channel_id WHERE p.id = :p AND c.board_id = :b"""),
                      {"p": post_id, "b": board_id}).fetchone()


# --- a guild-wide chat mute (the old `chatbans` table) ---------------------------------------------------------------------

def muted_ids(db, board_id):
    return [row[0] for row in db.execute(text("SELECT user_id FROM chatbans WHERE board_id = :b ORDER BY id"), {"b": board_id})]


def is_muted(db, board_id, user_id):
    return db.execute(text("SELECT 1 FROM chatbans WHERE board_id = :b AND user_id = :u"), {"b": board_id, "u": user_id}).fetchone() is not None


def mute(db, board_id, user_id, mod_id, now):
    """False when they were already muted."""
    if is_muted(db, board_id, user_id):
        return False
    db.execute(text("INSERT INTO chatbans (user_id, board_id, created_utc, banning_mod_id) VALUES (:u, :b, :now, :m)"),
               {"u": user_id, "b": board_id, "now": now, "m": mod_id})
    return True


def unmute(db, board_id, user_id):
    return bool(db.execute(text("DELETE FROM chatbans WHERE board_id = :b AND user_id = :u"), {"b": board_id, "u": user_id}).rowcount)


# --- Redis: slow mode, the burst limit and a signature's one use ---------------------------------------------------------------

def slow_wait(r, cid, user_id, seconds):
    """0 when this person may write now (and slow mode starts), else the seconds left."""
    if not seconds:
        return 0
    key = f"gc:slow:{cid}:{user_id}"
    if r.set(key, "1", nx=True, ex=int(seconds)):
        return 0
    try:
        left = int(r.ttl(key))
    except (TypeError, ValueError):
        left = 0
    return left if left > 0 else 1


def slow_release(r, cid, user_id):
    """The message was not sent after all: slow mode does not start."""
    try:
        r.delete(f"gc:slow:{cid}:{user_id}")
    except Exception:
        pass


def burst_count(r, cid, user_id, now):
    """This person's messages in this channel this minute, counting this one."""
    key = f"gc:burst:{cid}:{user_id}:{int(now) // 60}"
    n = int(r.incr(key))
    if n == 1:
        r.expire(key, 90)
    return n


def new_nonce(r):
    """A one-time number the site puts in a message it signs: held until the webhook sees the message."""
    nonce = secrets.token_hex(16)
    r.set(f"gc:n:{nonce}", "", ex=rules.NONCE_SECONDS)
    return nonce


def claim_nonce(r, nonce, event_id):
    """Is this message the first (or the same one delivered again) to use its nonce? A nonce the site never made, one
    that ran out, or one a different message already used is not: that message is a copy. The claim is one set-if-absent,
    so two copies arriving together cannot both win."""
    if r.get(f"gc:n:{nonce}") is None:
        return False
    used = f"gc:u:{nonce}"
    if r.set(used, event_id, nx=True, ex=rules.NONCE_SECONDS):
        return True
    return r.get(used) == event_id


# --- categories --------------------------------------------------------------------------------------------------------------

_CATEGORY = "id, board_id, name, word_severity, position"


def categories_of(db, board_id):
    return db.execute(text(f"SELECT {_CATEGORY} FROM guild_channel_categories WHERE board_id = :b ORDER BY position, id"),
                      {"b": board_id}).fetchall()


def category(db, board_id, category_id):
    if not category_id:
        return None
    return db.execute(text(f"SELECT {_CATEGORY} FROM guild_channel_categories WHERE id = :c AND board_id = :b"),
                      {"c": category_id, "b": board_id}).fetchone()


def category_count(db, board_id):
    return db.execute(text("SELECT COUNT(*) FROM guild_channel_categories WHERE board_id = :b"), {"b": board_id}).scalar() or 0


def category_taken(db, board_id, name, except_id=0):
    wanted = name.casefold()
    return any(row[1].casefold() == wanted for row in
               db.execute(text("SELECT id, name FROM guild_channel_categories WHERE board_id = :b AND id <> :x"),
                          {"b": board_id, "x": except_id or 0}))


def make_category(db, board_id, name, severity):
    position = db.execute(text("SELECT COALESCE(MAX(position), 0) + 1 FROM guild_channel_categories WHERE board_id = :b"),
                          {"b": board_id}).scalar()
    db.execute(text("INSERT INTO guild_channel_categories (board_id, name, word_severity, position) VALUES (:b, :n, :s, :p)"),
               {"b": board_id, "n": name, "s": severity, "p": position})
    return db.execute(text("SELECT MAX(id) FROM guild_channel_categories WHERE board_id = :b"), {"b": board_id}).scalar()


def rename_category(db, board_id, category_id, name, severity):
    db.execute(text("UPDATE guild_channel_categories SET name = :n, word_severity = :s WHERE id = :c AND board_id = :b"),
               {"n": name, "s": severity, "c": category_id, "b": board_id})


def delete_category(db, board_id, category_id):
    """Its channels stay, without a category."""
    db.execute(text("UPDATE guild_channels SET category_id = NULL WHERE category_id = :c AND board_id = :b"),
               {"c": category_id, "b": board_id})
    db.execute(text("DELETE FROM guild_channel_categories WHERE id = :c AND board_id = :b"), {"c": category_id, "b": board_id})


def move_categories(db, board_id, ordered_ids):
    for index, category_id in enumerate(ordered_ids, start=1):
        db.execute(text("UPDATE guild_channel_categories SET position = :p WHERE id = :c AND board_id = :b"),
                   {"p": index, "c": category_id, "b": board_id})


def rescan_category_severities(db, rate):
    for category_id, name in db.execute(text("SELECT id, name FROM guild_channel_categories")).fetchall():
        db.execute(text("UPDATE guild_channel_categories SET word_severity = :s WHERE id = :c"), {"s": rate(name), "c": category_id})


# --- pins -------------------------------------------------------------------------------------------------------------------

def pin_count(db, cid):
    return db.execute(text("SELECT COUNT(*) FROM guild_channel_pins WHERE channel_id = :c"), {"c": cid}).scalar() or 0


def is_pinned(db, cid, post_id):
    return db.execute(text("SELECT 1 FROM guild_channel_pins WHERE channel_id = :c AND post_id = :p"),
                      {"c": cid, "p": post_id}).fetchone() is not None


def pin(db, cid, post_id, by, now):
    db.execute(text("""INSERT INTO guild_channel_pins (channel_id, post_id, pinned_by, created_utc) VALUES (:c, :p, :by, :now)
                       ON CONFLICT (channel_id, post_id) DO NOTHING"""), {"c": cid, "p": post_id, "by": by, "now": now})


def unpin(db, cid, post_id):
    return bool(db.execute(text("DELETE FROM guild_channel_pins WHERE channel_id = :c AND post_id = :p"),
                           {"c": cid, "p": post_id}).rowcount)


def pins_of(db, cid):
    """(event id, writer, text, rating, written, pinned) of the pinned messages, the newest pin first. A removed message
    is not pinned any more (remove_post takes its pin)."""
    return db.execute(text("""SELECT p.event_id, p.author_id, p.body, p.word_severity, p.created_utc, n.created_utc
                              FROM guild_channel_pins n JOIN guild_channel_posts p ON p.id = n.post_id
                              WHERE n.channel_id = :c AND p.removed_utc = 0 ORDER BY n.created_utc DESC, p.id DESC"""),
                      {"c": cid}).fetchall()


# --- a member's own mute, the rules they accepted ------------------------------------------------------------------------------

def set_mute(db, cid, user_id, until):
    db.execute(text("""INSERT INTO guild_channel_prefs (channel_id, user_id, muted_until) VALUES (:c, :u, :t)
                       ON CONFLICT (channel_id, user_id) DO UPDATE SET muted_until = :t"""), {"c": cid, "u": user_id, "t": until})


def muted_until(db, cid, user_id):
    return db.execute(text("SELECT muted_until FROM guild_channel_prefs WHERE channel_id = :c AND user_id = :u"),
                      {"c": cid, "u": user_id}).scalar() or 0


def accept_rules(db, cid, user_id, key, now):
    db.execute(text("""INSERT INTO guild_channel_rules_ack (channel_id, user_id, rules_key, created_utc) VALUES (:c, :u, :k, :now)
                       ON CONFLICT (channel_id, user_id) DO UPDATE SET rules_key = :k, created_utc = :now"""),
               {"c": cid, "u": user_id, "k": key, "now": now})


# --- mentions -----------------------------------------------------------------------------------------------------------------

def record_mentions(db, cid, post_id, user_ids, now):
    for uid in user_ids:
        db.execute(text("INSERT INTO guild_channel_mentions (channel_id, post_id, user_id, created_utc, read_utc) VALUES (:c, :p, :u, :now, 0)"),
                   {"c": cid, "p": post_id, "u": uid, "now": now})


def clear_mentions(db, cid, user_id, now):
    db.execute(text("UPDATE guild_channel_mentions SET read_utc = :now WHERE channel_id = :c AND user_id = :u AND read_utc = 0"),
               {"c": cid, "u": user_id, "now": now})


def notice_due(r, cid, writer_id, user_id):
    """One mention notification per writer, person and channel an hour (Redis set-if-absent). True when this one may go."""
    try:
        return bool(r.set(f"gc:mention:{cid}:{writer_id}:{user_id}", "1", nx=True, ex=rules.MENTION_NOTICE_SECONDS))
    except Exception:
        return False


# --- the navbar ---------------------------------------------------------------------------------------------------------------

def unread_total(db, user_id, now):
    """What the navbar's chat badge adds for guild channels: every channel the person has opened that has news (a muted
    one has none), and their unread mentions. A count of channels and mentions, never the messages."""
    channels = db.execute(text("""SELECT COUNT(*) FROM guild_channel_reads r JOIN guild_channels c ON c.id = r.channel_id
                                  LEFT JOIN guild_channel_prefs pf ON pf.channel_id = c.id AND pf.user_id = r.user_id
                                  WHERE r.user_id = :u AND c.deleted_utc = 0 AND c.activity_seq > r.last_read_seq
                                    AND COALESCE(pf.muted_until, 0) <= :now"""), {"u": user_id, "now": now}).scalar() or 0
    mentions = db.execute(text("""SELECT COUNT(*) FROM guild_channel_mentions m JOIN guild_channels c ON c.id = m.channel_id
                                  WHERE m.user_id = :u AND m.read_utc = 0 AND c.deleted_utc = 0"""), {"u": user_id}).scalar() or 0
    return int(channels) + int(mentions)


# --- search -------------------------------------------------------------------------------------------------------------------

def search(db, cid, terms, since, limit=rules.SEARCH_MAX):
    """(event id, writer, text, rating, written) of messages in the channel with every term in them, newest first, among
    those whose text is still kept (KEEP_TEXT_DAYS). Who may see what is the route's."""
    if not terms:
        return []
    clauses = " AND ".join(f"LOWER(body) LIKE :t{i} ESCAPE '\\'" for i in range(len(terms)))
    params = {f"t{i}": rules.like_pattern(term) for i, term in enumerate(terms)}
    params.update({"c": cid, "since": since, "n": limit})
    return db.execute(text(f"""SELECT event_id, author_id, body, word_severity, created_utc FROM guild_channel_posts
                               WHERE channel_id = :c AND removed_utc = 0 AND body <> '' AND created_utc >= :since AND {clauses}
                               ORDER BY created_utc DESC, id DESC LIMIT :n"""), params).fetchall()


# --- a guild's auto-moderation --------------------------------------------------------------------------------------------------------

def automod_of(db, board_id):
    """The guild's settings row (min_account_days, min_member_days, link_member_days, blocked_words) or None."""
    return db.execute(text("""SELECT board_id, min_account_days, min_member_days, link_member_days, blocked_words
                              FROM guild_chat_settings WHERE board_id = :b"""), {"b": board_id}).fetchone()


def save_automod(db, board_id, account_days, member_days, link_days, words):
    db.execute(text("""INSERT INTO guild_chat_settings (board_id, min_account_days, min_member_days, link_member_days, blocked_words)
                       VALUES (:b, :a, :m, :l, :w)
                       ON CONFLICT (board_id) DO UPDATE SET min_account_days = :a, min_member_days = :m, link_member_days = :l,
                                                            blocked_words = :w"""),
               {"b": board_id, "a": account_days, "m": member_days, "l": link_days, "w": words})


def member_since(db, board_id, user_id):
    """When this person came into the guild (joined it or was approved, whichever is first), or None."""
    row = db.execute(text("""SELECT MIN(t) FROM (
                               SELECT created_utc AS t FROM subscriptions WHERE user_id = :u AND board_id = :b AND is_active
                               UNION ALL SELECT created_utc AS t FROM contributors WHERE user_id = :u AND board_id = :b AND is_active
                             ) x"""), {"u": user_id, "b": board_id}).scalar()
    return row


# --- bot accounts feeding an announcement channel -------------------------------------------------------------------------------------

def feeds_of(db, cid):
    """[(bot user id, who added it, when, the last post id sent)] of a channel."""
    return db.execute(text("SELECT bot_user_id, added_by, created_utc, last_post_id FROM guild_channel_feeds WHERE channel_id = :c ORDER BY created_utc, bot_user_id"),
                      {"c": cid}).fetchall()


def add_feed(db, cid, bot_id, by, now, last_post_id):
    """False when the bot already feeds this channel."""
    if db.execute(text("SELECT 1 FROM guild_channel_feeds WHERE channel_id = :c AND bot_user_id = :b"), {"c": cid, "b": bot_id}).fetchone():
        return False
    db.execute(text("""INSERT INTO guild_channel_feeds (channel_id, bot_user_id, added_by, created_utc, last_post_id)
                       VALUES (:c, :b, :by, :now, :last)"""), {"c": cid, "b": bot_id, "by": by, "now": now, "last": last_post_id})
    return True


def remove_feed(db, cid, bot_id):
    return bool(db.execute(text("DELETE FROM guild_channel_feeds WHERE channel_id = :c AND bot_user_id = :b"), {"c": cid, "b": bot_id}).rowcount)


def all_feeds(db):
    """Every feed with its channel: (channel id, room id, board id, bot user id, last post id), of channels still open."""
    return db.execute(text("""SELECT f.channel_id, c.matrix_room_id, c.board_id, f.bot_user_id, f.last_post_id
                              FROM guild_channel_feeds f JOIN guild_channels c ON c.id = f.channel_id
                              WHERE c.deleted_utc = 0 AND c.kind = 'announcement' ORDER BY f.channel_id, f.bot_user_id""")).fetchall()


def set_feed_cursor(db, cid, bot_id, post_id):
    db.execute(text("UPDATE guild_channel_feeds SET last_post_id = :p WHERE channel_id = :c AND bot_user_id = :b AND last_post_id < :p"),
               {"p": post_id, "c": cid, "b": bot_id})


def latest_bot_post(db, bot_id):
    """The newest public post the account has made (0 for none): a new feed starts after it, so it doesn't flood the
    channel with history."""
    return db.execute(text("SELECT COALESCE(MAX(id), 0) FROM submissions WHERE author_id = :b"), {"b": bot_id}).scalar() or 0


def bot_posts_after(db, bot_id, after, limit):
    """(post id, title, link) of the bot's public, live, original posts after `after`, oldest first."""
    return db.execute(text("""SELECT s.id, a.title, a.url FROM submissions s JOIN submissions_aux a ON a.id = s.id
                              WHERE s.author_id = :b AND s.id > :after AND s.deleted_utc = 0 AND s.is_banned IS NOT TRUE
                                AND s.post_public AND COALESCE(s.repost_id, 0) = 0 ORDER BY s.id LIMIT :n"""),
                      {"b": bot_id, "after": after, "n": limit}).fetchall()
