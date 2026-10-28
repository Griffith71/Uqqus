"""Bot accounts: an account that posts by itself, approved by the site's admins.

Two kinds, by what running it costs:
* a SITE bot runs on free feeds and costs nobody anything. Anyone may propose one (a member or an admin), with its
  name, bio, picture and banner; admins may edit all of that before approving it, and once approved it belongs to
  nobody: the site runs it (`owner_id` NULL), its profile says "Automated by <site>", and only admins manage it.
* a MEMBER bot needs its member: a bot key for their own script (e.g. for X), an API bill. It stays theirs, its
  profile says "Automated by @owner", and they manage it (feeds they add still wait for an admin).
Both are approved by an admin and both post under the same rules (`post_refusal`).

Two ways a bot gets its posts:
* the site reads the feeds it was approved with (RSS, Atom, JSON Feed; scripts/run_bots.py, helpers/feed_fetch.py,
  helpers/feed_items.py) and posts each new item as a link post: its title and its link, nothing copied from the page;
* the owner's own script posts through a bot key (an OAuth grant for the bot account), e.g. for X, which has no free
  feed: the owner uses their own X access and posts the tweet's link, which the site draws as X's own embed.

A bot account is an ordinary `users` row with `is_bot` (and `bot_owner_id` for a member bot), whose password hash never
verifies, and login refuses it too: nobody logs into a bot, so it is never anyone's alt.
Bots post and forward; they never comment, vote, chat, take tips or lead a guild.

Pure rules (stdlib, and the interface's languages: helpers/i18n_runtime.py). The database side is helpers/bot_store.py.
"""
import email.utils
import hashlib
import html
import math
import os
import re
import time
from urllib.parse import urlsplit, urlunsplit

from ruqqus.helpers.i18n_runtime import N_, tr, tr_lazy, trn

PENDING, APPROVED, REJECTED, PAUSED = "pending", "approved", "rejected", "paused"
STATUSES = (PENDING, APPROVED, REJECTED, PAUSED)
# Translators: the state of a bot account, shown as a badge on its page.
STATUS_LABELS = {PENDING: tr_lazy("Waiting for approval"), APPROVED: tr_lazy("Running"), REJECTED: tr_lazy("Rejected"),
                 PAUSED: tr_lazy("Paused")}

SITE, MEMBER, SELF = "site", "member", "self"
KINDS = (SITE, MEMBER, SELF)
# SELF: automated posting on the member's OWN account, which they keep using as themselves (the owner, 2026-10-21).
# Its row's user_id is the member's account; nothing that deletes, renames or edits a bot account may touch it.
# Translators: kinds of bot. "Own account": a member's own account that posts from feeds automatically.
KIND_LABELS = {SITE: tr_lazy("Site bot"), MEMBER: tr_lazy("Member bot"), SELF: tr_lazy("Own account")}

IMAGE_MAX_BYTES = 8 * 1024 * 1024            # a bot's picture or banner
IMAGE_FORMATS = ("PNG", "JPEG", "GIF", "WEBP")

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
# how often a feed is read (next_interval): as often as it safely can be. Never more often than FLOOR_MINUTES, nor
# than the feed asks (RSS <ttl>, sy:updatePeriod, HTTP max-age); a feed with nothing new is read less and less
# often, up to QUIET_MAX_MINUTES, and goes back to its shortest as soon as it has something new
FLOOR_MINUTES = 5
QUIET_MAX_MINUTES = 60
QUIET_STRETCH = 1.5
ASK_MAX_MINUTES = 24 * 60        # a feed asking to be left alone longer than a day is read daily
PER_RUN = 3                      # posts per source per run of the job
FIRST_READ = 3                   # a source's first read posts only its newest items: no backlog flood
DAILY_CAP = 40                   # posts per bot per day, by the job and by keys alike
FAILS_BEFORE_PAUSE = 10
BACKOFF_MAX = 86400
SEEN_DAYS = 90
TITLE_MAX = 280                  # helpers/post_fields.TITLE_MAX
MAX_KEYS = 2                     # bot keys per bot
STALE_AFTER = 2 * 86400          # an item dated further back than this when first seen is never posted (promos)

# query parameters that only track who clicked; a bot's link drops them (`clean_link`). Narrower than trending's
# list on purpose: `ref`, `si`, `feature` and `share` change the page on some sites (GitHub's ?ref=), so they stay.
_TRACKING_PARAM = re.compile(r"^(?:utm_.*|at_.*|fbclid|gclid|dclid|msclkid|igshid|mc_cid|mc_eid|yclid|_ga)$", re.I)

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

def is_admin(member):
    return bool(member) and (getattr(member, "admin_level", 0) or 0) >= 3


def owner_refusal(owner, bots_owned, now=None):
    """Why this member may not propose another bot, or None. `owner` has created_utc, is_bot, is_deleted and
    is_suspended; `bots_owned` counts their member bots that are not rejected and their site-bot proposals still
    waiting (`bot_store.active_count`). Admins (who also make site bots) are held to neither the age nor the count."""
    if owner is None or getattr(owner, "is_deleted", False):
        return tr("Sign in to apply for a bot.")
    if getattr(owner, "is_bot", False):
        return tr("A bot can't run bots.")
    if getattr(owner, "is_suspended", False):
        return tr("You can't apply for a bot while your account is suspended.")
    if is_admin(owner):
        return None
    if _now(now) - (owner.created_utc or 0) < OWNER_MIN_AGE:
        return tr("Your account needs to be at least 30 days old to run a bot.")
    if bots_owned >= MAX_BOTS:
        return trn("You can run at most %(num)s bot.", "You can run at most %(num)s bots.", MAX_BOTS)
    return None


def parse_kind(raw):
    return raw if raw in KINDS else None


def is_self(bot):
    """Automated posting on the member's own account: its row names their account, which must never be deleted,
    deactivated, renamed or have its profile edited from a bot's page."""
    return bot is not None and getattr(bot, "kind", None) == SELF


def self_refusal(owner, has_one, now=None):
    """Why this member may not set up automated posting on their own account, or None. One per account, beside (not
    counted with) the bot accounts they run; the account must be in good standing and 30 days old, as for a bot."""
    if has_one:
        return tr("Your account already posts automatically. Change its feeds on its page.")
    if owner is None or getattr(owner, "is_deleted", False):
        return tr("Sign in to post automatically.")
    if getattr(owner, "is_bot", False):
        return tr("A bot can't run bots.")
    if getattr(owner, "is_suspended", False):
        return tr("Your account can't post automatically while it is suspended.")
    if not is_admin(owner) and _now(now) - (owner.created_utc or 0) < OWNER_MIN_AGE:
        return tr("Your account needs to be at least 30 days old to post automatically.")
    return None


def kind_refusal(kind, feed_count):
    """A site bot is the free kind: it reads feeds, so it needs at least one. A member bot may have none (it posts with
    its member's key). Automated posting on one's own account is feeds only."""
    if kind not in KINDS:
        return tr("Choose what kind of bot it is.")
    if kind == SITE and not feed_count:
        return tr("A site bot reads at least one feed: that is what makes it free to run.")
    if kind == SELF and not feed_count:
        return tr("Add at least one feed for your account to post from.")
    return None


def may_manage(bot, viewer_id, admin):
    """May this person change this bot? Admins: any bot. A member: their own member bot, the automated posting of
    their own account, and a site bot they proposed while it is still waiting (after approval a site bot is the
    site's, run by the admins)."""
    if bot is None or bot.status == REJECTED:
        return False
    if admin:
        return True
    if bot.kind in (MEMBER, SELF):
        return viewer_id is not None and bot.owner_id == viewer_id
    return bot.status == PENDING and viewer_id is not None and bot.proposed_by_id == viewer_id


def subject(bot, capital=True):
    """How a notice names it: "Your bot @x", or "Your account's automated posting" for the member's own account."""
    words = "your account's automated posting" if is_self(bot) else f"your bot @{bot.username}"
    return words[0].upper() + words[1:] if capital else words


# What a bot's member is told (alerts.notify: stored in English with the values, read in their own language). A whole
# sentence for a bot account and another for a member's own account: `subject` glued into a sentence can't be translated.
_NOTICES = {
    ("paused", False): N_("Your bot @%(name)s has been paused: %(reason)s"),
    ("paused", True): N_("Your account's automated posting has been paused: %(reason)s"),
    # Translators: %(fails)s is a number of failed tries in a row (at least 10); %(reason)s is the last error, a sentence.
    ("stopped_reading", False): N_("Your bot @%(name)s stopped reading %(url)s after %(fails)s failed tries: %(reason)s "
                                   "Start it again from Settings > Bots once it works."),
    ("stopped_reading", True): N_("Your account's automated posting stopped reading %(url)s after %(fails)s failed tries: "
                                  "%(reason)s Start it again from Settings > Bots once it works."),
    ("admin_paused", False): N_("An admin paused your bot @%(name)s: %(reason)s"),
    ("admin_paused", True): N_("An admin paused your account's automated posting: %(reason)s"),
    ("running_again", False): N_("Your bot @%(name)s is running again."),
    ("running_again", True): N_("Your account's automated posting is running again."),
}


def notice(bot, which, **values):
    """(message, values) for alerts.notify: what the member who runs `bot` is told. `which` is "paused",
    "stopped_reading" (url, fails, reason), "admin_paused" (reason) or "running_again"."""
    return _NOTICES[(which, is_self(bot))], {"name": bot.username, **values}


def responsible_id(bot):
    """Who is told about a bot: its member (member bot, own account) or whoever proposed it, for the decision on a
    site bot."""
    if bot is None:
        return None
    return bot.owner_id if bot.kind in (MEMBER, SELF) else bot.proposed_by_id


def parse_username(raw):
    """(name, error) for a bot's username: the same characters as any account's."""
    name = str(raw or "").strip().lstrip("@")
    if not USERNAME.fullmatch(name):
        return None, tr("A bot's name is 3 to 25 letters, numbers or underscores.")
    return name, None


def plain_text(raw, limit):
    """One line of plain text: control characters dropped, spaces collapsed, cut to `limit`."""
    return _SPACE.sub(" ", _CONTROL.sub("", str(raw or ""))).strip()[:limit]


def parse_purpose(raw):
    purpose = plain_text(raw, PURPOSE_MAX)
    if len(purpose) < 10:
        return None, tr("Say what the bot will post and where it comes from (at least 10 characters).")
    return purpose, None


def parse_guild_names(raw):
    """(names, error) from "+news, tech" or one a line: guild names without the +, once each."""
    names = []
    for item in re.split(r"[,\s]+", str(raw or "")):
        name = item.strip().lstrip("+")
        if not name:
            continue
        if not GUILD_NAME.fullmatch(name):
            return None, tr("\"%(guild)s\" is not a guild name.", guild=name[:30])
        if name.lower() not in (n.lower() for n in names):
            names.append(name)
    if len(names) > MAX_FORWARD_GUILDS:
        return None, trn("A bot can forward to at most %(num)s guild.", "A bot can forward to at most %(num)s guilds.",
                         MAX_FORWARD_GUILDS)
    return names, None


def own_host():
    """This site's own host name, never a source (a bot reading this site would copy private posts out)."""
    return (os.environ.get("SERVER_NAME") or os.environ.get("domain") or "").strip().lower().split(":")[0]


def own_hosts():
    """Every name and address this site answers on (audit B-01: only the main name was refused): the main name, the
    short domain, the onion name and the origin's own addresses (`ORIGIN_IPS`, comma-separated)."""
    names = [own_host(), os.environ.get("SHORT_DOMAIN", ""), os.environ.get("ONION_NAME", "")]
    names += os.environ.get("ORIGIN_IPS", "").split(",")
    out = set()
    for name in (n.strip().lower().rstrip(".") for n in names if n and n.strip()):
        out.add(name.split(":")[0] if name.count(":") == 1 else name)       # a port goes; an IPv6 address stays
    return out


def is_own_host(host, sites=None):
    host = (host or "").lower().rstrip(".")
    return any(host == site or host.endswith("." + site) for site in (own_hosts() if sites is None else sites))


def parse_feed_url(raw, allow_local=False, site_host=None):
    """(address, error) for a feed address a member typed. https, a host name, no user name or password, not this
    site. Where the name leads is judged when it is fetched (helpers/feed_fetch.py, through helpers/safe_fetch.py)."""
    url = str(raw or "").strip()
    if not url:
        return None, tr("Enter the feed's address.")
    if len(url) > URL_MAX or any(c.isspace() for c in url):
        return None, tr("That feed address is too long or has spaces in it.")
    try:
        parts = urlsplit(url)
        parts.port                      # reading it raises ValueError for a port that is not a number
    except ValueError:
        return None, tr("That is not a usable address.")
    if parts.scheme != "https" and not (allow_local and parts.scheme == "http"):
        return None, tr("A feed's address must start with https://")
    if parts.username is not None or parts.password is not None:
        return None, tr("A feed's address can't hold a user name or password.")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        return None, tr("That address has no server name.")
    if is_own_host(host, None if site_host is None else {site_host.lower()} - {""}):
        return None, tr("A bot can't read this site itself.")
    return url, None


def parse_check_minutes(raw):
    try:
        minutes = int(str(raw).strip())
    except (TypeError, ValueError):
        return FLOOR_MINUTES
    return max(FLOOR_MINUTES, min(ASK_MAX_MINUTES, minutes))


_PERIOD_MINUTES = {"hourly": 60, "daily": 1440, "weekly": 10080, "monthly": 43200, "yearly": 525600}


def _whole_minutes(value):
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return 0
    return int(math.ceil(number)) if number > 0 else 0


def feed_asks(ttl=None, update_period=None, update_frequency=None):
    """The minutes a feed asks to be left between reads: RSS <ttl>, or the syndication module's updatePeriod divided
    by updateFrequency ("hourly" and 2 is every 30 minutes). The longer of the two, at most a day; 0 for nothing."""
    asks = [_whole_minutes(ttl)]
    period = _PERIOD_MINUTES.get(str(update_period or "").strip().lower())
    if period:
        frequency = _whole_minutes(update_frequency) or 1
        asks.append(int(math.ceil(period / frequency)))
    return min(ASK_MAX_MINUTES, max(asks))


def server_asks(max_age_seconds):
    """The minutes the feed's server asks to be left alone (Cache-Control max-age or Expires), at most a day."""
    return min(ASK_MAX_MINUTES, _whole_minutes((max_age_seconds or 0) / 60))


def next_interval(asked, current, got_new):
    """Minutes until the next read. The shortest allowed is the floor or what the feed asks, whichever is longer. A
    read that found nothing new stretches the wait by half, up to QUIET_MAX_MINUTES (or the shortest allowed, if that
    is longer); a read that found something goes straight back to the shortest."""
    shortest = max(FLOOR_MINUTES, int(asked or 0))
    if got_new or not current:
        return shortest
    stretched = int(math.ceil(int(current) * QUIET_STRETCH))
    return max(shortest, min(max(QUIET_MAX_MINUTES, shortest), stretched))


def cadence_label(check_minutes, asked_minutes, status=APPROVED):
    """How a feed is being read, for its bot's page: "Read every 15 minutes (the feed asks for at least 15)"."""
    if status != APPROVED:
        return ""
    minutes = max(FLOOR_MINUTES, int(check_minutes or FLOOR_MINUTES))
    shortest = max(FLOOR_MINUTES, int(asked_minutes or 0))
    if minutes > shortest:
        return trn("Read every %(num)s minute (quiet lately)", "Read every %(num)s minutes (quiet lately)", minutes)
    if shortest > FLOOR_MINUTES:
        return trn("Read every %(num)s minute (the feed asks for at least %(shortest)s)",
                   "Read every %(num)s minutes (the feed asks for at least %(shortest)s)", minutes, shortest=shortest)
    return trn("Read every %(num)s minute", "Read every %(num)s minutes", minutes)


def retry_wait(header, now=None):
    """Seconds a 429 or 503 asked us to wait (Retry-After: seconds, or an HTTP date), at most a day. None when the
    header is missing or unreadable."""
    raw = str(header or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        seconds = int(raw)
    else:
        try:
            when = email.utils.parsedate_to_datetime(raw)
        except (TypeError, ValueError, IndexError):
            return None
        if when is None:
            return None
        seconds = int(when.timestamp()) - _now(now)
    return max(0, min(BACKOFF_MAX, seconds))


# --- posting -------------------------------------------------------------------------------------------------

def post_refusal(bot, owner, posts_today, is_on=None):
    """Why this bot account may not post now, or None. Asked by submit_post for every bot account, whether the post
    comes from the job or from a bot key. `bot` is the `bots` row (status, kind), `owner` the member who runs a member
    bot (a site bot has none: the site runs it)."""
    if not (enabled() if is_on is None else is_on):
        return tr("Bots are switched off on this site for now.")
    if bot is None or bot.status != APPROVED:
        return tr("This bot is not running.") if bot is not None and bot.status == PAUSED else tr("This bot has not been approved.")
    if getattr(bot, "kind", MEMBER) in (MEMBER, SELF) and (
            owner is None or getattr(owner, "is_deleted", False) or getattr(owner, "is_suspended", False)):
        return tr("This bot's owner can't run it right now.")
    if posts_today >= DAILY_CAP:
        return trn("A bot can post at most %(num)s time a day.", "A bot can post at most %(num)s times a day.", DAILY_CAP)
    return None


def is_automated(account_is_bot, says_bot, bot):
    """Is this post automated, so held to the bot rules (approval, the daily cap, the options a bot may not use, the
    spam guard's refuse-don't-ban)? Every post of a bot account; and a post sent with the bot header from an account
    whose automated posting is set up: approved (running or paused). A header post from any other account, or from one
    whose automated posting is still waiting or was rejected, keeps the old rules: it is only marked (bug hunt: a
    rejected row, which is kept, refused every one of the member's own scripted posts as "not approved")."""
    return bool(account_is_bot) or (bool(says_bot) and is_self(bot) and getattr(bot, "status", None) in (APPROVED, PAUSED))


def option_refusal(anonymous, audience, coauthors):
    """A bot's post is public, signed and its own: never anonymous, never for a Circle, never co-authored."""
    if anonymous:
        return tr("A bot can't post anonymously.")
    if audience:
        return tr("A bot's posts are public.")
    if coauthors:
        return tr("A bot's posts can't have co-authors.")
    return None


def loopback_for(bot_id):
    """The address the job posts from for this bot: its own, so bots never share the posting throttle's per-address
    bucket (helpers/throttle.py) and never share an address with a member (helpers/guild_limits.addresses_of)."""
    n = int(bot_id)                                   # one address each, never .0 or .255, up to 16 million bots
    return f"127.{(n // (254 * 256)) % 256}.{(n // 254) % 256}.{n % 254 + 1}"


def next_check(now, check_minutes, fail_count):
    """When to read a source again: its interval (next_interval), doubled for each failure in a row, at most a day."""
    wait = max(FLOOR_MINUTES, int(check_minutes or FLOOR_MINUTES)) * 60
    if fail_count:
        wait = min(BACKOFF_MAX, wait * (2 ** min(int(fail_count), 12)))
    return _now(now) + wait


def item_key(guid, link):
    """A feed item's stable key: its guid (or id) when it has one, else its link."""
    basis = (str(guid or "").strip() or str(link or "").strip())
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def clean_link(url):
    """A feed item's link without its tracking parameters (the BBC's at_medium / at_campaign, utm_*, click ids).
    Every other parameter stays, in its order, and so does the fragment; the "?" goes when nothing is left."""
    parts = urlsplit(url)
    if not parts.query:
        return url
    kept = [pair for pair in parts.query.split("&") if pair and not _TRACKING_PARAM.match(pair.split("=", 1)[0])]
    return urlunsplit(parts._replace(query="&".join(kept)))


def is_stale(published, now=None):
    """Too old to post when first seen: a promo the feed keeps (BBC's "Sign up now") or an old story put back.
    An item with no date is never stale."""
    return bool(published) and published < _now(now) - STALE_AFTER


def clean_title(raw, limit=TITLE_MAX):
    """A feed item's title as a post title: plain text on one line, cut to the post title limit."""
    text = html.unescape(_TAG.sub(" ", html.unescape(str(raw or ""))))
    text = _SPACE.sub(" ", _CONTROL.sub("", text)).strip()
    if len(text) > limit:
        text = text[:limit - 1].rstrip() + "…"
    return text
