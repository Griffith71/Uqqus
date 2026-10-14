"""A profile's lists (Following, Followers, Subscribers, Subscriptions), who may open them, and the counts the
profile header shows. The rules and the SQL are in helpers/follow_lists.py; this module is named apart from it so the
star imports in routes/__init__.py never replace the helper (the `coauthors` lesson in CLAUDE.md)."""
import time

from flask import abort, g, jsonify, redirect, render_template, request

from ruqqus.helpers import follow_lists
from ruqqus.helpers.get import get_user
from ruqqus.helpers.visibility import _cut, user_hidden, viewer_level
from ruqqus.helpers.wrappers import auth_desired, auth_required, page_number, validate_formkey
from ruqqus.__main__ import app


def _lists_state(u, v):
    """For the header: each list's count and whether this viewer may open it."""
    now = int(time.time())
    numbers = follow_lists.counts(g.db, u.id, now)
    is_owner = bool(v and v.id == u.id)
    follows_owner = (not is_owner) and follow_lists.follows(g.db, v.id if v else None, u.id)
    out = {}
    for name in follow_lists.LISTS:
        setting = getattr(u, follow_lists.SETTING[name], follow_lists.ONLY_ME)
        out[name] = {"count": numbers[name],
                     "open": follow_lists.may_open(setting, is_owner=is_owner, follows_owner=follows_owner),
                     "url": f"{u.permalink}/{name}"}
    out["circle_price"] = follow_lists.circle_price(g.db, u.id)
    out["show_subscribers"] = numbers["subscribers"] > 0 or out["circle_price"] > 0
    return out


app.jinja_env.globals.update(profile_lists=_lists_state)


def _profile_or_404(username, v):
    """The account a list belongs to, with the profile page's own refusals: gone, suspended, private mode, a block
    either way or a filtered name all answer 404 (a list never confirms more than the profile would)."""
    u = get_user(username, v=v, graceful=True)
    if not u or u.reserved or user_hidden(u, v):
        abort(404)
    staff = bool(v and v.admin_level >= 3)
    own = bool(v and v.id == u.id)
    if (u.is_deleted and not staff) or (u.is_suspended and not (staff or own)) or (u.is_private and not (staff or own)):
        abort(404)
    if v and not staff and (u.is_blocking or u.is_blocked):
        abort(404)
    return u


def _list_page(username, name, v):
    from ruqqus.classes import User

    u = _profile_or_404(username, v)
    state = _lists_state(u, v)
    title = follow_lists.TITLES[name]
    if not state[name]["open"]:
        return render_template("follow_list.html", v=v, u=u, name=name, title=title, lists=state, closed=True,
                               users=[], page=1, next_exists=False, hidden=0), 403

    level = viewer_level(v)
    cut = _cut(level) if level > 0 else None
    page = page_number()
    ids, next_exists, shown = follow_lists.page(g.db, name, u.id, v.id if v else None, cut, int(time.time()), page)
    by_id = {x.id: x for x in g.db.query(User).filter(User.id.in_(ids)).all()} if ids else {}
    users = [by_id[i] for i in ids if i in by_id]
    following = v.following_ids if v else set()
    return render_template("follow_list.html", v=v, u=u, name=name, title=title, lists=state, closed=False,
                           users=users, page=page, next_exists=next_exists, following=following,
                           hidden=max(0, state[name]["count"] - shown))


@app.get("/@<username>/following")
@auth_desired
def profile_following(username, v):
    return _list_page(username, "following", v)


@app.get("/@<username>/followers")
@auth_desired
def profile_followers(username, v):
    return _list_page(username, "followers", v)


@app.get("/@<username>/subscribers")
@auth_desired
def profile_subscribers(username, v):
    return _list_page(username, "subscribers", v)


@app.get("/@<username>/subscriptions")
@auth_desired
def profile_subscriptions(username, v):
    return _list_page(username, "subscriptions", v)


@app.post("/settings/list_visibility")
@auth_required
@validate_formkey
def settings_list_visibility(v):
    """Who may open each of the caller's lists. Only the four fields, only 0, 1 or 2; a field left out is unchanged."""
    changed = False
    for name in follow_lists.LISTS:
        raw = request.values.get(name)
        if raw is None:
            continue
        choice = follow_lists.parse_choice(raw)
        if choice is None:
            return jsonify({"error": "Choose Everyone, Followers or Only me."}), 400
        setattr(v, follow_lists.SETTING[name], choice)
        changed = True
    if not changed:
        return jsonify({"error": "Nothing to change."}), 400
    g.db.add(v)
    g.db.commit()
    if request.values.get("back"):
        return redirect("/settings/profile#lists")
    return jsonify({"message": "Saved. Your lists are shown as you chose."})
