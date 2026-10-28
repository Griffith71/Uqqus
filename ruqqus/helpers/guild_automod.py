"""A guild's auto-moderation for its channels (pure rules, no I/O; the settings are `guild_chat_settings`, kept by
helpers/guild_channel_store.py, set on the guildmasters' Channels page, and checked in routes/guild_channels.py before a
message is sent). Guildmasters are never held to it.

- **New accounts**: an account must be `min_account_days` old to write in the guild's channels.
- **New members**: a person must have been in the guild `min_member_days` to write.
- **Links**: someone must have been in the guild `link_member_days` to send a message with a link in it (a new member
  can talk, but not advertise).
- **Words**: up to MAX_WORDS words or phrases the guild doesn't allow, matched as whole words in any case. The site's own
  word filter, which rates every message for each reader, is separate and always on; this is the guild's own list.

A refusal never says which word it was, so the list is not a game to guess.
"""
import re
import unicodedata

from ruqqus.helpers.i18n_runtime import tr, trn

MAX_DAYS = 365
MAX_WORDS = 50
WORD_MAX = 40

_URL = re.compile(r"(?i)(?:https?://|www\.)\S+")


class Refused(ValueError):
    """Shown to the guildmaster as it is."""


def _days_refusal(label):
    """The sentence for a number of days that isn't one, whole for each setting the Channels page sends."""
    if label == "The account age":
        return tr("The account age must be a whole number of days, 0 to %(max)s.", max=MAX_DAYS)
    if label == "The time in the guild":
        return tr("The time in the guild must be a whole number of days, 0 to %(max)s.", max=MAX_DAYS)
    if label == "The time before links":
        return tr("The time before links must be a whole number of days, 0 to %(max)s.", max=MAX_DAYS)
    return tr("%(setting)s must be a whole number of days, 0 to %(max)s.", setting=label, max=MAX_DAYS)


def clean_days(value, label):
    text = str(value if value not in (None, "") else 0).strip()
    if not (text.isascii() and text.isdigit()) or int(text) > MAX_DAYS:
        raise Refused(_days_refusal(label))
    return int(text)


def clean_words(value):
    """The words as stored: one a line, lowercase, no repeats, plain text."""
    words = []
    for line in str(value or "").replace("\r", "\n").split("\n"):
        word = " ".join(unicodedata.normalize("NFC", line).lower().split())
        if not word:
            continue
        if len(word) > WORD_MAX:
            raise Refused(trn("A word or phrase can be at most %(num)s character.",
                                  "A word or phrase can be at most %(num)s characters.", WORD_MAX))
        if any(unicodedata.category(c)[0] == "C" for c in word):
            raise Refused(tr("A word or phrase can't contain control or invisible characters."))
        if word not in words:
            words.append(word)
    if len(words) > MAX_WORDS:
        raise Refused(trn("You can list at most %(num)s word or phrase.", "You can list at most %(num)s words or phrases.",
                          MAX_WORDS))
    return "\n".join(words)


def words_of(stored):
    return [w for w in (stored or "").split("\n") if w]


def has_link(body):
    return bool(_URL.search(body or ""))


def has_blocked_word(body, stored):
    text = " ".join(unicodedata.normalize("NFC", body or "").lower().split())
    return any(re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", text) for word in words_of(stored))


def refusal(settings, *, account_days, member_days, body, exempt):
    """Why this person's message is refused by the guild's own rules (None: it isn't). `settings`: a row with the four
    columns, or None for a guild that set none; `member_days`: how long they have been in the guild (0 if unknown)."""
    if settings is None or exempt:
        return None
    if settings.min_account_days and account_days < settings.min_account_days:
        return tr("Your account is too new to write in this guild's channels yet.")
    if settings.min_member_days and member_days < settings.min_member_days:
        return trn("You can write in this guild's channels once you've been in it for %(num)s day.",
                   "You can write in this guild's channels once you've been in it for %(num)s days.",
                   settings.min_member_days)
    if settings.link_member_days and member_days < settings.link_member_days and has_link(body):
        return trn("Links can be sent once you've been in the guild for %(num)s day.",
                   "Links can be sent once you've been in the guild for %(num)s days.", settings.link_member_days)
    if has_blocked_word(body, settings.blocked_words):
        return tr("Your message includes a word this guild doesn't allow.")
    return None
