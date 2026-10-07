"""Muting an account (helpers/muting.py): the routes, and the template helper.

Mirrors the block routes in routes/settings.py, but writes `usermutes`, so nothing about
what the other account may do changes, and nothing tells them."""
import time

from flask import g, jsonify, request
from jinja2 import Undefined

from ruqqus.classes import User, UserMute
from ruqqus.helpers import muting
from ruqqus.helpers.get import get_user
from ruqqus.helpers.wrappers import auth_required, validate_formkey
from ruqqus.__main__ import app


def _target(v):
    """(account, None) or (None, error response) for the username in the request."""
    username = (request.values.get("username") or "").strip()
    user = get_user(username, graceful=True) if username else None

    if not user:
        return None, (jsonify({"error": "That user doesn't exist."}), 404)
    if user.id == v.id:
        return None, (jsonify({"error": "You can't mute yourself."}), 409)
    if user.id == 1:
        return None, (jsonify({"error": f"You can't mute @{user.username}."}), 409)
    if user.is_deleted:
        return None, (jsonify({"error": "That account has been deactivated"}), 410)
    return user, None


@app.route("/settings/mute", methods=["POST"])
@auth_required
@validate_formkey
def settings_mute_user(v):
    user, error = _target(v)
    if error:
        return error

    if g.db.query(UserMute).filter_by(user_id=v.id, target_id=user.id).first():
        return jsonify({"error": f"You have already muted @{user.username}."}), 409

    g.db.add(UserMute(user_id=v.id, target_id=user.id, created_utc=int(time.time())))
    g.db.commit()

    muting.forget(v)
    muting.clear_cached_feeds(v)
    return jsonify({"message": f"@{user.username} muted."})


@app.route("/settings/unmute", methods=["POST"])
@auth_required
@validate_formkey
def settings_unmute_user(v):
    user, error = _target(v)
    if error:
        return error

    mute = g.db.query(UserMute).filter_by(user_id=v.id, target_id=user.id).first()
    if not mute:
        return jsonify({"error": f"You have not muted @{user.username}."}), 409

    g.db.delete(mute)
    g.db.commit()

    muting.forget(v)
    muting.clear_cached_feeds(v)
    return jsonify({"message": f"@{user.username} unmuted."})


def muted_accounts(v):
    """The mutes of `v` whose account still exists, newest first, for the settings page."""
    return g.db.query(UserMute).join(
        User, User.id == UserMute.target_id
    ).filter(
        UserMute.user_id == v.id, User.is_deleted == False
    ).order_by(UserMute.id.desc()).all()


def _is_muted(item, v=None):
    return muting.is_muted(item, None if isinstance(v, Undefined) else v)


def _has_muted(user, v=None):
    """Has the viewer muted this account (for a profile page)?"""
    v = None if isinstance(v, Undefined) else v
    return bool(v and user is not None and user.id in muting.muted_set(v))


app.jinja_env.globals.update(is_muted=_is_muted, has_muted=_has_muted)
