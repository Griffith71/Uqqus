"""Ads (Google AdSense): whether a page may show one. The rules, no I/O.

Nothing shows unless the site is given an AdSense client id (`ADSENSE_CLIENT`, "ca-pub-" and digits)
and a slot id for that kind of place (`ADSENSE_SLOT_FEED`, `_POST`, `_SIDEBAR`). Without them there is no ad
script, no slot, no consent link and no /ads.txt anywhere.

Pages are opt-in: only the feed (home.html's post list), the post page and the right sidebar of a page
that shows posts call `ad_slot` (routes/ads.py). Even there, no ad for:
  - a Premium member (an ad-free perk), checked without renewing their Premium;
  - a viewer on the Child word filter;
  - a side-panel frame (?embed=1);
  - a post that is not public (a Circle or private-guild post), removed or deleted, marked sensitive, or rated
    profane or extreme by the word filter (AdSense allows no ads beside such content);
  - a guild that is private, a Circle guild, banned, sensitive, NSFL or has a filtered name.

The creator fund (helpers/creator_fund.py) has nothing to do with ads: it must never read anything here.
"""
import os
import re

from ruqqus.helpers.visibility import CHILD, viewer_level

PAGES = ("feed", "post", "sidebar")
FEED_EVERY = 8              # an in-feed ad after every 8 post cards (assets/js/ads.js)

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


def viewer_allows(viewer, embedded=False):
    if embedded:
        return False
    if viewer is None:
        return True
    if getattr(viewer, "has_premium_no_renew", False):     # never has_premium: reading it spends a coin
        return False
    return viewer_level(viewer) < CHILD


def board_allows(board):
    if board is None:
        return True
    return not (board.is_banned or board.is_private or getattr(board, "is_circle", False) or board.is_sensitive
                or getattr(board, "is_nsfl", False) or (getattr(board, "name_severity", 0) or 0) > 0)


def post_allows(post):
    if post is None:
        return True
    if post.is_banned or post.deleted_utc or not post.post_public:
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


def allows(page, viewer, embedded=False, post=None, board=None, cfg=None, account=None):
    """May this place on this page show an ad to this viewer? `account` is the profile the page shows, if any."""
    if cfg is None or page not in cfg["slots"]:
        return False
    return viewer_allows(viewer, embedded) and post_allows(post) and board_allows(board) and account_allows(account)
