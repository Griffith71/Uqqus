from flask import *
from flask import session as flask_session
import time
import hmac
import gevent
from os import environ
import re
import random
from urllib.parse import urlencode

from ruqqus.helpers.word_filter_store import apply_user_severity
from ruqqus.classes import *
from sqlalchemy import func
from ruqqus.helpers.wrappers import *
from ruqqus.helpers.base36 import *
from ruqqus.helpers.security import *
from ruqqus.helpers.alerts import *
from ruqqus.helpers.get import *
from ruqqus.helpers.regions import resolve_region
from ruqqus.helpers.redirects import same_site
from ruqqus.helpers import same_origin
from ruqqus.helpers import account_sessions, chat_devices, mfa_backup, mfa_backup_store, signin_alerts, user_agents
from ruqqus.helpers.regions import COUNTRY_NAMES
from ruqqus.mail import send_verification_email
from secrets import token_hex


from ruqqus.mail import *
from ruqqus.__main__ import app, limiter, r

valid_username_regex = re.compile(r"^[a-zA-Z0-9_]{3,25}$")
valid_password_regex = re.compile(r"^.{8,100}$")
# an email address is optional; when given it needs a name, one @ and a domain with a dot (audit I-03: no check ran,
# and every failure of the sign-up said the email was wrong)
valid_email_regex = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]+$")

# login form


@app.route("/login", methods=["GET"])
@no_cors
@auth_desired
def login_get(v):

    # only a path on this site: a redirect value is whatever a link said (bug hunt H5)
    redir = same_site(request.args.get("redirect", "/"))
    if v:
        return redirect(redir)

    return render_template("login.html",
                           failed=False,
                           i=random_image(),
                           redirect=redir)


def check_for_alts(current_id):
    # account history
    past_accs = set(flask_session.get("history", []))
    past_accs.add(current_id)
    flask_session["history"] = list(past_accs)

    # record alts
    for past_id in flask_session["history"]:

        if past_id == current_id:
            continue

        check1 = g.db.query(Alt).filter_by(
            user1=current_id, user2=past_id).first()
        check2 = g.db.query(Alt).filter_by(
            user1=past_id, user2=current_id).first()

        if not check1 and not check2:

            try:
                new_alt = Alt(user1=past_id,
                              user2=current_id)
                g.db.add(new_alt)

            except BaseException:
                pass

# login post procedure


# (an @no_cors above @app.route here never applied: Flask registers the function below it. Removed in the bug hunt,
# L1; a sign-in from another site's page is refused by same_origin at the top instead, onion address included)
@app.route("/login", methods=["POST"])
@limiter.limit("6/minute")
@throttle_check_ip('login')
def login_post():

    # a sign-in another site's page sent (login CSRF, bug hunt M8: it would sign the visitor into someone else's account)
    if same_origin.from_another_site(request.headers.get("Origin"), request.headers.get("Sec-Fetch-Site"), request.host,
                                     (app.config.get("SERVER_NAME") or "", environ.get("ONION_NAME", ""))):
        abort(403)

    username = (request.form.get("username") or "").strip()
    if not username:
        return render_template("login.html", failed=True, i=random_image()), 400

    if "@" in username:
        # an exact, case-insensitive match: never a LIKE pattern, where % and _ match anything (audit I-01)
        account = g.db.query(User).filter(
            func.lower(User.email) == username.lower(),
            User.is_deleted == False).first()
    else:
        account = get_user(username, graceful=True)

    if not account:
        time.sleep(random.uniform(0, 2))
        return render_template("login.html", failed=True, i=random_image())

    if account.is_deleted:
        time.sleep(random.uniform(0, 2))
        return render_template("login.html", failed=True, i=random_image())

    # nobody logs into a bot account (helpers/bots.py): it reads as a wrong password, so it is never anyone's alt
    if account.is_bot:
        time.sleep(random.uniform(0, 2))
        return render_template("login.html", failed=True, i=random_image())

    # test password

    if request.form.get("password"):

        if not account.verifyPass(request.form.get("password")):
            time.sleep(random.uniform(0, 2))
            return render_template("login.html", failed=True, i=random_image())

        if account.mfa_secret:
            now = int(time.time())
            hash = generate_hash(f"{account.id}+{now}+2fachallenge")
            return render_template("login_2fa.html",
                                   v=account,
                                   time=now,
                                   hash=hash,
                                   i=random_image(),
                                   redirect=request.form.get("redirect", "/")
                                   )
    elif request.form.get("2fa_token") and request.form.get("time", "").isdigit() and request.form.get("hash"):
        now = int(time.time())

        if now - int(request.form.get("time")) > 600:
            return redirect('/login')

        formhash = request.form.get("hash")
        if not validate_hash(f"{account.id}+{request.form.get('time')}+2fachallenge",
                             formhash
                             ):
            return redirect("/login")
        
        answer = request.form.get("2fa_token", "").strip()
        # a six-digit code from the authenticator (accepted once), or one of the account's backup codes (spent, and
        # two-factor stays on), or the older recovery code, which turns two-factor off and works only for an account
        # that has made no backup codes (helpers/mfa_backup.py)
        is_2fa = account.accept_2fa(answer)
        is_backup = not is_2fa and mfa_backup_store.spend(g.db, account.id, answer, now)
        is_recovery = (not is_2fa and not is_backup
                       and mfa_backup.legacy_code_allowed(mfa_backup_store.has_codes(g.db, account.id))
                       and safe_compare(answer.lower().replace(' ', ''), account.mfa_removal_code))

        if not is_2fa and not is_backup and not is_recovery:
            
            hash = generate_hash(f"{account.id}+{now}+2fachallenge")
            return render_template("login_2fa.html",
                                   v=account,
                                   time=now,
                                   hash=hash,
                                   failed=True,
                                   i=random_image()
                                   )
        elif is_recovery:
            account.mfa_secret=None
            g.db.add(account)
            g.db.commit()
        elif is_backup:
            g.db.commit()              # the code is spent

    else:
        # neither a password nor a complete 2FA answer (audit I-02: no password used to crash here)
        time.sleep(random.uniform(0, 2))
        return render_template("login.html", failed=True, i=random_image()), 400

    # set session and user id
    flask_session["user_id"] = account.id
    flask_session["session_id"] = token_hex(16)
    flask_session["login_nonce"] = account.login_nonce
    flask_session.permanent = True

    cf_country = request.headers.get("cf-ipcountry")
    agent = user_agents.stored(request.headers.get("User-Agent"))
    signed_in_utc = int(time.time())
    # a sign-in from somewhere new is emailed to a verified address (helpers/signin_alerts.py). Worked out BEFORE this
    # sign-in is recorded, or it would be compared with itself; sent after, and nothing here can stop the sign-in
    try:
        alert = signin_alerts.check(g.db, account, cf_country, agent, signed_in_utc, r, COUNTRY_NAMES)
    except Exception as e:
        print(f"sign-in alert not checked: {type(e).__name__}", flush=True)
        alert = None
    g.db.add(LoginEvent(
        user_id=account.id,
        created_utc=signed_in_utc,
        ip=request.remote_addr,
        cf_country=cf_country,
        region_code=resolve_region(cf_country),
        user_agent=agent,
    ))
    g.db.commit()
    if alert:
        try:
            html = render_template("email/new_signin.html", v=account, action_url=f"https://{app.config['SERVER_NAME']}/settings/security",
                                   settings_url=f"https://{app.config['SERVER_NAME']}/settings/security#sign-ins", **alert)
            gevent.spawn(send_mail_quietly, account.email, signin_alerts.subject(app.config["SITE_NAME"]), html)
        except Exception as e:
            print(f"sign-in alert not sent: {type(e).__name__}", flush=True)

    check_for_alts(account.id)

    account.refresh_selfset_badges()

    # check for previous page

    redir = same_site(request.form.get("redirect", "/"))       # only a path on this site (bug hunt H5)
    if redir:
        return redirect(redir)
    else:
        return redirect(account.url)


@app.route("/me", methods=["GET"])
@auth_required
def me(v):
    return redirect(v.url)


@app.route("/logout", methods=["POST"])
@auth_required
@validate_formkey
def logout(v):
        
    flask_session["user_id"]=None
    flask_session["session_id"]=None

    flask_session.modified=True

    return redirect("/")

# signing up


@app.route("/signup", methods=["GET"])
@no_cors
@auth_desired
def sign_up_get(v):
    if v:
        return redirect("/")

    agent = request.headers.get("User-Agent", None)
    if not agent:
        abort(403)

    # check tor
    # if request.headers.get("CF-IPCountry")=="T1":
    #    return render_template("sign_up_tor.html",
    #        i=random_image())

    # Make a unique form key valid for one account creation
    now = int(time.time())
    token = token_hex(16)
    flask_session["signup_token"] = token
    ip = request.remote_addr

    formkey_hashstr = str(now) + token + agent

    # formkey is a hash of session token, timestamp, and IP address
    formkey = hmac.new(key=bytes(environ.get("MASTER_KEY"), "utf-16"),
                       msg=bytes(formkey_hashstr, "utf-16"),
                       digestmod='md5'
                       ).hexdigest()

    redir = request.args.get("redirect", None)

    error = request.args.get("error", None)

    return render_template("sign_up.html",
                           formkey=formkey,
                           now=now,
                           i=random_image(),
                           redirect=redir,
                           error=error,
                           hcaptcha=app.config["HCAPTCHA_SITEKEY"]
                           )

# signup api


@app.route("/signup", methods=["POST"])
@no_cors
@auth_desired
@throttle_check_ip('signup')
def sign_up_post(v):

    if v:
        abort(403)

    agent = request.headers.get("User-Agent", None)
    if not agent:
        abort(403)

    # check tor
    # if request.headers.get("CF-IPCountry")=="T1":
    #    return render_template("sign_up_tor.html",
    #        i=random_image()
    #    )

    form_timestamp = request.form.get("now", '0')
    form_formkey = request.form.get("formkey", "none")

    submitted_token = flask_session.get("signup_token", "")
    if not submitted_token:
        abort(400)

    correct_formkey_hashstr = form_timestamp + submitted_token + agent

    correct_formkey = hmac.new(key=bytes(environ.get("MASTER_KEY"), "utf-16"),
                               msg=bytes(correct_formkey_hashstr, "utf-16"),
                               digestmod='md5'
                               ).hexdigest()

    now = int(time.time())

    username = request.form.get("username") or ""          # a missing field is a refusal, never a TypeError (bug hunt)

    # define function that takes an error message and generates a new signup
    # form
    def new_signup(error):

        return redirect(f"/signup?{urlencode({'error': error})}")

    if app.config["DISABLE_SIGNUPS"]:
        return new_signup("New account registration is currently closed. Please come back later.")

    if not (form_timestamp.isascii() and form_timestamp.isdigit() and len(form_timestamp) <= 12) or now - int(form_timestamp) < 5:
        #print(f"signup fail - {username } - too fast")
        return new_signup("There was a problem. Please try again.")

    if not hmac.compare_digest(correct_formkey, form_formkey):
        #print(f"signup fail - {username } - mismatched formkeys")
        return new_signup("There was a problem. Please try again.")

    # check for matched passwords
    if not request.form.get(
            "password") == request.form.get("password_confirm"):
        return new_signup("Passwords did not match. Please try again.")

    # check username/pass conditions
    if not re.fullmatch(valid_username_regex, username):
        #print(f"signup fail - {username } - mismatched passwords")
        return new_signup("Invalid username")

    if not re.fullmatch(valid_password_regex, request.form.get("password") or ""):
        #print(f"signup fail - {username } - invalid password")
        return new_signup("Password must be between 8 and 100 characters.")

    # Check for existing acocunts
    email = (request.form.get("email") or "").strip()
    if not email:
        email = None
    elif len(email) > 255 or not valid_email_regex.match(email):
        return new_signup("That email address doesn't look right. You can also leave it empty.")

    #counteract gmail username+2 and extra period tricks - convert submitted email to actual inbox
    if email and email.endswith("@gmail.com"):
        gmail_username=email.split('@')[0]
        gmail_username=gmail_username.split('+')[0]
        gmail_username=gmail_username.replace('.','')
        email=f"{gmail_username}@gmail.com"


    existing_account = get_user(request.form.get("username"), graceful=True)
    if existing_account and existing_account.reserved:
        return redirect(existing_account.permalink)

    if existing_account or (email and g.db.query(
            User).filter(func.lower(User.email) == email.lower()).first()):
        # #print(f"signup fail - {username } - email already exists")
        return new_signup(
            "An account with that username or email already exists.")


    # ip ratelimit
    previous = g.db.query(User).filter_by(
        creation_ip=request.remote_addr).filter(
        User.created_utc > int(
            time.time()) - 60 * 60).first()
    if previous:
        abort(429)

    # check bot
    if app.config.get("HCAPTCHA_SITEKEY"):
        token = request.form.get("h-captcha-response")
        if not token:
            return new_signup("Unable to verify captcha [1].")

        data = {"secret": app.config["HCAPTCHA_SECRET"],
                "response": token,
                "sitekey": app.config["HCAPTCHA_SITEKEY"]}
        url = "https://hcaptcha.com/siteverify"

        x = requests.post(url, data=data)

        if not x.json()["success"]:
            #print(x.json())
            return new_signup("Unable to verify captcha [2].")

    # kill tokens
    flask_session.pop("signup_token")

    # make new user
    signup_utc = int(time.time())
    cf_country = request.headers.get("cf-ipcountry")
    signup_region = resolve_region(cf_country)

    try:
        new_user = User(
            username=username,
            original_username = username,
            password=request.form.get("password"),
            email=email,
            created_utc=signup_utc,
            creation_ip=request.remote_addr,
            tos_agreed_utc=signup_utc,
            creation_region=cf_country,
            display_region=signup_region,
            region_settled_utc=signup_utc,
            # how scores read: only 0 (upvotes, coloured; the default) or 1 (upvotes minus downvotes)
            score_display=1 if request.form.get("score_display") == "1" else 0,
            # a new account's lists start open to everyone, as on X (existing accounts were left at Only me by the
            # migration); the sign-up page says so and Settings > Profile changes it (helpers/follow_lists.py)
            following_visibility=0, followers_visibility=0, subscribers_visibility=0, subscriptions_visibility=0,
            ban_evade =  int(any([x.is_suspended for x in g.db.query(User).filter(User.id.in_(tuple(flask_session.get("history", [])))).all() if x]))
            )
        apply_user_severity(new_user)

    except Exception as e:
        print(f"sign-up failed: {type(e).__name__}: {e}", flush=True)
        return new_signup("Your account couldn't be created. Please try again.")

    g.db.add(new_user)
    g.db.commit()

    g.db.add(LoginEvent(
        user_id=new_user.id,
        created_utc=signup_utc,
        ip=request.remote_addr,
        cf_country=cf_country,
        region_code=signup_region,
        user_agent=user_agents.stored(request.headers.get("User-Agent")),
    ))
    g.db.commit()

    # check alts

    check_for_alts(new_user.id)

    # send welcome/verify email
    if email:
        send_verification_email(new_user)

    # send welcome message
    # the welcome message (audit U-02: it named another site, linked to ruqqus.com and promised a front page that is
    # never ranked, which For You and the ads made untrue)
    site = app.config["SITE_NAME"]
    text = f"""Welcome to {site}, {new_user.username}. We're glad to have you here.
\n\nA few things to start with:
- Follow people and [join some guilds](/browse) so your Following feed has something in it
- Read the [rules](/help/rules) and the [help pages](/help)
- Choose how much you see in [Settings > Content](/settings/content)
\n\n-The {site} team"""
    send_notification(new_user, text)

    session["user_id"] = new_user.id
    session["session_id"] = token_hex(16)

    redir = request.form.get("redirect", None)

    # #print(f"Signup event: @{new_user.username}")

    return redirect("/")


@app.route("/forgot", methods=["GET"])
def get_forgot():

    return render_template("forgot_password.html", v=None,
                           i=random_image()
                           )


@app.route("/forgot", methods=["POST"])
@throttle_check_ip('password_reset')
def post_forgot():

    username = (request.form.get("username") or "").lstrip('@')
    email = (request.form.get("email") or '').strip()

    # exact, case-insensitive matches: never LIKE patterns (audit I-01)
    user = g.db.query(User).filter(
        func.lower(User.username) == username.lower(),
        func.lower(User.email) == email.lower(),
        User.is_deleted == False).first()

    if user:
        # generate url
        now = int(time.time())
        token = generate_hash(f"{user.id}+{now}+forgot+{user.login_nonce}")
        url = f"https://{app.config['SERVER_NAME']}/reset?id={user.id}&time={now}&token={token}"

        send_mail(to_address=user.email,
                  subject=f"{app.config['SITE_NAME']} - Password Reset Request",
                  html=render_template("email/password_reset.html",
                                       action_url=url,
                                       v=user)
                  )

    return render_template("forgot_password.html", v=None,
                           msg="If the username and email matches an account, you will be sent a password reset email. You have ten minutes to complete the password reset process.",
                           i=random_image())


@app.route("/reset", methods=["GET"])
def get_reset():

    user_id = request.args.get("id") or ""
    timestamp = request.args.get("time") or ""
    token = request.args.get("token") or ""
    if not (user_id.isascii() and user_id.isdigit() and timestamp.isascii() and timestamp.isdigit() and token):
        abort(400)                                   # a link cut short or altered (bug hunt H6: it was a 500)
    timestamp = int(timestamp)

    now = int(time.time())

    if now - timestamp > 600:
        return render_template("message.html", v=None,
            title="Password reset link expired",
            error="That password reset link has expired.")

    user = g.db.query(User).filter_by(id=int(user_id)).first()

    if not user:
        abort(404)
    if user.is_deleted:
        abort(404)

    if not validate_hash(f"{user_id}+{timestamp}+forgot+{user.login_nonce}", token):
        abort(400)

    reset_token = generate_hash(f"{user.id}+{timestamp}+reset+{user.login_nonce}")

    return render_template("reset_password.html",
                           v=user,
                           token=reset_token,
                           time=timestamp,
                           i=random_image()
                           )


@app.route("/reset", methods=["POST"])
@auth_desired
def post_reset(v):
    if v:
        return redirect('/')

    # a form missing a field, or with one that isn't a number, is a 400 (bug hunt H6: it was a 500)
    user_id = request.form.get("user_id") or ""
    timestamp = request.form.get("time") or ""
    token = request.form.get("token") or ""
    if not (user_id.isascii() and user_id.isdigit() and timestamp.isascii() and timestamp.isdigit() and token):
        abort(400)
    timestamp = int(timestamp)

    password = request.form.get("password") or ""
    confirm_password = request.form.get("confirm_password") or ""

    now = int(time.time())

    if now - timestamp > 600:
        return render_template("message.html", v=None,
                               title="Password reset expired",
                               error="That password reset form has expired.")

    user = g.db.query(User).filter_by(id=int(user_id)).first()

    if not user:
        abort(404)
    if user.is_deleted:
        abort(404)
    if not validate_hash(f"{user_id}+{timestamp}+reset+{user.login_nonce}", token):
        abort(400)

    if not password == confirm_password:
        return render_template("reset_password.html",
                               v=user,
                               token=token,
                               time=timestamp,
                               i=random_image(),
                               error="Passwords didn't match.")

    # the same rule as signing up (bug hunt H6: a reset saved any password, an empty one included)
    if not re.fullmatch(valid_password_regex, password):
        return render_template("reset_password.html",
                               v=user,
                               token=token,
                               time=timestamp,
                               i=random_image(),
                               error="Password must be between 8 and 100 characters.")

    user.passhash = hash_password(password)
    # every sign-in of the account ends, and this reset link stops working (its token holds the nonce); so does
    # every chat app signed in with it (helpers/chat_devices.py)
    account_sessions.end_other_sessions(user)
    chat_devices.sign_out(g.db, user.id, app.config["SITE_NAME"])
    g.db.add(user)

    return render_template("message_success.html", v=None,
                           title="Password reset successful!",
                           message="Login normally to access your account.")
