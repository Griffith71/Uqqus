import time
import json
from os import environ, path
from sqlalchemy import text, func
from flask import g
import calendar
import re
from urllib.parse import quote_plus

from ruqqus.classes.user import User
from ruqqus.helpers import compact_age as compact_age_rules
from .get import *
from .regions import REGION_COUNTRIES, COUNTRY_NAMES
import requests

from ruqqus.__main__ import app, cache, db_session


post_regex = re.compile(r"^https?://[a-zA-Z0-9_.-]+/\+\w+/post/(\w+)(/[a-zA-Z0-9_-]+/?)?$")


# How a viewer reads scores (users.score_display): 0, the default and what visitors get, is upvotes coloured by their
# share of the votes (helpers/vote_colour.py); 1 is upvotes minus downvotes. Only those two values are ever drawn.
SCORE_UPVOTES, SCORE_NET = 0, 1


@app.template_global("compact_age")
def compact_age(timestamp, now=None):
    """A short age for a row of the notifications page: "now", "5m", "2h", "3d", then a short date ("Oct 25")."""
    from ruqqus.helpers.i18n_runtime import date_text, tr
    seconds = max(0, int((time.time() if now is None else now) - int(timestamp or 0)))
    parts = compact_age_rules.parts(seconds)
    if parts is None:
        return date_text(timestamp, "%b %d", "MMM d")
    unit, number = parts
    if unit == "now":
        return tr("now")
    return {"m": lambda: tr("%(num)sm", num=number), "h": lambda: tr("%(num)sh", num=number),
            "d": lambda: tr("%(num)sd", num=number)}[unit]()


@app.template_global("fold_comments")
def fold_comments(c, v, linked=None):
    """(shown, folded): the comments under `c`, the low-scoring ones held back behind "Show N more comments"
    (helpers/reply_fold.py), by the score this reader sees. Never folded: the reader's own comments, and `linked`
    (the comment a permalink points to) with the comments above it."""
    from ruqqus.helpers.reply_fold import fold
    net = score_mode(v) == SCORE_NET

    def score_of(x):
        return x.score_fuzzed if net else x.upvotes_fuzzed

    path = set()
    node, steps = linked, 0
    while node is not None and steps < 8:
        path.add(node.id)
        node, steps = getattr(node, "parent_comment", None), steps + 1

    def keep(x):
        return x.id in path or (v is not None and x.author_id == v.id)

    return fold(score_of(c), c.nested_comments, score_of, keep)


@app.template_global("excerpt_of")
def excerpt_of(p, with_media=False):
    """(text, more): the first lines of a post's body for its feed card (helpers/post_excerpt.py); a card that also
    shows a picture or link shows two lines, not four."""
    from ruqqus.helpers.post_excerpt import SHOWS_TEXT, SHOWS_WITH_MEDIA, excerpt
    return excerpt(p.body_html, shows=SHOWS_WITH_MEDIA if with_media else SHOWS_TEXT)


@app.template_filter("compact_count")
def compact_count_filter(count):
    """999, 1.2k, 12k, 1.2M (helpers/compact_count.py): scores and counts where room is short."""
    from ruqqus.helpers.compact_count import compact
    return compact(count)


@app.template_global("language_name")
def language_name(code):
    """A language's row label from a stored code (helpers/languages.py: "mk" reads "Bulgarian"), or ""
    for no language or one no service translates. Named in the reader's interface language (helpers/i18n.py: English
    is exactly the row label)."""
    from ruqqus.helpers import i18n
    from ruqqus.helpers.i18n_runtime import current_locale
    return i18n.language_name(code, current_locale()) or ""


@app.template_global("reading_minutes")
def reading_minutes(p):
    """About how long a post's text takes to read, in whole minutes (230 words a minute, rounded up)."""
    words = len((getattr(p, "body", "") or "").split())
    return -(-words // 230) if words else 0


@app.template_global("guild_topic")
def guild_topic(b):
    """The topic a guild is in (helpers/topics.py: /topic/<slug>), or None: an Uncategorized, hidden or NSFW one has
    none. A guild filed under a category alone is in the category's topic."""
    from ruqqus.helpers import topics
    if b is None:
        return None
    if b.cat_id:
        category = b.category
        found = topics.find(category.name) if category is not None else None
        return found if found is not None and b.cat_id in found.cat_ids else None
    sub = getattr(b, "subcat", None)
    if sub is None:
        return None
    found = topics.find(sub.name)
    return found if found is not None and b.subcat_id in found.subcat_ids else None


@app.template_global("post_places")
def post_places(p, v):
    """The other places a post is discussed, most traction first (helpers/post_places.py)."""
    from ruqqus.helpers.post_places import places_for
    return places_for(p, v)


@cache.memoize(timeout=60)
def _standing_counts(sql_name, board, start, end, score):
    from flask import g
    from ruqqus.helpers import post_standing
    return post_standing.counts(g.db, getattr(post_standing, sql_name), board, start, end, score)


@app.template_global("post_standing")
def post_standing_for(p):
    """Where a post stands among the posts made around it (helpers/post_standing.py), the counts cached a minute."""
    from flask import g
    from ruqqus.helpers import post_standing
    names = {post_standing.GUILD_SQL: "GUILD_SQL", post_standing.SITE_SQL: "SITE_SQL"}
    return post_standing.standing_for(g.db, p, app_config("SITE_NAME"),
                                      counted=lambda db, sql, *args: _standing_counts(names[sql], *args))


@app.template_global("upvote_share")
def upvote_share(ups, downs):
    """The share of the votes that are upvotes, a whole percent, or None with no votes (Post Info's "N% upvoted")."""
    total = (ups or 0) + (downs or 0)
    return round(100 * (ups or 0) / total) if total > 0 else None


@app.template_global("score_mode")
def score_mode(v):
    return SCORE_NET if v is not None and getattr(v, "score_display", 0) == SCORE_NET else SCORE_UPVOTES


@app.template_global("ratio_class")
def ratio_class_global(ups, downs):
    from ruqqus.helpers.vote_colour import ratio_class
    return ratio_class(ups, downs)


@app.template_global("player_of")
def player_of(p):
    """How a post's player is drawn (helpers/social_embeds.player_layout): {"load": "auto"|"click"|"now", "aspect",
    "height"}, or None when the post has no player."""
    from ruqqus.helpers import social_embeds

    return social_embeds.player_layout(getattr(p, "embed_kind", None), getattr(p, "embed_url", "") or "")


@app.template_global("quote_of")
def quote_of(p, v):
    """What a post quotes, for its quote card (helpers/quote_cards.py, partials/quote_card.html): a social site's post
    ({"kind": "social", ...}), a post on this site drawn for this reader ({"kind": "post", "post", "notice"}), or None.
    As on X, the card shows what IT quotes inside (`inner`), and no deeper (`quoting`, one line)."""
    from jinja2 import Undefined

    return _quote_for(p, None if isinstance(v, Undefined) else v, 0)


def _quote_for(p, v, depth):
    """The quote of post `p` at `depth` 0 (under the member's words) or 1 (inside that card: no card of its own inside)."""
    from ruqqus.helpers import quote_cards

    url = getattr(p, "url", None)
    if not url or getattr(p, "is_image", False):
        return None
    quoted = quote_cards.quoted_post_id(url, app.config["SERVER_NAME"], app.config.get("SHORT_DOMAIN"))
    if quoted:
        return None if quoted == p.base36id else _post_quote(quoted, v, depth)
    # a video, song or GIF from a social site plays in its own player instead (social_embeds.player_layout)
    if getattr(p, "embed_kind", None) in ("iframe", "vertical", "audio", "image", "video"):
        return None
    card = quote_cards.social_card(url, p.embed_url or "", p.meta_title or "", p.meta_description or "", p.preview_image_url or "")
    return quote_cards.inside(card) if depth else card


def _post_quote(b36, v, depth=0):
    """A post on this site as this reader may see it, asked once per request: the rules of opening it
    (quote_cards.post_notice). A post the reader may not see at all (a Circle, a private guild) is only "not available".
    What it quotes is drawn inside it by the same rules (`inner`), one level only."""
    from ruqqus.helpers import quote_cards
    from ruqqus.helpers.muting import is_muted
    from ruqqus.helpers.visibility import post_hidden

    cache = g.__dict__.setdefault("_quote_posts", {})
    key = (b36, v.id if v else None, depth)
    if key not in cache:
        post = get_post(b36, v=v, graceful=True)
        missing = post is None or post.in_private_guild_for(v)
        notice = quote_cards.UNAVAILABLE if missing else quote_cards.post_notice(
            missing=False, deleted=bool(post.deleted_utc), removed=bool(post.is_banned or post.board.is_banned),
            filtered=post_hidden(post, v), blocked=bool(v and post.author_id != v.id and v.any_block_exists(post.author)),
            muted=is_muted(post, v), anonymous=bool(post.is_anonymous))
        inner = None if notice or depth else _quote_for(post, v, 1)
        quoting = "" if notice or not depth else quote_cards.quoting_line(post.url, app.config["SERVER_NAME"], app.config.get("SHORT_DOMAIN"))
        cache[key] = {"kind": "post", "post": None if notice else post, "notice": notice, "inner": inner, "quoting": quoting}
    return cache[key]


@app.template_global("may_share")
def may_share_global(post, v):
    """Draw a post's share controls? (helpers/sharing.py: anyone when it is public, only its author otherwise)"""
    from ruqqus.helpers.sharing import may_share
    return may_share(post, v)


@app.template_global("may_share_comment")
def may_share_comment_global(comment, v):
    from ruqqus.helpers.sharing import may_share_comment
    return may_share_comment(comment, v)


@app.template_global("handle_tag")
def handle_tag(u):
    """The @handle drawn muted after a display name in a byline or a list, as on x.com. Nothing for an account
    with no display name (its handle is already the name) and for the anonymous stand-in. Hidden on a phone,
    where a byline has no room for both (the profile always shows the handle)."""
    from markupsafe import Markup, escape

    if not getattr(u, "display_name", None) or not getattr(u, "username", None):
        return ""
    return Markup('<span class="user-handle text-muted ml-1 d-none d-sm-inline">@%s</span>') % escape(u.username)


# The About box of a profile (partials/profile_about.html, helpers/profile_about.py)
@app.template_global("profile_guilds")
def profile_guilds(u, v):
    from ruqqus.helpers.profile_about import guilds_for
    return guilds_for(u, v)


@app.template_global("post_author_box")
def post_author_box(p, v):
    """The author whose About box a profile post's page shows in its sidebar, or None (helpers/profile_about.py
    author_box_shown: never for an anonymous post)."""
    if p is None or getattr(p, "is_anonymous", False):
        return None
    from ruqqus.helpers.anonymity import author_of
    from ruqqus.helpers.profile_about import author_box_shown
    author = author_of(p, v)
    return author if author_box_shown(p, author, v) else None


@app.template_global("profile_curations")
def profile_curations(u, v):
    from flask import g
    from ruqqus.helpers.profile_about import curations_for
    return curations_for(g.db, u, v)


@app.template_filter("total_users")
@cache.memoize(timeout=60)
def total_users(x):
    db = db_session()
    return db.query(User).filter_by(is_banned=0).count()


@app.template_filter("region_country_list")
def region_country_list(region_code):
    if not region_code:
        return []
    codes = REGION_COUNTRIES.get(region_code, [])
    from ruqqus.helpers import i18n
    from ruqqus.helpers.i18n_runtime import babel_locale, current_locale
    if current_locale() == i18n.ENGLISH:
        return sorted(COUNTRY_NAMES.get(c, c) for c in codes)
    # another interface language: CLDR's names for the countries (English for one it doesn't name)
    from babel import Locale
    try:
        names = Locale.parse(babel_locale()).territories
    except (ValueError, LookupError):
        names = {}
    return sorted(names.get(c) or COUNTRY_NAMES.get(c, c) for c in codes)


@app.template_filter("source_code")
@cache.memoize(timeout=60 * 60 * 24)
def source_code(file_name):
    # Use a path relative to the project root, not the home directory
    import os
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    abs_path = os.path.join(base_dir, file_name)
    try:
        with open(abs_path, mode="r") as f:
            return f.read()
    except Exception as e:
        return f"[Error loading source: {e}]"


@app.template_filter("full_link")
def full_link(url):

    return f"https://{app.config['SERVER_NAME']}{url}"


@app.template_filter("env")
def env_var_filter(x):

    x = environ.get(x, 1)

    try:
        return int(x)
    except BaseException:
        try:
            return float(x)
        except BaseException:
            return x


@app.template_filter("js_str_escape")
def js_str_escape(s):

    s = s.replace("'", r"\'")

    return s


@app.template_filter("is_mod")
@cache.memoize(60)
def jinja_is_mod(uid, bid):

    return bool(get_mod(uid, bid))

@app.template_filter("coin_goal")
@cache.cached(timeout=600, key_prefix="premium_coin_goal")
def coin_goal(x):
    
    now = time.gmtime()
    midnight_month_start = time.struct_time((now.tm_year,
                                              now.tm_mon,
                                              1,
                                              0,
                                              0,
                                              0,
                                              now.tm_wday,
                                              now.tm_yday,
                                              0)
                                             )
    cutoff = calendar.timegm(midnight_month_start)
    
    coins=g.db.query(func.sum(PayPalTxn.coin_count)).filter(
        PayPalTxn.created_utc>cutoff,
        PayPalTxn.status==3).all()[0][0] or 0
    
    
    return int(100*coins/500)


@app.template_filter("app_config")
def app_config(x):
    return app.config.get(x)

# @app.template_filter("eval")
# def eval_filter(s):

#     return render_template_string(s)

@app.template_filter("urlencode")
def urlencode(s):
    return quote_plus(s)


# @app.template_filter("general_chat_count")
# def general_chat_count(x):
#     return get_guild("general").chat_count


@app.template_filter("lines")
def lines_count(x):

    return x.count("\n")+1
