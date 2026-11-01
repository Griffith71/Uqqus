"""QR sign-in's routes: a computer shows a code, a signed-in phone approves it, the computer confirms. The rules are
helpers/qr_login.py and CLAUDE.md "QR sign-in" says what it defends against. (Not `qr_login.py`: the star-import trap.)"""
import base64
import io
import json
import time
from os import environ

import qrcode
from flask import *
from flask import session as flask_session

from ruqqus.classes import *
from ruqqus.helpers import ads as ads_rules
from ruqqus.helpers import mfa_backup_store, qr_colour, qr_login, same_origin, sign_in_history, signin_alerts, user_agents
from ruqqus.helpers.alerts import notify
from ruqqus.helpers.i18n_runtime import N_, msg
from ruqqus.helpers.redirects import same_site
from ruqqus.helpers.regions import COUNTRY_NAMES, resolve_region
from ruqqus.helpers.wrappers import *
from ruqqus.routes.login import check_for_alts, complete_sign_in
from ruqqus.__main__ import app, limiter, r

# every refusal a phone can get, for a code that ran out, an answer tried too often, or an account that may not use this:
# one sentence, so nobody learns from it which it was or that an account has switched this off
CANT_USE = N_("This code can't be used. Get a new one on the computer, or sign in there with your password.")
WRONG_PASSWORD = N_("That password isn't right.")
WRONG_CODE = N_("That code isn't right.")


def _offered_or_404():
    if not qr_login.offered(environ, r, app.config["READ_ONLY"]):
        abort(404)


def _refuse_another_site():
    if same_origin.from_another_site(request.headers.get("Origin"), request.headers.get("Sec-Fetch-Site"), request.host,
                                     (app.config.get("SERVER_NAME") or "", environ.get("ONION_NAME", ""))):
        abort(403)


def _picture(address):
    """The code as a PNG in a data address: the site's colour, darkened until a camera reads it from white, with the quiet zone
    in the picture itself, as the two-factor code is drawn (routes/users.py)."""
    code = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=6, border=4)
    code.add_data(address)
    image = code.make_image(fill_color=qr_colour.on_white(app.config["SITE_COLOR"]), back_color="white")
    out = io.BytesIO()
    image.save(out, format="PNG")
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode("ascii")


def _digest_of_this_browser():
    return qr_login.pending_digest(flask_session, int(time.time()))


def _key_of_this_code():
    return f"qr:{_digest_of_this_browser()}"


def _has_code():
    return bool(_digest_of_this_browser())


def _has_no_code():
    return not _has_code()


def _approval_for(account_row_id, nonce):
    """The account an approval names, if it may still sign in that way, else None."""
    account = g.db.query(User).filter_by(id=account_row_id).first() if isinstance(account_row_id, int) else None
    return None if qr_login.refusal(account, nonce) else account


@app.route("/login/qr/start", methods=["POST"])
@limiter.limit("10/minute")
def login_qr_start():
    """The button: nothing is written anywhere until someone presses it. A second press replaces the first code."""
    _offered_or_404()
    _refuse_another_site()
    v, _client = get_logged_in_user()
    if v:
        abort(403)
    now = int(time.time())
    code, digest = qr_login.new_code()
    previous = _digest_of_this_browser()
    try:
        if previous:
            r.delete(qr_login.wait_key(previous), qr_login.ok_key(previous), qr_login.no_key(previous), qr_login.try_key(previous))
        # the two words the phone shows and never the header, whose oddities can tell one browser from another
        r.set(qr_login.wait_key(digest),
              json.dumps({"ua": user_agents.parse(request.headers.get("User-Agent")),
                          "cc": request.headers.get("cf-ipcountry"), "t": now}),
              ex=qr_login.CODE_SECONDS)
    except Exception as e:
        print(f"QR sign-in couldn't start: {type(e).__name__}", flush=True)
        return jsonify({"error": "unavailable"}), 503
    qr_login.remember_pending(flask_session, digest, now)
    address = qr_login.approve_address(app.config["SERVER_NAME"], app.config.get("FORCE_HTTPS"), code)
    return jsonify({"image": _picture(address), "seconds": qr_login.CODE_SECONDS})


# Limited by the code a request is about, never by address alone: computers behind one address (a classroom, an office) would use up one
# budget and its 429s would put the address in time out (routes/errors.py), and anyone on the address could stop someone else's Confirm.
# A request about no code has nothing to read and a budget of its own, which is all it can use up.
@app.route("/login/qr/status", methods=["GET"])
@limiter.limit("120/minute", key_func=_key_of_this_code, exempt_when=_has_no_code)
@limiter.limit("120/minute", exempt_when=_has_code)
def login_qr_status():
    """Asked every few seconds by the browser that pressed the button: it carries no code, and the name of whoever approved
    goes only to the session the code was made for. Never writes the cookie."""
    g.skip_session_cookie = True
    _offered_or_404()
    try:
        state, approval = qr_login.status_of(r, _digest_of_this_browser())
    except Exception as e:
        print(f"QR sign-in status not read: {type(e).__name__}", flush=True)
        return jsonify({"state": "expired"})
    if state == "approved":
        account = _approval_for(approval.get("uid"), approval.get("nonce"))
        return jsonify({"state": "approved", "name": account.username} if account else {"state": "expired"})
    return jsonify({"state": state})


@app.route("/login/qr/finish", methods=["POST"])
@limiter.limit("6/minute", key_func=_key_of_this_code, exempt_when=_has_no_code)
@limiter.limit("60/minute", exempt_when=_has_code)
def login_qr_finish():
    """Only the computer's Confirm comes here. The approval is taken in one step, so it signs in once and never again."""
    _offered_or_404()
    _refuse_another_site()
    v, _client = get_logged_in_user()
    if v:
        abort(403)                  # already signed in: this would quietly turn into someone else
    digest = _digest_of_this_browser()
    approval = qr_login.read_json(qr_login.take(r, qr_login.ok_key(digest))) if digest else None
    account = _approval_for(approval.get("uid"), approval.get("nonce")) if approval else None
    if not account:
        # the phone may have taken its approval back since the computer showed Confirm
        refused = digest and r.get(qr_login.no_key(digest))
        return jsonify({"state": "denied" if refused else "expired"}), 410
    flask_session.pop(qr_login.PENDING_KEY, None)
    complete_sign_in(account, sign_in_history.METHOD_QR)
    _tell_the_member(account)
    return jsonify({"redirect": same_site(request.form.get("redirect", "/"))})


def _tell_the_member(account):
    """Always a notification, whatever the sign-in emails' setting; the email is signin_alerts.check's. Best effort: the sign-in has
    happened."""
    try:
        facts = signin_alerts.where_and_when(request.headers.get("cf-ipcountry"),
                                             user_agents.stored(request.headers.get("User-Agent")),
                                             int(time.time()), COUNTRY_NAMES)
        notify(account, N_("""Someone signed in to your account with a QR code you approved: %(browser)s, %(place)s, %(when)s.
\n\nIf it wasn't you, [log out everywhere](/settings/security#log-out-everywhere) and check [your apps](/settings/apps) and \
[your chat apps](/settings/chat_apps): logging out doesn't end those."""), **facts)
    except Exception as e:
        g.db.rollback()
        print(f"QR sign-in notice not sent: {type(e).__name__}", flush=True)


@app.route("/login/qr/cancel", methods=["POST"])
@limiter.limit("30/minute")
def login_qr_cancel():
    """The computer's Cancel, or leaving the panel: whatever this browser was waiting on goes."""
    _offered_or_404()
    _refuse_another_site()
    digest = _digest_of_this_browser()
    if digest:
        try:
            r.delete(qr_login.wait_key(digest), qr_login.ok_key(digest), qr_login.no_key(digest), qr_login.try_key(digest))
        except Exception as e:
            print(f"QR sign-in not cancelled: {type(e).__name__}", flush=True)
    flask_session.pop(qr_login.PENDING_KEY, None)
    return jsonify({"state": "cancelled"})


@app.route("/login/qr/approve", methods=["GET"])
@auth_desired
def login_qr_approve_page(v):
    """The page a phone's camera opens. Drawn knowing nothing of the code: that is in the fragment, read by the script."""
    _offered_or_404()
    if not v:
        state = "signed_out"
    elif v.qr_limited:
        state = "limited"
    elif qr_login.refusal(v):
        state = "refused"
    else:
        state = "ready"
    # a camera opens the page as if typed; anything else came from a link someone put in front of them. Only friction.
    from_a_link = (request.headers.get("Sec-Fetch-Site") or "none").strip().lower() != "none"
    return render_template("login_qr_approve.html", v=v, state=state, from_a_link=from_a_link, i=random_image())


def _code_digest():
    """The digest of the code a phone sent, while that code still stands."""
    digest = qr_login.digest_of(request.form.get("code"))
    return digest if digest and r.get(qr_login.wait_key(digest)) else None


def _cant_use():
    return jsonify({"error": msg(CANT_USE)}), 410


@app.route("/login/qr/info", methods=["POST"])
@limiter.limit("30/minute")
@auth_required
@not_qr_limited
@validate_formkey
def login_qr_info(v):
    """What whoever started this says about themselves, for the phone's screen."""
    _offered_or_404()
    digest = _code_digest()
    wait = qr_login.read_json(r.get(qr_login.wait_key(digest))) if digest else None
    if not wait or qr_login.refusal(v):
        return _cant_use()
    facts = signin_alerts.where_and_when(wait.get("cc"), None, int(wait.get("t") or 0), COUNTRY_NAMES)
    words = wait.get("ua")
    if isinstance(words, list) and len(words) == 2:
        facts["browser"] = user_agents.describe_parts(*words)
    return jsonify(facts)


def _answer_is_right(v, answer):
    """A two-factor code or backup code for an account with two-factor, else its password. Never the older recovery code, which
    switches two-factor off: the phone is approving someone else's screen."""
    if not answer:
        return False
    if v.mfa_secret:
        return bool(v.accept_2fa(answer.strip()) or mfa_backup_store.spend(g.db, v.id, answer.strip(), int(time.time())))
    return bool(v.verifyPass(answer))


@app.route("/login/qr/approve", methods=["POST"])
@limiter.limit("6/minute")
@auth_required
@not_qr_limited
@validate_formkey
@throttle_check_ip('qr_approve')
def login_qr_approve(v):
    _offered_or_404()
    digest = _code_digest()
    if not digest or qr_login.refusal(v):
        return _cant_use()
    if not _answer_is_right(v, request.form.get("answer") or ""):
        if qr_login.wrong_answer(r, digest):
            return jsonify({"error": msg(WRONG_CODE if v.mfa_secret else WRONG_PASSWORD)}), 400
        return _cant_use()
    g.db.commit()                                   # a backup code is spent, whatever happens next
    # one DEL decides: of an approval and a refusal, or of two accounts on one code, only one ever gets a 1
    if r.delete(qr_login.wait_key(digest)) != 1:
        return _cant_use()
    r.delete(qr_login.try_key(digest))
    r.set(qr_login.ok_key(digest), json.dumps({"uid": v.id, "nonce": v.login_nonce, "t": int(time.time())}),
          ex=qr_login.APPROVED_SECONDS)
    return jsonify({"ok": True})


@app.route("/login/qr/deny", methods=["POST"])
@limiter.limit("30/minute")
@auth_required
@validate_formkey
def login_qr_deny(v):
    """"Not me", and it asks nothing of whoever presses it. `ok` only when nobody can use this code any more: refused now, refused
    before, or an approval of this account's own, which it takes back, as long as the computer hasn't finished. Anything else (run out,
    used, another account's approval) is the sentence for a code that can't be used, because the phone mustn't say nobody signed in."""
    _offered_or_404()
    digest = qr_login.digest_of(request.form.get("code"))
    if not digest:
        return _cant_use()
    if r.delete(qr_login.wait_key(digest)) == 1:
        r.delete(qr_login.try_key(digest))
        r.set(qr_login.no_key(digest), 1, ex=qr_login.CODE_SECONDS)
        return jsonify({"ok": True})
    if r.get(qr_login.no_key(digest)):
        return jsonify({"ok": True})
    approval = qr_login.read_json(r.get(qr_login.ok_key(digest)))
    if approval and approval.get("uid") == v.id and qr_login.take(r, qr_login.ok_key(digest)) is not None:
        r.set(qr_login.no_key(digest), 1, ex=qr_login.CODE_SECONDS)
        return jsonify({"ok": True})
    return _cant_use()


@app.route("/login/qr/upgrade", methods=["GET"])
@auth_required
def login_qr_upgrade_page(v):
    if not v.qr_limited:
        return redirect(same_site(request.args.get("redirect", "/")))
    return upgrade_page(v, same_site(request.args.get("redirect", "/")))


@app.route("/login/qr/upgrade", methods=["POST"])
@limiter.limit("6/minute")
@auth_required
@validate_formkey
@throttle_check_ip('qr_upgrade')
def login_qr_upgrade(v):
    """The password, and a two-factor code where the account has it: a QR sign-in becomes an ordinary one. Its form leaves a key for
    chat to take (assets/js/chat_key.js), so no ad for a while (helpers/ads.py), as for the other three forms that write one."""
    back = same_site(request.form.get("redirect", "/"))
    if not v.qr_limited:
        return redirect(back)
    now = int(time.time())
    answer = (request.form.get("2fa_token") or "").strip()
    right = v.verifyPass(request.form.get("password") or "") and (
        not v.mfa_secret or v.accept_2fa(answer) or mfa_backup_store.spend(g.db, v.id, answer, now))
    if not right:
        return upgrade_page(v, back, failed=True), 400
    check_for_alts(v.id)                            # only now: a password was typed on this browser (routes/login.py)
    # the same for what this sign-in's row left empty, from this request: this is the first the site can say the member is here
    expires = flask_session.get(qr_login.EXPIRES_KEY)
    if isinstance(expires, int):
        g.db.query(LoginEvent).filter(
            LoginEvent.user_id == v.id, LoginEvent.method == sign_in_history.METHOD_QR, LoginEvent.ip.is_(None),
            LoginEvent.created_utc == expires - qr_login.SESSION_SECONDS,
        ).update({"ip": request.remote_addr, "region_code": resolve_region(request.headers.get("cf-ipcountry"))},
                 synchronize_session=False)
    g.db.commit()
    qr_login.end_limited(flask_session)
    ads_rules.hold_after_password(flask_session, now)
    return redirect(back)
