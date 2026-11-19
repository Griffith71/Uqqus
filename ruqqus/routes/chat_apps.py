"""Settings > Chat apps: using your chats in an outside Matrix app (Element, FluffyChat...). The page says what to type
in the app, shows the member's security key (unlocked in their own browser with their password: the site never sees
it, assets/chat_src/src/chat_apps.js) and lists their chat devices, any of which they can sign out. Signing in itself
is routes/oidc_pages.py.

Which devices are the site's own and which are this sign-in's is read from `chat_site_devices` (helpers/chat_signouts.py): `site` when the
device has a row (or, for one made before rows were kept, when its name says so), `here` when the row's `sign_in` is this cookie's. `here`
is a label, never a reason to hide: a copy of the cookie carries the same sign-in, so every device is listed with its Sign out, this
sign-in's included. chat_apps.js reads nothing from this browser's storage, and asks first (the confirm sheet) before signing out a `here`
device: that browser's chat then starts again as a new device, its store kept (assets/chat_src/src/token.js)."""
import re
from os import environ

from flask import *
from flask import session as flask_session
from sqlalchemy import text

from ruqqus.helpers.wrappers import *
from ruqqus.helpers import oidc_provider as oidc
from ruqqus.helpers import chat_signouts
from ruqqus.helpers.chat_devices import is_site_device
from ruqqus.helpers.i18n_runtime import tr
from ruqqus.classes import *
import ruqqus.helpers.matrix_client as matrix
from ruqqus.__main__ import app, limiter

_DEVICE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def _identity(v):
    return g.db.query(ChatIdentity).filter_by(user_id=v.id).first()


def device_rows(devices, site_name, site_rows=None, sign_in=None):
    """Synapse's list, each device marked `site` (it has a `chat_site_devices` row; a device with none falls back to its name) and
    `here` (its row was last started by this cookie's sign-in, and isn't revoked). `site_rows`: {device_id: (revoked_utc, sign_in)}."""
    site_rows = site_rows or {}
    rows = []
    for d in devices:
        if not d.get("device_id"):
            continue
        row = site_rows.get(d["device_id"])
        rows.append({"device_id": d["device_id"], "name": d["name"] or f"{site_name} chat", "last_seen": d["last_seen"],
                     "site": row is not None or is_site_device(d["name"], site_name),
                     "here": bool(row and not row[0] and sign_in and row[1] == sign_in)})
    return sorted(rows, key=lambda d: -(d["last_seen"] or 0))


def _site_rows(user_id):
    if not chat_signouts.tables_ready(g.db):
        return {}
    return {d: (revoked, sign_in) for d, revoked, sign_in in g.db.execute(
        text("SELECT device_id, revoked_utc, sign_in FROM chat_site_devices WHERE user_id = :u"), {"u": user_id}).all()}


@app.get("/settings/chat_apps")
@auth_required
@not_qr_limited
@not_child_locked("chat")
def settings_chat_apps(v):
    identity = _identity(v)
    ready = bool(identity and identity.key_blob_password and identity.ready_utc)
    return render_template("settings_chat_apps.html", v=v, chat_ready=ready,
                           sso_on=oidc.configured(environ.get("OIDC_CLIENT_ID", "synapse"),
                                                  environ.get("OIDC_CLIENT_SECRET", "").strip(),
                                                  environ.get("OIDC_PRIVATE_KEY")),
                           homeserver=app.config["MATRIX_PUBLIC_URL"], site_address=app.config["SERVER_NAME"],
                           refusal=oidc.account_refusal(v, ready) or (oidc.REFUSED if v.is_quarantined else None))


@app.get("/api/chat/devices")
@limiter.limit("30/minute")
@auth_required
@not_qr_limited
@not_child_locked("chat")
def chat_devices(v):
    identity = _identity(v)
    if not identity:
        return jsonify({"devices": []})
    try:
        devices = matrix.list_devices(identity.matrix_user_id)
    except matrix.MatrixError:
        return jsonify({"error": tr("Your chat devices can't be listed right now.")}), 503
    resp = jsonify({"devices": device_rows(devices, app.config["SITE_NAME"], _site_rows(v.id),
                                           chat_signouts.sign_in_hash(flask_session.get("session_id")))})
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.post("/api/chat/devices/sign_out")
@limiter.limit("20/minute")
@auth_required
@not_qr_limited
@not_child_locked("chat")
@validate_formkey
def chat_device_sign_out(v):
    """Signs one device out at once, as before, and revokes its site row so the token route never serves it again. For one of the site's
    devices the row is revoked and COMMITTED before Synapse deletes it (chat_signouts.revoke_device): a token request still at work on
    the device holds the row, so the revocation waits for it, and every later request finds it revoked; deleting first let a request in
    flight re-create the device with a live token. When the chat server can't be reached (it answers an error, or doesn't answer), a
    device the site made, or one Synapse has just listed, is queued instead (helpers/chat_signouts.py, retried until it lands) and the
    page says so; an outside app's device can't be told apart from a stranger's without the server, so that is a 503, and so is any
    failure on a database without the migration (nothing could be kept: never a 500, never "shortly")."""
    identity = _identity(v)
    device_id = request.form.get("device_id", "")
    if not identity or not _DEVICE.match(device_id):
        abort(404)
    mxid = identity.matrix_user_id
    ready = chat_signouts.tables_ready(g.db)
    known = device_id in _site_rows(v.id)          # a device the site made is this account's, whatever the chat server says now
    try:
        listed = next((d for d in matrix.list_devices(mxid) if d["device_id"] == device_id), None)
    except matrix.MatrixError:
        return _sign_out_later(v, device_id, mxid) if known else _not_now()
    if listed is None:
        abort(404)
    if ready and (known or is_site_device(listed.get("name"), app.config["SITE_NAME"])):
        chat_signouts.revoke_device(g.db, v.id, device_id, "signed_out_here")
        g.db.commit()
    try:
        matrix.delete_device(mxid, device_id)
    except matrix.MatrixError:
        return _sign_out_later(v, device_id, mxid)
    return "", 204


def _not_now():
    return jsonify({"error": tr("That device couldn't be signed out right now.")}), 503


def _sign_out_later(v, device_id, mxid):
    if chat_signouts.queue(g.db, v.id, "signed_out_here", "listed", device_ids=[device_id], mxid=mxid) is None:
        return _not_now()                          # the migration missing: nothing kept, so nothing can land later
    return jsonify({"pending": True, "message": tr("This device will be signed out shortly.")}), 202
