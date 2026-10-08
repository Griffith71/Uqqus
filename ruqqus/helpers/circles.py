"""Circles: an exclusive audience for an account's posts (and, in a later step, for a guild). The rules, with no I/O.

An account has one Circle. Two kinds of people are in it: **Close Friends**, whom the owner adds (free), and
**Subscribers**, who pay the owner's price in coins every 30 days. Something posted for an audience is seen by:

    Public        everyone
    Subscribers   subscribers AND close friends (friends get the paid area free)
    Close Friends close friends only
    (3) a Circle guild: its members, of either kind

and always by its author. An audience nobody knows about is seen by nobody (fail closed).

A subscriber's access is the date their payment runs to (`renews_utc`): when it passes they are out, even if the
renewal job is late. A subscriber who cancelled keeps access until that date and is not charged again; a
subscriber renews at the price they signed up for, so the owner raising the price never charges them more.
"""
import re
import time

PUBLIC = 0
SUBSCRIBERS = 1
FRIENDS = 2
GUILD = 3
# not a choice, never stored on a post or a story: what a PUBLIC story's picture is treated as when it is served (it must
# not be cached by a CDN past the story's 24 hours, so it is shown only to a signed-in viewer who is not blocked)
STORY_OPEN = 8

# what a member may choose for a post on their own profile (GUILD is only for a post inside a Circle guild)
CHOICES = (PUBLIC, SUBSCRIBERS, FRIENDS)
NAMES = {PUBLIC: "Public", SUBSCRIBERS: "Subscribers", FRIENDS: "Close Friends", GUILD: "Circle guild members"}

FRIEND = "friend"
SUBSCRIBER = "subscriber"
ACTIVE = "active"
ENDED = "ended"

PRICE_MAX = 100
PERIOD = 30 * 24 * 60 * 60
FRIEND_LIMIT = 500

KEEP = "keep"
RENEW = "renew"
LAPSE = "lapse"
END = "end"

_DIGITS = re.compile(r"[0-9]{1,3}")


def parse_price(raw):
    """(coins, None) or (None, message). 0 is allowed: nobody can subscribe, the Circle is close friends only."""
    text = "" if raw is None else str(raw).strip()
    if not _DIGITS.fullmatch(text):
        return None, f"Enter a whole number of coins from 0 to {PRICE_MAX}."
    value = int(text)
    if value > PRICE_MAX:
        return None, f"The most a Circle can ask is {PRICE_MAX} coins every 30 days."
    return value, None


def parse_audience(raw):
    """(audience, None) or (None, message). An absent field is Public."""
    text = "" if raw is None else str(raw).strip()
    if text == "":
        return PUBLIC, None
    if text.isascii() and text.isdigit() and int(text) in CHOICES:
        return int(text), None
    return None, "Choose who can see this: Public, Subscribers or Close Friends."


def tier_of(member_tier, status, renews_utc, now):
    """The tier that counts for access right now (FRIEND, SUBSCRIBER) or None."""
    if status != ACTIVE:
        return None
    if member_tier == FRIEND:
        return FRIEND
    if member_tier == SUBSCRIBER and renews_utc > now:
        return SUBSCRIBER
    return None


def can_see(audience, *, is_author, tier):
    """May someone see something posted for `audience`? `tier` is theirs from `tier_of` (None: not in the Circle)."""
    if audience == PUBLIC:
        return True
    if is_author:
        return True
    if tier is None:
        return False
    if audience == FRIENDS:
        return tier == FRIEND
    if audience in (SUBSCRIBERS, GUILD):
        return tier in (FRIEND, SUBSCRIBER)
    return False


def post_refusal(audience, *, anonymous=False, coauthors=False, forwards=False, own_video=False, shared=False):
    """Why a post cannot be made for an audience other than Public, or None. A Circle post stays between the author and
    the people in the Circle: it cannot be anonymous (they know who it is), have co-authors (their audience is not
    this one), be forwarded to a guild, or carry a video uploaded to the author's own YouTube channel (a link anyone
    holding it can watch)."""
    if audience == PUBLIC:
        return None
    if anonymous:
        return "A post for your Circle can't be anonymous: the people in it already know who you are."
    if coauthors:
        return "A post for your Circle can't have co-authors."
    if forwards:
        return "A post for your Circle can't be forwarded to a guild."
    if shared:
        return "This post has already been forwarded or reposted, so it can't be moved to your Circle."
    if own_video:
        return "A video uploaded to your YouTube channel can't be kept private to your Circle: anyone with its link can watch it. Share it publicly, or use a picture."
    return None


def refusal_to_add_friend(*, owner_id, target_id, blocked, target_gone, friends, already):
    """Why the owner cannot add this account as a close friend, or None. Never says who blocked whom."""
    if target_id == owner_id:
        return "You are always in your own Circle."
    if target_gone:
        return "That account isn't available."
    if blocked:
        return "You can't add that account."
    if already:
        return "They are already one of your Close Friends."
    if friends >= FRIEND_LIMIT:
        return f"You can have {FRIEND_LIMIT} Close Friends at most."
    return None


def refusal_to_subscribe(*, owner_id, viewer_id, price, blocked, owner_gone, tier, cancelled):
    """Why this account cannot subscribe to the Circle, or None. `tier` is the viewer's current one from `tier_of`.
    A cancelled subscriber who still has access is not refused: subscribing again just resumes it (the caller
    checks `cancelled` and does not charge)."""
    if viewer_id == owner_id:
        return "You are always in your own Circle."
    if owner_gone:
        return "That account isn't available."
    if blocked:
        return "You can't subscribe to this Circle."
    if tier == FRIEND:
        return "You are already one of their Close Friends, so you can see everything without subscribing."
    if tier == SUBSCRIBER and not cancelled:
        return "You are already subscribed."
    if price <= 0:
        return "This Circle isn't taking subscribers."
    return None


def renewal(now, renews_utc, cancelled, balance, price):
    """What to do with a subscriber: KEEP (paid ahead), RENEW (take `price` coins), LAPSE (they cannot pay) or END
    (they cancelled, or their price is gone)."""
    if renews_utc > now:
        return KEEP
    if cancelled or price <= 0:
        return END
    if balance < price:
        return LAPSE
    return RENEW


def next_renewal(now, renews_utc):
    """The next date a payment runs to. A late renewal counts from now, never from a date long past."""
    return max(now, renews_utc) + PERIOD


def date_text(timestamp):
    """A day as the site writes it ("08 October 2026"), in UTC."""
    return time.strftime("%d %B %Y", time.gmtime(int(timestamp or 0)))


def describe_price(price):
    return "Close Friends only" if price <= 0 else f"{price} coin{'s' if price != 1 else ''} every 30 days"
