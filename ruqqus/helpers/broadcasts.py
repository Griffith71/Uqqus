"""Broadcast channels: one-to-many, as Instagram's (pure rules, no I/O; the SQL is helpers/broadcast_store.py, the
routes routes/broadcasts.py, the chat page shows them under Channels).

An account owns up to MAX_PER_OWNER channels. Only the owner and up to MAX_HELPERS helpers they choose write in one;
members read it and react. A channel is a Matrix room made by the site's bot, which alone changes who is in it.

- **A public channel** is not end-to-end encrypted (the owner's choice: public live things can be moderated): anyone
  signed in may join, up to MAX_PUBLIC_MEMBERS, and reads what was written before they joined. **Its messages go
  through the site** (`POST /api/broadcasts/<id>/messages`), which rates them with the word filter, counts them
  (POSTS_PER_DAY) and refuses a suspended writer, then sends them as the writer; members' own word filter hides a
  rated message (its rating travels with it, `SEVERITY_KEY`). Anyone in it can report a message; admins remove it.
- **A channel for circles** is end-to-end encrypted, up to MAX_CIRCLE_MEMBERS, for the people in any of its circles
  right now (the posts' rule, `circle_guard.may_see`). Its writers' own browsers write it; the site can't read it.
  Someone who leaves its circles is taken out within the hour (`scripts/sync_broadcasts.py`) and on their next visit.
- **A block either way with the owner** keeps someone out ("This channel isn't available."), and ends a membership.
- **Never** a bot's, never anonymous; a member who muted the owner gets no unread count from it.
"""

from ruqqus.helpers import display_names
from ruqqus.helpers.i18n_runtime import number_text, tr, tr_lazy, trn

PUBLIC, CIRCLES = 0, 4
MAX_PER_OWNER = 3
MAX_HELPERS = 3
MAX_PUBLIC_MEMBERS = 10000
MAX_CIRCLE_MEMBERS = 500
POSTS_PER_DAY = 50
MESSAGE_MAX = 2000
REPORT_MAX = 200
SEVERITY_KEY = "org.uqqus.severity"       # a public channel message's word-filter rating, in the event's content
SITE_KEY = "org.uqqus.site"               # the site's signature on a public channel message it sent (audit M-06)
PICTURE_KEY = "org.uqqus.picture"         # a picture's address on this site (/media/...), signed with the message
REACTION_MAX = 8                          # a reaction is an emoji, never free text

OWNER, HELPER, MEMBER = "owner", "helper", "member"

UNAVAILABLE = tr_lazy("This channel isn't available.")


class Refused(ValueError):
    """Shown to the member as it is."""


def clean_name(value):
    try:
        name = display_names.clean(value, display_names.CHANNEL)
    except display_names.Refused as e:
        raise Refused(str(e))
    if not name:
        raise Refused(tr("Give the channel a name."))
    return name


def make_refusal(is_bot, owned, audience, circle_ids):
    if is_bot:
        return tr("Bots can't have channels.")
    if owned >= MAX_PER_OWNER:
        return trn("You can have at most %(num)s channel.", "You can have at most %(num)s channels.", MAX_PER_OWNER)
    if audience not in (PUBLIC, CIRCLES):
        return tr("Choose who can join.")
    if audience == CIRCLES and not circle_ids:
        return tr("Choose at least one of your circles.")
    return None


def join_refusal(deleted, blocked, may_see, size, audience):
    if deleted or blocked or not may_see:
        return UNAVAILABLE
    if size >= (MAX_CIRCLE_MEMBERS if audience == CIRCLES else MAX_PUBLIC_MEMBERS):
        return tr("This channel is full.")
    return None


def may_write(role):
    return role in (OWNER, HELPER)


def helper_refusal(actor_role, target_role, helpers, adding):
    if actor_role != OWNER:
        return tr("Only the channel's owner chooses its helpers.")
    if target_role is None:
        return tr("They need to join the channel first.")
    if target_role == OWNER:
        return tr("That's the owner.")
    if adding and target_role == HELPER:
        return tr("They're already a helper.")
    if adding and helpers >= MAX_HELPERS:
        return trn("A channel can have at most %(num)s helper.", "A channel can have at most %(num)s helpers.", MAX_HELPERS)
    return None


def clean_message(text):
    body = (text or "").strip()
    if not body:
        raise Refused(tr("Write something first."))
    if len(body) > MESSAGE_MAX:
        raise Refused(trn("A message can be at most %(num)s character.", "A message can be at most %(num)s characters.",
                           MESSAGE_MAX, num=number_text(MESSAGE_MAX)))
    return body


def post_refusal(role, suspended, posts_today):
    if not may_write(role):
        return tr("Only the channel's owner and helpers write in it.")
    if suspended:
        return tr("Your account is suspended.")
    if posts_today >= POSTS_PER_DAY:
        return trn("A channel can have at most %(num)s message a day.", "A channel can have at most %(num)s messages a day.",
                   POSTS_PER_DAY)
    return None


def site_mark(secret, room_id, body, picture=None):
    """The signature the site puts on a public channel's message when it sends it. A writer's own Matrix client can't
    make one (it needs the site's secret), so the appservice webhook removes a message without it: everything in a
    public channel is rated, counted and reportable (audit M-06). A picture's address is signed with the words, so it
    can't be swapped for another file."""
    import hashlib
    import hmac
    signed = f"broadcast:{room_id}:{body}" + (f"\npicture:{picture}" if picture else "")
    return hmac.new((secret or "").encode(), signed.encode(), hashlib.sha256).hexdigest()[:32]


def mark_ok(secret, room_id, content):
    import hmac
    body = content.get("body") if isinstance(content, dict) else None
    given = content.get(SITE_KEY) if isinstance(content, dict) else None
    picture = content.get(PICTURE_KEY) if isinstance(content, dict) else None
    if picture is not None and not isinstance(picture, str):
        return False
    return isinstance(body, str) and isinstance(given, str) and bool(secret) and \
        hmac.compare_digest(given, site_mark(secret, room_id, body, picture))


def reaction_ok(key):
    """A reaction key a member may leave in a public channel: a short emoji, no letters or digits."""
    return isinstance(key, str) and 0 < len(key) <= REACTION_MAX and not any(c.isalnum() for c in key)
