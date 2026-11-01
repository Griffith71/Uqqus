from flask import (
    request, g, abort, jsonify, make_response, redirect, render_template
)
from flask import session as flask_session
from os import environ
import requests
from werkzeug.wrappers.response import Response as RespObj
import time
import random
import json

from ruqqus.classes import *
from .get import *
from .alerts import send_notification
from .i18n_runtime import N_, tr
from .i18n_runtime import site_name as _site_name
from ruqqus.__main__ import Base, app, db_session
from ruqqus.helpers.session_helpers import *
from urllib.parse import parse_qsl, urlencode, urlsplit

from ruqqus.helpers import qr_login, session_cookie, sign_in_history
from ruqqus.helpers.redirects import same_site
from sqlalchemy import event
from sqlalchemy.orm.attributes import set_committed_value


def page_number():
    """The request's ?page=: a whole number from 1. Anything else (0, -1, "abc") is page 1, so a list never asks the
    database for a negative OFFSET."""
    raw = request.args.get("page", "1") or "1"
    if len(raw) > 6 and raw.isascii() and raw.isdigit():
        raw = "100000"                # far past any list's end, and never an OFFSET past bigint (bug hunt: a 500)
    return max(int(raw), 1) if raw.isascii() and raw.isdigit() else 1


def whole_arg(name, default=0):
    """A query value that is a whole number (`?utc_greater_than=`, `?context=`), else `default`: never a 500 for
    "abc" (bug hunt L3: every feed answered ?utc_greater_than=abc with a 500)."""
    raw = request.args.get(name, "") or ""
    return int(raw) if raw.isascii() and raw.isdigit() and len(raw) <= 12 else default


def flag_arg(name):
    """A yes/no query value: only 1, true, yes or on mean yes (bug hunt L3: `bool("false")` is True, so
    ?ignore_pinned=false hid the pinned posts)."""
    return (request.args.get(name, "") or "").strip().lower() in ("1", "true", "yes", "on")


def get_logged_in_user(db=None):

    if not db:
        db=g.db

    # /api/v1 is token-only; /api/v2 takes a token when one is sent (help/oauth.html) and the session otherwise
    if request.path.startswith("/api/v1") or (
            request.path.startswith("/api/v2") and request.headers.get("Authorization")):
        token = request.headers.get("Authorization")
        if not token:
            return None, None

        token = token.split()
        if len(token) < 2:
            return None, None

        token = token[1]
        if not token:
            return None, None

        client = db.query(ClientAuth).filter(
            ClientAuth.access_token == token,
            ClientAuth.access_token_expire_utc > int(time.time())
        ).first()

        x = (client.user, client) if client else (None, None)

    elif 'user_id' in flask_session:
        uid = flask_session.get("user_id")
        nonce = flask_session.get("login_nonce", 0)
        # a QR sign-in past its 12 hours is signed out, whatever else the cookie says (helpers/qr_login.py)
        if not uid or qr_login.session_over(flask_session, time.time()):
            x = (None, None)
        else:
            v = db.query(User).options(
                joinedload(User.moderates).joinedload(ModRelationship.board),
                joinedload(User.subscriptions).joinedload(Subscription.board)
            ).filter_by(
                id=uid,
                is_deleted=False
            ).first()

            if not v or nonce < v.login_nonce:
                session_cookie.forget_sign_in(flask_session)
                x = (None, None)
            else:
                if v and qr_login.is_limited(flask_session):
                    _limit_to_qr_session(v)
                x = (v, None)

    else:
        x = (None, None)

    if x[0]:
        x[0].client = x[1]
        _note_active(x[0])

    return x


def _limit_to_qr_session(user):
    """This request's `user` object is a limited QR sign-in's (helpers/qr_login.py). Its admin level reads 0 for this request,
    without changing anything to save (a committed value, so nothing is written): every check of `v.admin_level` that isn't a
    route's wrapper, in a route, a helper or a template, is then an ordinary member's, so an admin's phone tricked into approving
    a code never yields admin powers. Its real level stays in `qr_held_level` for `admin_level_required`, to ask for the
    password rather than say no. `Board.has_mod` reads `qr_limited` the same way."""
    if user.qr_limited:
        return
    user.qr_limited = True
    user.qr_held_level = user.admin_level or 0
    set_committed_value(user, "admin_level", 0)


@event.listens_for(User, "refresh")
def _keep_qr_limit_after_a_reload(target, context, attrs):
    # a commit expires the object and the next read loads it again, the real level with it
    if getattr(target, "qr_limited", False) and (attrs is None or "admin_level" in attrs):
        set_committed_value(target, "admin_level", 0)


ACTIVE_STAMP_SECONDS = 86400


def _note_active(user):
    """When the account was last on the site, to the day (shared drafts leave authors gone longer than their rules
    say uncounted: helpers/collab_rules.py). One write a day at most; the live stream never writes."""
    if request.path.startswith(("/api/live/events", "/assets/")):
        return
    now = int(time.time())
    if now - (getattr(user, "last_active_utc", 0) or 0) >= ACTIVE_STAMP_SECONDS:
        user.last_active_utc = now

def check_ban_evade(v):

    if not v or not v.ban_evade:
        return
    
    if random.randint(0,30) < v.ban_evade and not v.is_suspended:
        v.ban(reason=N_("Evading a site-wide ban"))
        # stored in English with its message and values, so each reader sees it in their language (alerts.notify)
        notice = N_("Your %(site)s account has been permanently suspended for the following reason:\n\n> ban evasion")
        values = {"site": _site_name()}
        send_notification(v, notice % values, notice=(notice, json.dumps(values, sort_keys=True)))

        for post in g.db.query(Submission).filter_by(author_id=v.id).all():
            if post.is_banned:
                continue

            post.is_banned=True
            post.ban_reason=N_("Ban evasion. This submission's owner was banned on another account.")
            g.db.add(post)

            ma=ModAction(
                kind="ban_post",
                user_id=1,
                target_submission_id=post.id,
                board_id=post.board_id,
                note=N_("ban evasion")
                )
            g.db.add(ma)

        g.db.commit()

        for comment in g.db.query(Comment).filter_by(author_id=v.id).all():
            if comment.is_banned:
                continue

            comment.is_banned=True
            comment.ban_reason=N_("Ban evasion. This comment's owner was banned on another account.")
            g.db.add(comment)

            ma=ModAction(
                kind="ban_comment",
                user_id=1,
                target_comment_id=comment.id,
                board_id=comment.post.board_id,
                note=N_("ban evasion")
                )
            g.db.add(ma)

        g.db.commit()
        abort(403)

    else:
        v.ban_evade +=1
        g.db.add(v)
        g.db.commit()




# Wrappers
def auth_desired(f):
    # decorator for any view that changes if user is logged in (most pages)

    def wrapper(*args, **kwargs):

        v, c = get_logged_in_user()

        if c:
            kwargs["c"] = c
            
        check_ban_evade(v)
        
        g.v=v

        resp = make_response(f(*args, v=v, **kwargs))
        if v:
            resp.headers.add("Cache-Control", "private")
            resp.headers.add(
                "Access-Control-Allow-Origin",
                app.config["SERVER_NAME"])
        else:
            resp.headers.add("Cache-Control", "public")
        return resp

    wrapper.__name__ = f.__name__
    wrapper.__doc__ = f.__doc__
    return wrapper


def auth_required(f):
    # decorator for any view that requires login (ex. settings)

    def wrapper(*args, **kwargs):

        v, c = get_logged_in_user()

        #print(v, c)

        if not v:
            abort(401)
            
        check_ban_evade(v)

        if c:
            kwargs["c"] = c

        g.v = v

        # an ugly hack to make api work
        resp = make_response(f(*args, v=v, **kwargs))

        resp.headers.add("Cache-Control", "private")
        resp.headers.add(
            "Access-Control-Allow-Origin",
            app.config["SERVER_NAME"])
        return resp

    wrapper.__name__ = f.__name__
    wrapper.__doc__ = f.__doc__
    return wrapper


def is_not_banned(f):
    # decorator that enforces lack of ban

    def wrapper(*args, **kwargs):

        v, c = get_logged_in_user()

        #print(v, c)

        if not v:
            abort(401)
            
        check_ban_evade(v)

        if v.is_suspended:
            abort(403)

        if c:
            kwargs["c"] = c

        g.v = v

        resp = make_response(f(*args, v=v, **kwargs))
        resp.headers.add("Cache-Control", "private")
        resp.headers.add(
            "Access-Control-Allow-Origin",
            app.config["SERVER_NAME"])
        return resp

    wrapper.__name__ = f.__name__
    wrapper.__doc__ = f.__doc__
    return wrapper

# Require tos agreement


def tos_agreed(f):

    def wrapper(*args, **kwargs):

        v = kwargs['v']

        cutoff = int(environ.get("tos_cutoff", 0))

        if v.tos_agreed_utc > cutoff:
            return f(*args, **kwargs)
        else:
            return redirect("/help/terms#agreebox")

    wrapper.__name__ = f.__name__
    wrapper.__doc__ = f.__doc__
    return wrapper

def premium_required(f):

    #decorator that enforces valid premium status
    #use under auth_required or is_not_banned

    def wrapper(*args, **kwargs):

        v=kwargs["v"]

        if not v.has_premium:
            abort(403)

        return f(*args, **kwargs)

    wrapper.__name__=f.__name__
    wrapper.__doc__ = f.__doc__
    return wrapper


def no_negative_balance(s):

    def wrapper_maker(f):

    #decorator that enforces valid premium status
    #use under auth_required or is_not_banned

        def wrapper(*args, **kwargs):

            v=kwargs["v"]

            if v.negative_balance_cents:
                if s=="toast":
                    return (jsonify({"error": tr("You can't do that while your account balance is negative. Visit your account settings to bring your balance up to zero.")}), 402)
                elif s=="html":
                    raise(PaymentRequired)
                else:
                    raise(PaymentRequired)

            return f(*args, **kwargs)

        wrapper.__name__=f.__name__
        wrapper.__doc__ = f.__doc__
        return wrapper

    return wrapper_maker


def not_quarantined(action):
    """Refuse an account the signup guard is holding (helpers/quarantine.py). Goes under the route's sign-in wrapper,
    so `v` is known, and right above its `@validate_formkey` (several tests pin that line right above the function). A
    form the browser navigated with gets the message page, anything else (the site's scripts, the API) a 403 JSON
    error."""

    def wrapper_maker(f):

        def wrapper(*args, **kwargs):
            from ruqqus.__main__ import r
            from .quarantine import HELD_TITLE, refusal
            from .i18n_runtime import msg
            held = refusal(kwargs.get("v"), action, r)
            if held:
                if request.headers.get("Sec-Fetch-Mode") == "navigate" and not request.path.startswith("/api/"):
                    return render_template("message.html", v=kwargs.get("v"), title=msg(HELD_TITLE), message=held,
                                           link="/settings/security", link_text=tr("Verify an email")), 403
                return jsonify({"error": held}), 403
            return f(*args, **kwargs)

        wrapper.__name__ = f.__name__
        wrapper.__doc__ = f.__doc__
        return wrapper

    return wrapper_maker


QR_LIMITED_REFUSAL = N_("You signed in with a QR code. Enter your password to do this.")


def _outside_the_frame(address):
    """A page's address as a page of its own: a side panel's frame asks for /chat?embed=1."""
    parts = urlsplit(address)
    query = [(k, value) for k, value in parse_qsl(parts.query, keep_blank_values=True) if k not in ("embed", "single")]
    return same_site(parts.path + ("?" + urlencode(query) if query else ""))


def upgrade_page(v, back="/", failed=False):
    """The password form that ends a QR sign-in's limits, with where to go after it. In a frame (the chat side panel, `?embed=1`) it is
    only a link out of the frame: the page around it may hold Google's tag, which would read the pre-key this form writes (a frame shares
    the tab's sessionStorage, helpers/ads.py), and neither a wrong password's answer nor "Not now" may be drawn in a frame."""
    ends = flask_session.get(qr_login.EXPIRES_KEY)
    in_frame = request.args.get("embed") == "1"
    return render_template("login_qr_upgrade.html", v=v, back=_outside_the_frame(back) if in_frame else back, failed=failed, in_frame=in_frame,
                           i=random_image(), ends=sign_in_history.label(ends) if ends else None)


def _where_they_were():
    """Where the password form goes back to: the page asked for, or for a form, the page that holds it. Only a path on this site."""
    if request.method == "GET":
        return same_site(request.full_path.rstrip("?"))
    page = urlsplit(request.referrer or "")
    return same_site(page.path) if page.netloc == request.host else "/"


def qr_limited_answer(v):
    """What a limited QR sign-in gets instead: the password form for a page or a form the browser navigated with, a 403 for a
    script, which says so with `need_password`, so the site's scripts can tell it from any other refusal."""
    if request.method == "GET" and not request.path.startswith("/api/") or request.headers.get("Sec-Fetch-Mode") == "navigate":
        return upgrade_page(v, _where_they_were()), 403
    from .i18n_runtime import msg
    return jsonify({"error": msg(QR_LIMITED_REFUSAL), "need_password": True}), 403


def not_qr_limited(f):
    """Refuse a QR sign-in until its password is given (helpers/qr_login.py): whatever outlives "log out all", moves coins or
    reaches a secret. Goes under the route's sign-in wrapper, right above its `@validate_formkey` like `@not_quarantined`, so a
    form key from before the sign-in is refused for the right reason. tests/test_qr_login_assets.py holds the list."""

    def wrapper(*args, **kwargs):
        v = kwargs.get("v")
        if getattr(v, "qr_limited", False):
            return qr_limited_answer(v)
        return f(*args, **kwargs)

    wrapper.__name__ = f.__name__
    wrapper.__doc__ = f.__doc__
    return wrapper


COOLDOWN_MESSAGE = N_("Slow down - cooldown active for another %(minutes)s:%(seconds)s.")


def _response_status(resp):
    if isinstance(resp, tuple):
        return resp[1] if len(resp) > 1 else 200
    if isinstance(resp, RespObj):
        return resp.status_code
    return 200


def throttle_check(f):
    """Enforces the boil/gear escalating spam cooldown for the combined
    account+IP content-action namespace (post/comment/forward/repost).
    Use directly under @auth_required/@is_not_banned (needs kwargs['v']).
    Does not apply to votes."""

    def wrapper(*args, **kwargs):

        from .throttle import content_cooldown_remaining, content_register_action

        v = kwargs["v"]
        ip = request.remote_addr

        remaining = content_cooldown_remaining(v.id, ip)
        if remaining > 0:
            m, s = divmod(int(remaining) + 1, 60)
            # English for everyone: the scheduler reads the time back out of it (post_drafts.cooldown_seconds)
            return jsonify({"error": COOLDOWN_MESSAGE % {"minutes": m, "seconds": f"{s:02d}"}}), 429

        resp = f(*args, **kwargs)

        # Success in this codebase's HTML-facing routes is often a 3xx
        # redirect (e.g. submit_post redirects to the new post), not a
        # 2xx body - so "not a 4xx/5xx" is the right success test here,
        # not a strict 200-299 range.
        if _response_status(resp) < 400:
            # A route can set g.throttle_weight to charge more than one
            # action's worth of heat for a single request that did more
            # than one action's worth of work (e.g. submit_post forwarding
            # to several guilds at once) - defaults to a plain 1.
            content_register_action(v.id, ip, weight=getattr(g, "throttle_weight", 1))

        return resp

    wrapper.__name__ = f.__name__
    wrapper.__doc__ = f.__doc__
    return wrapper


def throttle_check_ip(namespace):
    """Enforces the boil/gear escalating spam cooldown for an IP-only
    namespace (login/signup/password-reset - no account exists yet at
    this point, so there's nothing to combine with the IP). Unlike
    throttle_check, heat is added on every attempt regardless of outcome
    - the threat model here (credential brute-forcing, mass account
    creation, reset-email harassment) is driven by failed/repeated
    attempts, not successful ones, so gating heat on success would let an
    attacker retry indefinitely without ever tripping the cooldown."""

    def wrapper_maker(f):

        def wrapper(*args, **kwargs):

            from .throttle import cooldown_remaining, register_action

            ip = request.remote_addr

            remaining = cooldown_remaining(namespace, ip)
            if remaining > 0:
                m, s = divmod(int(remaining) + 1, 60)
                # Translators: a waiting time in minutes and seconds, as 4:07
                return jsonify({"error": tr("Slow down - try again in %(minutes)s:%(seconds)s.",
                                            minutes=m, seconds=f"{s:02d}")}), 429

            resp = f(*args, **kwargs)
            register_action(namespace, ip)

            return resp

        wrapper.__name__ = f.__name__
        wrapper.__doc__ = f.__doc__
        return wrapper

    return wrapper_maker


def is_guildmaster(*perms):
    # decorator that enforces guildmaster status and verifies permissions
    # use under auth_required
    def wrapper_maker(f):

        def wrapper(*args, **kwargs):

            v = kwargs["v"]
            boardname = kwargs.get("guildname", kwargs.get("boardname"))
            board_id = kwargs.get("bid")
            bid=request.values.get("bid", request.values.get("board_id"))

            if boardname:
                board = get_guild(boardname)
            elif board_id:
                board = get_board(board_id)
            elif bid:
                board = get_board(bid)
            else:
                return jsonify({"error": tr("no guild specified")}), 400

            # a limited QR sign-in holds no guildmaster's powers until its password is given (`has_mod` says no for it)
            if getattr(v, "qr_limited", False) and board.leads(v):
                return qr_limited_answer(v)

            m=board.has_mod(v)
            if not m:
                return jsonify({"error": tr("You aren't a guildmaster of +%(guild)s", guild=board.name)}), 403

            if perms:
                for perm in perms:
                    if not m.__dict__.get(f"perm_{perm}") and not m.perm_full:
                        return jsonify({"error": tr("Permission `%(permission)s` required", permission=perm)}), 403


            # a guildmaster under any ban, temporary or permanent, can't moderate
            if v.is_suspended:
                abort(403)

            return f(*args, board=board, **kwargs)

        wrapper.__name__ = f.__name__
        wrapper.__doc__ = f"<small>guildmaster permissions: <code>{', '.join(perms)}</code></small><br>{f.__doc__}" if perms else f.__doc__
        return wrapper

    return wrapper_maker


# this wrapper takes args and is a bit more complicated
def admin_level_required(x):

    def wrapper_maker(f):

        def wrapper(*args, **kwargs):

            v, c = get_logged_in_user()

            if c:
                return jsonify({"error": "No admin api access"}), 403

            if not v:
                abort(401)

            # a limited QR sign-in is no admin until its password is given: it asks, where the account is one
            if getattr(v, "qr_limited", False) and v.qr_held_level >= x:
                return qr_limited_answer(v)

            # an expired temporary ban no longer locks an admin out
            if v.is_suspended:
                abort(403)

            if v.admin_level < x:
                abort(403)

            g.v = v

            response = f(*args, v=v, **kwargs)

            # (body, status) keeps its status; some routes return (response, the object they changed)
            if isinstance(response, tuple) and len(response) in (2, 3) and isinstance(response[1], int):
                resp = make_response(response)
            elif isinstance(response, tuple):
                resp = make_response(response[0])
            else:
                resp = make_response(response)

            resp.headers.add("Cache-Control", "private")
            resp.headers.add(
                "Access-Control-Allow-Origin",
                app.config["SERVER_NAME"])
            return resp

        wrapper.__name__ = f.__name__
        wrapper.__doc__ = f.__doc__
        return wrapper

    return wrapper_maker


def validate_formkey(f):
    """Always use @auth_required or @admin_level_required above @validate_form"""

    def wrapper(*args, v, **kwargs):

        # a request signed with an OAuth token (the Authorization header) cannot be forged by another site
        if not request.path.startswith("/api/v1") and not getattr(v, "client", None):

            submitted_key = request.values.get("formkey", "none")

            if not submitted_key:

                abort(401)

            elif not v.validate_formkey(submitted_key):
                abort(401)

        return f(*args, v=v, **kwargs)

    wrapper.__name__ = f.__name__
    wrapper.__doc__ = f.__doc__
    return wrapper


def no_cors(f):
    """
    Decorator prevents content being iframe'd
    """

    def wrapper(*args, **kwargs):

        origin = request.headers.get("Origin", None)

        if origin and origin != "https://" + app.config["SERVER_NAME"] and app.config["FORCE_HTTPS"]==1:

            return tr("This page may not be embedded in other webpages."), 403

        resp = make_response(f(*args, **kwargs))
        resp.headers.add("Access-Control-Allow-Origin",
                         app.config["SERVER_NAME"]
                         )

        return resp

    wrapper.__name__ = f.__name__
    wrapper.__doc__ = f.__doc__
    return wrapper

# wrapper for api-related things that discriminates between an api url
# and an html url for the same content
# f should return {'api':lambda:some_func(), 'html':lambda:other_func()}


def api(*scopes, error_handler=False):

    def wrapper_maker(f):

        def wrapper(*args, **kwargs):

            # an error page answers JSON on the API with its own status; the
            # login and token checks below are for routes, not for errors
            if error_handler and request.path.startswith(('/api/v1','/api/v2')):
                result = f(*args, **kwargs)
                return result['api']() if isinstance(result, dict) else result

            if request.path.startswith(('/api/v1','/api/v2')):

                v = kwargs.get('v')
                client = kwargs.get('c')

                if client:

                    if not v or not client:
                        return jsonify(
                            {"error": tr("401 Not Authorized. Invalid or Expired Token")}), 401

                    kwargs.pop('c')

                    # validate app associated with token
                    if client.application.is_banned:
                        return jsonify({"error": tr("403 Forbidden. The application `%(app)s` is suspended.",
                                                       app=client.application.app_name)}), 403

                    # validate correct scopes for request
                    for scope in scopes:
                        if not client.__dict__.get(f"scope_{scope}"):
                            return jsonify({"error": tr("401 Not Authorized. Scope `%(scope)s` is required.", scope=scope)}), 403

                if not v:
                    return jsonify({"error": tr("401 Not Authorized. You must log in.")}), 401

                if v.is_suspended:
                    return jsonify({"error": tr("403 Forbidden. You are banned.")}), 403

                if request.method != "GET" and not client:
                    return jsonify({"error": tr("401 Not Authorized. You must use an OAuth access token to create or edit content.")}), 401
                    

                result = f(*args, **kwargs)

                if isinstance(result, dict):
                    resp = result['api']()
                else:
                    resp = result

                if not isinstance(resp, RespObj):
                    resp = make_response(resp)

                resp.headers.add("Cache-Control", "private")
                resp.headers.add(
                    "Access-Control-Allow-Origin",
                    app.config["SERVER_NAME"])
                return resp

            else:

                result = f(*args, **kwargs)

                if not isinstance(result, dict):
                    return result

                try:
                    if request.path.startswith('/inpage/'):
                        # error handlers only answer html/api
                        return (result.get('inpage') or result['html'])()
                    elif request.path.startswith(('/api/vue/','/test/')):
                        return result['api']()
                    else:
                        return result['html']()
                except KeyError:
                    return result

        wrapper.__name__ = f.__name__
        wrapper.__doc__ = f"<small>oauth scopes: <code>{', '.join(scopes)}</code></small><br>{f.__doc__}" if scopes else f.__doc__
        return wrapper

    return wrapper_maker


SANCTIONS=[
    "CU",   #Cuba
    "IR",   #Iran
    "KP",   #North Korea
    "SY",   #Syria
    "TR",   #Turkey
    "VE",   #Venezuela
]

def no_sanctions(f):

    def wrapper(*args, **kwargs):

        if request.headers.get("cf-ipcountry","") in SANCTIONS:
            abort(451)

        return f(*args, **kwargs)

    wrapper.__name__=f.__name__
    wrapper.__doc__ = f.__doc__
    return wrapper



