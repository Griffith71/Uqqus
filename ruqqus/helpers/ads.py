"""Ads (Google AdSense): whether a page may show one. The rules, no I/O.

Nothing shows unless the site is given an AdSense client id (`ADSENSE_CLIENT`, "ca-pub-" and digits)
and a slot id for that kind of place (`ADSENSE_SLOT_FEED`, `_POST`, `_SIDEBAR`). Without them there is no ad
script, no slot, no consent link and no /ads.txt anywhere.

Pages are opt-in: only the feed (home.html's post list), the post page and the right sidebar of a page
that shows posts call `ad_slot` (routes/ads.py). Even there, no ad for:
  - a Premium member (an ad-free perk), checked without renewing their Premium;
  - a viewer who chose the Child word filter, and every child account (locked to it: helpers/age_declaration.py). Someone browsing
    at the Child level only because of where they are (the UK, the EU/EEA or Australia, with no confirmed age: the regional rule in helpers/visibility.py)
    still gets the ads, non-personalised (the owner, 2026-11-19), so this goes by the account's own level (`viewer_allows`);
  - a side-panel frame (?embed=1);
  - a post that is not public (a Circle or private-guild post), removed by the site or by its guild's moderators, deleted,
    marked sensitive, or rated profane or extreme by the word filter (AdSense allows no ads beside such content);
  - a guild that is private, a Circle guild, banned, sensitive, NSFL or has a filtered name.

Google judges the whole page, what members wrote included, so one card, comment, quoted post or live update that fails takes
every box off the page (`page_allows`, `post_page_allows`, `quote_allows`, `live_thread_allows`). A search's page has none: the
query would be in the address it reports. Of the pages with a post in hand only the post's own page counts (`POST_ENDPOINTS`): the
shared-draft pages show old revisions, which nothing here reads.

Ads are `live` with a client id and at least one slot. Only that is what the reader is told about and what loads the tag;
a client id alone serves /ads.txt, so the site can be verified before Google has approved anything.

The creator fund (helpers/creator_fund.py) and the Creator Studio (helpers/studio.py) have nothing to do with ads: they must never
read anything here. Ad income stays the owner's, in AdSense's own reports; the site holds no figure of it
(tests/test_ad_income_stays_out_assets.py).
"""
import os
import re

from ruqqus.helpers import age_declaration, consent
from ruqqus.helpers.visibility import CHILD, stored_level

PAGES = ("feed", "post", "sidebar")
PLACE_LABELS = {"feed": "Feed", "post": "Under a post", "sidebar": "Right sidebar"}
FEED_EVERY = 8              # an in-feed ad after every 8 post cards (assets/js/ads.js)
SEARCH_ENDPOINTS = frozenset(("search", "search_guild"))
# the post's own page. Anything else with a post in its context (a shared draft's revisions and who-wrote-what) is not judged here
POST_ENDPOINTS = frozenset(("post_base36id_with_comments", "post_pid_comment_cid"))
PREKEY_GRACE = 15 * 60      # no tag for this long after a password sign-in, so chat can take the key made from it

CLIENT = re.compile(r"ca-pub-[0-9]{10,20}")
SLOT = re.compile(r"[0-9]{5,20}")
LAYOUT_KEY = re.compile(r"[-+a-z0-9]{1,40}")

# Google's line for ads.txt: its seller id is fixed (support.google.com/adsense/answer/12171612)
ADS_TXT = "google.com, {publisher}, DIRECT, f08c47fec0942fa0\n"


def config(environ=None):
    """The site's AdSense settings, or None when ads are off. Every value is checked against a fixed
    pattern: it is written into the page."""
    environ = os.environ if environ is None else environ
    client = (environ.get("ADSENSE_CLIENT") or "").strip()
    if not CLIENT.fullmatch(client):
        return None
    slots = {}
    for page in PAGES:
        slot = (environ.get(f"ADSENSE_SLOT_{page.upper()}") or "").strip()
        if SLOT.fullmatch(slot):
            slots[page] = slot
    key = (environ.get("ADSENSE_FEED_LAYOUT_KEY") or "").strip()
    return {"client": client, "publisher": client[len("ca-"):], "slots": slots,
            "feed_layout_key": key if LAYOUT_KEY.fullmatch(key) else ""}


def live(cfg):
    """Are ads really on? A client id and at least one slot. What a reader is told, and what loads Google's tag."""
    return cfg is not None and bool(cfg["slots"])


def status(cfg):
    """What the admins' money page says about ads (templates/admin/money.html): which of the three states the site is in and
    which places have a slot, by name only. Never the client id or a slot id: the page has no need of them. It says what the
    site is set to do, not whether Google approved it or fills a box, which only AdSense's own reports can say."""
    if cfg is None:
        return {"state": "off", "places": (), "unset": PAGES, "feed_layout_key": False}
    places = tuple(page for page in PAGES if page in cfg["slots"])
    return {"state": "live" if places else "client_only", "places": places,
            "unset": tuple(page for page in PAGES if page not in places), "feed_layout_key": bool(cfg["feed_layout_key"])}


def hold_after_password(signed_in, now):
    """Typing a password writes the key chat unlocks itself with (assets/js/chat_key.js), and partials/ad_script.html removes it
    just before Google's tag. So no ad on this browser for a while, to give chat the chance to take it: a password sign-in, a
    sign-up and a password change in Settings. Nothing is written while ads are off."""
    if live(config()):
        signed_in["no_ads_until"] = now + PREKEY_GRACE


def held_after_sign_in(until, now):
    """Is this signed-in browser still inside the time after a password sign-in when no tag is drawn? `until` is the
    session's `no_ads_until`. Only ever asked for a page drawn for a member: such a page is private, and a visitor's page,
    which a cache may keep, never depends on a session value."""
    return isinstance(until, int) and now < until


def personalised_allowed(country, viewer):
    """May Google personalise the ads on this page? Never in the UK, the EEA and Switzerland or where the country isn't known
    (`consent.needs_asking`, Google's own list), whoever is reading; never for anyone browsing at the Child level for want of a
    confirmed age where the law asks for one (`age_declaration.region_locked`: Australia is strict but not one of the consent
    regions, so a ticked "18 or over" there counts for nothing, as it counts for nothing in what they may read; the owner,
    2026-11-19); elsewhere only for an account that said it is 18 or over. So never for a visitor, whom nothing says is an
    adult, or a member who hasn't answered: a visitor's page doesn't depend on the country at all, and a member's is private
    (`Vary: CF-IPCountry` covers every page besides)."""
    if viewer is None or consent.needs_asking(country) or age_declaration.region_locked(viewer, country):
        return False
    return getattr(viewer, "age_declared", None) == age_declaration.ADULT


def viewer_allows(viewer, embedded=False):
    if embedded:
        return False
    if viewer is None:
        return True
    if getattr(viewer, "has_premium_no_renew", False):     # never has_premium: reading it spends a coin
        return False
    # the account's own level, not the regional rule: a visitor or an unconfirmed adult in a strict country still gets the
    # (non-personalised) ads they had; a locked account, which is on the Child filter, gets none
    return stored_level(viewer) < CHILD and not age_declaration.is_locked(viewer)


def board_allows(board):
    if board is None:
        return True
    return not (board.is_banned or board.is_private or getattr(board, "is_circle", False) or board.is_sensitive
                or getattr(board, "is_nsfl", False) or (getattr(board, "name_severity", 0) or 0) > 0)


def post_allows(post):
    if post is None:
        return True
    if post.is_banned or post.hidden_by_guild or post.deleted_utc or not post.post_public:
        return False
    if post.is_sensitive or (post.word_severity or 0) > 0:
        return False
    # a sensitive account's post, unless anonymous (never judged by whose it is: helpers/sensitive.py)
    if not getattr(post, "is_anonymous", False) and not account_allows(getattr(post, "author", None)):
        return False
    return board_allows(getattr(post, "board", None))


def account_allows(account):
    """A sensitive account's profile and posts get no ads, like a sensitive guild's."""
    return account is None or not getattr(account, "is_sensitive", False)


# what a comment can show that isn't words. The site's own emoji and a mention's avatar are pictures too and don't count, but the class
# alone doesn't say it is one: a member can type <img class="emoji" src="/media/..."> and helpers/sanitize.py keeps that class. So the
# address has to be the one the renderer writes (helpers/markdown.py), whole, with nothing in it a browser would read as another folder.
_MEDIA = re.compile(r"<(?:img|audio|video|iframe|source)\b[^>]*>", re.I)
_ATTRIBUTE = r"""(?<![\w-])%s\s*=\s*["']([^"']*)["']"""
_CLASS = re.compile(_ATTRIBUTE % "class", re.I)
_SRC = re.compile(_ATTRIBUTE % "src", re.I)
_OWN_PICTURES = {"emoji": re.compile(r"/assets/images/(?:emojis|primojis)/[\w+-]+(?:\.[\w+-]+)*"),
                 "profile-pic-20": re.compile(r"/(?:uid/\w+|@\w+|\+\w+)/pic/profile")}
_TAG = re.compile(r"<[^>]*>")


def _own_picture(tag):
    classes, src = _CLASS.search(tag), _SRC.search(tag)
    if not classes or not src or not tag.lower().startswith("<img"):
        return False
    return any(name in classes.group(1).split() and address.fullmatch(src.group(1)) for name, address in _OWN_PICTURES.items())


def holds_media(html):
    return any(not _own_picture(found.group(0)) for found in _MEDIA.finditer(html or ""))


def comment_allows(comment):
    """Is a comment fit to stand on a page with ads? A removed or deleted one is drawn as a placeholder, so it can't count
    against the page. A picture or a sound in a comment is something nobody has looked at. A sensitive account's comment counts
    against it as its post would, unless anonymous (never judged by whose it is: helpers/sensitive.py)."""
    if comment.is_banned or comment.deleted_utc:
        return True
    if comment.is_sensitive or (comment.word_severity or 0) > 0:
        return False
    if not getattr(comment, "is_anonymous", False) and not account_allows(getattr(comment, "author", None)):
        return False
    return not holds_media(comment.body_html)


def quote_allows(quote):
    """May a page carry ads with this quote card on it (helpers/jinja2.py quote_of, as drawn for this reader)? A post on this site
    answers as a card of its own would, and so does the post quoted inside it. A notice ("This post isn't available") shows nothing
    of the post. A post on another site can't be judged, so it doesn't count (CLAUDE.md "Ads" says so)."""
    while quote:
        if quote.get("kind") == "post" and quote.get("post") is not None and not post_allows(quote["post"]):
            return False
        quote = quote.get("inner")
    return True


def live_thread_allows(live, updates=(), summary_severity=0, summary_html=""):
    """May a post page with a live thread carry ads? Not while it is live: its updates arrive on the open page, where nothing here
    can judge them. After that, by what is drawn: `updates` are (word_severity, body_html) of the updates on the page, and the
    pinned summary answers the same way."""
    if live or (summary_severity or 0) > 0 or holds_media(summary_html):
        return False
    return not any((severity or 0) > 0 or holds_media(html) for severity, html in updates)


def page_allows(posts=(), comments=()):
    """May this page of cards carry ads at all? One that fails takes every box off the page. A comment in a list (a profile's
    comments) answers for the post it sits under too, since its card names that post."""
    return all(post_allows(post) for post in posts) and all(
        comment_allows(comment) and post_allows(getattr(comment, "post", None)) for comment in comments)


def post_page_allows(post, comments=()):
    """May this post's page carry ads? As `page_allows`, for the comments drawn on it, and not with nothing to read: a post
    with no text and no comment is Google's low-value page, whatever else it links to."""
    if not post_allows(post) or not all(comment_allows(comment) for comment in comments):
        return False
    return bool(_TAG.sub("", getattr(post, "body_html", "") or "").strip()) or any(
        not (comment.is_banned or comment.deleted_utc) for comment in comments)


def allows(page, viewer, embedded=False, post=None, board=None, cfg=None, account=None):
    """May this place on this page show an ad to this viewer? `account` is the profile the page shows, if any."""
    if cfg is None or page not in cfg["slots"]:
        return False
    return viewer_allows(viewer, embedded) and post_allows(post) and board_allows(board) and account_allows(account)
