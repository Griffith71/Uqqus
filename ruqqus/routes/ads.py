"""Ads (Google AdSense): /ads.txt and the template helpers that draw a slot and load the scripts.
The rules are helpers/ads.py; nothing here runs, and nothing is drawn, unless ADSENSE_CLIENT is set."""
import jinja2
from flask import Response, abort, g, render_template, request
from markupsafe import Markup

from ruqqus.helpers import ads
from ruqqus.__main__ import app


@app.get("/ads.txt")
def ads_txt():
    cfg = ads.config()
    if cfg is None:
        abort(404)
    return Response(ads.ADS_TXT.format(publisher=cfg["publisher"]), mimetype="text/plain")


def _place(context, page):
    """(post, board) a slot on this page is judged by, or None when the page shows no posts at all
    (an error page, settings, chat...): those never get an ad, whatever template they extend."""
    post, board, listing = context.get("p"), context.get("b"), context.get("listing")
    if page == "post":
        return (post, board) if post is not None else None
    if page == "feed":
        return (None, board)
    if post is not None:                # the sidebar of a post page follows the post's rules
        return (post, board)
    if listing:
        return (None, board)
    return None


@jinja2.pass_context
def ad_slot(context, page):
    """An ad box for `page` ("feed", "post" or "sidebar"), or nothing."""
    cfg = ads.config()
    place = _place(context, page) if cfg else None
    if place is None:
        return ""
    post, board = place
    if not ads.allows(page, context.get("v"), embedded=bool(request.args.get("embed")), post=post, board=board, cfg=cfg,
                      account=context.get("profile_user")):
        return ""
    g.ads_used = True
    return Markup(render_template("partials/ad_slot.html", page=page, cfg=cfg))


def ads_script(consent=False):
    """The AdSense script and assets/js/ads.js, once, at the end of a page that drew a slot (or, with
    `consent`, on the privacy page, so the consent choices can be changed there)."""
    cfg = ads.config()
    if cfg is None or not (g.get("ads_used") or consent) or g.get("ads_script_done"):
        return ""
    g.ads_script_done = True
    return Markup(render_template("partials/ad_script.html", cfg=cfg))


def ads_configured():
    return ads.config() is not None


app.jinja_env.globals.update(ad_slot=ad_slot, ads_script=ads_script, ads_configured=ads_configured)
