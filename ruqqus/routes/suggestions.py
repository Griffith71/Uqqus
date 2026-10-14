from flask import render_template, request

from ruqqus.helpers.wrappers import auth_required, auth_desired
from ruqqus.helpers.get import get_user
from ruqqus.helpers.visibility import user_hidden
from ruqqus.helpers import suggestions
from ruqqus.__main__ import app

KINDS = ("users", "guilds", "curations")


@app.get("/who_to_follow")
@auth_required
def who_to_follow(v):
    """The full list of suggestions, one kind at a time (?kind=users|guilds|curations)."""
    kind = request.args.get("kind", "users")
    if kind not in KINDS:
        kind = "users"

    finder = {"users": suggestions.users_for,
              "guilds": suggestions.guilds_for,
              "curations": suggestions.curations_for}[kind]

    return render_template("who_to_follow.html", v=v, kind=kind, kinds=KINDS, items=finder(v))


@app.get("/inpage/who_to_follow")
@auth_required
def who_to_follow_box(v):
    """The sidebar box (one suggestion of each kind). The sidebar loads it after the page,
    so no other page pays for the queries."""
    return render_template("partials/who_to_follow_box.html", v=v, picks=suggestions.sidebar_suggestions(v))


@app.get("/inpage/similar_accounts/<username>")
@auth_desired
def similar_accounts_box(username, v):
    """A profile's "Similar accounts" box (helpers/similar_store.py), loaded after the page like Who to follow.
    Visitors get it too. Nothing is drawn for an account that is gone, hidden from the viewer, private or nofollow:
    no suggestions are made from it."""
    u = get_user(username, v=v, graceful=True)
    if not u or u.is_deleted or u.is_banned or u.is_private or u.is_nofollow or user_hidden(u, v):
        return ""
    return render_template("partials/similar_accounts_box.html", v=v, u=u, picks=suggestions.similar_for(u, v))
