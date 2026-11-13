"""What a push notification says and who is sent one (pure, no I/O; the sending is helpers/push_send.py).

**A notification never carries a message's words**: chats are end-to-end encrypted and the site can't read them, and
a lock screen is seen by anyone holding the phone. It says "New message from <name>" or, when the member chose
(`users.push_names` off), just "New message", and opens the chat. In the member's own language.

Who is told: the other person of a 1:1 chat (a message request too), and a group's joined members by their own choice
for that group (`chat_group_members.push_mode`: every message, calls only, nothing); never the writer, never anyone in
a block with them either way or who muted them. A call rings the other person of a 1:1 chat, and tells a group's
members unless they chose nothing. Anonymity doesn't arise: chats have no anonymous messages.
"""
from ruqqus.helpers.i18n_runtime import tr

EVERY, CALLS, NOTHING = 0, 1, 2
MODES = (EVERY, CALLS, NOTHING)

MESSAGE, REQUEST, CALL, GROUP_CALL = "message", "request", "call", "group_call"


def mode(value):
    """A group's push choice from a form: one of MODES, or None."""
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if value in MODES else None


def told_in_group(push_mode, kind):
    """Whether a group member with this choice is told of this kind of thing."""
    if push_mode == NOTHING:
        return False
    if push_mode == CALLS:
        return kind == GROUP_CALL
    return True


def wording(kind, name, show_names, video=False):
    """(title, body) in the current language. `name` is how the sender is shown to this reader (or None, unknown)."""
    named = bool(show_names and name)
    if kind == REQUEST:
        body = tr("New message request from %(name)s", name=name) if named else tr("New message request")
    elif kind in (CALL, GROUP_CALL):
        if video:
            body = tr("Incoming video call from %(name)s", name=name) if named else tr("Incoming video call")
        else:
            body = tr("Incoming call from %(name)s", name=name) if named else tr("Incoming call")
    else:
        body = tr("New message from %(name)s", name=name) if named else tr("New message")
    return body


def payload(kind, body, site_name, room_id):
    """What the browser's service worker is handed (assets/js/sw.js): the words to show, where a tap goes, and a tag so a
    second message from the same chat replaces the first instead of piling up."""
    return {"title": site_name, "body": body, "url": f"/chat?room={room_id}" if room_id else "/chat",
            "tag": f"chat:{room_id}" if room_id else "chat", "call": kind in (CALL, GROUP_CALL)}
