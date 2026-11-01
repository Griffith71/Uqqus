"""Ads (Google AdSense): /ads.txt and the template helpers that draw a slot and load the scripts.
The rules are helpers/ads.py; nothing here runs, and nothing is drawn, unless ADSENSE_CLIENT is set."""
import time

import jinja2
from flask import Response, abort, g, render_template, request, session
from markupsafe import Markup

from ruqqus.helpers import ads, chat_devices, live_thread_store, live_view
from ruqqus.helpers.jinja2 import quote_of
from ruqqus.helpers.visibility import comment_hidden
from ruqqus.__main__ import app


@app.get("/ads.txt")
def ads_txt():
    cfg = ads.config()
    if cfg is None:
        abort(404)
    return Response(ads.ADS_TXT.format(publisher=cfg["publisher"]), mimetype="text/plain")


def _place(context, page):
    """(post, board) a slot on this page is judged by, or None when the page shows no posts at all
    (an error page, settings, chat...): those never get an ad, whatever template they extend. Nor does a search, whose
    query would be in the address Google is told, nor a page that has a post in hand and isn't that post's own page (a shared
    draft's revisions show text `_page_safe` never reads)."""
    if request.endpoint in ads.SEARCH_ENDPOINTS:
        return None
    post, board, listing = context.get("p"), context.get("b"), context.get("listing")
    if post is not None:                # the sidebar of a post page follows the post's rules
        return (post, board) if request.endpoint in ads.POST_ENDPOINTS else None
    if page == "post":
        return None
    if listing:
        return (None, board)
    return None


def _live_allows(post):
    """A post page's live thread, by what the panel draws (partials/live_panel.html), the query only for a post that has one."""
    thread = live_view.live_of(post)
    if not thread:
        return True
    primary = thread["post_id"]
    row = live_thread_store.thread(g.db, primary)
    updates = [(update[5], update[4]) for update in live_thread_store.updates(g.db, primary)]
    return ads.live_thread_allows(thread["live"], updates, row[7] if row else 0, row[6] if row else "")


def _page_safe(context):
    """Everything on this page that members wrote, judged once per request (helpers/ads.py page_allows). Comments are the
    ones the viewer is drawn; those a press loads later are not looked at. A quote card is judged as drawn for this viewer."""
    if "ads_page_safe" not in g:
        post, v = context.get("p"), context.get("v")
        if post is not None:
            drawn = [c for c in post.__dict__.get("_preloaded_comments") or [] if not comment_hidden(c, v)]
            g.ads_page_safe = ads.post_page_allows(post, drawn) and ads.quote_allows(quote_of(post, v)) and _live_allows(post)
        else:
            cards = list(context.get("listing") or []) + list(context.get("pinned_posts") or [])
            comments = [card for card in cards if hasattr(card, "parent_submission")]
            posts = [card for card in cards if not hasattr(card, "parent_submission")]
            g.ads_page_safe = ads.page_allows(posts, comments) and all(ads.quote_allows(quote_of(card, v)) for card in posts)
    return g.ads_page_safe


def _held(v):
    """A signed-in browser's first minutes after typing its password: chat may not have taken the key made from it yet. Read
    only for a page drawn for a member, which is private: it never reaches a page a cache may keep."""
    return bool(v) and ads.held_after_sign_in(session.get("no_ads_until"), int(time.time()))


def _chat_owner(v):
    """Whose chat a browser may hold beside Google's tag: the member this page is drawn for. Nobody's on a visitor's page. See
    partials/ad_hold.html."""
    return chat_devices.mxid_of(g.db, v.id) if v else None


@jinja2.pass_context
def ad_slot(context, page):
    """An ad box for `page` ("feed", "post" or "sidebar"), or nothing."""
    cfg = ads.config()
    place = _place(context, page) if cfg else None
    if place is None:
        return ""
    post, board = place
    v = context.get("v")
    if _held(v):
        return ""
    if not ads.allows(page, v, embedded=bool(request.args.get("embed")), post=post, board=board, cfg=cfg,
                      account=context.get("profile_user")):
        return ""
    if not _page_safe(context):
        return ""
    g.ads_used = True
    return Markup(render_template("partials/ad_slot.html", page=page, cfg=cfg, owner=_chat_owner(v)))


@jinja2.pass_context
def ads_script(context, consent=False):
    """The AdSense script and assets/js/ads.js, once, at the end of a page that drew a slot (or, with
    `consent`, on the privacy page, so the consent choices can be changed there). On that page a Premium or Child viewer, who
    sees no ads, doesn't load Google's tag for reading it: only if they press the choices link (`on_press`)."""
    cfg = ads.config()
    if not ads.live(cfg) or not (g.get("ads_used") or consent) or g.get("ads_script_done"):
        return ""
    v = context.get("v")
    if _held(v):
        return ""
    g.ads_script_done = True
    return Markup(render_template(
        "partials/ad_script.html", cfg=cfg, owner=_chat_owner(v),
        personalised=ads.personalised_allowed(request.headers.get("CF-IPCountry"), v),
        on_press=bool(consent and not g.get("ads_used") and not ads.viewer_allows(v))))


@jinja2.pass_context
def ad_safe_mark(context, post):
    """` data-ad-safe="1"` or `"0"` on a feed card while ads are live. assets/js/ads.js stops and hides a page's ads when the
    continuous feed brings in a card marked 0, since the server only judged the cards it drew."""
    if "ads_live_now" not in g:
        g.ads_live_now = ads.live(ads.config())
    if not g.ads_live_now:
        return ""
    safe = ads.post_allows(post) and ads.quote_allows(quote_of(post, context.get("v")))
    return Markup(' data-ad-safe="%d"' % safe)


def ads_configured():
    """A client id: what /ads.txt needs. Nothing a reader is told goes by this: see ads_live."""
    return ads.config() is not None


def ads_live():
    """A client id and a slot: what the privacy page, the FAQ, Premium's "No ads" and the footer's link go by."""
    return ads.live(ads.config())


app.jinja_env.globals.update(ad_slot=ad_slot, ads_script=ads_script, ads_configured=ads_configured, ads_live=ads_live,
                             ad_safe_mark=ad_safe_mark)
