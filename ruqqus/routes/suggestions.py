from flask import render_template, request

from ruqqus.helpers.wrappers import auth_required
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
