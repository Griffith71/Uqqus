"""Reporting a message in a private chat (a 1:1 chat or a group), WhatsApp's way: the chats are end-to-end encrypted and
the site can't read them, so the reporter's own browser sends the reported message and the few before it, as it shows
them (the words, who wrote each and when), and the site keeps that copy for its admins (/admin/chat_reports). The site
checks through its bot that each message exists in that chat, and takes who sent it and when from the chat server, never
from the browser; **the words can't be checked** (the bot can't read them), and the admin page says so. Nobody but the
admins sees a report, and the reported person is never told who reported them. Pure rules, no I/O: the SQL is
helpers/chat_report_store.py, the routes routes/chat_reports_pages.py.
"""
import json
import re

from ruqqus.helpers.i18n_runtime import tr

REASON_MAX = 200              # what the reporter writes about it
CONTEXT_MAX = 5               # the reported message and at most four before it
BODY_MAX = 2000               # one message's words, as the browser shows them
PER_HOUR = 10                 # reports a member may send in an hour
PER_DAY = 30                  # ...and in a day
KEEP_TEXT_DAYS = 90           # the copied words are emptied this long after a report is closed (helpers/retention.py)

# Matrix ids as this site's rooms and events have them (room version 12: no server part; older ones have one)
ROOM_ID = re.compile(r"^![A-Za-z0-9_\-+/=]{8,128}(:[A-Za-z0-9.\-]{1,255}(:\d{1,5})?)?$")
EVENT_ID = re.compile(r"^\$[A-Za-z0-9_\-+/=]{8,128}(:[A-Za-z0-9.\-]{1,255}(:\d{1,5})?)?$")

OPEN, DISMISSED, ACTIONED = "open", "dismissed", "actioned"


class Refused(Exception):
    pass


def clean_reason(value):
    return " ".join(str(value or "").split())[:REASON_MAX]


def parse_context(raw, reported_event_id):
    """The messages the browser sent, as [(event_id, words)], oldest first, the reported one last. Each event once, at most
    CONTEXT_MAX, each id shaped as Matrix makes them, the words cut to BODY_MAX; the reported message must be among them
    (it is put last whatever order they came in). Anything else is refused."""
    try:
        items = json.loads(raw or "[]")
    except (TypeError, ValueError, RecursionError):
        raise Refused(tr("That report couldn't be read. Try again."))
    if not isinstance(items, list) or not items or len(items) > CONTEXT_MAX:
        raise Refused(tr("That report couldn't be read. Try again."))
    seen, out, reported = set(), [], None
    for item in items:
        if not isinstance(item, dict):
            raise Refused(tr("That report couldn't be read. Try again."))
        event_id, body = item.get("event_id"), item.get("body")
        if not isinstance(event_id, str) or not EVENT_ID.match(event_id) or event_id in seen or not isinstance(body, str):
            raise Refused(tr("That report couldn't be read. Try again."))
        seen.add(event_id)
        entry = (event_id, body[:BODY_MAX])
        if event_id == reported_event_id:
            reported = entry
        else:
            out.append(entry)
    if reported is None:
        raise Refused(tr("That report couldn't be read. Try again."))
    return out + [reported]


def checked(event, room_id):
    """What the chat server says about one event (`matrix_client.event`): (sender mxid, sent in ms), or None when it is
    not a message of that room. Only its sender and time are trusted; its words are the browser's."""
    if not isinstance(event, dict) or event.get("room_id") not in (None, room_id):
        return None
    if event.get("type") not in ("m.room.message", "m.room.encrypted"):
        return None
    sender, sent = event.get("sender"), event.get("origin_server_ts")
    if not isinstance(sender, str) or not isinstance(sent, int) or isinstance(sent, bool):
        return None
    return sender, sent


def refusal(reporter_mxid, reported_sender, recent_hour, recent_day, already):
    """None when the report may be kept, otherwise the message."""
    if reported_sender is None:
        return tr("That message isn't in this chat any more.")
    if reported_sender == reporter_mxid:
        return tr("You can't report your own message.")
    if already:
        return tr("You've already reported this message.")
    if recent_hour >= PER_HOUR or recent_day >= PER_DAY:
        return tr("You've sent a lot of reports. Try again later.")
    return None
