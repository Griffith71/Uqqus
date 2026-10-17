"""Guild channels: text and announcement channels inside a guild (pure rules, no I/O; the SQL is
helpers/guild_channel_store.py, the routes routes/guild_channels.py, the chat page shows them under Guilds and a guild's
Chat tab frames the chat page).

A channel is a Matrix room made by the site's bot, like a broadcast channel's, but **not end-to-end encrypted** in any
guild (the owner's choice: a guild's posts are plain text too, so its channels can be rated, slowed, reported and
removed). Every message goes through the site (`POST /api/guilds/channels/<id>/messages`), which checks who may write,
slow mode and the daily count, rates the text with the word filter and sends it as the writer with the site's signature
(`site_mark`); the appservice webhook removes a message without a valid, unused signature, so nobody gets round those
checks by writing to the room from their own Matrix client.

- **Who reads and writes** is a ladder per channel (`read_level`, `write_level`): `members` (joined the guild, or approved,
  or a guildmaster), `approved` (approved contributors and guildmasters), `mods` (guildmasters). An announcement channel
  is written by guildmasters with the content permission only. Writing is never looser than reading.
- **Always required**: a guild the person may see, not exiled from it, not a bot; a suspended account or one a guildmaster
  muted in the guild's channels can read but not write. A refusal to read never says why.
- **Slow mode** (a number of seconds between one person's messages, guildmasters exempt), a burst limit and a daily count.
- **No row per member**: opening a channel puts the person in its room (`guild_channel_reads`), and the sync step takes
  out anyone no longer allowed, or idle for IDLE_DAYS (they come back by opening it again).
"""
import hashlib
import hmac
import re
import unicodedata

from ruqqus.helpers import broadcasts, display_names

_EVENT_ID = re.compile(r"\$[A-Za-z0-9_+/=\-]{10,120}")

TEXT, ANNOUNCEMENT, VOICE, STAGE = "text", "announcement", "voice", "stage"
KINDS = (TEXT, ANNOUNCEMENT, VOICE, STAGE)
KIND_LABELS = {TEXT: "Text channel", ANNOUNCEMENT: "Announcement channel", VOICE: "Voice channel", STAGE: "Stage (shows a Space)"}
MESSAGE_KINDS = (TEXT, ANNOUNCEMENT)               # the kinds whose room holds messages
ROOMLESS = (STAGE,)                                 # no Matrix room at all: the row holds a placeholder

MEMBERS, APPROVED, MODS = "members", "approved", "mods"
LEVELS = (MEMBERS, APPROVED, MODS)
_RANK = {MEMBERS: 0, APPROVED: 1, MODS: 2}
LEVEL_LABELS = {MEMBERS: "Everyone in the guild", APPROVED: "Approved members and guildmasters",
                MODS: "Guildmasters only"}

MAX_CHANNELS = 20
MAX_MEMBERS = broadcasts.MAX_PUBLIC_MEMBERS          # people in one channel's room
NAME_MAX = 40
TOPIC_MAX = 200
RULES_MAX = 1000
MESSAGE_MAX = 2000
REPORT_MAX = 200
PER_MINUTE = 20                                      # messages one person sends in one channel in a minute
PER_DAY = 2000                                       # ... and in 24 hours
IDLE_DAYS = 30                                       # taken out of a room after this long unopened
KEEP_TEXT_DAYS = 90                                  # a message's text is kept this long for reports
NONCE_SECONDS = 3600                                 # a signature can be used once, within this long

SLOW_CHOICES = (0, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 21600)

MAX_CATEGORIES = 10
CATEGORY_MAX = 40
MAX_PINS = 10
MAX_MENTIONS = 5                                     # people one message can mention (and so tell)
MENTION_NOTICE_SECONDS = 3600                        # one notification per writer, person and channel an hour
MUTE_CHOICES = (1, 8, 24, -1)                        # hours a member mutes a channel for; -1 until they turn it on
FOREVER = 2_000_000_000
SEARCH_MAX = 30                                      # results of one search
MAX_ENCRYPTED_MEMBERS = 500                          # people in an end-to-end encrypted channel's room
MAX_FEEDS = 3                                        # bot accounts feeding one announcement channel
FEED_PER_DAY = 50                                    # items one bot puts into one channel in a day
VOICE_MAX_SECONDS = 12 * 3600                        # one person's stay in a voice channel, before they are let go
VOICE_JOINS_PER_HOUR = 60                            # times one person may join voice channels in an hour
SEARCH_TERMS = 5

SEVERITY_KEY = broadcasts.SEVERITY_KEY               # a message's word-filter rating, in the event's content
SITE_KEY = broadcasts.SITE_KEY                       # the site's signature
NONCE_KEY = "org.uqqus.nonce"                        # what makes a signature good once

UNAVAILABLE = "This channel isn't available."
RULES_PENDING = "Read and accept this channel's rules to write here."
JOIN_FIRST = "Join the guild to read its channels."
CLOSED = "This guild's channels are closed."


class Refused(ValueError):
    """Shown to the member as it is."""


# --- what a guildmaster writes ----------------------------------------------------------------------------------------

_KEEP = {chr(0x200D), chr(0xFE0F)}                   # emoji sequences are glued with a joiner and a variation selector


def _plain(value, limit, label, multiline=False):
    """Plain text: spaces collapsed, no control or invisible characters (they make one name look like another)."""
    text = unicodedata.normalize("NFC", str(value or ""))
    if multiline:
        lines = [" ".join(line.split()) for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
        text = "\n".join(lines).strip("\n")
        while "\n\n\n" in text:
            text = text.replace("\n\n\n", "\n\n")
    else:
        text = " ".join(text.split())
    for char in text:
        if char == "\n" or char in _KEEP:
            continue
        category = unicodedata.category(char)
        if category[0] == "C" or category in ("Zl", "Zp"):
            raise Refused(f"{label} can't contain control or invisible characters.")
    if len(text) > limit:
        raise Refused(f"{label} can be at most {limit:,} characters.")
    return text


def clean_name(value):
    """A channel's name as it is stored: plain text without a leading #, which the page draws."""
    try:
        name = display_names.clean(str(value or "").strip().lstrip("#"))
    except display_names.Refused as e:
        raise Refused(str(e).replace("A display name", "A channel name"))
    if not name:
        raise Refused("Give the channel a name.")
    if len(name) > NAME_MAX:
        raise Refused(f"A channel name can be at most {NAME_MAX} characters.")
    return name


def clean_topic(value):
    return _plain(value, TOPIC_MAX, "A topic")


def clean_rules(value):
    return _plain(value, RULES_MAX, "The rules", multiline=True)


def clean_slow(value):
    try:
        seconds = int(value or 0)
    except (TypeError, ValueError):
        raise Refused("Choose how long people wait between messages.")
    if seconds not in SLOW_CHOICES:
        raise Refused("Choose how long people wait between messages.")
    return seconds


def clean_settings(kind, read_level, write_level, slow):
    """(kind, read_level, write_level, slow_mode_seconds) as they are stored. An announcement channel is written by
    guildmasters, so it needs no slow mode; writing is never looser than reading."""
    if kind not in KINDS:
        raise Refused("Choose a kind of channel.")
    if read_level not in LEVELS:
        raise Refused("Choose who can read the channel.")
    if kind in (ANNOUNCEMENT, STAGE):
        write_level, slow = MODS, 0
    elif write_level not in LEVELS:
        raise Refused("Choose who can write in the channel." if kind != VOICE else "Choose who can speak in the channel.")
    if kind == VOICE:
        slow = 0                                    # nobody types in it
    if _RANK[write_level] < _RANK[read_level]:
        raise Refused("People who can't read a channel can't write in it." if kind != VOICE else "People who can't see a channel can't speak in it.")
    return kind, read_level, write_level, clean_slow(slow)


def make_refusal(count, name_taken):
    if count >= MAX_CHANNELS:
        return f"A guild can have at most {MAX_CHANNELS} channels."
    if name_taken:
        return "The guild already has a channel with that name."
    return None


def rename_refusal(name_taken):
    return "The guild already has a channel with that name." if name_taken else None


# --- who may read and write -------------------------------------------------------------------------------------------

def guild_visible(is_private, contributor, mod, admin=False):
    """Board.can_view as a rule, for the places that read everyone's standing in one query (the sync step, the list)."""
    return bool(admin or not is_private or contributor or mod)


def read_refusal(level, *, can_view, hidden, bot, exiled, joined, contributor, mod, admin=False, role_read=None):
    """Why this person can't read a channel (None: they can). `admin`: a site admin who may see the guild (level 4).
    `role_read`: what their roles say about this channel (guild_roles.resolve: True, False or None), over the ladder but
    never over the first line, and never against a guildmaster. Nothing but "join the guild" says why: a channel a
    person may not read is one that isn't there."""
    if bot or not can_view or hidden or exiled:
        return UNAVAILABLE
    if admin or mod:
        return None
    if role_read is True:
        return None
    if role_read is False:
        return UNAVAILABLE
    if level == MEMBERS:
        return None if (joined or contributor or mod) else JOIN_FIRST
    if level == APPROVED:
        return None if (contributor or mod) else UNAVAILABLE
    return None if mod else UNAVAILABLE


def write_refusal(write_level, read_result, *, suspended, muted, joined, contributor, mod, content_mod,
                  rules_pending=False, role_write=None):
    """Why this person can't write in a channel (None: they can). `read_result` is read_refusal's answer;
    `rules_pending`: the channel asks its rules to be accepted and this person hasn't (a guildmaster is never asked);
    `role_write`: what their roles say about writing here (True allows, over the ladder; False denies, but never a
    guildmaster)."""
    if read_result:
        return read_result
    if suspended:
        return "Your account is suspended."
    if muted:
        return "A guildmaster has muted you in this guild's channels."
    if role_write is True:
        refusal = None
    elif role_write is False and not mod:
        refusal = "You can't write in this channel."
    elif write_level == MODS:
        refusal = None if content_mod else "Only the guild's guildmasters write in this channel."
    elif write_level == APPROVED:
        refusal = None if (contributor or mod) else "Only approved members write in this channel."
    else:
        refusal = None if (joined or contributor or mod) else JOIN_FIRST
    if refusal is None and rules_pending and not mod:
        return RULES_PENDING
    return refusal


def full_refusal(size, encrypted=False):
    return "This channel is full." if size >= (MAX_ENCRYPTED_MEMBERS if encrypted else MAX_MEMBERS) else None


ENCRYPTED_REFUSAL = "This channel is end-to-end encrypted: the site can't read it, so it has no word filter, slow mode, pins, search or reports."


def encrypted_refusal(kind, encrypted, guild_private, read_level, write_level, gate):
    """Why a channel can't be made end-to-end encrypted (None: it can, or it isn't asked to be). Only a private or Circle
    guild's text channel, written by everyone who reads it (the room's own rules are the only way to limit its writing)
    and with no rules to accept (accepting is the site's to check)."""
    if not encrypted:
        return None
    if kind != TEXT:
        return "Only a text channel can be end-to-end encrypted."
    if not guild_private:
        return "Only a private or Circle guild's channels can be end-to-end encrypted."
    if read_level != write_level:
        return "An encrypted channel is written by everyone who can read it."
    if gate:
        return "An encrypted channel can't ask for its rules to be accepted: the site can't check who wrote."
    return None


def slow_label(seconds):
    if not seconds:
        return "Off"
    if seconds < 60:
        return f"{seconds} seconds"
    if seconds < 3600:
        minutes = seconds // 60
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours = seconds // 3600
    return f"{hours} hour{'s' if hours != 1 else ''}"


def slow_exempt(mod):
    return bool(mod)


def slow_message(seconds):
    seconds = max(1, int(seconds or 0))
    if seconds >= 120:
        return f"Slow mode: wait {(seconds + 59) // 60} minutes before your next message."
    return f"Slow mode: wait {seconds} second{'s' if seconds != 1 else ''} before your next message."


def clean_message(text):
    body = (text or "").strip()
    if not body:
        raise Refused("Write something first.")
    if len(body) > MESSAGE_MAX:
        raise Refused(f"A message can be at most {MESSAGE_MAX:,} characters.")
    return body


def message_refusal(in_minute, today):
    """`in_minute`: this person's messages in this channel this minute, counting the one being sent."""
    if in_minute > PER_MINUTE:
        return "You're sending messages too quickly. Wait a moment."
    if today >= PER_DAY:
        return f"You can send at most {PER_DAY:,} messages a day in a channel."
    return None


# --- the site's signature on a message ---------------------------------------------------------------------------------

def site_mark(secret, room_id, sender, severity, nonce, body):
    """What the site signs when it sends a message for someone. It covers the room, who sent it, the word-filter rating,
    a one-time nonce and the text, so a member's own Matrix client can neither make a message (it needs the site's
    secret) nor copy another person's, nor keep sending its own again, nor lower a rating."""
    message = f"guildchannel:{room_id}:{sender}:{int(severity)}:{nonce}:{body}"
    return hmac.new((secret or "").encode(), message.encode(), hashlib.sha256).hexdigest()[:32]


def mark_ok(secret, room_id, sender, content):
    """Is this event content signed for `sender` in `room_id`? (Whether its nonce is still unused is
    guild_channel_store.claim_nonce.)"""
    if not isinstance(content, dict) or not secret:
        return False
    body, given, nonce = content.get("body"), content.get(SITE_KEY), content.get(NONCE_KEY)
    severity = content.get(SEVERITY_KEY)
    if not (isinstance(body, str) and isinstance(given, str) and isinstance(nonce, str) and 8 <= len(nonce) <= 64):
        return False
    if isinstance(severity, bool) or not isinstance(severity, int):
        return False
    return hmac.compare_digest(given, site_mark(secret, room_id, sender, severity, nonce, body))


def reaction_ok(key):
    return broadcasts.reaction_ok(key)


def quoted_event_id(value):
    """The event id a message quotes, or None. Matrix event ids are `$` and a hash; anything else is dropped."""
    value = (value or "").strip()
    return value if _EVENT_ID.fullmatch(value) else None


# --- categories, rules to accept, mentions, mute, threads, search -----------------------------------------------------------

def clean_category(value):
    """A category's name: plain text, like a channel's."""
    try:
        name = display_names.clean(str(value or "").strip().lstrip("#"))
    except display_names.Refused as e:
        raise Refused(str(e).replace("A display name", "A category name"))
    if not name:
        raise Refused("Give the category a name.")
    if len(name) > CATEGORY_MAX:
        raise Refused(f"A category name can be at most {CATEGORY_MAX} characters.")
    return name


def category_refusal(count, name_taken):
    if count >= MAX_CATEGORIES:
        return f"A guild can have at most {MAX_CATEGORIES} categories."
    if name_taken:
        return "The guild already has a category with that name."
    return None


def rules_key(channel_rules):
    """What accepting a channel's rules is bound to: change the rules and everyone is asked again."""
    return hashlib.sha256((channel_rules or "").encode()).hexdigest()[:32]


def rules_pending(gate, channel_rules, acked_key):
    return bool(gate and channel_rules and acked_key != rules_key(channel_rules))


def gate_refusal(gate, channel_rules):
    return "Write the channel's rules first: there is nothing to accept." if gate and not channel_rules else None


_MENTION = re.compile(r"(?<![\w@])@([A-Za-z0-9_]{3,25})(?![\w@])")


def parse_mentions(body):
    """The usernames a message mentions (@name), each once, at most MAX_MENTIONS."""
    seen, out = set(), []
    for name in _MENTION.findall(body or ""):
        if name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
        if len(out) >= MAX_MENTIONS:
            break
    return out


def mention_text(writer, channel_name, guild_name, url):
    """A mention's notification (markdown: the names are stripped of anything that could make a link)."""
    from ruqqus.helpers import circles
    return (f"@{circles.plain_name(writer)} mentioned you in #{circles.plain_name(channel_name)} of +{circles.plain_name(guild_name)}.\n\n"
            f"[Open the channel]({url})")


def mute_until(hours, now):
    """The end of a member's own mute of a channel: 0 turns it off, -1 is until they turn it on."""
    try:
        hours = int(hours)
    except (TypeError, ValueError):
        raise Refused("Choose how long to mute the channel.")
    if hours == 0:
        return 0
    if hours not in MUTE_CHOICES:
        raise Refused("Choose how long to mute the channel.")
    return FOREVER if hours < 0 else now + hours * 3600


def is_muted(muted_until, now):
    return bool(muted_until) and muted_until > now


def thread_refusal(root):
    """Why a message can't start or join a thread (None: it can). `root`: (id, author_id, body, removed_utc,
    thread_event_id) of the message it would be under. Threads are one level deep, as Matrix's are."""
    if root is None or root[3]:
        return "That message can't have a thread."
    if root[4]:
        return "A message in a thread can't have a thread of its own."
    return None


def search_terms(query):
    """The words to look for: lowercase, at most SEARCH_TERMS, none shorter than 2 characters."""
    words = [w for w in " ".join(str(query or "").lower().split()).split(" ") if len(w) >= 2]
    return words[:SEARCH_TERMS]


def like_pattern(term):
    """A term as a LIKE pattern with its own wildcards escaped (the SQL says ESCAPE '\\')."""
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def clean_space_ref(value):
    """A stage channel's Space: its post's address or id, as the post's base 36 id."""
    from ruqqus.helpers import guild_wiki
    try:
        return guild_wiki.clean_resource_target("post", value)[1]
    except guild_wiki.Refused:
        raise Refused("Give the Space's post address or id.")


def voice_label(channel_name, guild_name):
    return f"{channel_name} · +{guild_name}"
