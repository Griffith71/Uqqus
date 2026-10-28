"""Polls: the rules, without a database (the storage is helpers/poll_store.py, the routes
routes/polls.py).

A poll is added to a post when it is written: the post's title is the question, and the poll has 2 to 4
options, one vote each (a vote is final), and a length picked from a short fixed list. It belongs to the
PRIMARY post, so a guild forward or a repost of that post votes the very same poll. Results are shown to
whoever has voted, to the author, and to everyone once the poll has ended; nobody is ever shown who
voted for what."""
import math
import re

from babel.support import LazyProxy

from ruqqus.helpers import text_length
from ruqqus.helpers.i18n_runtime import tr, trn

MIN_OPTIONS = 2
MAX_OPTIONS = 4
OPTION_CHARS = 40



def _length_label(hours):
    """A length on offer as the reader reads it: "6 hours", "3 days"."""
    if hours < 24:
        return trn("%(num)s hour", "%(num)s hours", hours)
    return trn("%(num)s day", "%(num)s days", hours // 24)


# the lengths on offer: never a free number (the labels are worked out in each reader's language when shown)
DURATIONS = tuple((hours, LazyProxy(_length_label, hours, enable_cache=False)) for hours in (1, 6, 24, 72, 168))
HOURS = tuple(hours for hours, _ in DURATIONS)
DEFAULT_HOURS = 24


class PollError(ValueError):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


def clean_options(raw, complete=True):
    """The options as they are kept, from the composer's `poll_option` fields: blank ones are dropped,
    whitespace is single, plain text only. A draft may be unfinished (`complete=False`); a post may not:
    when it has any option it needs at least MIN_OPTIONS."""
    seen, options = set(), []
    for item in raw or []:
        text = re.sub(r"\s+", " ", item or "").strip()
        if not text:
            continue
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
            raise PollError(tr("A poll option can't hold control characters."))
        if text_length.over_limit(text, OPTION_CHARS, stored_max=OPTION_CHARS):
            # the same sentence as text_length.limit_message, written whole
            raise PollError(trn("A poll option can be %(num)s character (Chinese, Japanese and Korean characters and emoji "
                                "count as 2).",
                                "A poll option can be %(num)s characters (Chinese, Japanese and Korean characters and emoji "
                                "count as 2).", OPTION_CHARS))
        key = text.casefold()
        if key in seen:
            raise PollError(tr("“%(option)s” is in the poll twice.", option=text))
        seen.add(key)
        options.append(text)

    if len(options) > MAX_OPTIONS:
        raise PollError(trn("A poll can have %(num)s option at most.", "A poll can have %(num)s options at most.", MAX_OPTIONS))
    if complete and options and len(options) < MIN_OPTIONS:
        raise PollError(trn("A poll needs at least %(num)s option.", "A poll needs at least %(num)s options.", MIN_OPTIONS))
    return options


def clean_hours(raw):
    """The poll's length in hours: one of HOURS (blank means the default)."""
    value = str(raw if raw is not None else "").strip()
    if not value:
        return DEFAULT_HOURS
    if re.fullmatch(r"[0-9]{1,4}", value) and int(value) in HOURS:
        return int(value)
    raise PollError(tr("Pick how long the poll runs."))


def option_text(options):
    """The options as plain text for the word filter (they must count, or a poll option would be a way
    round it)."""
    return "\n".join(options or [])


def closes_at(now, hours):
    return int(now) + int(hours) * 3600


def is_closed(closes_utc, now):
    return int(now) >= int(closes_utc)


def results_visible(closed, voted, is_author):
    """Who is shown the counts: whoever has voted, the author, and everyone once the poll has ended
    (so a vote can't be steered by what is winning, and a visitor reads the result of a finished poll)."""
    return bool(closed or voted or is_author)


def percentages(counts):
    """Whole percentages of the counts that add up to 100 (largest remainder), all 0 with no votes."""
    total = sum(counts)
    if total <= 0:
        return [0] * len(counts)
    exact = [count * 100 / total for count in counts]
    whole = [int(x) for x in exact]
    order = sorted(range(len(counts)), key=lambda i: exact[i] - whole[i], reverse=True)
    for i in order[:100 - sum(whole)]:
        whole[i] += 1
    return whole


def time_left(closes_utc, now):
    """"Final results", or how long is left, in the biggest whole unit ("3 hours left")."""
    seconds = int(closes_utc) - int(now)
    if seconds <= 0:
        return tr("Final results")
    if seconds < 3600:
        return trn("%(num)s minute left", "%(num)s minutes left", max(1, math.ceil(seconds / 60)))
    if seconds < 48 * 3600:
        return trn("%(num)s hour left", "%(num)s hours left", math.ceil(seconds / 3600))
    return trn("%(num)s day left", "%(num)s days left", math.ceil(seconds / 86400))


def vote_refusal(gone, closed, voted, option_known):
    """Why a vote can't be counted, or None."""
    if gone:
        return tr("This poll is no longer available.")
    if closed:
        return tr("This poll has ended.")
    if voted:
        return tr("You already voted in this poll.")
    if not option_known:
        return tr("Pick one of the options.")
    return None
