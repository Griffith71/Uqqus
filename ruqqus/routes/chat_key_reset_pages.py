"""Starting chat over (helpers/chat_key_reset.py): the member proves who they are for a one-time grant, then the site
uploads their new cross-signing keys to the chat server as them. Named `chat_key_reset_pages.py`, not `chat_key_reset.py`:
`from ruqqus.routes import *` would put the module where the helper of the same name is expected."""
import time

import gevent
from flask import g, jsonify, render_template, request
from flask import session as flask_session

from ruqqus.classes import ChatIdentity
from ruqqus.helpers import action_log, chat_key_reset, mfa_backup_store, signin_alerts, user_agents
from ruqqus.helpers.alerts import notify
from ruqqus.helpers.i18n_runtime import N_, force_locale, locale_for_user, msg, tr
from ruqqus.helpers.regions import COUNTRY_NAMES
from ruqqus.helpers.wrappers import auth_required, not_child_locked, not_qr_limited, throttle_check_ip, validate_formkey
from ruqqus.mail import send_mail_quietly
import ruqqus.helpers.matrix_client as matrix
from ruqqus.__main__ import app, limiter, member_limit_key, r

GONE = N_("Confirm with your password again.")


def _identity(v):
    return g.db.query(ChatIdentity).filter_by(user_id=v.id).first()


@app.route("/api/chat/key_reset/grant", methods=["POST"])
@auth_required
@not_qr_limited
@not_child_locked("messaging")
@validate_formkey
@limiter.limit("10/hour", key_func=member_limit_key)
@throttle_check_ip("chat_key_reset")
def chat_key_reset_grant(v):
    """The member's password, and their two-factor code (or a backup code) when they have two-factor: a grant good for one
    upload of new cross-signing keys, for five minutes. The older recovery code, which turns two-factor off, is never taken."""
    if not _identity(v):
        return jsonify({"error": tr("Chat isn't set up for your account yet.")}), 404
    if not v.verifyPass(request.form.get("password", "")):
        return jsonify({"error": tr("That isn't your password."), "field": "password"}), 403
    if v.mfa_secret:
        code = (request.form.get("otp") or "").strip()
        if not code:
            return jsonify({"error": tr("Enter your two-factor code."), "field": "otp"}), 403
        if not (v.accept_2fa(code) or mfa_backup_store.spend(g.db, v.id, code, int(time.time()))):
            return jsonify({"error": tr("That code isn't right."), "field": "otp"}), 403
    grant = chat_key_reset.new_grant()
    now = int(time.time())
    # kept before a backup code's use is committed: Redis failing here leaves the code unspent (the request's rollback)
    r.set(chat_key_reset.redis_key(grant),
          chat_key_reset.grant_record(v.id, v.login_nonce, now, chat_key_reset.sign_in_of(flask_session.get("session_id"))),
          ex=chat_key_reset.GRANT_SECONDS)
    g.db.commit()
    response = jsonify({"grant": grant, "seconds": chat_key_reset.GRANT_SECONDS})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/chat/signing_keys", methods=["POST"])
@auth_required
@not_qr_limited
@not_child_locked("messaging")
@validate_formkey
@limiter.limit("15/hour", key_func=member_limit_key)          # the SDK retries a 503 up to four times, each a second upload
def chat_signing_keys(v):
    """The upload the member's browser would have sent to the chat server (`keys`, its JSON text), with a grant: checked to
    be exactly this member's new cross-signing keys, then sent as them with the appservice's token. Answers the chat
    server's body on a 200; any other answer of the chat server is a 502 carrying its status and body (`matrix_status`,
    `body`), so it is never read as one of the site's own codes (a 401 is a stale form key here, a 403 the grant). The grant
    is used up first, and put back only when the chat server couldn't be reached or failed (5xx): sending the same keys again
    is harmless, and the member, part way through a reset, then needn't type their password again."""
    identity = _identity(v)
    if not identity:
        return jsonify({"error": tr("Chat isn't set up for your account yet.")}), 404
    grant = request.form.get("grant") or ""
    stored = chat_key_reset.redis_key(grant)
    raw = r.getdel(stored) if grant else None
    now = int(time.time())
    if chat_key_reset.grant_refusal(raw, v.id, v.login_nonce, now, chat_key_reset.sign_in_of(flask_session.get("session_id"))):
        return jsonify({"error": msg(GONE), "grant": "gone"}), 403
    keys = chat_key_reset.check_upload(request.form.get("keys"), identity.matrix_user_id)
    if keys is None:
        return jsonify({"error": tr("Those keys couldn't be used.")}), 400
    try:
        status, body = matrix.upload_signing_keys(identity.matrix_user_id, keys)
    except matrix.MatrixError:
        status, body = None, {}
    if status is None or status >= 500:
        _put_back(stored, raw, now)
        return jsonify({"error": tr("Chat isn't answering right now. Try again in a minute."), "grant": "kept"}), 503
    if status != 200:
        response = jsonify({"error": tr("Chat couldn't take those keys."), "matrix_status": status, "body": body})
        response.status_code = 502
        response.headers["Cache-Control"] = "no-store"
        return response
    if "master_key" in keys:
        _tell(v)
    response = jsonify(body)
    response.headers["Cache-Control"] = "no-store"
    return response


def _put_back(stored, raw, now):
    """The grant again, for what was left of its five minutes. Best effort."""
    left = chat_key_reset.seconds_left(raw, now)
    if left > 0:
        try:
            r.set(stored, raw, ex=left)
        except Exception as e:
            print(f"Chat reset grant not kept: {type(e).__name__}", flush=True)


def _tell(v):
    """New keys replaced the old ones: a staff-only Log entry, a notification always, and an email by the sign-in emails'
    rule. Best effort: the keys are replaced, and none of this failing undoes it."""
    action_log.record_quietly(g.db, "chat_keys_reset", v.id, target_user_id=v.id, floor=action_log.STAFF)
    try:
        g.db.commit()
    except Exception as e:
        g.db.rollback()
        print(f"Chat reset Log entry not kept: {type(e).__name__}", flush=True)
    try:
        notify(v, N_("Your chat encryption was started over. Messages from before can't be read on your devices unless "
                     "a browser where you used chat brings them back. If this wasn't you, change your password in "
                     "[Settings > Security](/settings/security)."))
    except Exception as e:
        g.db.rollback()
        print(f"Chat reset notice not sent: {type(e).__name__}", flush=True)
    if not signin_alerts.wants_email(v):
        return
    try:
        with force_locale(locale_for_user(v)):
            html = render_template("email/chat_reset.html", v=v,
                                   action_url=f"https://{app.config['SERVER_NAME']}/settings/security",
                                   settings_url=f"https://{app.config['SERVER_NAME']}/settings/security#sign-ins",
                                   **signin_alerts.where_and_when(request.headers.get("cf-ipcountry"),
                                                                  user_agents.stored(request.headers.get("User-Agent")),
                                                                  int(time.time()), COUNTRY_NAMES))
            subject = signin_alerts.chat_reset_subject(app.config["SITE_NAME"])
        gevent.spawn(send_mail_quietly, v.email, subject, html)
    except Exception as e:
        print(f"Chat reset email not sent: {type(e).__name__}", flush=True)
