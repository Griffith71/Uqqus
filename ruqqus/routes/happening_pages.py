"""The "What's happening" layer: the right sidebar's box, the /happening page and the admin page that feeds it.

    GET /inpage/happening      the box (templates/partials/happening_box.html), loaded after the page by
                               assets/js/happening.js; `?rev=N` answers 204 when nothing changed since that signature
    GET /happening[/<tab>]     the page: Overview, Trending, Live, Calendar (month)
    /admin/happening ...       enter, edit, hide events by hand (level 3 reads, level 4 writes; English, untranslated)

Rules are helpers/site_events.py, the SQL helpers/site_event_store.py, what is drawn helpers/happening_box.py and
helpers/happening_calendar.py. The module is named `happening_pages` and has no model of the same name: a module that
shares a name with one in ruqqus.classes is replaced by it in any file that star-imports both.

`HAPPENING_ENABLED=0` switches the layer off: the sidebar goes back to the Trending box alone and these pages are 404.
The box is private and always asked again (ruqqus/__main__.py after_request); nothing about a reader's time zone is sent.
"""
import os
import time
from urllib.parse import quote

from flask import abort, g, redirect, render_template, request

from ruqqus.__main__ import app, limiter, member_limit_key
from ruqqus.helpers import happening_box, happening_calendar, hot_threads
from ruqqus.helpers import guild_events as grid
from ruqqus.helpers import site_event_store as store
from ruqqus.helpers import site_events as ev
from ruqqus.helpers.i18n_runtime import N_, msg
from ruqqus.helpers.sensitive import shows_sensitive
from ruqqus.helpers.visibility import viewer_level
from ruqqus.helpers.wrappers import admin_level_required, auth_desired, validate_formkey
from ruqqus.routes.trending import _current, _title

# the Live tab's chips (marked here so the catalogues find them; drawn with msg)
KIND_LABELS = {"score": N_("Scores"), "poll": N_("Polls"), "prediction": N_("Predictions"), "countdown": N_("Countdowns"),
               "election": N_("Elections"), "alert": N_("Alerts"), "news": N_("News"), "event": N_("Events")}
TABS = ("overview", "trending", "live", "calendar")
OVERVIEW_ROWS = 6
LIVE_PAGE_ROWS = 50


def enabled():
    return os.environ.get("HAPPENING_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")


app.jinja_env.globals.update(happening_enabled=enabled)


def _viewer(v):
    return viewer_level(v), shows_sensitive(v), int(time.time())


# --- the box ---------------------------------------------------------------------------------------------------------

@app.get("/inpage/happening")
@limiter.limit("120/minute", key_func=member_limit_key)
@auth_desired
def happening_box_fragment(v):
    """The sidebar box. Every part has an empty state, so the box never vanishes (the way Trending did when nothing
    trended). The same signature in `?rev=` means the reader's box is current: 204 and no body."""
    if not enabled():
        abort(404)
    level, sensitive, now = _viewer(v)
    scopes, topics, place = _current(v)
    hot = [] if topics else hot_threads.for_viewer(g.db, v, level)
    model = happening_box.box(g.db, level, sensitive, now, topics, hot)
    if request.args.get("rev") == str(model["rev"]):
        return "", 204
    return render_template("partials/happening_box.html", v=v, m=model, heading=_title(place))


# --- the page --------------------------------------------------------------------------------------------------------

@app.get("/happening")
@auth_desired
def happening_overview(v):
    return _page(v, "overview")


@app.get("/happening/<tab>")
@auth_desired
def happening_tab(tab, v):
    if tab not in TABS[1:]:
        abort(404)
    return _page(v, tab)


def _page(v, tab):
    if not enabled():
        abort(404)
    level, sensitive, now = _viewer(v)
    context = {"tab": tab, "tabs": TABS}
    if tab in ("overview", "trending"):
        scopes, topics, place = _current(v)
        if tab == "overview":
            hot = [] if topics else hot_threads.for_viewer(g.db, v, level, limit=OVERVIEW_ROWS)
            context["m"] = happening_box.box(g.db, level, sensitive, now, topics, hot, rows=OVERVIEW_ROWS)
        else:
            context["topics"] = topics
            context["hot"] = hot_threads.for_viewer(g.db, v, level, limit=5)
        context["heading"] = _title(place)
    elif tab == "live":
        rows = ev.live_rows(store.live_candidates(g.db, now), now, level, sensitive)
        kind = request.args.get("kind", "")
        kinds = sorted({r["kind"] for r in rows}, key=ev.LIVE_KINDS.index)
        if kind and kind not in ev.LIVE_KINDS:
            abort(404)
        shown = [r for r in rows if not kind or r["kind"] == kind]
        context.update(live=[happening_box.present(r, now) for r in shown[:LIVE_PAGE_ROWS]], kind=kind, kinds=kinds,
                       kind_labels={k: msg(label) for k, label in KIND_LABELS.items()}, live_cut=len(shown) > LIVE_PAGE_ROWS)
    else:
        year, month = grid.parse_month(request.args.get("month"), now)
        if not 1970 <= year <= ev.MAX_YEAR:                    # the grid of a far-off year runs past what a date can hold
            year, month = grid.parse_month(None, now)
        context["cal"] = happening_calendar.month(g.db, year, month, now, level, sensitive)
    return render_template("happening.html", v=v, **context)


# --- the admin page ------------------------------------------------------------------------------------------------------

def _back(msg=None, error=None, to="/admin/happening"):
    if error:
        return redirect(f"{to}?error=" + quote(error))
    return redirect(f"{to}?msg=" + quote(msg or "Saved."))


def _poll_problem(fields):
    """Why the poll post an admin pasted can't be linked (None: it can). It must exist, be public, live, and be a poll: the
    box links it for everyone, so a Circle or private post, a deleted one or a post that is no poll would say it exists."""
    post_id = fields.get("poll_post_id")
    if post_id is None:
        return None
    from sqlalchemy import text
    row = g.db.execute(text("SELECT COALESCE(NULLIF(repost_id, 0), id), post_public, is_banned, deleted_utc "
                            "FROM submissions WHERE id = :i"), {"i": post_id}).fetchone()
    if row is None:
        return "There is no post with that address."
    primary, public, banned, deleted = row
    if primary != post_id:                       # a forwarded copy: the poll belongs to the original
        row = g.db.execute(text("SELECT id, post_public, is_banned, deleted_utc FROM submissions WHERE id = :i"),
                           {"i": primary}).fetchone()
        if row is None:
            return "There is no post with that address."
        _, public, banned, deleted = row
    if not public or banned or deleted:
        return "That post is not public (it is deleted, removed or shared with a circle or a private guild)."
    if g.db.execute(text("SELECT 1 FROM polls WHERE post_id = :i"), {"i": primary}).fetchone() is None:
        return "That post has no poll."
    return None


def _event_or_404(event_id):
    event = store.get(g.db, event_id)
    if event is None:
        abort(404)
    return event


@app.get("/admin/happening")
@admin_level_required(3)
def admin_happening(v):
    now = int(time.time())
    rows = store.admin_list(g.db, now)
    shown = [dict(r, view=happening_box.present(r, now)) for r in rows]
    return render_template("admin/happening.html", v=v, events=shown, now=now, enabled=enabled(), kinds=ev.KINDS,
                           statuses=ev.STATUSES, can_edit=v.admin_level >= 4, edit=None, f={},
                           msg=request.args.get("msg"), error=request.args.get("error"))


@app.get("/admin/happening/<int:event_id>")
@admin_level_required(3)
def admin_happening_event(event_id, v):
    now = int(time.time())
    event = _event_or_404(event_id)
    return render_template("admin/happening.html", v=v, events=[], now=now, enabled=enabled(), kinds=ev.KINDS,
                           statuses=ev.STATUSES, can_edit=v.admin_level >= 4, edit=event,
                           f=ev.form_values(event), log=store.log_of(g.db, event_id),
                           msg=request.args.get("msg"), error=request.args.get("error"))


@app.post("/admin/happening/create")
@admin_level_required(4)
@validate_formkey
def admin_happening_create(v):
    now = int(time.time())
    try:
        fields = ev.clean_event(request.form, now)
    except ev.Refused as e:
        return _back(error=str(e))
    problem = _poll_problem(fields)
    if problem:
        return _back(error=problem)
    rate, version = store.rating(g.db)
    event_id = store.create(g.db, fields, v.id, now, rate, version)
    return _back("Added.", to=f"/admin/happening/{event_id}")


@app.post("/admin/happening/<int:event_id>/edit")
@admin_level_required(4)
@validate_formkey
def admin_happening_edit(event_id, v):
    now = int(time.time())
    _event_or_404(event_id)
    try:
        fields = ev.clean_event(request.form, now)
        expected = int(request.form.get("expected") or 0)
    except (ev.Refused, ValueError) as e:
        return _back(error=str(e), to=f"/admin/happening/{event_id}")
    problem = _poll_problem(fields)
    if problem:
        return _back(error=problem, to=f"/admin/happening/{event_id}")
    rate, version = store.rating(g.db)
    if not store.update(g.db, event_id, fields, v.id, now, expected, rate, version):
        return _back(error="Someone changed this event while you had it open. Reload and try again.",
                     to=f"/admin/happening/{event_id}")
    return _back("Saved.", to=f"/admin/happening/{event_id}")


@app.post("/admin/happening/<int:event_id>/state")
@admin_level_required(4)
@validate_formkey
def admin_happening_state(event_id, v):
    """The quick edit while a match is on or a prediction resolves: its status and its own state keys only."""
    now = int(time.time())
    event = _event_or_404(event_id)
    to = f"/admin/happening/{event_id}"
    try:
        state = ev.clean_state(event["kind"], request.form)
        expected = int(request.form.get("expected") or 0)
        status = request.form.get("status") or event["status"]
        rate, version = store.rating(g.db)
        if not store.set_state(g.db, event_id, status, state, v.id, now, expected, rate, version):
            return _back(error="Someone changed this event while you had it open. Reload and try again.", to=to)
    except (ev.Refused, ValueError) as e:
        return _back(error=str(e), to=to)
    return _back("Updated.", to=to)


@app.post("/admin/happening/<int:event_id>/hide")
@admin_level_required(4)
@validate_formkey
def admin_happening_hide(event_id, v):
    _event_or_404(event_id)
    store.hide(g.db, event_id, v.id, int(time.time()))
    return _back("Hidden everywhere.")


@app.post("/admin/happening/<int:event_id>/unhide")
@admin_level_required(4)
@validate_formkey
def admin_happening_unhide(event_id, v):
    _event_or_404(event_id)
    store.unhide(g.db, event_id, v.id, int(time.time()))
    return _back("Shown again.")


@app.post("/admin/happening/seed")
@admin_level_required(4)
@validate_formkey
def admin_happening_seed(v):
    """Load (or refresh) the reviewed events that ship with the site: safe to press twice, and it never undoes an
    admin's hide, edit or priority (site_event_store.upsert_source)."""
    from ruqqus.helpers import happening_seed
    rate, version = store.rating(g.db)
    counts = happening_seed.sync(g.db, int(time.time()), rate, version)
    return _back("Seed: %(inserted)d added, %(updated)d refreshed, %(kept)d left as edited." % counts)
