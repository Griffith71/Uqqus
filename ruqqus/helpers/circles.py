"""Circles: the audiences an account shares with, beside Public. The rules, with no I/O.

An account has three kinds of circle:

    Friends   everyone it follows who follows it back, right now. Every account has it from the start; nobody is added
              or removed by hand, and no members are stored (helpers/circle_store.py `viewer_circle_ids` asks the
              follows when a post is read).
    custom    a circle the owner names ("Family", "Uni friends in Spain") and fills by @name. Nobody is told they were
              added, and nothing they see names the circle: a post shows "Shared with a circle".
    paid      a circle people pay the owner's price in coins to be in, every 30 days. Making one needs Premium; one that
              exists keeps renewing whatever happens to the owner's Premium. Its name is shown, since people join it.

A Circle guild's circle (kind 'guild') is its paying members (helpers/circle_store.py, the Circle guild part).

A post or a story is Public (0) or for some of its author's circles (CIRCLES, 4: the circles are listed for it), and is
seen by its author and by anyone in ANY of those circles (`can_see`). A post made inside a Circle guild is GUILD (3).
An audience nobody knows about is seen by nobody but the author (fail closed).

A subscriber's access is the date their payment runs to (`renews_utc`): when it passes they are out, even if the
renewal job is late. A subscriber who cancelled keeps access until that date and is not charged again; a subscriber
renews at the price they signed up for, so the owner raising the price never charges them more.
"""
import re
import unicodedata

from babel.support import LazyProxy

from ruqqus.helpers.i18n_runtime import N_, msg, tr, tr_lazy, trn, trp
from ruqqus.helpers.i18n_runtime import date_text as _date_text

PUBLIC = 0
GUILD = 3
CIRCLES = 4
# not a choice, never stored on a post or a story: what a PUBLIC story's picture is treated as when it is served (it must
# not be cached by a CDN past the story's 24 hours, so it is shown only to a signed-in viewer who is not blocked)
STORY_OPEN = 8
# not stored either: a post in a private guild that was never public there (or a comment on one), whose pictures follow
# the post page's own rule (Submission.in_private_guild_for: its author, or whoever may view the guild); never cached
PRIVATE_GUILD = 9

# who a post is for, shown only ("Public" alone is ambiguous: it is the audience, with its context)
NAMES = {PUBLIC: LazyProxy(lambda: trp("audience", "Public"), enable_cache=False), CIRCLES: tr_lazy("Circles"),
         GUILD: tr_lazy("Circle guild members")}

# kinds of circle
FRIENDS = "friends"
CUSTOM = "custom"
PAID = "paid"
GUILD_KIND = "guild"
# what a post form names the Friends circle by (its row is made the first time it is chosen)
FRIENDS_TOKEN = "friends"
FRIENDS_NAME = N_("Friends")          # compared as it is (a circle of your own can't take it); shown with msg()

# a member row's tier: added by the owner (custom circles) or paying (paid circles, Circle guilds)
MEMBER = "member"
SUBSCRIBER = "subscriber"
ACTIVE = "active"
ENDED = "ended"

PRICE_MAX = 100
PERIOD = 30 * 24 * 60 * 60
CUSTOM_LIMIT = 20                 # custom circles per account
PAID_LIMIT = 5                    # paid circles per account
MEMBER_LIMIT = 500                # people in one custom circle
NAME_MAX = 40

KEEP = "keep"
RENEW = "renew"
LAPSE = "lapse"
END = "end"

_DIGITS = re.compile(r"[0-9]{1,3}")
_ID = re.compile(r"[0-9]{1,9}")      # inside a database integer (ten digits could pass 2**31: a 500, bug hunt)
_SPACES = re.compile(r"\s+")
_JOINER = chr(0x200D)
_MARKDOWN = re.compile(r"[\[\]()*_`~<>#|!\\]")


def parse_price(raw):
    """(coins, None) or (None, message). 0 is allowed: the paid circle takes no new subscribers."""
    text = "" if raw is None else str(raw).strip()
    if not _DIGITS.fullmatch(text):
        return None, tr("Enter a whole number of coins from 0 to %(max)s.", max=PRICE_MAX)
    value = int(text)
    if value > PRICE_MAX:
        return None, trn("The most a circle can ask is %(num)s coin every 30 days.",
                         "The most a circle can ask is %(num)s coins every 30 days.", PRICE_MAX)
    return value, None


def parse_name(raw):
    """(name, None) or (None, message) for a circle the owner names. Plain text: spaces collapsed, no control or
    invisible characters, at most NAME_MAX characters. "Friends" is the automatic circle's own name."""
    text = _SPACES.sub(" ", str(raw or ""))
    # no control or invisible characters (right-to-left overrides, zero-width spaces), but the joiner emoji are made of
    text = "".join(ch for ch in text if ch == _JOINER or unicodedata.category(ch)[0] != "C").strip()
    if not text:
        return None, tr("Give the circle a name.")
    if len(text) > NAME_MAX:
        return None, trn("A circle's name can be %(num)s character at most.",
                         "A circle's name can be %(num)s characters at most.", NAME_MAX)
    if text.casefold() == FRIENDS_NAME.casefold():
        return None, tr("Friends is your automatic circle of mutual follows. Choose another name.")
    return text, None


DESCRIPTION_MAX = 300
_BLANK_LINES = re.compile(r"\n{3,}")


def parse_description(raw):
    """(description, None) or (None, message) for what a paid circle's owner says subscribers get. Plain text: line
    breaks kept (at most one blank line), no control or invisible characters, DESCRIPTION_MAX characters. Empty clears
    it."""
    lines = str(raw or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    clean = []
    for line in lines:
        line = "".join(ch for ch in line if ch == _JOINER or unicodedata.category(ch)[0] != "C")
        clean.append(_SPACES.sub(" ", line).strip())
    text = _BLANK_LINES.sub("\n\n", "\n".join(clean)).strip()
    if len(text) > DESCRIPTION_MAX:
        return None, trn("A description can be %(num)s character at most.",
                         "A description can be %(num)s characters at most.", DESCRIPTION_MAX)
    return text, None


def parse_audience(raw_audience, raw_circles=()):
    """(audience, tokens, None) or (None, (), message) from a form: `audience` is 0 (Public) or 4 (circles), and
    `circles` the chosen circles, each FRIENDS_TOKEN or a circle id. Whose circles they are is the store's to check
    (circle_store.resolve). An absent audience with circles chosen means circles; with none, Public. An audience that
    names no circle is refused rather than made public."""
    text = "" if raw_audience is None else str(raw_audience).strip()
    tokens = []
    for raw in raw_circles or ():
        token = str(raw or "").strip().lower()
        if not token:
            continue
        if token != FRIENDS_TOKEN and not _ID.fullmatch(token):
            return None, (), tr("Choose who can see this from your circles.")
        if token not in tokens:
            tokens.append(token)
    if text == "":
        return (CIRCLES, tuple(tokens), None) if tokens else (PUBLIC, (), None)
    if text == str(PUBLIC):
        return PUBLIC, (), None
    if text == str(CIRCLES):
        if not tokens:
            return None, (), tr("Choose at least one circle, or make it public.")
        return CIRCLES, tuple(tokens), None
    return None, (), tr("Choose who can see this: Public or some of your circles.")


def tier_of(member_tier, status, renews_utc, now):
    """The tier that counts for access right now (MEMBER, SUBSCRIBER) or None."""
    if status != ACTIVE:
        return None
    if member_tier == MEMBER:
        return MEMBER
    if member_tier == SUBSCRIBER and renews_utc > now:
        return SUBSCRIBER
    return None


def counts_in(kind, tier):
    """Does a member row of this tier put someone in a circle of this kind? A custom circle is its members, a paid
    circle and a Circle guild their paid-up subscribers. (Friends has no rows: it is the mutual follows.)"""
    if kind == CUSTOM:
        return tier == MEMBER
    if kind in (PAID, GUILD_KIND):
        return tier == SUBSCRIBER
    return False


def can_see(audience, *, is_author, viewer_circles=(), item_circles=()):
    """May someone see something made for `audience`? `viewer_circles` are the author's circles they are in right now,
    `item_circles` the circles the item is for. A Circle guild's post is the guard's to decide (its members)."""
    if audience == PUBLIC:
        return True
    if is_author:
        return True
    if audience == CIRCLES:
        return bool(set(viewer_circles) & set(item_circles))
    return False


def post_refusal(audience, *, anonymous=False, coauthors=False, forwards=False, own_video=False):
    """Why a post cannot be made for an audience other than Public, or None. A post for circles stays between the
    author and the people in them: it cannot be anonymous (they know who it is), have co-authors (their audience is
    not this one), be forwarded to a guild, or carry a video uploaded to the author's own YouTube or Dailymotion channel
    (a link anyone holding it can watch). A public post that was already shared may still move to circles: what
    happens to its copies is helpers/sharing.py `narrowing_note`."""
    if audience == PUBLIC:
        return None
    if anonymous:
        return tr("A post for your circles can't be anonymous: the people in them already know who you are.")
    if coauthors:
        return tr("A post for your circles can't have co-authors.")
    if forwards:
        return tr("A post for your circles can't be forwarded to a guild.")
    if own_video:
        return tr("A video uploaded to your YouTube or Dailymotion channel can't be kept to your circles: "
                  "anyone with its link can watch it. Share it publicly, or use a picture.")
    return None


def refusal_to_make(kind, *, count, has_premium):
    """Why the owner cannot make another circle of this kind, or None. `count`: how many of that kind they have."""
    if kind == CUSTOM:
        if count < CUSTOM_LIMIT:
            return None
        return trn("You can have %(num)s circle of your own at most.", "You can have %(num)s circles of your own at most.",
                   CUSTOM_LIMIT)
    if kind == PAID:
        if not has_premium:
            return tr("Paid circles are part of Premium.")
        if count < PAID_LIMIT:
            return None
        return trn("You can have %(num)s paid circle at most.", "You can have %(num)s paid circles at most.", PAID_LIMIT)
    return tr("That kind of circle can't be made.")


def refusal_to_add_member(*, owner_id, target_id, blocked, target_gone, members, already):
    """Why the owner cannot add this account to a custom circle, or None. Never says who blocked whom."""
    if target_id == owner_id:
        return tr("You can always see your own posts.")
    if target_gone:
        return tr("That account isn't available.")
    if blocked:
        return tr("You can't add that account.")
    if already:
        return tr("They are already in this circle.")
    if members >= MEMBER_LIMIT:
        return trn("A circle can have %(num)s person at most.", "A circle can have %(num)s people at most.", MEMBER_LIMIT)
    return None


def refusal_to_subscribe(*, owner_id, viewer_id, price, blocked, owner_gone, tier, cancelled):
    """Why this account cannot subscribe to a paid circle, or None. `tier` is the viewer's current one in it from
    `tier_of`. A cancelled subscriber who still has access is not refused: subscribing again just resumes it (the caller
    checks `cancelled` and does not charge)."""
    if viewer_id == owner_id:
        return tr("That's your own circle.")
    if owner_gone:
        return tr("That account isn't available.")
    if blocked:
        return tr("You can't subscribe to this circle.")
    if tier == SUBSCRIBER and not cancelled:
        return tr("You are already subscribed.")
    if price <= 0:
        return tr("This circle isn't taking subscribers.")
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
    """A day as the site writes it ("08 October 2026"), in UTC; in the reader's language (helpers/i18n_runtime.py)."""
    return _date_text(int(timestamp or 0), "%d %B %Y")


def describe_price(price):
    if price <= 0:
        return tr("Not taking subscribers")
    return trn("%(num)s coin every 30 days", "%(num)s coins every 30 days", price)


def plain_name(name):
    """A circle's name for a notification, which is markdown: its punctuation taken out so it can't make a link."""
    return _MARKDOWN.sub("", str(name or "")).strip() or "a paid circle"


def shared_label(names):
    """What the author is told a post or story is for: "Friends", "Friends and Family", "Friends, Family and 2 more"."""
    from ruqqus.helpers.text import word_list
    names = [msg(n) if n == FRIENDS_NAME else n for n in names if n]
    if not names:
        return tr("Only you")
    if len(names) <= 3:
        return word_list(names)
    # Translators: who a post is shared with, such as "Friends, Family and 2 more" (two circles' names, then a count)
    return trn("%(first)s, %(second)s and %(num)s more", "%(first)s, %(second)s and %(num)s more", len(names) - 2,
               first=names[0], second=names[1])


def viewer_label(paid_names):
    """What someone else is told a post is for. A custom circle's name is never shown to the people in it; a paid
    circle's is, to its subscribers (they chose it)."""
    if paid_names:
        # Translators: who can see a post, such as "For subscribers of Supporters" (the names of paid circles)
        return tr("For subscribers of %(circles)s", circles=shared_label(paid_names))
    return tr("Shared with a circle")
