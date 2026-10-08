"""Polls: the rules, without a database (the storage is helpers/poll_store.py, the routes
routes/polls.py).

A poll is added to a post when it is written: the post's title is the question, and the poll has 2 to 4
options, one vote each (a vote is final), and a length picked from a short fixed list. It belongs to the
PRIMARY post, so a guild forward or a repost of that post votes the very same poll. Results are shown to
whoever has voted, to the author, and to everyone once the poll has ended; nobody is ever shown who
voted for what."""
import math
import re

MIN_OPTIONS = 2
MAX_OPTIONS = 4
OPTION_CHARS = 40

# the lengths on offer: never a free number
DURATIONS = ((1, "1 hour"), (6, "6 hours"), (24, "1 day"), (72, "3 days"), (168, "7 days"))
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
            raise PollError("A poll option can't hold control characters.")
        if len(text) > OPTION_CHARS:
            raise PollError(f"A poll option can be {OPTION_CHARS} characters at most.")
        key = text.casefold()
        if key in seen:
            raise PollError(f"“{text}” is in the poll twice.")
        seen.add(key)
        options.append(text)

    if len(options) > MAX_OPTIONS:
        raise PollError(f"A poll can have {MAX_OPTIONS} options at most.")
    if complete and options and len(options) < MIN_OPTIONS:
        raise PollError(f"A poll needs at least {MIN_OPTIONS} options.")
    return options


def clean_hours(raw):
    """The poll's length in hours: one of HOURS (blank means the default)."""
    value = str(raw if raw is not None else "").strip()
    if not value:
        return DEFAULT_HOURS
    if re.fullmatch(r"[0-9]{1,4}", value) and int(value) in HOURS:
        return int(value)
    raise PollError("Pick how long the poll runs.")


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
        return "Final results"
    if seconds < 3600:
        count, unit = max(1, math.ceil(seconds / 60)), "minute"
    elif seconds < 48 * 3600:
        count, unit = math.ceil(seconds / 3600), "hour"
    else:
        count, unit = math.ceil(seconds / 86400), "day"
    return f"{count} {unit}{'' if count == 1 else 's'} left"


def vote_refusal(gone, closed, voted, option_known):
    """Why a vote can't be counted, or None."""
    if gone:
        return "This poll is no longer available."
    if closed:
        return "This poll has ended."
    if voted:
        return "You already voted in this poll."
    if not option_known:
        return "Pick one of the options."
    return None
