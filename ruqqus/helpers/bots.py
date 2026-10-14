"""Bot accounts: a member applies for one, a site admin approves it, and the site then posts to it.

Two ways a bot gets its posts:
* the site reads the feeds it was approved with (RSS, Atom, JSON Feed; scripts/run_bots.py, helpers/feed_fetch.py,
  helpers/feed_items.py) and posts each new item as a link post: its title and its link, nothing copied from the page;
* the owner's own script posts through a bot key (an OAuth grant for the bot account), e.g. for X, which has no free
  feed: the owner uses their own X access and posts the tweet's link, which the site draws as X's own embed.

A bot account is an ordinary `users` row with `is_bot` and `bot_owner_id`, whose password hash never verifies, and
login refuses it too: nobody logs into a bot, so it is never anyone's alt. Its profile says "Automated by @owner".
Bots post and forward; they never comment, vote, chat, take tips or lead a guild.

Pure rules, stdlib only. The database side is helpers/bot_store.py.
"""
import hashlib
import html
import os
import re
import time
from urllib.parse import urlsplit

PENDING, APPROVED, REJECTED, PAUSED = "pending", "approved", "rejected", "paused"
STATUSES = (PENDING, APPROVED, REJECTED, PAUSED)
STATUS_LABELS = {PENDING: "Waiting for approval", APPROVED: "Running", REJECTED: "Rejected", PAUSED: "Paused"}

USERNAME = re.compile(r"^[a-zA-Z0-9_]{3,25}$")         # the sign-up rule (routes/login.py valid_username_regex)
GUILD_NAME = re.compile(r"^[a-zA-Z0-9_]{1,25}$")
UNUSABLE_PASSWORD = "!"                                # werkzeug's check_password_hash never accepts it

MAX_BOTS = 3                     # per owner, not counting rejected ones
MAX_SOURCES = 5                  # per bot
MAX_FORWARD_GUILDS = 5
PURPOSE_MAX = 500
NOTE_MAX = 500
BIO_MAX = 300
URL_MAX = 300
OWNER_MIN_AGE = 30 * 86400       # an owner's account must be this old
CHECK_MINUTES_DEFAULT = 30
CHECK_MINUTES_MIN = 15
PER_RUN = 3                      # posts per source per run of the job
FIRST_READ = 3                   # a source's first read posts only its newest items: no backlog flood
DAILY_CAP = 40                   # posts per bot per day, by the job and by keys alike
FAILS_BEFORE_PAUSE = 10
BACKOFF_MAX = 86400
SEEN_DAYS = 90
TITLE_MAX = 280                  # helpers/post_fields.TITLE_MAX
MAX_KEYS = 2                     # bot keys per bot

_TAG = re.compile(r"<[^>]*>")
_SPACE = re.compile(r"\s+")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def enabled():
    """BOTS_ENABLED=0 stops every bot: the job posts nothing and keys are refused."""
    return os.environ.get("BOTS_ENABLED", "1") != "0"


def _now(now):
    return int(time.time()) if now is None else int(now)


def utc_day(now=None):
    return _now(now) // 86400


# --- applying ------------------------------------------------------------------------------------------------

def owner_refusal(owner, bots_owned, now=None):
    """Why this member may not apply for another bot, or None. `owner` has created_utc, is_bot, is_deleted and
    is_suspended; `bots_owned` counts their bots that are not rejected."""
    if owner is None or getattr(owner, "is_deleted", False):
        return "Sign in to apply for a bot."
    if getattr(owner, "is_bot", False):
        return "A bot can't run bots."
    if getattr(owner, "is_suspended", False):
        return "You can't apply for a bot while your account is suspended."
    if _now(now) - (owner.created_utc or 0) < OWNER_MIN_AGE:
        return "Your account needs to be at least 30 days old to run a bot."
    if bots_owned >= MAX_BOTS:
        return f"You can run at most {MAX_BOTS} bots."
    return None


def parse_username(raw):
    """(name, error) for a bot's username: the same characters as any account's."""
    name = str(raw or "").strip().lstrip("@")
    if not USERNAME.fullmatch(name):
        return None, "A bot's name is 3 to 25 letters, numbers or underscores."
    return name, None


def plain_text(raw, limit):
    """One line of plain text: control characters dropped, spaces collapsed, cut to `limit`."""
    return _SPACE.sub(" ", _CONTROL.sub("", str(raw or ""))).strip()[:limit]


def parse_purpose(raw):
    purpose = plain_text(raw, PURPOSE_MAX)
    if len(purpose) < 10:
        return None, "Say what the bot will post and where it comes from (at least 10 characters)."
    return purpose, None


def parse_guild_names(raw):
    """(names, error) from "+news, tech" or one a line: guild names without the +, once each."""
    names = []
    for item in re.split(r"[,\s]+", str(raw or "")):
        name = item.strip().lstrip("+")
        if not name:
            continue
        if not GUILD_NAME.fullmatch(name):
            return None, f"\"{name[:30]}\" is not a guild name."
        if name.lower() not in (n.lower() for n in names):
            names.append(name)
    if len(names) > MAX_FORWARD_GUILDS:
        return None, f"A bot can forward to at most {MAX_FORWARD_GUILDS} guilds."
    return names, None


def own_host():
    """This site's own host name, never a source (a bot reading this site would copy private posts out)."""
    return (os.environ.get("SERVER_NAME") or os.environ.get("domain") or "").strip().lower().split(":")[0]


def parse_feed_url(raw, allow_local=False, site_host=None):
    """(address, error) for a feed address a member typed. https, a host name, no user name or password, not this
    site. Where the name leads is judged when it is fetched (helpers/feed_fetch.py, through helpers/safe_fetch.py)."""
    url = str(raw or "").strip()
    if not url:
        return None, "Enter the feed's address."
    if len(url) > URL_MAX or any(c.isspace() for c in url):
        return None, "That feed address is too long or has spaces in it."
    try:
        parts = urlsplit(url)
        parts.port                      # reading it raises ValueError for a port that is not a number
    except ValueError:
        return None, "That is not a usable address."
    if parts.scheme != "https" and not (allow_local and parts.scheme == "http"):
        return None, "A feed's address must start with https://"
    if parts.username is not None or parts.password is not None:
        return None, "A feed's address can't hold a user name or password."
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        return None, "That address has no server name."
    site = (own_host() if site_host is None else site_host).lower()
    if site and (host == site or host.endswith("." + site)):
        return None, "A bot can't read this site itself."
    return url, None


def parse_check_minutes(raw):
    try:
        minutes = int(str(raw).strip())
    except (TypeError, ValueError):
        return CHECK_MINUTES_DEFAULT
    return max(CHECK_MINUTES_MIN, min(24 * 60, minutes))


# --- posting -------------------------------------------------------------------------------------------------

def post_refusal(bot, owner, posts_today, is_on=None):
    """Why this bot account may not post now, or None. Asked by submit_post for every bot account, whether the post
    comes from the job or from a bot key. `bot` is the `bots` row (status), `owner` the member who runs it."""
    if not (enabled() if is_on is None else is_on):
        return "Bots are switched off on this site for now."
    if bot is None or bot.status != APPROVED:
        return "This bot is not running." if bot is not None and bot.status == PAUSED else "This bot has not been approved."
    if owner is None or getattr(owner, "is_deleted", False) or getattr(owner, "is_suspended", False):
        return "This bot's owner can't run it right now."
    if posts_today >= DAILY_CAP:
        return f"A bot can post at most {DAILY_CAP} times a day."
    return None


def option_refusal(anonymous, audience, coauthors):
    """A bot's post is public, signed and its own: never anonymous, never for a Circle, never co-authored."""
    if anonymous:
        return "A bot can't post anonymously."
    if audience:
        return "A bot's posts are public."
    if coauthors:
        return "A bot's posts can't have co-authors."
    return None


def loopback_for(bot_id):
    """The address the job posts from for this bot: its own, so bots never share the posting throttle's per-address
    bucket (helpers/throttle.py) and never share an address with a member (helpers/guild_limits.addresses_of)."""
    n = int(bot_id)                                   # one address each, never .0 or .255, up to 16 million bots
    return f"127.{(n // (254 * 256)) % 256}.{(n // 254) % 256}.{n % 254 + 1}"


def next_check(now, check_minutes, fail_count):
    """When to read a source again: its own interval, doubled for each failure in a row, at most a day."""
    wait = max(CHECK_MINUTES_MIN, int(check_minutes or CHECK_MINUTES_DEFAULT)) * 60
    if fail_count:
        wait = min(BACKOFF_MAX, wait * (2 ** min(int(fail_count), 12)))
    return _now(now) + wait


def item_key(guid, link):
    """A feed item's stable key: its guid (or id) when it has one, else its link."""
    basis = (str(guid or "").strip() or str(link or "").strip())
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def clean_title(raw, limit=TITLE_MAX):
    """A feed item's title as a post title: plain text on one line, cut to the post title limit."""
    text = html.unescape(_TAG.sub(" ", html.unescape(str(raw or ""))))
    text = _SPACE.sub(" ", _CONTROL.sub("", text)).strip()
    if len(text) > limit:
        text = text[:limit - 1].rstrip() + "…"
    return text
