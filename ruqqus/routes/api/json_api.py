import time
from flask import jsonify, abort

from sqlalchemy import *

from ruqqus.helpers.wrappers import *
from ruqqus.helpers.get import *
from ruqqus.helpers.visibility import board_hidden, user_hidden, NOTICE_TITLE
from ruqqus.routes.users import profile_json_refusal

from ruqqus.__main__ import app, cache
from ruqqus.classes.boards import Board


@app.route("/api/v1/guild/<boardname>", methods=["GET"])
@auth_desired
@api("read")
def guild_info(v, boardname):
    guild = get_guild(boardname)

    if board_hidden(guild, v):
        return jsonify({"error": NOTICE_TITLE}), 403
    if guild.is_circle and not guild.can_view(v):
        abort(404)

    return jsonify(guild.json)


@app.route("/api/v1/user/<username>", methods=["GET"])
@auth_desired
@api("read")
def user_info(v, username):

    user = get_user(username, v=v)

    if user_hidden(user, v):
        return jsonify({"error": NOTICE_TITLE}), 403
    refused = profile_json_refusal(user, v)
    if refused:
        return jsonify({"error": refused}), 403

    return jsonify(user.json)
