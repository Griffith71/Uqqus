"""Spaces: live audio rooms, as on X (pure rules, no I/O; the SQL is helpers/space_store.py, the routes
routes/spaces.py, the page assets/chat_src/src/space.js).

**A Space is a post.** The host makes it from a composer ("Host a Space"), so it is listed, followed, forwarded,
filtered, muted, blocked and reported like any post, it has the post's audience (Public or circles), and **the post's
comments are its live discussion**. The `spaces` row holds the rest: when it starts, its state, whether it is
recorded, and its replay.

- **People**: the host (who made it), up to MAX_COHOSTS co-hosts the host names, speakers, and listeners. Up to
  MAX_STAGE people are on stage (host, co-hosts and speakers) and MAX_LISTENERS listen: there is no media server,
  so each person on stage sends their voice to everyone in the Space (the owner's choice, 2026-10-16).
- **Everyone in a Space is shown to everyone in it**, as on X (the owner's choice): the join sheet says so first.
- **Never anonymous** (it is a voice), never by a bot, never in a Circle guild.
- **Joining** needs a signed-in member who may see the post, who is not in a block with the host or a co-host either
  way and whom the host has not removed. One message for every refusal that could reveal a block.
- **The host and co-hosts run it**: invite a listener to speak (who then accepts), move a speaker back to listening,
  ask a speaker to mute, remove someone (they can't come back to this Space), end it. A raised hand is seen by
  them. Admins (level 3) can end any Space.
- **A replay** is opt-in when the Space is made (`record`): everyone is told before they join and while it runs.
  The host's browser records the Space and uploads it to the host's own linked storage when it ends (helpers/media);
  it becomes the post's sound. No replay is made if the host's browser closes first.
"""

HOST, COHOST, SPEAKER, LISTENER = "host", "cohost", "speaker", "listener"
STAGE_ROLES = (HOST, COHOST, SPEAKER)
SCHEDULED, LIVE, ENDED = "scheduled", "live", "ended"

MAX_STAGE = 5
MAX_COHOSTS = 2
MAX_LISTENERS = 40
MAX_SECONDS = 4 * 3600
SPACES_PER_DAY = 5
SCHEDULE_DAYS = 14
HOST_GONE_SECONDS = 120           # a live Space whose host has not been heard of for this long ends
REPORT_MAX = 200

UNAVAILABLE = "This Space isn't available."
NOT_STARTED = "This Space hasn't started yet."
OVER = "This Space has ended."
FULL = f"This Space is full ({MAX_LISTENERS} listeners)."
STAGE_FULL = f"The stage is full ({MAX_STAGE} people)."


def parse_start(value, now):
    """When the host asked it to start: a Unix time the composer works out in the member's own time zone (the
    `space_start` field), or now. (start, error)."""
    text = str(value or "").strip()
    if not text or text == "0":
        return now, None
    try:
        start = int(float(text))
    except ValueError:
        return None, "That start time isn't a time."
    if start < now - 300:
        return None, "A Space can't start in the past."
    if start > now + SCHEDULE_DAYS * 86400:
        return None, f"A Space can be scheduled at most {SCHEDULE_DAYS} days ahead."
    return max(start, now), None


def make_refusal(anonymous, bot, circle_guild, made_today):
    if bot:
        return "Bots can't host Spaces."
    if anonymous:
        return "A Space can't be anonymous: people hear your voice."
    if circle_guild:
        return "A Space can't be hosted in a Circle guild."
    if made_today >= SPACES_PER_DAY:
        return f"You can host at most {SPACES_PER_DAY} Spaces a day."
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
        return "Only the host and co-hosts can invite people to speak."
    if on_stage(target_role):
        return "They're already on stage."
    if already_invited:
        return "They've already been invited to speak."
    if stage_count >= MAX_STAGE:
        return STAGE_FULL
    return None


def cohost_refusal(actor_role, target_role, cohosts):
    if actor_role != HOST:
        return "Only the host can choose co-hosts."
    if target_role == HOST:
        return "That's the host."
    if target_role == COHOST:
        return None                         # taking it back
    if target_role != SPEAKER:
        return "Invite them to speak first."
    if cohosts >= MAX_COHOSTS:
        return f"A Space can have at most {MAX_COHOSTS} co-hosts."
    return None


def remove_refusal(actor_role, target_role, actor_id, target_id):
    if target_id == actor_id:
        return "Use Leave to leave the Space."
    if not may_run(actor_role):
        return "Only the host and co-hosts can remove people."
    if target_role == HOST or (target_role == COHOST and actor_role != HOST):
        return "Only the host can remove a co-host."
    return None


def clean_reason(text):
    return " ".join(str(text or "").split())[:REPORT_MAX]


def state_label(status, start_utc, now):
    if status == LIVE:
        return "Live"
    if status == ENDED:
        return "Ended"
    return "Starting soon" if start_utc <= now else "Scheduled"
