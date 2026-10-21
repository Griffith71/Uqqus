"""Settings > Chat apps: using your chats in an outside Matrix app (Element, FluffyChat...). The page says what to type
in the app, shows the member's security key (unlocked in their own browser with their password: the site never sees
it, assets/chat_src/src/chat_apps.js) and lists their chat devices, any of which they can sign out. Signing in itself
is routes/oidc_pages.py."""
import re
from os import environ

from flask import *

from ruqqus.helpers.wrappers import *
from ruqqus.helpers import oidc_provider as oidc
from ruqqus.helpers.chat_devices import is_site_device
from ruqqus.classes import *
import ruqqus.helpers.matrix_client as matrix
from ruqqus.__main__ import app, limiter

_DEVICE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def _identity(v):
    return g.db.query(ChatIdentity).filter_by(user_id=v.id).first()


def device_rows(devices, site_name):
    rows = [{"device_id": d["device_id"], "name": d["name"] or f"{site_name} chat", "last_seen": d["last_seen"],
             "site": is_site_device(d["name"], site_name)} for d in devices if d.get("device_id")]
    return sorted(rows, key=lambda d: -(d["last_seen"] or 0))


@app.get("/settings/chat_apps")
@auth_required
def settings_chat_apps(v):
    identity = _identity(v)
    ready = bool(identity and identity.key_blob_password and identity.ready_utc)
    return render_template("settings_chat_apps.html", v=v, chat_ready=ready,
                           sso_on=oidc.configured(environ.get("OIDC_CLIENT_ID", "synapse"),
                                                  environ.get("OIDC_CLIENT_SECRET", "").strip(),
                                                  environ.get("OIDC_PRIVATE_KEY")),
                           homeserver=app.config["MATRIX_PUBLIC_URL"], site_address=app.config["SERVER_NAME"],
                           refusal=oidc.account_refusal(v, ready))


@app.get("/api/chat/devices")
@limiter.limit("30/minute")
@auth_required
def chat_devices(v):
    identity = _identity(v)
    if not identity:
        return jsonify({"devices": []})
    try:
        devices = matrix.list_devices(identity.matrix_user_id)
    except matrix.MatrixError:
        return jsonify({"error": "Your chat devices can't be listed right now."}), 503
    resp = jsonify({"devices": device_rows(devices, app.config["SITE_NAME"])})
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.post("/api/chat/devices/sign_out")
@limiter.limit("20/minute")
@auth_required
@validate_formkey
def chat_device_sign_out(v):
    identity = _identity(v)
    device_id = request.form.get("device_id", "")
    if not identity or not _DEVICE.match(device_id):
        abort(404)
    try:
        if device_id not in {d["device_id"] for d in matrix.list_devices(identity.matrix_user_id)}:
            abort(404)
        matrix.delete_device(identity.matrix_user_id, device_id)
    except matrix.MatrixError:
        return jsonify({"error": "That device couldn't be signed out right now."}), 503
    return "", 204
