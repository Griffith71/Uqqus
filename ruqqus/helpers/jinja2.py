import time
import json
from os import environ, path
from sqlalchemy import text, func
from flask import g
import calendar
import re
from urllib.parse import quote_plus

from ruqqus.classes.user import User
from .get import *
from .regions import REGION_COUNTRIES, COUNTRY_NAMES
import requests

from ruqqus.__main__ import app, cache, db_session


post_regex = re.compile(r"^https?://[a-zA-Z0-9_.-]+/\+\w+/post/(\w+)(/[a-zA-Z0-9_-]+/?)?$")


# How a viewer reads scores (users.score_display): 0, the default and what visitors get, is upvotes coloured by their
# share of the votes (helpers/vote_colour.py); 1 is upvotes minus downvotes. Only those two values are ever drawn.
SCORE_UPVOTES, SCORE_NET = 0, 1


@app.template_global("score_mode")
def score_mode(v):
    return SCORE_NET if v is not None and getattr(v, "score_display", 0) == SCORE_NET else SCORE_UPVOTES


@app.template_global("ratio_class")
def ratio_class_global(ups, downs):
    from ruqqus.helpers.vote_colour import ratio_class
    return ratio_class(ups, downs)


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
    return sorted(COUNTRY_NAMES.get(c, c) for c in codes)


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


@app.template_filter("post_embed")
def post_link_embed(url):

    matches = re.match(post_regex, url)

    b36id = matches.group(1)

    p = get_post(b36id, v=g.v, graceful=True)

    if not p or p.is_deleted or p.is_banned or not p.is_public:
        return ""

    return render_template(
        "submission_listing.html",
        listing=[p],
        v=g.v
        )

# @app.template_filter("general_chat_count")
# def general_chat_count(x):
#     return get_guild("general").chat_count


@app.template_filter("lines")
def lines_count(x):

    return x.count("\n")+1