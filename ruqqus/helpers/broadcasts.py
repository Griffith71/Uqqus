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

PUBLIC, CIRCLES = 0, 4
MAX_PER_OWNER = 3
MAX_HELPERS = 3
MAX_PUBLIC_MEMBERS = 10000
MAX_CIRCLE_MEMBERS = 500
POSTS_PER_DAY = 50
MESSAGE_MAX = 2000
REPORT_MAX = 200
SEVERITY_KEY = "org.uqqus.severity"       # a public channel message's word-filter rating, in the event's content

OWNER, HELPER, MEMBER = "owner", "helper", "member"

UNAVAILABLE = "This channel isn't available."


class Refused(ValueError):
    """Shown to the member as it is."""


def clean_name(value):
    try:
        name = display_names.clean(value)
    except display_names.Refused as e:
        raise Refused(str(e).replace("A display name", "A channel name"))
    if not name:
        raise Refused("Give the channel a name.")
    return name


def make_refusal(is_bot, owned, audience, circle_ids):
    if is_bot:
        return "Bots can't have channels."
    if owned >= MAX_PER_OWNER:
        return f"You can have at most {MAX_PER_OWNER} channels."
    if audience not in (PUBLIC, CIRCLES):
        return "Choose who can join."
    if audience == CIRCLES and not circle_ids:
        return "Choose at least one of your circles."
    return None


def join_refusal(deleted, blocked, may_see, size, audience):
    if deleted or blocked or not may_see:
        return UNAVAILABLE
    if size >= (MAX_CIRCLE_MEMBERS if audience == CIRCLES else MAX_PUBLIC_MEMBERS):
        return "This channel is full."
    return None


def may_write(role):
    return role in (OWNER, HELPER)


def helper_refusal(actor_role, target_role, helpers, adding):
    if actor_role != OWNER:
        return "Only the channel's owner chooses its helpers."
    if target_role is None:
        return "They need to join the channel first."
    if target_role == OWNER:
        return "That's the owner."
    if adding and target_role == HELPER:
        return "They're already a helper."
    if adding and helpers >= MAX_HELPERS:
        return f"A channel can have at most {MAX_HELPERS} helpers."
    return None


def clean_message(text):
    body = (text or "").strip()
    if not body:
        raise Refused("Write something first.")
    if len(body) > MESSAGE_MAX:
        raise Refused(f"A message can be at most {MESSAGE_MAX:,} characters.")
    return body


def post_refusal(role, suspended, posts_today):
    if not may_write(role):
        return "Only the channel's owner and helpers write in it."
    if suspended:
        return "Your account is suspended."
    if posts_today >= POSTS_PER_DAY:
        return f"A channel can have at most {POSTS_PER_DAY} messages a day."
    return None
