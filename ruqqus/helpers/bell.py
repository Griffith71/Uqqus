"""The bells (the owner's choice, 2026-10-20): finer than following.

* **An account's bell** (`follows.get_notifs`, on a follow): you are told when the account posts (as before, from
  `submit_post`), forwards or reposts something, goes live in a Space, or shares a story. Never for anything
  anonymous; something shared with circles reaches only the people in them.
* **A guild's bell** (`subscriptions.get_notifs`, on a membership): only the guildmasters' announcements, that is a post
  they herald (distinguish), a post they pin, or a new event. It no longer rings for every post in the guild.

Who is rung (`account_ring_ids`, `guild_ring_ids`): never the actor, anyone in a block with the actor either way, a
deleted or suspended account, or (account) someone who muted the actor or (guild) muted the guild or is exiled from it.
A private or nofollow account rings nobody. A private or Circle guild rings only its approved members and guildmasters.
Whether each reader may open the thing is the caller's to check beyond that (circle_guard.eligible_ids, a private
guild), and at most MAX_RINGS people are told of one thing.

**One thing rings once** (`claim`): `bell_rings` keeps (actor, kind, target), so heralding a post again, pinning it
again or forwarding it to a second guild does not ring twice. **A notice names who did it and links to it, never a
title or text**: the page it links to applies every rule (the word filter, blocks, circles) when it is opened.

Plain text() SQL, tested on SQLite (tests/test_bell.py).
"""
import time

from sqlalchemy import text

MAX_RINGS = 1000

# kinds of thing that ring (bell_rings.kind)
FORWARD = "forward"
REPOST = "repost"
COMMENT_FORWARD = "comment_forward"
COMMENT_REPOST = "comment_repost"
SPACE_LIVE = "space_live"
STORY = "story"
HERALD = "g_herald"
PIN = "g_pin"
EVENT = "g_event"
KINDS = (FORWARD, REPOST, COMMENT_FORWARD, COMMENT_REPOST, SPACE_LIVE, STORY, HERALD, PIN, EVENT)

KEEP_DAYS = 90                    # helpers/retention.py prunes bell_rings older than this

_NOT_BLOCKED = """
  AND NOT EXISTS (SELECT 1 FROM userblocks b
                  WHERE (b.user_id = r.id AND b.target_id = :actor) OR (b.user_id = :actor AND b.target_id = r.id))"""
_LIVE = " AND r.is_deleted = :no AND COALESCE(r.is_banned, 0) = 0"

_ACCOUNT = text(f"""
    SELECT r.id FROM follows f JOIN users r ON r.id = f.user_id JOIN users a ON a.id = f.target_id
    WHERE f.target_id = :actor AND f.get_notifs = :yes AND r.id <> :actor
      AND COALESCE(a.is_private, :no) = :no AND COALESCE(a.is_nofollow, :no) = :no{_LIVE}{_NOT_BLOCKED}
      AND NOT EXISTS (SELECT 1 FROM usermutes m WHERE m.user_id = r.id AND m.target_id = :actor)
    ORDER BY f.id LIMIT :cap""")

_GUILD = f"""
    SELECT r.id FROM subscriptions s JOIN users r ON r.id = s.user_id
    WHERE s.board_id = :board AND s.is_active = :yes AND s.get_notifs = :yes AND r.id <> :actor{_LIVE}{_NOT_BLOCKED}
      AND NOT EXISTS (SELECT 1 FROM boardblocks bb WHERE bb.user_id = r.id AND bb.board_id = :board)
      AND NOT EXISTS (SELECT 1 FROM bans x WHERE x.user_id = r.id AND x.board_id = :board AND x.is_active = :yes)"""
_MEMBERS_ONLY = """
      AND (EXISTS (SELECT 1 FROM contributors c WHERE c.user_id = r.id AND c.board_id = :board AND c.is_active = :yes)
           OR EXISTS (SELECT 1 FROM mods g WHERE g.user_id = r.id AND g.board_id = :board AND g.accepted = :yes
                      AND COALESCE(g.invite_rescinded, :no) = :no))"""


def _params(**extra):
    return {"yes": True, "no": False, **extra}


def account_ring_ids(db, actor_id, cap=MAX_RINGS):
    """The people who rang this account's bell and may be told of what it does."""
    return [r[0] for r in db.execute(_ACCOUNT, _params(actor=actor_id, cap=int(cap))).fetchall()]


def guild_ring_ids(db, board_id, actor_id, members_only, cap=MAX_RINGS):
    """The people who rang this guild's bell and may be told of its announcements. `members_only` for a private or
    Circle guild: only its approved members and guildmasters."""
    sql = _GUILD + (_MEMBERS_ONLY if members_only else "") + " ORDER BY s.id LIMIT :cap"
    return [r[0] for r in db.execute(text(sql), _params(board=board_id, actor=actor_id, cap=int(cap))).fetchall()]


def claim(db, actor_id, kind, target_id, now=None):
    """True the first time this (actor, kind, target) rings; False ever after (until KEEP_DAYS have passed)."""
    if kind not in KINDS:
        raise ValueError(kind)
    result = db.execute(text("""
        INSERT INTO bell_rings (actor_id, kind, target_id, created_utc) VALUES (:a, :k, :t, :n)
        ON CONFLICT (actor_id, kind, target_id) DO NOTHING"""),
        {"a": actor_id, "k": kind, "t": target_id, "n": int(now if now is not None else time.time())})
    return result.rowcount == 1


# --- ringing, from a route after its own commit ---------------------------------------------------------------------
# Best effort: the action is already saved, so a failure here is logged and rolled back, never shown as a failed action.

def ring_account(actor, kind, target_id, link, guild_name=None, only=None):
    """Tell the people who rang `actor`'s bell, once per (actor, kind, target). `only`: who of them may read the thing,
    either a set of ids (a circle's people) or a function from a list of ids to the ones that may; None when anyone who
    may see the account may."""
    from flask import g
    from ruqqus.helpers.alerts import send_to_many
    try:
        if not claim(g.db, actor.id, kind, target_id):
            g.db.rollback()
            return 0
        ids = account_ring_ids(g.db, actor.id)
        if callable(only):
            ids = list(only(ids)) if ids else []
        elif only is not None:
            ids = [i for i in ids if i in only]
        g.db.commit()
        return send_to_many(ids, account_notice(actor.username, kind, link, guild_name))
    except Exception as error:          # the action stands; only its bell failed
        g.db.rollback()
        print(f"bell: {kind} {target_id} did not ring: {error}", flush=True)
        return 0


def ring_guild(board, actor, kind, target_id, link):
    """Tell the people who rang `board`'s bell of a guildmaster's announcement, once per (guild, kind, target)."""
    from flask import g
    from ruqqus.helpers.alerts import send_to_many
    try:
        if not claim(g.db, board.id, kind, target_id):
            g.db.rollback()
            return 0
        members_only = bool(board.is_private or getattr(board, "is_circle", False))
        ids = guild_ring_ids(g.db, board.id, actor.id, members_only)
        g.db.commit()
        return send_to_many(ids, guild_notice(board.name, kind, link))
    except Exception as error:
        g.db.rollback()
        print(f"bell: +{board.name} {kind} {target_id} did not ring: {error}", flush=True)
        return 0


# --- what a notice says: who did it, and a link; never a title or text --------------------------------------------

def account_notice(username, kind, link, guild_name=None):
    who = f"@{username}"
    if kind == FORWARD:
        return f"{who} forwarded [a post]({link}) to +{guild_name}."
    if kind == COMMENT_FORWARD:
        return f"{who} forwarded [a comment]({link}) to +{guild_name}."
    if kind == REPOST:
        return f"{who} reposted [a post]({link})."
    if kind == COMMENT_REPOST:
        return f"{who} reposted [a comment]({link})."
    if kind == SPACE_LIVE:
        return f"{who} is live in [a Space]({link})."
    if kind == STORY:
        return f"{who} shared [a story]({link})."
    raise ValueError(kind)


def guild_notice(guild_name, kind, link):
    who = f"+{guild_name}"
    if kind == HERALD:
        return f"{who}: the guildmasters made [an announcement]({link})."
    if kind == PIN:
        return f"{who}: the guildmasters pinned [a post]({link})."
    if kind == EVENT:
        return f"{who} has [a new event]({link})."
    raise ValueError(kind)
