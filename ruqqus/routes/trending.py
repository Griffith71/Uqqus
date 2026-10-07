from urllib.parse import quote

from flask import abort, g, redirect, render_template, request

from ruqqus.helpers.wrappers import admin_level_required, auth_desired, validate_formkey
from ruqqus.helpers.get import get_posts
from ruqqus.helpers.visibility import viewer_level
from ruqqus.helpers.languages import LANGUAGE_NAMES
from ruqqus.helpers import trending_store
from ruqqus.classes import Region, TrendingBlocked
from ruqqus.routes.front import _resolve_active_filters
from ruqqus.__main__ import app

SORTS = ("new", "top")


def _viewer_filters(v):
    """The regions and languages the viewer browses with (none for ?scope=all)."""
    if request.args.get("scope") == "all":
        return [], []
    regions, languages, _ = _resolve_active_filters(v)
    return regions, languages


def _place(scopes):
    """What the list is of, for its title: None for the whole site."""
    if scopes == [trending_store.SITE]:
        return None
    if len(scopes) > 1:
        return "your filters"
    kind, _, code = scopes[0].partition(":")
    if kind == "lang":
        return LANGUAGE_NAMES.get(code, code)
    region = g.db.query(Region).filter_by(code=code).first()
    return region.current_name if region else code


def _title(place):
    if place is None:
        return "Trending"
    return f"Trending for {place}" if place == "your filters" else f"Trending in {place}"


def _current(v):
    regions, languages = _viewer_filters(v)
    scopes, topics = trending_store.current(g.db, viewer_level(v), regions, languages)
    return scopes, topics, _place(scopes)


@app.get("/inpage/trending")
@auth_desired
def trending_box(v):
    """The sidebar box. The sidebar loads it after the page, so no other page pays for it."""
    scopes, topics, place = _current(v)
    return render_template("partials/trending_box.html", v=v, topics=topics, heading=_title(place))


@app.get("/trending")
@auth_desired
def trending_page(v):
    """The top 10 as a page (?scope=all: the whole site, whatever the viewer's filters)."""
    scopes, topics, place = _current(v)
    return render_template("trending.html", v=v, topics=topics, heading=_title(place), place=place)


@app.get("/trending/<slug>")
@auth_desired
def trending_topic(slug, v):
    """The posts of one topic, through the same rules as any feed."""
    regions, languages = _viewer_filters(v)
    label, post_ids = trending_store.find(g.db, slug, viewer_level(v), regions, languages)

    sort = request.args.get("sort", "new")
    if sort not in SORTS:
        abort(400)
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        abort(400)

    ids = trending_store.visible_post_ids(g.db, v, post_ids, sort=sort, page=page)
    next_exists = len(ids) > trending_store.PAGE
    listing = get_posts(ids[:trending_store.PAGE], v=v)

    return render_template(
        "trending_topic.html", v=v, slug=slug, label=label, listing=listing, sort_method=sort, page=page,
        next_exists=next_exists, scope_all=request.args.get("scope") == "all",
    ), (200 if label else 404)


# --- admin ---------------------------------------------------------------------------------

def _admin_redirect(msg=None, error=None):
    if error:
        return redirect("/admin/trending?error=" + quote(error))
    return redirect("/admin/trending?msg=" + quote(msg or "Saved."))


@app.get("/admin/trending")
@admin_level_required(4)
def admin_trending(v):
    return render_template(
        "admin/trending.html", v=v,
        lists=trending_store.overview(g.db),
        blocked=g.db.query(TrendingBlocked).order_by(TrendingBlocked.created_utc.desc()).all(),
        min_authors=trending_store.min_authors(),
        levels=["Off", "Standard", "Child"],
        msg=request.args.get("msg"), error=request.args.get("error"),
    )


@app.post("/admin/trending/hide")
@admin_level_required(4)
@validate_formkey
def admin_trending_hide(v):
    key = request.form.get("key", "").strip()
    if not key:
        return _admin_redirect(error="Enter a topic to hide.")
    if not trending_store.block(g.db, key, request.form.get("label", ""), admin_id=v.id):
        return _admin_redirect(error="That topic is already hidden.")
    return _admin_redirect("Hidden from every trending list.")


@app.post("/admin/trending/<int:blocked_id>/unhide")
@admin_level_required(4)
@validate_formkey
def admin_trending_unhide(blocked_id, v):
    trending_store.unblock(g.db, blocked_id)
    return _admin_redirect("Shown again from the next run (within a few minutes).")
