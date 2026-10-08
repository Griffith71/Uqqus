"""How many guilds an account may lead.

An account may lead at most PER_PERSON guilds, and the accounts that share a network (an IP address)
may lead at most PER_IP guilds between them, so a second account on the same connection is not a way
round the first limit. Both limits only stop a NEW guild (creating, accepting an invitation, taking
one by siege); nobody is made to step down, and an account that already leads more keeps its guilds.
Site admins are exempt.

An account's addresses are the one it signed up from (`users.creation_ip`) and the ones it logged in
from in the last WINDOW (`login_events.ip`). The accounts "on" an address are every account that
signed up from it or logged in from it in that window. Banned and deleted accounts do not count, and
neither do banned guilds or siege-protected ones (the same guilds `User.can_join_gms` already skips).

The person's own count is the cheap one that every page already pays for (`User.boards_modded`); the
address count is a query, so it is only asked at the places that add a guild. A message never says
which other accounts share an address.

The rules (`refusal`, `say`) are pure; `check` is the one function that reads the database.
"""
import time

from sqlalchemy import bindparam, text

PER_PERSON = 5
PER_IP = 5
WINDOW = 90 * 24 * 60 * 60
ADMIN_LEVEL = 3          # the bar the site uses for "admins may do this"

PERSON = "person"
IP = "ip"


def refusal(led_by_person, led_on_ip, admin_level=0):
    """Why an account may not lead another guild: PERSON, IP, or None."""
    if admin_level >= ADMIN_LEVEL:
        return None
    if led_by_person >= PER_PERSON:
        return PERSON
    if led_on_ip >= PER_IP:
        return IP
    return None


def say(kind, username=None):
    """The message for a refusal, to the account itself or (with a username) about another one."""
    if username:
        return f"@{username} can't lead another guild right now."
    if kind == PERSON:
        return f"You already lead {PER_PERSON} guilds. Step down from one before you lead another."
    return (f"Too many guilds are already led from your network (at most {PER_IP}). "
            "You can't lead another one right now.")


_IPS = text("""
    SELECT creation_ip AS ip FROM users WHERE id = :user
    UNION
    SELECT ip FROM login_events WHERE user_id = :user AND created_utc >= :since
""")

_LED_ON = text("""
    SELECT COUNT(DISTINCT m.board_id) FROM mods m
    JOIN boards b ON b.id = m.board_id
    JOIN users u ON u.id = m.user_id
    WHERE m.accepted = :yes AND COALESCE(m.invite_rescinded, :no) = :no
      AND COALESCE(b.is_banned, :no) = :no AND COALESCE(b.is_siegable, :yes) = :yes
      AND COALESCE(u.is_deleted, :no) = :no
      AND NOT (COALESCE(u.is_banned, 0) <> 0 AND COALESCE(u.unban_utc, 0) = 0)
      AND m.user_id IN (
          SELECT x.id FROM users x WHERE x.creation_ip IN :ips
          UNION
          SELECT e.user_id FROM login_events e WHERE e.ip IN :ips AND e.created_utc >= :since
      )
""").bindparams(bindparam("ips", expanding=True))


def addresses_of(db, user_id, now=None):
    """The addresses an account signed up or logged in from within the window."""
    since = int(now or time.time()) - WINDOW
    rows = db.execute(_IPS, {"user": user_id, "since": since}).fetchall()
    return sorted({row[0] for row in rows if row[0]})


def led_on(db, ips, now=None):
    """How many different guilds are led by accounts on any of these addresses."""
    if not ips:
        return 0
    since = int(now or time.time()) - WINDOW
    return int(db.execute(_LED_ON, {"ips": list(ips), "since": since, "yes": True, "no": False}).scalar() or 0)


def check(db, user, now=None):
    """None if `user` may lead another guild, else PERSON or IP. Admins are never asked."""
    if user.admin_level >= ADMIN_LEVEL:
        return None
    by_person = len([b for b in user.boards_modded if b.is_siegable])
    kind = refusal(by_person, 0, user.admin_level)
    if kind:
        return kind
    return refusal(by_person, led_on(db, addresses_of(db, user.id, now), now), user.admin_level)
