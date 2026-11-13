from flask import *
from sqlalchemy import func
import time
import gevent
import mistletoe
import re
from ruqqus.helpers.word_filter_store import apply_user_severity
from ruqqus.helpers import action_log
from ruqqus.helpers import ads as ads_rules
from ruqqus.helpers import coins as coins_store
from ruqqus.classes import *
from ruqqus.helpers.wrappers import *
from ruqqus.helpers.security import *
from ruqqus.helpers.sanitize import *
from ruqqus.helpers.filters import filter_comment_html
from ruqqus.helpers.markdown import *
from ruqqus.helpers.discord import remove_user, set_nick
from ruqqus.helpers.aws import *
from ruqqus.helpers.chat_permissions import on_block_created
from ruqqus.helpers import circle_store
from ruqqus.helpers import blocklist
from ruqqus.helpers import chat_devices
from ruqqus.helpers import bot_store
from ruqqus.helpers import account_sessions, mfa_backup, mfa_backup_store, sign_in_ends, sign_in_history, signin_alerts, user_agents
from ruqqus.helpers.jinja2 import qr_login_offered
from ruqqus.helpers.regions import COUNTRY_NAMES
from ruqqus.helpers import totp as totp_rules
from ruqqus.helpers import post_feedback as post_feedback_rules
from ruqqus.helpers import muting as muting_rules
from ruqqus.helpers import sensitive as sensitive_rules
from ruqqus.helpers import child_accounts
from ruqqus.helpers import text_length
from ruqqus.helpers import profile_edit
from ruqqus.helpers import age_declaration
from ruqqus.mail import *
from .front import frontlist
from ruqqus.__main__ import app, cache, limiter, r
from flask import session as flask_session
from markupsafe import escape        # flask.escape went with Flask 3: every message of the security form crashed
from urllib.parse import quote         # a message in an address (?msg=, ?error=): escape() cut it at an apostrophe
from ruqqus.helpers.alerts import notify
from ruqqus.helpers.i18n_runtime import N_, force_locale, locale_for_user, msg, tr, trn


valid_username_regex = re.compile(r"^[a-zA-Z0-9_]{3,25}$")
valid_password_regex = re.compile(r"^.{8,100}$")


@app.route("/settings/profile", methods=["POST"])
@app.route("/api/vue/settings/profile", methods=["POST"])
@auth_required
@api()
@not_qr_limited
@validate_formkey
def settings_profile_post(v):

    updated = False

    # word filter: 0 Off, 1 Standard, 2 Child (see helpers/visibility.py)
    new_level = request.values.get("filter_level")
    if new_level not in (None, ""):
        if new_level not in ("0", "1", "2"):
            abort(400)
        new_level = int(new_level)
        current_level = 1 if v.filter_level is None else v.filter_level
        if new_level != current_level:
            # a child account's filter stays on Child (helpers/age_declaration.py): its password doesn't change that
            locked = age_declaration.locked_filter(v, new_level)
            if locked:
                return jsonify({"error": msg(locked)}), 403
            # the Child filter is a lock: lowering it takes the account password
            if current_level >= 2 and new_level < 2 and not v.verifyPass(request.values.get("password", "")):
                return jsonify({"error": tr("Enter your account password to turn the Child filter down.")}), 403
            v.filter_level = new_level
            v.hide_offensive = new_level > 0   # legacy column, no longer read
            updated = True
            cache.delete_memoized(User.idlist, v)
		
    if request.values.get("hide_bot",
                          v.hide_bot) != v.hide_bot:
        updated = True
        v.hide_bot = request.values.get("hide_bot", None) == 'true'
        cache.delete_memoized(User.idlist, v)

    if request.values.get("private", v.is_private) != v.is_private:
        updated = True
        v.is_private = request.values.get("private", None) == 'true'

    if request.values.get("nofollow", v.is_nofollow) != v.is_nofollow:
        updated = True
        v.is_nofollow = request.values.get("nofollow", None) == 'true'

    if request.values.get("join_chat", v.auto_join_chat) != v.auto_join_chat:
        updated = True
        v.auto_join_chat = request.values.get("join_chat", None) == 'true'

    if request.values.get("display_name_form") is not None:
        # the display name: free text, changed at any time, never an address (helpers/display_names.py); the same
        # checks as the Edit profile pop-up's (helpers/profile_edit.py). Nobody can wear another account's @handle
        # (ignoring case, spaces, dots, dashes, underscores), nor the staff's or the site's name unless an admin.
        new_name, message = profile_edit.clean_display_name(request.values.get("display_name", ""))
        if not message:
            message = profile_edit.display_name_refusal(g.db, v.id, new_name, (app.config["SITE_NAME"], "ruqqus", "uqqus"),
                                                        admin=v.admin_level >= 3)
        if message:
            return {"html": lambda: render_template("settings_profile.html", v=v, error=message),
                    "api": lambda: (jsonify({"error": message}), 400)}

        if new_name == (v.display_name or ""):
            return {"html": lambda: render_template("settings_profile.html", v=v, error=tr("You didn't change anything")),
                    "api": lambda: (jsonify({"error": tr("You didn't change anything")}), 400)}

        v.display_name = new_name
        apply_user_severity(v)
        g.db.add(v)
        done = tr("Your display name has been updated.") if new_name else tr("Your display name has been removed.")
        return {"html": lambda: render_template("settings_profile.html", v=v, msg=done),
                "api": lambda: jsonify({"message": done})}

    if request.values.get("details") is not None:
        # job, location and website on the profile header (helpers/profile_details.py); the same checks as the Edit
        # profile pop-up's (helpers/profile_edit.py), the banned-domain list included
        job, location, website, problems = profile_edit.clean_details(
            request.values.get("profile_job", ""), request.values.get("profile_location", ""),
            request.values.get("profile_website", ""))
        message = next(iter(problems.values()), None) or profile_edit.website_refusal(website)
        if message:
            return {"html": lambda: render_template("settings_profile.html", v=v, error=message),
                    "api": lambda: (jsonify({"error": message}), 400)}

        v.profile_job, v.profile_location, v.profile_website = job, location, website
        apply_user_severity(v)
        g.db.add(v)
        return {"html": lambda: render_template("settings_profile.html", v=v, msg=tr("Your profile details have been updated.")),
                "api": lambda: jsonify({"message": tr("Your profile details have been updated.")})}

    if request.values.get("bio") is not None:
        # the same checks as the Edit profile pop-up's (helpers/profile_edit.py): refused when too long, weighted
        # (helpers/text_length.py), rather than silently cut at 256; the narrow list of tags bios keep; the banned domains
        check = profile_edit.check_bio(request.values.get("bio"), v.bio)

        if check.code in ("too_long", "formatting"):
            message = check.message
            return {"html": lambda: render_template("settings_profile.html", v=v, error=message),
                    "api": lambda: (jsonify({"error": message}), 400)}

        if check.code == "unchanged":
            return {"html": lambda: render_template("settings_profile.html", v=v, error=check.message),
                    "api": lambda: jsonify({"error": check.message})}

        if check.code == "links":
            #auto ban for digitally malicious content
            if check.malicious:
                v.ban(days=30, reason=N_("Digitally malicious content is not allowed."))
            return jsonify({"error": check.message}), 401

        v.bio = check.text
        v.bio_html = check.html
        apply_user_severity(v)
        g.db.add(v)

        #seo profile spam
        if profile_edit.looks_like_link_spam(v, check.html, int(time.time())):
            v.ban(reason="seo spam")

        return {"html": lambda: render_template("settings_profile.html", v=v, msg=tr("Your bio has been updated.")),
                "api": lambda: jsonify({"message": tr("Your bio has been updated.")})}

    if request.values.get("filters") is not None:

        filters=request.values.get("filters")[0:1000].lstrip().rstrip()

        if filters==v.custom_filter_list:
            return {"html":lambda:render_template("settings_profile.html",
                                   v=v,
                                   error=tr("You didn't change anything")),
		    "api":lambda:jsonify({"error":tr("You didn't change anything")})
		   }

        v.custom_filter_list=filters
        g.db.add(v)
        return {"html":lambda:render_template("settings_profile.html",
                               v=v,
                               msg=tr("Your custom filters have been updated.")),
		"api":lambda:jsonify({"message":tr("Your custom filters have been updated")})}



    x = request.values.get("title_id", None)
    if x:
        if not (x.isascii() and x.isdigit() and len(x) <= 9):
            abort(400)                # not a title's id (bug hunt: "abc" was a 500)
        x = int(x)
        if x == 0:
            v.title_id = None
            updated = True
        elif x > 0:
            title = get_title(x)
            if bool(eval(title.qualification_expr)):
                v.title_id = title.id
                updated = True
            else:
                return jsonify({"error": tr("You don't meet the requirements for title `%(title)s`.", title=title.text)}), 403
        else:
            abort(400)

    defaultsorting = request.values.get("defaultsorting")
    if defaultsorting:
        if defaultsorting in ["hot", "new", "old", "activity", "disputed", "top"]:
            v.defaultsorting = defaultsorting
            updated = True
        else:
            abort(400)

    defaulttime = request.values.get("defaulttime")
    if defaulttime:
        if defaulttime in ["day", "week", "month", "year", "all"]:
            v.defaulttime = defaulttime
            updated = True
        else:
            abort(400)

    if updated:
        g.db.add(v)

        return jsonify({"message": tr("Your settings have been updated.")})

    else:
        return jsonify({"error": tr("You didn't change anything.")}), 400


def _backup_codes_page(v, codes, msg):
    """The Security page with a set of backup codes in it, this once: not cached, never in an address."""
    page = make_response(render_template(
        "settings_security.html", v=v, mfa_secret=None, error=None, msg=msg,
        new_backup_codes=[mfa_backup.display(c) for c in codes], **mfa_backup_store.page_context(g.db, v),
        **sign_in_history.page_context(g.db, v, COUNTRY_NAMES)))
    page.headers["Cache-Control"] = "no-store"
    return page


def _tell_qr_login_on(v):
    """QR sign-in was turned back on: a notification always, and an email by the sign-in emails' own rule (a verified
    address, the setting on). Best effort: it has already been turned on, and neither failing undoes that."""
    try:
        notify(v, N_("QR sign-in was turned on for your account. If this wasn't you, change your password in "
                     "[Settings > Security](/settings/security)."))
    except Exception as e:
        g.db.rollback()
        print(f"QR sign-in notice not sent: {type(e).__name__}", flush=True)
    if not signin_alerts.wants_email(v):
        return
    try:
        with force_locale(locale_for_user(v)):
            html = render_template("email/qr_login_on.html", v=v,
                                   action_url=f"https://{app.config['SERVER_NAME']}/settings/security",
                                   settings_url=f"https://{app.config['SERVER_NAME']}/settings/security#sign-ins",
                                   **signin_alerts.where_and_when(request.headers.get("cf-ipcountry"),
                                                                  user_agents.stored(request.headers.get("User-Agent")),
                                                                  int(time.time()), COUNTRY_NAMES))
            subject = signin_alerts.qr_on_subject(app.config["SITE_NAME"])
        gevent.spawn(send_mail_quietly, v.email, subject, html)
    except Exception as e:
        print(f"QR sign-in email not sent: {type(e).__name__}", flush=True)


@app.route("/settings/security", methods=["POST"])
@auth_required
@not_qr_limited
@validate_formkey
def settings_security_post(v):

    if request.form.get("new_password"):
        if request.form.get(
                "new_password") != request.form.get("cnf_password"):
            return redirect("/settings/security?error=" +
                            quote(tr("Passwords do not match.")))

        if not re.match(valid_password_regex, request.form.get("new_password")):
            #print(f"signup fail - {username } - invalid password")
            return redirect("/settings/security?error=" + 
                            quote(tr("Password must be between 8 and 100 characters.")))

        if not v.verifyPass(request.form.get("old_password")):
            return render_template(
                "settings_security.html", v=v, error=tr("Incorrect password"))

        v.passhash = v.hash_password(request.form.get("new_password"))
        # whoever held the old password is signed out, and so is chat everywhere else but this sign-in's own (helpers/chat_signouts.py)
        account_sessions.revoke(g.db, v, "password", session=flask_session, spare_this_sign_in=True)
        ads_rules.hold_after_password(flask_session, int(time.time()))   # the form left a new pre-key for chat (chat_key.js)

        g.db.add(v)

        # the chat key, locked again by the browser with the new password (assets/js/chat_key.js); without it the
        # old copy stays and chat asks for the recovery code or the old password on the next new device (M-03)
        blob = request.form.get("chat_key_blob", "")
        if blob and re.fullmatch(r"[A-Za-z0-9_\-]{40,4096}", blob):
            identity = g.db.query(ChatIdentity).filter_by(user_id=v.id).first()
            if identity is not None:
                identity.key_blob_password = blob
                identity.recovery_key_encrypted = None
                g.db.add(identity)

        return redirect("/settings/security?msg=" +
                        quote(tr("Your password has been changed. Your other devices have been signed out.")))

    if request.form.get("new_email"):

        if not v.verifyPass(request.form.get('password')):
            return redirect("/settings/security?error=" +
                            quote(tr("Invalid password.")))

        new_email = request.form.get("new_email","").lstrip().rstrip()
        #counteract gmail username+2 and extra period tricks - convert submitted email to actual inbox
        if new_email.endswith("@gmail.com"):
            gmail_username=new_email.split('@')[0]
            gmail_username=gmail_username.split("+")[0]
            gmail_username=gmail_username.replace('.','')
            new_email=f"{gmail_username}@gmail.com"
        if new_email == v.email:
            return redirect("/settings/security?error=" +
                            quote(tr("That email is already yours!")))

        # check to see if email is in use
        existing = g.db.query(User).filter(User.id != v.id,
                                           func.lower(User.email) == new_email.lower()).first()
        if existing:
            return redirect("/settings/security?error=" +
                            quote(tr("That email address is already in use.")))

        url = f"https://{app.config['SERVER_NAME']}/activate"

        now = int(time.time())

        token = generate_hash(f"{new_email}+{v.id}+{now}")
        params = f"?email={quote(new_email)}&id={v.id}&time={now}&token={token}"

        link = url + params

        # in the account's own language, the subject too
        with force_locale(locale_for_user(v)):
            send_mail(to_address=new_email,
                      subject=tr("Verify your email address."),
                      html=render_template("email/email_change.html",
                                           action_url=link,
                                           v=v)
                      )

        return redirect("/settings/security?msg=" + quote(
            tr("Check your email and click the verification link to complete the email change.")))

    if request.form.get("2fa_token", ""):

        if not v.verifyPass(request.form.get('password')):
            return redirect("/settings/security?error=" +
                            quote(tr("Invalid password or token.")))

        # already on: the secret is not replaced by whoever has the password and a session (that would swap the
        # account's authenticator for theirs without ever needing the old one); turn it off first, with a code
        if v.mfa_secret:
            return redirect("/settings/security?error=" +
                            quote(tr("Two-factor authentication is already on. Turn it off first to set up a new device.")))

        secret = request.form.get("2fa_secret") or ""
        if not re.fullmatch(r"[A-Z2-7]{16,64}", secret):
            return redirect("/settings/security?error=" + quote(tr("Invalid password or token.")))
        step = totp_rules.matched_step(secret, request.form.get("2fa_token"))
        if step is None:
            return redirect("/settings/security?error=" +
                            quote(tr("Invalid password or token.")))
        totp_rules.claim(r, v.id, step)               # that code is spent: it can't switch two-factor off a moment later

        v.mfa_secret = secret
        account_sessions.revoke(g.db, v, "mfa", session=flask_session, spare_this_sign_in=True)
        g.db.add(v)

        # the account's first backup codes, shown once, here
        codes = mfa_backup.generate()
        mfa_backup_store.replace(g.db, v.id, codes, int(time.time()))
        return _backup_codes_page(v, codes, tr("Two-factor authentication is on. Your other devices have been signed out."))

    if request.form.get("new_backup_codes", ""):

        if not v.mfa_secret:
            return redirect("/settings/security?error=" + quote(tr("Turn on two-factor authentication first.")))
        if not v.verifyPass(request.form.get("password")) or not v.accept_2fa(request.form.get("new_backup_codes")):
            return redirect("/settings/security?error=" + quote(tr("Invalid password or code.")))

        codes = mfa_backup.generate()                 # every earlier code, used or not, stops working
        mfa_backup_store.replace(g.db, v.id, codes, int(time.time()))
        return _backup_codes_page(v, codes, tr("Your new backup codes are below. The old ones no longer work."))

    if request.form.get("2fa_remove", ""):

        if not v.verifyPass(request.form.get('password')):
            return redirect("/settings/security?error=" +
                            quote(tr("Invalid password or token.")))

        token = request.form.get("2fa_remove") or ""

        # an authenticator code (accepted once), a backup code (spent), or the older recovery code, which only an
        # account with no backup codes still has (helpers/mfa_backup.py)
        if not (v.accept_2fa(token)
                or mfa_backup_store.spend(g.db, v.id, token, int(time.time()))
                or (mfa_backup.legacy_code_allowed(mfa_backup_store.has_codes(g.db, v.id))
                    and safe_compare(v.mfa_removal_code, token.lower().replace(' ', '')))):
            return redirect("/settings/security?error=" +
                            quote(tr("Invalid password or token.")))

        v.mfa_secret = None
        mfa_backup_store.delete_all(g.db, v.id)       # no second factor, no codes
        account_sessions.revoke(g.db, v, "mfa", session=flask_session, spare_this_sign_in=True)
        g.db.add(v)

        return redirect("/settings/security?msg=" +
                        quote(tr("Two-factor authentication disabled. Your other devices have been signed out.")))

    if request.form.get("signin_alerts_form", ""):

        # switching a security notice off is a security change, so it asks for the password like the others
        if not v.verifyPass(request.form.get("password")):
            return redirect("/settings/security?error=" + quote(tr("Invalid password.")))

        v.signin_alerts = bool(request.form.get("signin_alerts"))
        g.db.add(v)

        return redirect("/settings/security?msg=" + quote(
            tr("We'll email you about sign-ins from somewhere new.") if v.signin_alerts else tr("Sign-in emails are off.")))

    # QR sign-in: off takes a way in away, so it asks nothing; back on adds one, so it asks the password and tells the member.
    # While the site doesn't offer it the form isn't drawn, and a request for it falls through to "Nothing to change"
    if request.form.get("qr_login_form", "") and qr_login_offered():

        if request.form.get("qr_login") == "off":
            v.qr_login_off = True
            g.db.add(v)
            return redirect("/settings/security?msg=" + quote(tr("QR sign-in is off.")))

        if not v.qr_login_off:                         # on already (another tab): no password asked, nobody told
            return redirect("/settings/security?msg=" + quote(tr("QR sign-in is on.")))

        if not v.verifyPass(request.form.get("password") or ""):
            return redirect("/settings/security?error=" + quote(tr("Invalid password.")))

        v.qr_login_off = False
        g.db.add(v)
        g.db.commit()
        _tell_qr_login_on(v)

        return redirect("/settings/security?msg=" + quote(tr("QR sign-in is on.")))

    # a form with none of the fields above (audit CR-04: it fell through and answered 500)
    return redirect("/settings/security?error=" + quote(tr("Nothing to change.")))


@app.route("/settings/dark_mode/<x>", methods=["POST"])
@auth_required
@validate_formkey
def settings_dark_mode(x, v):

    try:
        x = int(x)
    except BaseException:
        abort(400)

    if x not in [0, 1]:
        abort(400)

    if not v.can_use_darkmode:
        flask_session["dark_mode_enabled"] = False
        abort(403)
    else:
        # print(f"current cookie is {flask_session.get('dark_mode_enabled')}")
        flask_session["dark_mode_enabled"] = x
        # print(f"set dark mode @{v.username} to {x}")
        # print(f"cookie is now {flask_session.get('dark_mode_enabled')}")
        flask_session.modified = True
        return "", 204


@app.route("/settings/log_out_all_others", methods=["POST"])
@auth_required
@not_qr_limited
@validate_formkey
def settings_log_out_others(v):

    submitted_password = request.form.get("password", "")

    if not v.verifyPass(submitted_password):
        return render_template("settings_security.html",
                               v=v, error=tr("Incorrect Password")), 401

    # chat too, chat apps included, all but this sign-in's own (helpers/chat_signouts.py)
    account_sessions.revoke(g.db, v, "others", session=flask_session, spare_this_sign_in=True)

    g.db.add(v)

    return render_template("settings_security.html", v=v,
                           msg=tr("All other devices have been logged out"))


@app.route("/settings/images/profile", methods=["POST"])
@is_not_banned
@validate_formkey
def settings_images_profile(v):
    if v.can_upload_avatar:
        upload = request.files.get("profile")
        if not upload or not upload.filename:                          # audit CR-04: no file answered 500
            return render_template("settings_profile.html", v=v, error=tr("Choose a picture first."))
        v.set_profile(upload)

        # anti csam (it removed `board`'s picture, a name that doesn't exist here, so a match failed to remove it)
        new_thread = gevent.spawn(check_csam_url,
                                      v.profile_url,
                                            v,
                                            lambda: v.del_profile()
                                            )
        new_thread.start()

        return render_template("settings_profile.html",
                               v=v, msg=tr("Profile picture successfully updated."))

    return render_template("settings_profile.html", v=v,
                           msg=tr("You must have a verified email address to upload images via Tor."))


@app.route("/settings/images/banner", methods=["POST"])
@is_not_banned
@validate_formkey
def settings_images_banner(v):
    if v.can_upload_banner:
        upload = request.files.get("banner")
        if not upload or not upload.filename:                          # audit CR-04: no file answered 500
            return render_template("settings_profile.html", v=v, error=tr("Choose a picture first."))
        v.set_banner(upload)

        # anti csam (it removed `board`'s picture, a name that doesn't exist here, so a match failed to remove it)
        new_thread = gevent.spawn(check_csam_url,
                                      v.banner_url,
                                            v,
                                            lambda: v.del_banner()
                                            )
        new_thread.start()

        return render_template("settings_profile.html",
                               v=v, msg=tr("Banner successfully updated."))

    return render_template("settings_profile.html", v=v,
                           msg=tr("You must have a verified email address to upload images via Tor."))


@app.route("/settings/delete/profile", methods=["POST"])
@auth_required
@validate_formkey
def settings_delete_profile(v):

    v.del_profile()

    return render_template("settings_profile.html", v=v,
                           msg=tr("Profile picture successfully removed."))


@app.route("/settings/new_feedkey", methods=["POST"])
@auth_required
@not_qr_limited
@validate_formkey
def settings_new_feedkey(v):

    v.feed_nonce += 1
    g.db.add(v)

    return render_template("settings_profile.html", v=v,
                           msg=tr("Your new custom RSS Feed Token has been generated."))


@app.route("/settings/delete/banner", methods=["POST"])
@auth_required
@validate_formkey
def settings_delete_banner(v):

    v.del_banner()

    return render_template("settings_profile.html", v=v,
                           msg=tr("Banner successfully removed."))


@app.route("/settings/read_announcement", methods=["POST"])
@auth_required
@validate_formkey
def update_announcement(v):

    v.read_announcement_utc = int(time.time())
    g.db.add(v)

    return "", 204


def _leave_chat_groups(v):
    """Out of every chat group, handing a group they own to its next admin or member (routes/chat_groups._leave,
    which also takes them out of the Matrix room). Best effort: chat being down never stops a deletion."""
    try:
        from ruqqus.helpers import chat_group_store
        from ruqqus.routes.chat_groups import _leave
        now = int(time.time())
        for status in ("joined", "invited"):
            for row in chat_group_store.groups_for(g.db, v.id, status, limit=200):
                group = chat_group_store.group(g.db, row[0])
                if group is None:
                    continue
                try:
                    _leave(group, v, now)
                except Exception as e:          # one group's Matrix trouble never keeps them in the others
                    app.logger.warning(f"delete_account: group {row[0]} not left for {v.id}: {e}")
    except Exception as e:
        app.logger.warning(f"delete_account: chat groups not left for {v.id}: {e}")


def close_own_account(v, reason, sign_out_browser=True):
    """Everything that closing an account does, short of the commit: the member deleting theirs, or the age check finding that
    the holder is under the youngest age where they are (routes/age_declaration_pages.py). Signs the request's browser out, unless
    this is another account being closed (`sign_out_browser=False`): a parent's child accounts go with it (helpers/child_accounts.py),
    and so does a child its parent deletes, from the parent's own request."""
    from ruqqus.helpers import child_store
    child_store.lock_parent(g.db, v.id)                              # a child being made at this moment waits, then is one of the children below
    for child_id in child_store.child_ids_safely(g.db, v.id):          # a parent's children are deleted with it
        child = g.db.query(User).filter_by(id=child_id).first()
        if child is not None and not child.is_deleted:
            close_own_account(child, reason, sign_out_browser=False)
    remove_user(v)

    v.discord_id=None
    v.is_deleted = True
    # every sign-in and every chat device of the account goes, outside apps and the site's own browsers (helpers/chat_signouts.py:
    # a queued sign-out, worked once this is committed); never this request's cookie for another account closed with it
    account_sessions.revoke(g.db, v, "deleted")
    v.delete_reason = reason
    v.patreon_id=None
    v.patreon_pledge_cents=0
    v.del_banner()
    v.del_profile()
    g.db.add(v)

    # what ends with the account is written down: a guildmaster's seat, an exile and a contributor's approval used to go silently, so the
    # Log (and a guild's own) read as if they were still in force (the Log, slice D2)
    mods = g.db.query(ModRelationship).filter_by(user_id=v.id).all()
    for mod in mods:
        if mod.accepted and not mod.invite_rescinded:
            action_log.record_automatic(g.db, "remove_mod", target_user_id=v.id, board_id=mod.board_id, rule="",
                                        reason="Its account was deleted.")
        g.db.delete(mod)

    bans = g.db.query(BanRelationship).filter_by(user_id=v.id).all()
    for ban in bans:
        if ban.is_active:
            action_log.record_automatic(g.db, "unexile_user", target_user_id=v.id, board_id=ban.board_id, rule="",
                                        reason="Its account was deleted.")
        g.db.delete(ban)

    contribs = g.db.query(ContributorRelationship).filter_by(
        user_id=v.id).all()
    for contrib in contribs:
        if contrib.is_active:
            action_log.record_automatic(g.db, "uncontrib_user", target_user_id=v.id, board_id=contrib.board_id, rule="",
                                        reason="Its account was deleted.")
        g.db.delete(contrib)

    # other people's blocks of this account stay: they are theirs (and matter if an admin restores the account)

    # everything else the account had running or kept (helpers/account_cleanup.py, audit I-05): circles,
    # co-authorships, stories, channels, shared drafts, answers, bookmarks, drafts, linked storage, the chat key,
    # sign-in addresses, profile details
    from ruqqus.helpers import account_cleanup
    account_cleanup.close_account(g.db, v.id, int(time.time()))
    _leave_chat_groups(v)
    # the chat name says it is gone (its devices were queued for sign-out above)
    chat_devices.sync_name(g.db, v)

    for b in v.boards_modded:
        if b.mods_count == 0 and not b.is_circle:
            b.is_private = False
            b.restricted_forwarding = False
            b.all_opt_out = False
            g.db.add(b)
            g.db.flush()
            action_log.record_automatic(g.db, "guild_made_public", board_id=b.id, rule="",
                                        reason="It had no guildmaster left, so it was opened up.")

    if sign_out_browser:
        # this browser's sign-in ends for good (helpers/sign_in_ends.py); never for another account closed with it (a parent's child)
        sign_in_ends.end(g.db, flask_session.get("session_id"), v.id, "deleted", time.time())
        g.sign_in_ended_here = True
        flask_session.pop("user_id", None)
        flask_session.pop("session_id", None)

    #deal with throwaway spam - auto nuke content if account age below threshold
    if int(time.time()) - v.created_utc < 60*60*12:
        for post in v.submissions:
            post.is_banned=True

            new_ma=ModAction(
                user_id=1,
                kind="ban_post",
                target_submission_id=post.id,
                note="spam",
		board_id=post.board_id
                )

            g.db.add(post)
            g.db.add(new_ma)

        for comment in v.comments:
            comment.is_banned=True
            new_ma=ModAction(
                user_id=1,
                kind="ban_comment",
                target_comment_id=comment.id,
                note="spam",
		board_id=comment.post.board_id
                )
            g.db.add(comment)
            g.db.add(new_ma)


child_accounts.closer = close_own_account        # a parent's banned or deleted account closes its children the same way (helpers/child_accounts.py)


@app.route("/settings/delete_account", methods=["POST"])
@is_not_banned
@no_negative_balance("html")
@not_qr_limited
@validate_formkey
def delete_account(v):

    if not v.verifyPass(request.form.get("password", "")) or (
            v.mfa_secret and not (v.accept_2fa(request.form.get("twofactor", ""))
                                  or mfa_backup_store.spend(g.db, v.id, request.form.get("twofactor", ""), int(time.time())))):
        return render_template("settings_security.html", v=v,
                               error=tr("Invalid password or token") if v.mfa_secret else tr("Invalid password"))

    from ruqqus.helpers import child_store
    if child_store.count_children_safely(g.db, v.id) and child_store.owing_children(g.db, v.id):
        # an account that owes money isn't deleted to escape it, and deleting its parent would delete it
        return render_template("settings_security.html", v=v, error=tr("One of your child accounts owes money, so your account can't be deleted yet."))

    # read before the accounts close: the page clears this account's chat here, and that of the children closed with it
    deleted_mxid, deleted_children = chat_devices.closing_with(g.db, v.id)
    close_own_account(v, request.form.get("delete_reason", ""))
    g.db.commit()

    # not a redirect: this answer, which only a deletion that went through ever gets, is what clears the account's chat device
    # out of this browser. A visitor's home page can be cached, and an address that said so could be linked to.
    return render_template("account_deleted.html", v=None, deleted_mxid=deleted_mxid, deleted_children=deleted_children,
                           title=tr("Your account has been deleted"),
                           message=tr("Your chats and their keys have been removed from this browser."))


@app.route("/settings/blocks", methods=["GET"])
@auth_required
def settings_blockedpage(v):
    """Block & Mute: the accounts a member blocks and mutes (?show=accounts, the default) or the guilds they mute
    (?show=guilds). One segment per page, so only it is read."""

    from .muting import muted_accounts

    show = "guilds" if request.args.get("show") == "guilds" else "accounts"

    counts = blocklist.counts_from_query(request.args.get("done"))
    return render_template("settings_blocks.html",
                           v=v,
                           show=show,
                           muted=muted_accounts(v) if show == "accounts" else [],
                           msg=blocklist.summary(counts) if counts else None,
                           error=blocklist.PROBLEMS.get(request.args.get("problem")),
                           max_import=blocklist.MAX_NAMES)


@app.route("/settings/filters", methods=["GET"])
@auth_required
def settings_blockedguilds(v):
    # the old Muted Guilds page is the Guilds segment of Block & Mute now; old links still land on it
    return redirect("/settings/blocks?show=guilds")


@app.route("/settings/block", methods=["POST"])
@auth_required
@validate_formkey
def settings_block_user(v):

    user = get_user(request.values.get("username"), graceful=True)

    # a block ends paid days with no refund, either way (circle_store.end_between), and coins ask for the password (helpers/qr_login.py)
    if v.qr_limited and user and circle_store.paid_between(g.db, v.id, user.id):
        return qr_limited_answer(v)

    done = blocklist.block_account(g.db, v, user)
    if done != "blocked":
        message, status = blocklist.REFUSALS[done]
        return jsonify({"error": message.format(name=user.username if user else "")}), status

    _forget_block_caches(v)

    return jsonify({"message": tr("@%(name)s blocked.", name=user.username)})


def _forget_block_caches(v):
    cache.delete_memoized(v.idlist)
    #cache.delete_memoized(Board.idlist, v=v)
    cache.delete_memoized(frontlist, v=v)


@app.route("/settings/blocks/export", methods=["GET"])
@limiter.limit("5/day")
@auth_required
def settings_blocks_export(v):
    """Your blocked and muted accounts as a CSV (`kind,username`), to keep or to import somewhere else."""
    from sqlalchemy import select as sa_select
    blocked = [r[0] for r in g.db.execute(sa_select(User.username).join(UserBlock, UserBlock.target_id == User.id).where(
        UserBlock.user_id == v.id, User.is_deleted == False))]
    muted = [r[0] for r in g.db.execute(sa_select(User.username).join(UserMute, UserMute.target_id == User.id).where(
        UserMute.user_id == v.id, User.is_deleted == False))]
    resp = make_response(blocklist.render(blocked, muted))
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = f'attachment; filename="{v.username}-blocklist.csv"'
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/settings/blocks/import", methods=["POST"])
@limiter.limit("5/day")
@auth_required
@not_qr_limited
@validate_formkey
def settings_blocks_import(v):
    """Blocks and mutes the accounts in an uploaded file or a pasted list (at most 200). Every account goes through
    the same routine as the Block button; the answer is counts only (helpers/blocklist.py)."""
    upload = request.files.get("file")
    raw = request.form.get("names", "")
    if upload and upload.filename:
        raw = upload.read(blocklist.MAX_CHARS + 1).decode("utf-8", errors="replace")
    rows, problem = blocklist.parse(raw)
    if problem:
        return redirect("/settings/blocks?show=accounts&problem=" + problem)

    counts, muted_any = blocklist.run_import(g.db, v, rows, lambda name: get_user(name, graceful=True))
    _forget_block_caches(v)
    if muted_any:
        from ruqqus.helpers import muting
        muting.forget(v)
        muting.clear_cached_feeds(v)
    return redirect("/settings/blocks?show=accounts&done=" + blocklist.counts_to_query(counts))


@app.route("/settings/unblock", methods=["POST"])
@auth_required
@not_qr_limited
@validate_formkey
def settings_unblock_user(v):

    user = get_user(request.values.get("username"))

    x = v.has_block(user)
    if not x:
        abort(409)

    g.db.delete(x)
    g.db.flush()
    # their 1:1 chat works again once no block stands either way (helpers/chat_permissions.py)
    from ruqqus.helpers import chat_permissions
    chat_permissions.on_block_removed(v, user)

    cache.delete_memoized(v.idlist)
    #cache.delete_memoized(Board.idlist, v=v)
    cache.delete_memoized(frontlist, v=v)

    return jsonify({"message": tr("@%(name)s unblocked.", name=user.username)})


# Muting a guild (helpers/muting.py): a `boardblocks` row. The old /settings/block_guild and /settings/unblock_guild
# addresses still work (they were the "hide from /all" buttons); the pages now use the mute_guild ones.
@app.route("/settings/mute_guild", methods=["POST"])
@app.route("/settings/block_guild", methods=["POST"])
@auth_required
@validate_formkey
def settings_block_guild(v):

    name = request.values.get("board") or ""                            # audit CR-04: no name answered 500
    board = get_guild(name, graceful=True) if name else None

    if not board:
        return jsonify({"error": tr("That guild doesn't exist.")}), 404

    if v.has_blocked_guild(board):
        return jsonify({"error": tr("You have already muted +%(guild)s.", guild=board.name)}), 409

    new_block = BoardBlock(user_id=v.id,
                           board_id=board.id,
                           created_utc=int(time.time())
                           )
    g.db.add(new_block)
    g.db.commit()

    muting_rules.clear_guild_mute_caches(v)

    joined = g.db.query(Subscription.id).filter_by(user_id=v.id, board_id=board.id, is_active=True).first()
    if joined:
        return jsonify({"message": tr("+%(guild)s muted. You are still a member, but its posts stay out of All, For You and Following until you unmute it.", guild=board.name)})
    return jsonify({"message": tr("+%(guild)s muted. Its posts stay out of All, For You and Following.", guild=board.name)})


@app.route("/settings/unmute_guild", methods=["POST"])
@app.route("/settings/unblock_guild", methods=["POST"])
@auth_required
@validate_formkey
def settings_unblock_guild(v):

    name = request.values.get("board") or ""
    board = get_guild(name, graceful=True) if name else None

    x = v.has_blocked_guild(board) if board else None
    if not x:
        abort(409)

    g.db.delete(x)
    g.db.commit()

    muting_rules.clear_guild_mute_caches(v)

    return jsonify({"message": tr("+%(guild)s unmuted.", guild=board.name)})


@app.route("/settings/apps", methods=["GET"])
@auth_required
@not_qr_limited
def settings_apps(v):

    return render_template("settings_apps.html", v=v)


@app.route("/settings/remove_discord", methods=["POST"])
@auth_required
@not_qr_limited
@validate_formkey
def settings_remove_discord(v):

    if v.admin_level>1:
        return render_template("settings_filters.html", v=v, error=tr("Admins can't disconnect Discord."))

    remove_user(v)

    v.discord_id=None
    g.db.add(v)
    g.db.commit()

    return redirect("/settings/profile")

@app.route("/settings/content", methods=["GET"])
@auth_required
def settings_content_get(v):

    return render_template("settings_filters.html", v=v)

@app.route("/settings/score_display", methods=["POST"])
@auth_required
@validate_formkey
def settings_score_display(v):
    """How the member reads scores (helpers/jinja2.py score_mode): 0 upvotes coloured by their share of the votes,
    1 upvotes minus downvotes. Anything else is refused."""
    choice = request.values.get("score_display")
    if choice not in ("0", "1"):
        return jsonify({"error": tr("Choose how scores read.")}), 400
    v.score_display = int(choice)
    g.db.add(v)
    g.db.commit()
    return jsonify({"message": tr("Saved.")})


@app.route("/settings/for_you/reset", methods=["POST"])
@auth_required
@validate_formkey
def settings_for_you_reset(v):
    """Start the For You feed over. The date is saved (For You does not read it yet:
    User.interest_subcats / social_proof_post_ids are where it will), and what the member asked with
    "Show fewer posts like this" is forgotten, so those posts and their categories come back. The feed
    lists that are kept for a few minutes per member are dropped so the change shows at once."""

    v.for_you_reset_utc = int(time.time())
    g.db.add(v)
    post_feedback_rules.clear(g.db, v.id)
    g.db.commit()

    cache.delete_memoized(v.interest_subcats)
    cache.delete_memoized(v.social_proof_post_ids)
    cache.delete_memoized(v.for_you_idlist)
    cache.delete_memoized(frontlist, v=v)

    return jsonify({"message": tr("Your For You feed has been reset.")})


@app.route("/settings/purchase_history", methods=["GET"])
@auth_required
def settings_purchase_history(v):

    return render_template("settings_txnlist.html", v=v)

@app.route("/settings/name_change", methods=["POST"])
@is_not_banned                   # a suspended account doesn't rename itself (bug hunt)
@no_negative_balance("html")     # and no coins are spent while the account owes after a chargeback, as elsewhere
@not_qr_limited
@validate_formkey
def settings_name_change(v):

    if v.admin_level:
        return render_template("settings_profile.html",
                           v=v,
                           error=tr("Admins can't change their name."))


    new_name = (request.form.get("name") or "").strip()                   # audit CR-04: no name answered 500

    #make sure name is different
    if new_name==v.username:
        return render_template("settings_profile.html",
                           v=v,
                           error=tr("You didn't change anything"))

    #can't change name on verified ID accounts
    if v.real_id:
        return render_template("settings_profile.html",
                           v=v,
                           error=tr("Your ID is verified so you can't change your username."))

    #7 day cooldown
    if v.name_changed_utc > int(time.time()) - 60*60*24*7:
        return render_template("settings_profile.html",
                           v=v,
                           error=trn("You changed your name %(num)s day ago. You need to wait 7 days between name changes.",
                                     "You changed your name %(num)s days ago. You need to wait 7 days between name changes.",
                                     (int(time.time()) - v.name_changed_utc)//(60*60*24)))

    #costs 3 coins
    if v.coin_balance < 20:
        return render_template("settings_profile.html",
                           v=v,
                           error=trn("Username changes cost 20 Coins. You only have a balance of %(num)s Coin",
                                     "Username changes cost 20 Coins. You only have a balance of %(num)s Coins",
                                     v.coin_balance))

    #verify acceptability
    if not re.match(valid_username_regex, new_name):
        return render_template("settings_profile.html",
                           v=v,
                           error=tr("That isn't a valid username."))

    #verify availability
    name=new_name.replace('_',r'\_')

    x= g.db.query(User).options(
        lazyload('*')
        ).filter(
        or_(
            User.username.ilike(name),
            User.original_username.ilike(name)
            )
        ).first()

    if x and x.id != v.id:
        return render_template("settings_profile.html",
                           v=v,
                           error=tr("Username `%(name)s` is already in use.", name=new_name))

    #all reqs passed

    #check user avatar/banner for rename if needed
    if v.has_profile and v.profile_url.startswith(f"https://{app.config['S3_BUCKET']}/users/"):
        upload_from_url(f"uid/{v.base36id}/profile-{v.profile_nonce}.png", f"{v.profile_url}")
        v.profile_set_utc=int(time.time())
        g.db.add(v)
        g.db.commit()

    if v.has_banner and v.banner_url.startswith(f"https://{app.config['S3_BUCKET']}/users/"):
        upload_from_url(f"uid/{v.base36id}/banner-{v.banner_nonce}.png", f"{v.banner_url}")
        v.banner_set_utc=int(time.time())
        g.db.add(v)
        g.db.commit()


    #do name change and deduct coins

    # the 20 coins go in one conditional statement (helpers/coins.py): two requests can't spend them twice
    g.db.flush()
    if not coins_store.spend(g.db, v.id, 20):
        g.db.rollback()
        return render_template("settings_profile.html", v=v,
                               error=tr("Username changes cost 20 Coins, and you don't have enough."))
    g.db.refresh(v)
    # the 7-day wait again, now that this request holds the row (the spend above locks it until the commit): two
    # requests at once both passed the first check and both paid (bug hunt)
    if v.name_changed_utc > int(time.time()) - 60*60*24*7:
        g.db.rollback()
        return render_template("settings_profile.html", v=v, error=tr("You need to wait 7 days between name changes."))

    v.username=new_name
    apply_user_severity(v)
    v.name_changed_utc=int(time.time())

    set_nick(v, new_name)
    bot_store.rename(g.db, v.id, new_name)      # their own account's automated posting (helpers/bots.py SELF), if any

    g.db.add(v)
    g.db.commit()
    chat_devices.sync_name(g.db, v)             # outside chat apps show the new @handle

    return render_template("settings_profile.html",
                       v=v,
                       msg=tr("Username changed successfully. 20 Coins have been deducted from your balance."))



@app.route("/admin/display_name/clear", methods=["POST"])
@admin_level_required(3)
@validate_formkey
def admin_clear_display_name(v):
    """Take an abusive or impersonating display name off an account (its @handle shows again). Only an account
    with a lower admin level than the admin's own: the same bar as banning (helpers/display_names.py)."""
    from ruqqus.helpers.alerts import notify

    target = get_user(request.form.get("username", ""), graceful=True)
    if target is None or target.is_deleted or v.admin_level <= (target.admin_level or 0):
        abort(404)
    if target.display_name:
        # the name that was removed goes in the staff note: it is gone from the account, and an admin may need it later
        action_log.record_quietly(g.db, "clear_display_name", v.id, target_user_id=target.id, role=action_log.ADMIN,
                                  reason=request.form.get("reason", ""), rule=request.form.get("rule"),
                                  staff_note=f"was: {target.display_name}", notice_sent=True)
        target.display_name = ""
        apply_user_severity(target)
        g.db.add(target)
        g.db.commit()
        notify(target, N_("An admin removed your display name because it broke the site's rules. "
                          "You can set a new one in Settings > Profile."))
    return redirect(target.permalink)


@app.route("/settings/all_shows_sensitive", methods=["POST"])
@auth_required
@validate_formkey
def settings_all_shows_sensitive(v):
    """The viewer's choice: show sensitive content in All and For You (helpers/sensitive.py). Off by default; the Child
    filter hides sensitive content whatever this says. The member's cached feeds are dropped so it shows at once."""
    locked = age_declaration.locked_sensitive(v, request.values.get("on") == "true")
    if locked:
        return jsonify({"error": msg(locked)}), 403
    v.all_shows_sensitive = request.values.get("on") == "true"
    g.db.add(v)
    g.db.commit()
    muting_rules.clear_cached_feeds(v)
    return jsonify({"message": tr("Saved.")})


@app.route("/settings/all_opt_out", methods=["POST"])
@auth_required
@validate_formkey
def settings_all_opt_out(v):
    """Keep your profile posts out of All (helpers/all_feed.py). Your anonymous posts and the guild copies of your posts
    stay in All. Your own cached feeds are dropped at once; other people's All catches up within its 15 minutes."""
    v.all_opt_out = request.values.get("on") == "true"
    g.db.add(v)
    g.db.commit()
    muting_rules.clear_cached_feeds(v)
    return jsonify({"message": tr("Your posts are kept out of All.") if v.all_opt_out else tr("Your posts can appear in All.")})


@app.route("/settings/sensitive_account", methods=["POST"])
@auth_required
@validate_formkey
def settings_sensitive_account(v):
    """Mark your own account sensitive (or not): its posts leave All and For You by default and new ones are blurred.
    Refused while an admin has forced it on (sensitive_forced)."""
    wanted = request.values.get("on") == "true"
    refused = sensitive_rules.owner_refusal(v.sensitive_forced, wanted)
    if refused:
        return jsonify({"error": refused}), 403
    locked = age_declaration.locked_sensitive(v, wanted)         # a child-locked account posts no Sensitive content
    if locked:
        return jsonify({"error": msg(locked)}), 403
    v.is_sensitive = wanted
    g.db.add(v)
    g.db.commit()
    muting_rules.clear_cached_feeds(v)
    return jsonify({"message": tr("Your account is marked sensitive.") if wanted else tr("Your account is no longer marked sensitive.")})


@app.route("/admin/sensitive/user", methods=["POST"])
@admin_level_required(3)
@validate_formkey
def admin_sensitive_user(v):
    """Force an account sensitive (the member can't switch it off), or unlock it (the member decides again). Only an
    account below the admin's own level, the bar for banning. The account is told."""
    from ruqqus.helpers.alerts import notify

    target = get_user(request.form.get("username", ""), graceful=True)
    if target is None or target.is_deleted or v.admin_level <= (target.admin_level or 0):
        abort(404)
    force = request.form.get("force") == "1"
    target.sensitive_forced = force
    if force:
        target.is_sensitive = True
    g.db.add(target)
    action_log.record_quietly(g.db, "force_sensitive_user", v.id, target_user_id=target.id, role=action_log.ADMIN,
                              reason=request.form.get("reason", ""), staff_note="forced on" if force else "unlocked",
                              notice_sent=True)
    g.db.commit()
    notify(target, N_("An admin marked your account sensitive: your posts are left out of All and For You "
                      "unless someone chooses to see sensitive content, and your new posts are blurred. "
                      "Only an admin can change this.") if force else
           N_("An admin unlocked the sensitive setting on your account. You can change it in "
              "Settings > Content."))
    return redirect(target.permalink)


@app.route("/admin/sensitive/post", methods=["POST"])
@admin_level_required(3)
@validate_formkey
def admin_sensitive_post(v):
    """Force a post sensitive (its author and the guild's guildmasters can't switch it off), or unlock it (they decide again;
    the mark stays until they do). It is the same post in every guild it was forwarded to, so the mark goes on all of it and the
    lock on the original, where a copy made later reads it (`Submission.sensitive_lock`). The author is told, by a link that
    names nobody, which holds for an anonymous post too."""
    from ruqqus.helpers.alerts import notify
    from ruqqus.helpers.base36 import base36decode

    try:
        post = g.db.query(Submission).filter_by(id=base36decode(request.form.get("post", ""))).first()
    except ValueError:
        post = None
    if post is None:
        abort(404)
    primary = g.db.query(Submission).filter_by(id=post.repost_id).first() if post.repost_id else post
    if primary is None:
        abort(404)
    force = request.form.get("force") == "1"
    primary.sensitive_forced = force
    g.db.add(primary)
    if force:
        for row in g.db.query(Submission).filter((Submission.id == primary.id) | (Submission.repost_id == primary.id)).all():
            row.is_sensitive = True
            g.db.add(row)
    action_log.record_quietly(g.db, "force_sensitive_post", v.id, post_id=primary.id, role=action_log.ADMIN,
                              reason=request.form.get("reason", ""), staff_note="forced on" if force else "unlocked",
                              notice_sent=True)
    g.db.commit()
    author = primary.author
    if author and not author.is_deleted:
        notify(author, N_("An admin marked [your post](%(link)s) sensitive: it is blurred until someone clicks to reveal it, and "
                          "left out of All and For You unless they choose to see sensitive content. Only an admin can "
                          "change this.") if force else
               N_("An admin unlocked the sensitive setting of [your post](%(link)s). You can change it again."),
               link=primary.permalink)
    return redirect(post.permalink)


@app.route("/admin/sensitive/guild", methods=["POST"])
@admin_level_required(3)
@validate_formkey
def admin_sensitive_guild(v):
    """Force a guild sensitive (its guildmasters can't switch it off), or unlock it. Written to the guild's public mod
    log; its guildmasters are told."""
    from ruqqus.helpers.alerts import notify
    from ruqqus.helpers.get import get_guild

    board = get_guild(request.form.get("guild", ""), graceful=True)
    if board is None:
        abort(404)
    force = request.form.get("force") == "1"
    board.sensitive_forced = force
    if force:
        board.is_sensitive = True
    g.db.add(board)
    g.db.add(ModAction(kind="update_settings", user_id=v.id, board_id=board.id,
                       note="sensitive content forced on by the site's admins" if force
                       else "sensitive content unlocked by the site's admins"))
    g.db.commit()
    message = (N_("An admin marked +%(guild)s sensitive: its posts are left out of All and For You unless someone "
                  "chooses to see sensitive content. Only an admin can change this.") if force else
               N_("An admin unlocked the sensitive setting of +%(guild)s. Its guildmasters can change it again."))
    for guildmaster in board.mods:
        if not guildmaster.is_deleted:
            notify(guildmaster, message, guild=board.name)
    return redirect(board.permalink)


@app.route("/settings/badges", methods=["POST"])
@auth_required
@validate_formkey
def settings_badge_recheck(v):

    v.refresh_selfset_badges()

    return jsonify({"message":tr("Badges Refreshed")})


@app.route("/settings/export", methods=["POST"])
@limiter.limit("3/hour")
@auth_required
@not_qr_limited
@validate_formkey
def settings_export(v):
    """Download your data: one JSON file of what the site keeps about this account (helpers/data_export.py, audit
    I-06). The password is asked again: the file holds your sign-in addresses and private lists."""
    if not v.verifyPass(request.form.get("password", "")):
        return render_template("settings_security.html", v=v, error=tr("Incorrect password")), 401
    from ruqqus.helpers import data_export
    body = data_export.as_json(data_export.build(g.db, v.id))
    resp = make_response(body)
    resp.headers["Content-Type"] = "application/json; charset=utf-8"
    resp.headers["Content-Disposition"] = f'attachment; filename="{v.username}-data.json"'
    resp.headers["Cache-Control"] = "no-store"
    return resp
