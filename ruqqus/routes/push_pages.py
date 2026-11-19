"""Push notifications: turning them on in a browser, off again, what they say, and a group's choice (helpers/web_push.py,
helpers/push_rules.py, helpers/push_store.py, helpers/push_send.py; named `_pages` so the star import of ruqqus.classes
can't replace the helpers). The browser side is assets/js/push_settings.js on Settings > Chat, and the service worker
that shows them is /sw.js (routes/static.py)."""
import time

from flask import *

from ruqqus.helpers.wrappers import *
from ruqqus.helpers.get import *
from ruqqus.classes import *
from ruqqus.__main__ import app, limiter, member_limit_key

from ruqqus.helpers import chat_group_store, push_rules, push_send, push_store, web_push
from ruqqus.helpers.i18n_runtime import tr


def _error(message, status=400):
    return jsonify({"error": message}), status


def _off():
    return _error(tr("Notifications aren't available on this site yet."), 404)


@app.route("/api/push/key", methods=["GET"])
@auth_required
def push_key(v):
    """The site's public key, which a browser's subscribe needs; and whether this browser's subscription is known."""
    key = push_send.public_key()
    if not key:
        return _off()
    endpoint = request.args.get("endpoint") or ""
    return jsonify({"key": key, "on": bool(endpoint) and push_store.has(g.db, v.id, endpoint)})


@app.route("/api/push/subscribe", methods=["POST"])
@limiter.limit("30/hour", key_func=member_limit_key)
@auth_required
@not_qr_limited
@not_child_locked("chat")
@not_quarantined("chat")
@validate_formkey
def push_subscribe(v):
    if not push_send.enabled():
        return _off()
    endpoint, p256dh, auth = (request.values.get(k) or "" for k in ("endpoint", "p256dh", "auth"))
    if not web_push.endpoint_ok(endpoint) or not web_push.keys_ok(p256dh, auth):
        return _error(tr("This browser's notifications couldn't be turned on. Try again."))
    push_store.save(g.db, v.id, endpoint, p256dh, auth, int(time.time()))
    g.db.commit()
    return jsonify({"ok": True})


@app.route("/api/push/unsubscribe", methods=["POST"])
@auth_required
@validate_formkey
def push_unsubscribe(v):
    push_store.remove(g.db, v.id, request.values.get("endpoint") or "")
    g.db.commit()
    return jsonify({"ok": True})


@app.route("/settings/chat/push_names", methods=["POST"])
@auth_required
@validate_formkey
def settings_chat_push_names(v):
    """Whether a notification says who it is from ("New message from Ann") or not ("New message")."""
    v.push_names = request.values.get("names") == "1"
    g.db.add(v)
    g.db.commit()
    return redirect("/settings/chat#notifications")


@app.route("/api/chat/groups/<int:gid>/push", methods=["POST"])
@auth_required
@not_qr_limited
@not_child_locked("chat")
@not_quarantined("chat")
@validate_formkey
def chat_group_push(gid, v):
    """What a member is told of in this group: every message, calls only, or nothing."""
    mode = push_rules.mode(request.values.get("mode"))
    if mode is None:
        return _error(tr("Choose what you're told of."))
    if chat_group_store.group(g.db, gid) is None or not chat_group_store.set_push_mode(g.db, gid, v.id, mode):
        abort(404)
    g.db.commit()
    return jsonify({"ok": True, "mode": mode})
