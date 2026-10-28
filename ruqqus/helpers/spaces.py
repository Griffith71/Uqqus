"""Spaces (pure rules, no I/O; the SQL is helpers/space_store.py, the routes routes/spaces.py, the page
assets/chat_src/src/space.js).

**A Space is not a post** (the owner, 2026-10-20): it is made only in the Spaces area (`/spaces/new`), lives at its own
address (`/@host/space/<id>`), is listed under a profile's Spaces tab and never its Posts, and is drawn as its own card.
It has **most of what a post has**: votes, ranking in feeds, shares, filters, mutes, blocks, reports and an audience
(Public or circles). Those rules are spread over hundreds of places, so a Space keeps a hidden backing row in
`submissions` (`is_space`) that takes part in all of them unchanged; that row is never shown as a post.

**A Space is exactly one of three kinds** (KINDS), chosen when it is made and never changed. What takes the place of
comments depends on the kind:

- **board**: a public message board for a set time (`ends_utc`, LENGTHS). Anyone who may open it writes on it.
- **live**: a live thread, as on Reddit: the host writes timestamped updates and a pinned summary (the live-thread
  machinery, helpers/live_thread_store.py, keyed by its backing row) and everyone comments under them (the ordinary
  comments of the backing row), for a set time.
- **call**: a live audio and video stage, as X's Spaces, with a live chat beside it (the board). At most MAX_SECONDS.

- **People** (a call): the host (who made it), up to MAX_COHOSTS co-hosts the host names, speakers, and listeners. Up
  to MAX_STAGE people are on stage and MAX_LISTENERS listen: there is no media server, so each person on stage sends
  their voice to everyone in it (the owner's choice, 2026-10-16).
- **Video** (a call whose host allowed it): up to VIDEO_STAGE_MAX people on stage at once, only while the call has at
  most VIDEO_PEOPLE_MAX people in it and the relay's month allows video (helpers/turn_usage.py).
- **Everyone in a call is shown to everyone in it**, as on X (the owner's choice): the join sheet says so first.
- **Never anonymous**, never by a bot, never in a Circle guild.
- **Joining** a call needs a signed-in member who may see it, who is not in a block with the host or a co-host either
  way and whom the host has not removed. One message for every refusal that could reveal a block. The same holds for
  writing on a board.
- **The board** (a board's main thing, a call's live chat): plain text up to BOARD_MAX characters, public to whoever
  may open the Space (not end-to-end encrypted, so it can be moderated), rated by the word filter, slowed by slow mode
  (the host and co-hosts are never slowed), capped per minute and per day; a message may quote one earlier message of
  the same Space.
- **Recorded or not**, for every kind (`record`): everyone is told before they join. **A Space that is not recorded
  leaves nothing**: when it ends it disappears from feeds and the host's profile with its board, updates and comments.
  A recorded board or live thread stays to read; **a recorded call keeps a replay**: the sound, or the video too when
  cameras were allowed (`video_replay`), saved to the host's own storage, as every upload on this site is.
"""
from babel.support import LazyProxy

from ruqqus.helpers.i18n_runtime import N_, tr, tr_lazy, trn, trp

HOST, COHOST, SPEAKER, LISTENER = "host", "cohost", "speaker", "listener"
STAGE_ROLES = (HOST, COHOST, SPEAKER)
SCHEDULED, LIVE, ENDED = "scheduled", "live", "ended"

BOARD, LIVE_THREAD, CALL = "board", "live", "call"
KINDS = (BOARD, LIVE_THREAD, CALL)
# marked with N_ and drawn with msg() / msg_text() (space_new.html, space_view.py); "Call" alone is ambiguous (a noun
# here, never the verb), so it is a lazy string with its context, which msg() and msg_text() draw as it is
KIND_LABELS = {BOARD: N_("Message board"), LIVE_THREAD: N_("Live thread"),
               CALL: LazyProxy(lambda: trp("Space kind", "Call"), enable_cache=False)}
KIND_HINTS = {
    BOARD: N_("A public board for a set time: anyone who can see it writes on it."),
    LIVE_THREAD: N_("You post timestamped updates as things happen; everyone comments under them."),
    CALL: N_("A live audio stage (video too, if you allow it), with a live chat beside it."),
}
KIND_ICONS = {BOARD: "fa-comments", LIVE_THREAD: "fa-tower-broadcast", CALL: "fa-microphone-lines"}
# how long a board or a live thread runs (the poll lengths: helpers/polls.py); a call runs at most MAX_SECONDS
# (labels marked with N_, drawn with msg_text() in space_new.html)
LENGTHS = ((1, N_("1 hour")), (6, N_("6 hours")), (24, N_("1 day")), (72, N_("3 days")), (168, N_("7 days")))
DEFAULT_HOURS = 24

MAX_STAGE = 5
MAX_COHOSTS = 2
MAX_LISTENERS = 40
MAX_SECONDS = 4 * 3600
SPACES_PER_DAY = 5
SCHEDULE_DAYS = 14
HOST_GONE_SECONDS = 120           # a live Space whose host has not been heard of for this long ends
REPORT_MAX = 200

VIDEO_STAGE_MAX = 4               # people on stage sending video at once
VIDEO_PEOPLE_MAX = 12             # a Space this big or smaller may use video

BOARD_MAX = 500
BOARD_PER_MINUTE = 6              # one person's burst limit
BOARD_PER_DAY = 300               # one person's messages in Spaces in a day
SLOW_CHOICES = (0, 10, 30, 60, 300)

# shown only (translated each time they are used); compared only with each other in one request
UNAVAILABLE = tr_lazy("This Space isn't available.")
NOT_HERE = tr_lazy("Spaces are started in the Spaces area.")
# set on the environ of the in-process request the Spaces area sends to the posting route (routes/spaces.py
# `space_create`); a browser can only send headers, which arrive as HTTP_* keys, so it can never set this one
ENVIRON_KEY = "ruqqus.space_create"
NOT_STARTED = tr_lazy("This Space hasn't started yet.")
OVER = tr_lazy("This Space has ended.")
FULL = LazyProxy(lambda: trn("This Space is full (%(num)s listener).", "This Space is full (%(num)s listeners).",
                             MAX_LISTENERS), enable_cache=False)
STAGE_FULL = LazyProxy(lambda: trn("The stage is full (%(num)s person).", "The stage is full (%(num)s people).",
                                   MAX_STAGE), enable_cache=False)
VIDEO_OFF = tr_lazy("Video is off in this Space.")
VIDEO_TOO_BIG = LazyProxy(lambda: trn("Video is off: this Space has more than %(num)s person in it now.",
                                      "Video is off: this Space has more than %(num)s people in it now.",
                                      VIDEO_PEOPLE_MAX), enable_cache=False)
VIDEO_RELAY = tr_lazy("Video is off this month to keep calls free. Audio still works.")
VIDEO_FULL = LazyProxy(lambda: trn("%(num)s person is already on video.", "%(num)s people are already on video.",
                                   VIDEO_STAGE_MAX), enable_cache=False)
BOARD_CLOSED = tr_lazy("The board is open while the Space is live.")
BOARD_JOIN = tr_lazy("Join the Space to write in its chat.")


class Refused(ValueError):
    """Shown to the member as it is."""


def parse_start(value, now):
    """When the host asked it to start: a Unix time the composer works out in the member's own time zone (the
    `space_start` field), or now. (start, error)."""
    text = str(value or "").strip()
    if not text or text == "0":
        return now, None
    try:
        start = int(float(text))
    except ValueError:
        return None, tr("That start time isn't a time.")
    if start < now - 300:
        return None, tr("A Space can't start in the past.")
    if start > now + SCHEDULE_DAYS * 86400:
        return None, trn("A Space can be scheduled at most %(num)s day ahead.",
                         "A Space can be scheduled at most %(num)s days ahead.", SCHEDULE_DAYS)
    return max(start, now), None


def parse_kind(value):
    """The kind a Space is made as: one of KINDS, else None."""
    text = str(value or "").strip().lower()
    return text if text in KINDS else None


def parse_length(kind, value):
    """How many hours a board or a live thread runs: one of LENGTHS (DEFAULT_HOURS when none is given). A call has
    no chosen length: None."""
    if kind == CALL:
        return None
    text = str(value or "").strip()
    if not text:
        return DEFAULT_HOURS
    if text.isascii() and text.isdigit() and int(text) in dict(LENGTHS):
        return int(text)
    raise Refused(tr("Pick one of the lengths."))


def ends_at(kind, start_utc, hours):
    """When a board or a live thread started at `start_utc` stops by itself (0 for a call: MAX_SECONDS and its host
    being gone end it)."""
    if kind == CALL or not hours:
        return 0
    return int(start_utc) + int(hours) * 3600


def is_due(kind, status, started_utc, ends_utc, now):
    """Whether a live Space has run its time: a board or a live thread at its end, a call after MAX_SECONDS."""
    if status != LIVE:
        return False
    if kind == CALL:
        return bool(started_utc) and now - started_utc > MAX_SECONDS
    return bool(ends_utc) and now >= ends_utc


def comment_mode(kind):
    """The backing row's "Who can comment": a live thread takes comments from everyone (0); a board and a call take
    none (helpers/comment_permission.py SPACE: their discussion is the board)."""
    return 0 if kind == LIVE_THREAD else 5


def video_replay(kind, record, video, audience):
    """Whether a recorded Space keeps its cameras in the replay: a call that allowed video, shared with everyone. A
    video replay lives on the host's own video channel, which names the channel and opens for anyone holding the link,
    so a Space for circles keeps the sound only (as circles.post_refusal refuses a channel video on a circle post)."""
    return kind == CALL and bool(record) and bool(video) and not audience


def make_refusal(anonymous, bot, circle_guild, made_today, kind=CALL):
    if kind not in KINDS:
        return tr("Choose what kind of Space it is.")
    if bot:
        return tr("Bots can't host Spaces.")
    if anonymous:
        return tr("A Space can't be anonymous.")
    if circle_guild:
        return tr("A Space can't be hosted in a Circle guild.")
    if made_today >= SPACES_PER_DAY:
        return trn("You can host at most %(num)s Space a day.", "You can host at most %(num)s Spaces a day.",
                   SPACES_PER_DAY)
    return None


def join_refusal(status, may_see, blocked_with_hosts, removed, listeners_now, on_stage):
    if not may_see or blocked_with_hosts or removed:
        return UNAVAILABLE
    if status == SCHEDULED:
        return NOT_STARTED
    if status != LIVE:
        return OVER
    if not on_stage and listeners_now >= MAX_LISTENERS:
        return FULL
    return None


def may_run(role):
    return role in (HOST, COHOST)


def on_stage(role):
    return role in STAGE_ROLES


def role_of(user_id, host_id, roles):
    """A member's role: `roles` maps user ids to (role, status) rows of space_roles; only accepted ones count."""
    if user_id == host_id:
        return HOST
    row = roles.get(user_id)
    if row and row[1] == "accepted":
        return row[0]
    return LISTENER


def invite_refusal(actor_role, target_role, stage_count, already_invited):
    if not may_run(actor_role):
        return tr("Only the host and co-hosts can invite people to speak.")
    if on_stage(target_role):
        return tr("They're already on stage.")
    if already_invited:
        return tr("They've already been invited to speak.")
    if stage_count >= MAX_STAGE:
        return STAGE_FULL
    return None


def cohost_refusal(actor_role, target_role, cohosts):
    if actor_role != HOST:
        return tr("Only the host can choose co-hosts.")
    if target_role == HOST:
        return tr("That's the host.")
    if target_role == COHOST:
        return None                         # taking it back
    if target_role != SPEAKER:
        return tr("Invite them to speak first.")
    if cohosts >= MAX_COHOSTS:
        return trn("A Space can have at most %(num)s co-host.", "A Space can have at most %(num)s co-hosts.",
                   MAX_COHOSTS)
    return None


def remove_refusal(actor_role, target_role, actor_id, target_id):
    if target_id == actor_id:
        return tr("Use Leave to leave the Space.")
    if not may_run(actor_role):
        return tr("Only the host and co-hosts can remove people.")
    if target_role == HOST or (target_role == COHOST and actor_role != HOST):
        return tr("Only the host can remove a co-host.")
    return None


def clean_reason(text):
    return " ".join(str(text or "").split())[:REPORT_MAX]


def state_label(status, start_utc, now):
    if status == LIVE:
        return trp("Space state", "Live")
    if status == ENDED:
        return trp("Space state", "Ended")
    return tr("Starting soon") if start_utc <= now else trp("Space state", "Scheduled")


# --- video --------------------------------------------------------------------------------------------------------

def video_refusal(space_video, people, relay_ok):
    """Why nobody may send video in this Space now (None: video is open). `people`: everyone in it now."""
    if not space_video:
        return VIDEO_OFF
    if not relay_ok:
        return VIDEO_RELAY
    if people > VIDEO_PEOPLE_MAX:
        return VIDEO_TOO_BIG
    return None


def camera_refusal(space_video, people, relay_ok, on_stage_now, on_video_now):
    """Why this member may not turn their camera on. `on_video_now`: the others on stage already sending video."""
    general = video_refusal(space_video, people, relay_ok)
    if general:
        return general
    if not on_stage_now:
        return tr("Only people on stage can turn their camera on.")
    if on_video_now >= VIDEO_STAGE_MAX:
        return VIDEO_FULL
    return None


# --- the board ------------------------------------------------------------------------------------------------------

def clean_message(text):
    """A board message as it is kept: plain text, no control characters but line breaks, at most BOARD_MAX."""
    body = "".join(ch for ch in str(text or "") if ch in "\n\t" or (ord(ch) >= 32 and ord(ch) != 127))
    body = "\n".join(line.rstrip() for line in body.replace("\r", "").split("\n")).strip()
    while "\n\n\n" in body:
        body = body.replace("\n\n\n", "\n\n")
    if not body:
        raise Refused(tr("Write something first."))
    if len(body) > BOARD_MAX:
        raise Refused(trn("A message can be at most %(num)s character.", "A message can be at most %(num)s characters.",
                          BOARD_MAX))
    return body


def message_refusal(status, inside, suspended, last_minute, today, kind=CALL, kept_out=False):
    """Why this member may not write on the board now (slow mode is the caller's: it lives in Redis). In a call only
    the people in it write (`inside`); on a board anyone who may open it, unless the host removed them or they are in
    a block with the host or a co-host (`kept_out`: one message that never says which)."""
    if status != LIVE:
        return BOARD_CLOSED
    if suspended:
        return tr("Your account is suspended.")
    if kept_out:
        return UNAVAILABLE
    if kind == CALL and not inside:
        return BOARD_JOIN
    if last_minute >= BOARD_PER_MINUTE:
        return tr("You're writing very fast. Wait a moment.")
    if today >= BOARD_PER_DAY:
        return trn("You can write at most %(num)s message in Spaces a day.",
                   "You can write at most %(num)s messages in Spaces a day.", BOARD_PER_DAY)
    return None


def clean_slow(raw):
    """Slow mode in seconds: one of SLOW_CHOICES."""
    value = str(raw if raw is not None else "").strip()
    if value.isascii() and value.isdigit() and int(value) in SLOW_CHOICES:
        return int(value)
    raise Refused(tr("Pick one of the slow mode lengths."))


def slow_label(seconds):
    if not seconds:
        return trp("slow mode", "Off")
    if seconds < 60:
        # Translators: slow mode's wait in seconds, shortened ("10 s")
        return trn("%(num)s s", "%(num)s s", seconds)
    # Translators: slow mode's wait in minutes, shortened ("5 min")
    return trn("%(num)s min", "%(num)s min", seconds // 60)


def may_moderate(role, admin):
    """Who removes a message on the board and sets slow mode: the host, co-hosts and site admins."""
    return may_run(role) or bool(admin)


def may_delete_message(viewer_id, author_id):
    return viewer_id is not None and viewer_id == author_id
