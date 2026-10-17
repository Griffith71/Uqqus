"""Voice and video calls in a chat, 1:1 or a group (pure rules, no I/O; who is in a call is kept by
helpers/call_store.py, the routes are routes/calls.py, the call window is assets/chat_src/src/call.js).

There is no media server (the owner's choice, 2026-10-16): every browser sends its audio and video to every other
one, always through the site's relay (helpers/turn_usage.py), so nobody learns anyone's IP address. That is why a
call is small: each person uploads one stream per other person.

- **Who may call**: in a 1:1 chat, only once both have it in their Inbox (a request can't be called), never with a
  block either way, never a bot; in a group, its joined members. The rules are the chat's own, asked again at every
  join, so leaving a group or a block ends your part in its calls.
- **How many**: AUDIO_MAX people; with video, the first VIDEO_MAX people send video and anyone after them joins with
  audio only (`video_allowed`), as does everyone once the relay's month is at 80% (turn_usage.AUDIO_ONLY).
- **Signalling is end-to-end encrypted**: the browsers exchange their offers over Olm-encrypted Matrix to-device
  messages and only accept them from the devices the server lists in the call (`call_store.roster`), so a device that
  is not in the call can't listen in, and neither the relay nor the server can stand in the middle.
- **Who is in a call** is known to the server (heartbeats every HEARTBEAT_SECONDS, gone after HEARTBEAT_TTL), never
  what is said. A call lasts at most MAX_CALL_SECONDS; a 1:1 call rings for RING_SECONDS.
"""

AUDIO, VIDEO = "audio", "video"
KINDS = (AUDIO, VIDEO)

AUDIO_MAX = 8
VIDEO_MAX = 4
RING_SECONDS = 45
HEARTBEAT_SECONDS = 10
HEARTBEAT_TTL = 30
MAX_CALL_SECONDS = 4 * 3600
STARTS_PER_HOUR = 20

SIGNAL_TYPE = "org.uqqus.call.signal"       # the to-device event the browsers exchange

NOT_ALLOWED = "You can't call in this chat."
REQUEST_FIRST = "You can call once they've accepted your message request."
FULL = f"This call is full ({AUDIO_MAX} people)."
VOICE_FULL = f"This voice channel is full ({AUDIO_MAX} people)."
TOO_MANY_STARTS = "You've started a lot of calls in the last hour. Try again later."


def parse_kind(value):
    return VIDEO if value == VIDEO else AUDIO


def pair_refusal(status, blocked, either_is_bot, other_deleted):
    """A 1:1 chat: None when the two may call each other. `status` is the conversation's ('request' or 'inbox')."""
    if either_is_bot or other_deleted or blocked:
        return NOT_ALLOWED
    if status != "inbox":
        return REQUEST_FIRST
    return None


def join_refusal(already_in, roster_size, starting, starts_last_hour):
    """None when this device may join a call that has `roster_size` other people in it now."""
    if already_in:
        return None
    if starting and starts_last_hour >= STARTS_PER_HOUR:
        return TOO_MANY_STARTS
    if roster_size >= AUDIO_MAX:
        return FULL
    return None


def video_allowed(kind, senders_of_video, relay_open):
    """Whether this device may send video: it asked, fewer than VIDEO_MAX others already do, the relay allows it."""
    return kind == VIDEO and relay_open and senders_of_video < VIDEO_MAX


def duration_label(seconds):
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours} h {minutes} min"
    if minutes:
        return f"{minutes} min"
    return f"{secs} s"


def summary(kind, seconds):
    """The line the last person to leave writes into the chat (call.js), e.g. "Video call · 12 min"."""
    return f"{'Video call' if kind == VIDEO else 'Voice call'} · {duration_label(seconds)}"
