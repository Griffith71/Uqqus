"""'Sign in with <site>' for outside Matrix apps: the site as the OpenID Connect provider of its own chat server
(helpers/oidc_provider.py has the rules). Synapse is the only client (`OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`), a code
goes only to Synapse's own callback, PKCE is required, and the member confirms with their password on the site. The
whole thing is off (404) until OIDC_CLIENT_SECRET and OIDC_PRIVATE_KEY are set.

Also `/.well-known/matrix/client`, which tells a Matrix app typed the site's name where the chat server is."""
import json
import secrets
import time
from os import environ
from urllib.parse import urlencode

from flask import *

from ruqqus.helpers.wrappers import *
from ruqqus.helpers import oidc_provider as oidc
from ruqqus.helpers.alerts import send_notification
from ruqqus.classes import *
from ruqqus.__main__ import app, limiter, r

CLIENT_ID = environ.get("OIDC_CLIENT_ID", "synapse").strip()
_KEY = {}


def _secret():
    return environ.get("OIDC_CLIENT_SECRET", "").strip()


def _key():
    if "key" not in _KEY:
        pem = oidc.key_from_env(environ.get("OIDC_PRIVATE_KEY", ""))
        _KEY["key"] = oidc.load_key(pem) if pem else None
    return _KEY["key"]


def _on():
    if not oidc.configured(CLIENT_ID, _secret(), environ.get("OIDC_PRIVATE_KEY")):
        abort(404)


def _issuer():
    return oidc.issuer(app.config["SERVER_NAME"], app.config.get("FORCE_HTTPS"))


def _redirect():
    return oidc.redirect_uri(app.config["MATRIX_PUBLIC_URL"])


def _no_store(resp, status=200):
    resp = make_response(resp, status)
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Pragma"] = "no-cache"
    return resp


def _chat_ready(user_id):
    identity = g.db.query(ChatIdentity).filter_by(user_id=user_id).first()
    return bool(identity and identity.key_blob_password and identity.ready_utc)


ERRORS = {
    "unknown_client": "This sign-in request didn't come from a chat app this site knows.",
    "bad_redirect": "This sign-in request didn't come from a chat app this site knows.",
}


def _request_args(source):
    keys = ("client_id", "redirect_uri", "response_type", "scope", "state", "nonce", "code_challenge",
            "code_challenge_method")
    return {k: source.get(k) for k in keys if source.get(k) is not None}


def _consent(v, args, error=None, status=200):
    resp = _no_store(render_template("oidc_consent.html", v=v, i=random_image(), args=args, error=error,
                                     refusal=oidc.account_refusal(v, _chat_ready(v.id))), status)
    # the site's own policy says forms go only to the site, and a browser holds a form's redirect to that too: this
    # page's answer is a redirect to the chat server's callback, so it alone may also send the browser there
    resp.headers["Content-Security-Policy"] = ("object-src 'none'; base-uri 'self'; "
                                               f"form-action 'self' {oidc.origin(app.config['MATRIX_PUBLIC_URL'])}; "
                                               "frame-ancestors 'none'")
    return resp


@app.get("/.well-known/openid-configuration")
def oidc_discovery():
    _on()
    return jsonify(oidc.discovery(_issuer()))


@app.get("/oidc/jwks")
def oidc_jwks():
    _on()
    return jsonify(oidc.jwks(_key()))


@app.get("/oidc/authorize")
@auth_required
def oidc_authorize(v):
    _on()
    args = _request_args(request.args)
    problem = oidc.authorize_refusal(args, CLIENT_ID, _redirect())
    if problem in ERRORS:
        return _no_store(render_template("oidc_consent.html", v=v, i=random_image(), args={}, error=ERRORS[problem], refusal=None,
                                         broken=True), 400)
    if problem:
        return redirect(_redirect() + "?" + urlencode({"error": "invalid_request", "error_description": problem,
                                                       "state": args.get("state", "")}))
    return _consent(v, args)


@app.post("/oidc/authorize")
@limiter.limit("10/minute")
@auth_required
@validate_formkey
def oidc_authorize_post(v):
    _on()
    args = _request_args(request.form)
    problem = oidc.authorize_refusal(args, CLIENT_ID, _redirect())
    if problem:
        abort(400)
    back = _redirect() + "?"
    if request.form.get("decision") != "allow":
        return redirect(back + urlencode({"error": "access_denied", "state": args["state"]}))
    if oidc.account_refusal(v, _chat_ready(v.id)):
        return _consent(v, args, status=403)
    if not v.verifyPass(request.form.get("password", "")):
        return _consent(v, args, error="That password isn't right.", status=401)
    now = int(time.time())
    code = secrets.token_urlsafe(32)
    r.setex("oidc:code:" + oidc.digest(code), oidc.CODE_SECONDS,
            oidc.code_record(v.id, args.get("nonce"), args["code_challenge"], now))
    return redirect(back + urlencode({"code": code, "state": args["state"]}))


def _token_error(error, status=400):
    return _no_store(jsonify({"error": error}), status)


def _take(key):
    """Reads and deletes in one step: a code or token is good once."""
    pipe = r.pipeline()
    pipe.get(key)
    pipe.delete(key)
    value, _ = pipe.execute()
    return value.decode() if isinstance(value, bytes) else value


@app.post("/oidc/token")
@limiter.limit("60/minute")
def oidc_token():
    _on()
    given_id, given_secret = oidc.basic_auth(request.headers.get("Authorization"))
    if given_id is None:
        given_id, given_secret = request.form.get("client_id"), request.form.get("client_secret")
    if not oidc.client_ok(given_id, given_secret, CLIENT_ID, _secret()):
        return _token_error("invalid_client", 401)
    if request.form.get("grant_type") != "authorization_code":
        return _token_error("unsupported_grant_type")
    if request.form.get("redirect_uri") != _redirect():
        return _token_error("invalid_grant")
    code = request.form.get("code") or ""
    raw = _take("oidc:code:" + oidc.digest(code)) if code else None
    if not raw:
        return _token_error("invalid_grant")
    record = json.loads(raw)
    if not oidc.pkce_ok(request.form.get("code_verifier"), record["challenge"]):
        return _token_error("invalid_grant")
    user = g.db.query(User).filter_by(id=record["user_id"]).first()
    if oidc.account_refusal(user, _chat_ready(record["user_id"])):
        return _token_error("invalid_grant")
    now = int(time.time())
    access = secrets.token_urlsafe(32)
    r.setex("oidc:token:" + oidc.digest(access), oidc.TOKEN_SECONDS, str(user.id))
    id_token = oidc.sign(oidc.id_claims(_issuer(), CLIENT_ID, user.id, record.get("nonce"), record["auth_time"], now), _key())
    try:
        send_notification(user, f"A chat app was signed in to your {app.config['SITE_NAME']} account. If that "
                                "wasn't you, sign it out in [Settings > Chat apps](/settings/chat_apps) and change "
                                "your password.")
    except Exception as e:  # the sign-in stands; the notice is best effort
        print(f"chat app sign-in notice not sent: {type(e).__name__}", flush=True)
    return _no_store(jsonify({"access_token": access, "token_type": "Bearer", "expires_in": oidc.TOKEN_SECONDS,
                              "id_token": id_token, "scope": "openid"}))


@app.route("/oidc/userinfo", methods=["GET", "POST"])
def oidc_userinfo():
    _on()
    header = request.headers.get("Authorization", "")
    token = header[7:] if header.startswith("Bearer ") else ""
    raw = r.get("oidc:token:" + oidc.digest(token)) if token else None
    if not raw:
        resp = _no_store(jsonify({"error": "invalid_token"}), 401)
        resp.headers["WWW-Authenticate"] = 'Bearer error="invalid_token"'
        return resp
    return _no_store(jsonify(oidc.userinfo(int(raw))))


@app.get("/.well-known/matrix/client")
def matrix_client_well_known():
    resp = jsonify({"m.homeserver": {"base_url": app.config["MATRIX_PUBLIC_URL"]}})
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp
