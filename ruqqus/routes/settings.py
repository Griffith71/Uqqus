from flask import *
from sqlalchemy import func
import time
import gevent
import mistletoe
import re
from ruqqus.helpers.word_filter_store import apply_user_severity
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
from ruqqus.helpers import account_sessions, mfa_backup, mfa_backup_store, sign_in_history
from ruqqus.helpers.regions import COUNTRY_NAMES
from ruqqus.helpers import totp as totp_rules
from ruqqus.helpers import post_feedback as post_feedback_rules
from ruqqus.helpers import muting as muting_rules
from ruqqus.helpers import sensitive as sensitive_rules
from ruqqus.mail import *
from .front import frontlist
from ruqqus.__main__ import app, cache, limiter, r
from flask import session as flask_session
from markupsafe import escape        # flask.escape went with Flask 3: every message of the security form crashed


valid_username_regex = re.compile(r"^[a-zA-Z0-9_]{3,25}$")
valid_password_regex = re.compile(r"^.{8,100}$")


@app.route("/settings/profile", methods=["POST"])
@app.route("/api/vue/settings/profile", methods=["POST"])
@auth_required
@api()
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
            # the Child filter is a lock: lowering it takes the account password
            if current_level >= 2 and new_level < 2 and not v.verifyPass(request.values.get("password", "")):
                return jsonify({"error": "Enter your account password to turn the Child filter down."}), 403
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
        # the display name: free text, changed at any time, never an address (helpers/display_names.py)
        from ruqqus.helpers import display_name_store, display_names
        try:
            new_name = display_names.clean(request.values.get("display_name", ""))
        except display_names.Refused as refused:
            message = str(refused)      # the except name is gone by the time the lambdas run
            return {"html": lambda: render_template("settings_profile.html", v=v, error=message),
                    "api": lambda: (jsonify({"error": message}), 400)}

        if new_name:
            # nobody can wear another account's @handle as a name (ignoring case, spaces, dots, dashes, underscores)
            if display_name_store.takes_a_handle(g.db, v.id, new_name):
                message = "That is another account's username, so it can't be your display name."
                return {"html": lambda: render_template("settings_profile.html", v=v, error=message),
                        "api": lambda: (jsonify({"error": message}), 400)}
            reserved = None if v.admin_level >= 3 else display_names.reserved_refusal(
                new_name, (app.config["SITE_NAME"], "ruqqus", "uqqus"))
            if reserved:
                return {"html": lambda: render_template("settings_profile.html", v=v, error=reserved),
                        "api": lambda: (jsonify({"error": reserved}), 400)}

        if new_name == (v.display_name or ""):
            return {"html": lambda: render_template("settings_profile.html", v=v, error="You didn't change anything"),
                    "api": lambda: (jsonify({"error": "You didn't change anything"}), 400)}

        v.display_name = new_name
        apply_user_severity(v)
        g.db.add(v)
        done = "Your display name has been updated." if new_name else "Your display name has been removed."
        return {"html": lambda: render_template("settings_profile.html", v=v, msg=done),
                "api": lambda: jsonify({"message": done})}

    if request.values.get("details") is not None:
        # job, location and website on the profile header (helpers/profile_details.py)
        from markupsafe import escape
        from ruqqus.helpers import profile_details
        try:
            job = profile_details.clean_text(request.values.get("profile_job", ""), "Job")
            location = profile_details.clean_text(request.values.get("profile_location", ""), "Location")
            website = profile_details.clean_website(request.values.get("profile_website", ""))
        except profile_details.Refused as refused:
            message = str(refused)      # the except name is gone by the time the lambdas run
            return {"html": lambda: render_template("settings_profile.html", v=v, error=message),
                    "api": lambda: (jsonify({"error": message}), 400)}

        if website:
            # the banned-domain list, as for a link in a bio
            bans = filter_comment_html(f'<a href="{escape(website)}">x</a>')
            if bans:
                reason = f"{bans[0].domain} can't be linked from a profile."
                if bans[0].reason:
                    reason += f" {bans[0].reason_text}"
                return {"html": lambda: render_template("settings_profile.html", v=v, error=reason),
                        "api": lambda: (jsonify({"error": reason}), 400)}

        v.profile_job, v.profile_location, v.profile_website = job, location, website
        apply_user_severity(v)
        g.db.add(v)
        return {"html": lambda: render_template("settings_profile.html", v=v, msg="Your profile details have been updated."),
                "api": lambda: jsonify({"message": "Your profile details have been updated."})}

    if request.values.get("bio") is not None:
        bio = request.values.get("bio")[0:256]

        bio=preprocess(bio)

        if bio == v.bio:
            return {"html":lambda:render_template("settings_profile.html",
                                   v=v,
                                   error="You didn't change anything"),
		    "api":lambda:jsonify({"error":"You didn't change anything"})
		   }


        with CustomRenderer() as renderer:
            bio_html = renderer.render(mistletoe.Document(bio))
        # the bio's own narrow list of tags (links and text styles, no headings, tables or pictures): it was written
        # for bios and never used (carried from BACKEND_AUDIT_FINDINGS); an older bio keeps its HTML until it is edited
        bio_html = sanitize(bio_html, bio=True, linkgen=True)

        # Run safety filter
        bans = filter_comment_html(bio_html)

        if bans:
            ban = bans[0]
            reason = f"Remove the {ban.domain} link from your bio and try again."
            if ban.reason:
                reason += f" {ban.reason_text}"
                
            #auto ban for digitally malicious content
            if any([x.reason==4 for x in bans]):
                v.ban(days=30, reason="Digitally malicious content is not allowed.")
            return jsonify({"error": reason}), 401

        v.bio = bio
        v.bio_html=bio_html
        apply_user_severity(v)
        g.db.add(v)
        
        #seo profile spam
        if int(time.time())-v.created_utc < 60*60*24 and not v.post_count and not v.comment_count and BeautifulSoup(bio_html).find('a'):
            v.ban(reason="seo spam")
        
        
        return {"html":lambda:render_template("settings_profile.html",
                               v=v,
                               msg="Your bio has been updated."),
		"api":lambda:jsonify({"message":"Your bio has been updated."})}

    if request.values.get("filters") is not None:

        filters=request.values.get("filters")[0:1000].lstrip().rstrip()

        if filters==v.custom_filter_list:
            return {"html":lambda:render_template("settings_profile.html",
                                   v=v,
                                   error="You didn't change anything"),
		    "api":lambda:jsonify({"error":"You didn't change anything"})
		   }

        v.custom_filter_list=filters
        g.db.add(v)
        return {"html":lambda:render_template("settings_profile.html",
                               v=v,
                               msg="Your custom filters have been updated."),
		"api":lambda:jsonify({"message":"Your custom filters have been updated"})}



    x = request.values.get("title_id", None)
    if x:
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
                return jsonify({"error": f"You don't meet the requirements for title `{title.text}`."}), 403
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

        return jsonify({"message": "Your settings have been updated."})

    else:
        return jsonify({"error": "You didn't change anything."}), 400


def _backup_codes_page(v, codes, msg):
    """The Security page with a set of backup codes in it, this once: not cached, never in an address."""
    page = make_response(render_template(
        "settings_security.html", v=v, mfa_secret=None, error=None, msg=msg,
        new_backup_codes=[mfa_backup.display(c) for c in codes], **mfa_backup_store.page_context(g.db, v),
        **sign_in_history.page_context(g.db, v, COUNTRY_NAMES)))
    page.headers["Cache-Control"] = "no-store"
    return page


@app.route("/settings/security", methods=["POST"])
@auth_required
@validate_formkey
def settings_security_post(v):

    if request.form.get("new_password"):
        if request.form.get(
                "new_password") != request.form.get("cnf_password"):
            return redirect("/settings/security?error=" +
                            escape("Passwords do not match."))

        if not re.match(valid_password_regex, request.form.get("new_password")):
            #print(f"signup fail - {username } - invalid password")
            return redirect("/settings/security?error=" + 
                            escape("Password must be between 8 and 100 characters."))

        if not v.verifyPass(request.form.get("old_password")):
            return render_template(
                "settings_security.html", v=v, error="Incorrect password")

        v.passhash = v.hash_password(request.form.get("new_password"))
        account_sessions.end_other_sessions(v, flask_session)       # whoever held the old password is signed out
        chat_devices.sign_out(g.db, v.id, app.config["SITE_NAME"])  # and so is any chat app signed in with it

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
                        escape("Your password has been changed. " + account_sessions.OTHERS_SIGNED_OUT))

    if request.form.get("new_email"):

        if not v.verifyPass(request.form.get('password')):
            return redirect("/settings/security?error=" +
                            escape("Invalid password."))

        new_email = request.form.get("new_email","").lstrip().rstrip()
        #counteract gmail username+2 and extra period tricks - convert submitted email to actual inbox
        if new_email.endswith("@gmail.com"):
            gmail_username=new_email.split('@')[0]
            gmail_username=gmail_username.split("+")[0]
            gmail_username=gmail_username.replace('.','')
            new_email=f"{gmail_username}@gmail.com"
        if new_email == v.email:
            return redirect("/settings/security?error=" +
                            escape("That email is already yours!"))

        # check to see if email is in use
        existing = g.db.query(User).filter(User.id != v.id,
                                           func.lower(User.email) == new_email.lower()).first()
        if existing:
            return redirect("/settings/security?error=" +
                            escape("That email address is already in use."))

        url = f"https://{app.config['SERVER_NAME']}/activate"

        now = int(time.time())

        token = generate_hash(f"{new_email}+{v.id}+{now}")
        params = f"?email={quote(new_email)}&id={v.id}&time={now}&token={token}"

        link = url + params

        send_mail(to_address=new_email,
                  subject="Verify your email address.",
                  html=render_template("email/email_change.html",
                                       action_url=link,
                                       v=v)
                  )

        return redirect("/settings/security?msg=" + escape(
            "Check your email and click the verification link to complete the email change."))

    if request.form.get("2fa_token", ""):

        if not v.verifyPass(request.form.get('password')):
            return redirect("/settings/security?error=" +
                            escape("Invalid password or token."))

        # already on: the secret is not replaced by whoever has the password and a session (that would swap the
        # account's authenticator for theirs without ever needing the old one); turn it off first, with a code
        if v.mfa_secret:
            return redirect("/settings/security?error=" +
                            escape("Two-factor authentication is already on. Turn it off first to set up a new device."))

        secret = request.form.get("2fa_secret") or ""
        if not re.fullmatch(r"[A-Z2-7]{16,64}", secret):
            return redirect("/settings/security?error=" + escape("Invalid password or token."))
        step = totp_rules.matched_step(secret, request.form.get("2fa_token"))
        if step is None:
            return redirect("/settings/security?error=" +
                            escape("Invalid password or token."))
        totp_rules.claim(r, v.id, step)               # that code is spent: it can't switch two-factor off a moment later

        v.mfa_secret = secret
        account_sessions.end_other_sessions(v, flask_session)
        g.db.add(v)

        # the account's first backup codes, shown once, here
        codes = mfa_backup.generate()
        mfa_backup_store.replace(g.db, v.id, codes, int(time.time()))
        return _backup_codes_page(v, codes, "Two-factor authentication is on. " + account_sessions.OTHERS_SIGNED_OUT)

    if request.form.get("new_backup_codes", ""):

        if not v.mfa_secret:
            return redirect("/settings/security?error=" + escape("Turn on two-factor authentication first."))
        if not v.verifyPass(request.form.get("password")) or not v.accept_2fa(request.form.get("new_backup_codes")):
            return redirect("/settings/security?error=" + escape("Invalid password or code."))

        codes = mfa_backup.generate()                 # every earlier code, used or not, stops working
        mfa_backup_store.replace(g.db, v.id, codes, int(time.time()))
        return _backup_codes_page(v, codes, "Your new backup codes are below. The old ones no longer work.")

    if request.form.get("2fa_remove", ""):

        if not v.verifyPass(request.form.get('password')):
            return redirect("/settings/security?error=" +
                            escape("Invalid password or token."))

        token = request.form.get("2fa_remove") or ""

        # an authenticator code (accepted once), a backup code (spent), or the older recovery code, which only an
        # account with no backup codes still has (helpers/mfa_backup.py)
        if not (v.accept_2fa(token)
                or mfa_backup_store.spend(g.db, v.id, token, int(time.time()))
                or (mfa_backup.legacy_code_allowed(mfa_backup_store.has_codes(g.db, v.id))
                    and safe_compare(v.mfa_removal_code, token.lower().replace(' ', '')))):
            return redirect("/settings/security?error=" +
                            escape("Invalid password or token."))

        v.mfa_secret = None
        mfa_backup_store.delete_all(g.db, v.id)       # no second factor, no codes
        account_sessions.end_other_sessions(v, flask_session)
        g.db.add(v)

        return redirect("/settings/security?msg=" +
                        escape("Two-factor authentication disabled. " + account_sessions.OTHERS_SIGNED_OUT))

    if request.form.get("signin_alerts_form", ""):

        # switching a security notice off is a security change, so it asks for the password like the others
        if not v.verifyPass(request.form.get("password")):
            return redirect("/settings/security?error=" + escape("Invalid password."))

        v.signin_alerts = bool(request.form.get("signin_alerts"))
        g.db.add(v)

        return redirect("/settings/security?msg=" + escape(
            "We'll email you about sign-ins from somewhere new." if v.signin_alerts else "Sign-in emails are off."))

    # a form with none of the fields above (audit CR-04: it fell through and answered 500)
    return redirect("/settings/security?error=" + escape("Nothing to change."))


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
@validate_formkey
def settings_log_out_others(v):

    submitted_password = request.form.get("password", "")

    if not v.verifyPass(submitted_password):
        return render_template("settings_security.html",
                               v=v, error="Incorrect Password"), 401

    account_sessions.end_other_sessions(v, flask_session)
    chat_devices.sign_out(g.db, v.id, app.config["SITE_NAME"])     # chat apps signed in with the account too

    g.db.add(v)

    return render_template("settings_security.html", v=v,
                           msg="All other devices have been logged out")


@app.route("/settings/images/profile", methods=["POST"])
@is_not_banned
@validate_formkey
def settings_images_profile(v):
    if v.can_upload_avatar:
        upload = request.files.get("profile")
        if not upload or not upload.filename:                          # audit CR-04: no file answered 500
            return render_template("settings_profile.html", v=v, error="Choose a picture first.")
        v.set_profile(upload)

        # anti csam (it removed `board`'s picture, a name that doesn't exist here, so a match failed to remove it)
        new_thread = gevent.spawn(check_csam_url,
                                      v.profile_url,
                                            v,
                                            lambda: v.del_profile()
                                            )
        new_thread.start()

        return render_template("settings_profile.html",
                               v=v, msg="Profile picture successfully updated.")

    return render_template("settings_profile.html", v=v,
                           msg="Avatars require 300 reputation.")


@app.route("/settings/images/banner", methods=["POST"])
@is_not_banned
@validate_formkey
def settings_images_banner(v):
    if v.can_upload_banner:
        upload = request.files.get("banner")
        if not upload or not upload.filename:                          # audit CR-04: no file answered 500
            return render_template("settings_profile.html", v=v, error="Choose a picture first.")
        v.set_banner(upload)

        # anti csam (it removed `board`'s picture, a name that doesn't exist here, so a match failed to remove it)
        new_thread = gevent.spawn(check_csam_url,
                                      v.banner_url,
                                            v,
                                            lambda: v.del_banner()
                                            )
        new_thread.start()

        return render_template("settings_profile.html",
                               v=v, msg="Banner successfully updated.")

    return render_template("settings_profile.html", v=v,
                           msg="Banners require 500 reputation.")


@app.route("/settings/delete/profile", methods=["POST"])
@auth_required
@validate_formkey
def settings_delete_profile(v):

    v.del_profile()

    return render_template("settings_profile.html", v=v,
                           msg="Profile picture successfully removed.")


@app.route("/settings/new_feedkey", methods=["POST"])
@auth_required
@validate_formkey
def settings_new_feedkey(v):

    v.feed_nonce += 1
    g.db.add(v)

    return render_template("settings_profile.html", v=v,
                           msg="Your new custom RSS Feed Token has been generated.")


@app.route("/settings/delete/banner", methods=["POST"])
@auth_required
@validate_formkey
def settings_delete_banner(v):

    v.del_banner()

    return render_template("settings_profile.html", v=v,
                           msg="Banner successfully removed.")


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


@app.route("/settings/delete_account", methods=["POST"])
@is_not_banned
@no_negative_balance("html")
@validate_formkey
def delete_account(v):

    if not v.verifyPass(request.form.get("password", "")) or (
            v.mfa_secret and not (v.accept_2fa(request.form.get("twofactor", ""))
                                  or mfa_backup_store.spend(g.db, v.id, request.form.get("twofactor", ""), int(time.time())))):
        return render_template("settings_security.html", v=v,
                               error="Invalid password or token" if v.mfa_secret else "Invalid password")


    remove_user(v)

    v.discord_id=None
    v.is_deleted = True
    v.login_nonce += 1
    v.delete_reason = request.form.get("delete_reason", "")
    v.patreon_id=None
    v.patreon_pledge_cents=0
    v.del_banner()
    v.del_profile()
    g.db.add(v)

    mods = g.db.query(ModRelationship).filter_by(user_id=v.id).all()
    for mod in mods:
        g.db.delete(mod)

    bans = g.db.query(BanRelationship).filter_by(user_id=v.id).all()
    for ban in bans:
        g.db.delete(ban)

    contribs = g.db.query(ContributorRelationship).filter_by(
        user_id=v.id).all()
    for contrib in contribs:
        g.db.delete(contrib)

    # other people's blocks of this account stay: they are theirs (and matter if an admin restores the account)

    # everything else the account had running or kept (helpers/account_cleanup.py, audit I-05): circles,
    # co-authorships, stories, channels, shared drafts, answers, bookmarks, drafts, linked storage, the chat key,
    # sign-in addresses, profile details
    from ruqqus.helpers import account_cleanup
    account_cleanup.close_account(g.db, v.id, int(time.time()))
    _leave_chat_groups(v)
    # every chat device signs out, outside apps and the site's own browsers, and the chat name says it is gone
    chat_devices.sign_out(g.db, v.id, app.config["SITE_NAME"], apps_only=False)
    chat_devices.sync_name(g.db, v)

    for b in v.boards_modded:
        if b.mods_count == 0 and not b.is_circle:
            b.is_private = False
            b.restricted_forwarding = False
            b.all_opt_out = False
            g.db.add(b)

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

    g.db.commit()

    return redirect('/')


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

    done = blocklist.block_account(g.db, v, user)
    if done != "blocked":
        message, status = blocklist.REFUSALS[done]
        return jsonify({"error": message.format(name=user.username if user else "")}), status

    _forget_block_caches(v)

    return jsonify({"message": f"@{user.username} blocked."})


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

    return jsonify({"message": f"@{user.username} unblocked."})


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
        return jsonify({"error": "That guild doesn't exist."}), 404

    if v.has_blocked_guild(board):
        return jsonify({"error": f"You have already muted +{board.name}."}), 409

    new_block = BoardBlock(user_id=v.id,
                           board_id=board.id,
                           created_utc=int(time.time())
                           )
    g.db.add(new_block)
    g.db.commit()

    muting_rules.clear_guild_mute_caches(v)

    joined = g.db.query(Subscription.id).filter_by(user_id=v.id, board_id=board.id, is_active=True).first()
    if joined:
        return jsonify({"message": f"+{board.name} muted. You are still a member, but its posts stay out of All, For You and Following until you unmute it."})
    return jsonify({"message": f"+{board.name} muted. Its posts stay out of All, For You and Following."})


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

    return jsonify({"message": f"+{board.name} unmuted."})


@app.route("/settings/apps", methods=["GET"])
@auth_required
def settings_apps(v):

    return render_template("settings_apps.html", v=v)


@app.route("/settings/remove_discord", methods=["POST"])
@auth_required
@validate_formkey
def settings_remove_discord(v):

    if v.admin_level>1:
        return render_template("settings_filters.html", v=v, error="Admins can't disconnect Discord.")

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
        return jsonify({"error": "Choose how scores read."}), 400
    v.score_display = int(choice)
    g.db.add(v)
    g.db.commit()
    return jsonify({"message": "Saved."})


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

    return jsonify({"message": "Your For You feed has been reset."})


@app.route("/settings/purchase_history", methods=["GET"])
@auth_required
def settings_purchase_history(v):

    return render_template("settings_txnlist.html", v=v)

@app.route("/settings/name_change", methods=["POST"])
@auth_required
@validate_formkey
def settings_name_change(v):

    if v.admin_level:
        return render_template("settings_profile.html",
                           v=v,
                           error="Admins can't change their name.")


    new_name = (request.form.get("name") or "").strip()                   # audit CR-04: no name answered 500

    #make sure name is different
    if new_name==v.username:
        return render_template("settings_profile.html",
                           v=v,
                           error="You didn't change anything")

    #can't change name on verified ID accounts
    if v.real_id:
        return render_template("settings_profile.html",
                           v=v,
                           error="Your ID is verified so you can't change your username.")

    #7 day cooldown
    if v.name_changed_utc > int(time.time()) - 60*60*24*7:
        return render_template("settings_profile.html",
                           v=v,
                           error=f"You changed your name {(int(time.time()) - v.name_changed_utc)//(60*60*24)} days ago. You need to wait 7 days between name changes.")

    #costs 3 coins
    if v.coin_balance < 20:
        return render_template("settings_profile.html",
                           v=v,
                           error=f"Username changes cost 20 Coins. You only have a balance of {v.coin_balance} Coins")

    #verify acceptability
    if not re.match(valid_username_regex, new_name):
        return render_template("settings_profile.html",
                           v=v,
                           error=f"That isn't a valid username.")

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
                           error=f"Username `{new_name}` is already in use.")

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
                               error="Username changes cost 20 Coins, and you don't have enough.")
    g.db.refresh(v)

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
                       msg=f"Username changed successfully. 20 Coins have been deducted from your balance.")



@app.route("/admin/display_name/clear", methods=["POST"])
@admin_level_required(3)
@validate_formkey
def admin_clear_display_name(v):
    """Take an abusive or impersonating display name off an account (its @handle shows again). Only an account
    with a lower admin level than the admin's own: the same bar as banning (helpers/display_names.py)."""
    from ruqqus.helpers.alerts import send_notification

    target = get_user(request.form.get("username", ""), graceful=True)
    if target is None or target.is_deleted or v.admin_level <= (target.admin_level or 0):
        abort(404)
    if target.display_name:
        target.display_name = ""
        apply_user_severity(target)
        g.db.add(target)
        g.db.commit()
        send_notification(target, "An admin removed your display name because it broke the site's rules. "
                                  "You can set a new one in Settings > Profile.")
    return redirect(target.permalink)


@app.route("/settings/all_shows_sensitive", methods=["POST"])
@auth_required
@validate_formkey
def settings_all_shows_sensitive(v):
    """The viewer's choice: show sensitive content in All and For You (helpers/sensitive.py). Off by default; the Child
    filter hides sensitive content whatever this says. The member's cached feeds are dropped so it shows at once."""
    v.all_shows_sensitive = request.values.get("on") == "true"
    g.db.add(v)
    g.db.commit()
    muting_rules.clear_cached_feeds(v)
    return jsonify({"message": "Saved."})


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
    v.is_sensitive = wanted
    g.db.add(v)
    g.db.commit()
    muting_rules.clear_cached_feeds(v)
    return jsonify({"message": "Your account is marked sensitive." if wanted else "Your account is no longer marked sensitive."})


@app.route("/admin/sensitive/user", methods=["POST"])
@admin_level_required(3)
@validate_formkey
def admin_sensitive_user(v):
    """Force an account sensitive (the member can't switch it off), or unlock it (the member decides again). Only an
    account below the admin's own level, the bar for banning. The account is told."""
    from ruqqus.helpers.alerts import send_notification

    target = get_user(request.form.get("username", ""), graceful=True)
    if target is None or target.is_deleted or v.admin_level <= (target.admin_level or 0):
        abort(404)
    force = request.form.get("force") == "1"
    target.sensitive_forced = force
    if force:
        target.is_sensitive = True
    g.db.add(target)
    g.db.commit()
    send_notification(target, "An admin marked your account sensitive: your posts are left out of All and For You "
                              "unless someone chooses to see sensitive content, and your new posts are blurred. "
                              "Only an admin can change this." if force else
                              "An admin unlocked the sensitive setting on your account. You can change it in "
                              "Settings > Content.")
    return redirect(target.permalink)


@app.route("/admin/sensitive/guild", methods=["POST"])
@admin_level_required(3)
@validate_formkey
def admin_sensitive_guild(v):
    """Force a guild sensitive (its guildmasters can't switch it off), or unlock it. Written to the guild's public mod
    log; its guildmasters are told."""
    from ruqqus.helpers.alerts import send_notification
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
    message = (f"An admin marked +{board.name} sensitive: its posts are left out of All and For You unless someone "
               "chooses to see sensitive content. Only an admin can change this." if force else
               f"An admin unlocked the sensitive setting of +{board.name}. Its guildmasters can change it again.")
    for guildmaster in board.mods:
        if not guildmaster.is_deleted:
            send_notification(guildmaster, message)
    return redirect(board.permalink)


@app.route("/settings/badges", methods=["POST"])
@auth_required
@validate_formkey
def settings_badge_recheck(v):

    v.refresh_selfset_badges()

    return jsonify({"message":"Badges Refreshed"})


@app.route("/settings/export", methods=["POST"])
@limiter.limit("3/hour")
@auth_required
@validate_formkey
def settings_export(v):
    """Download your data: one JSON file of what the site keeps about this account (helpers/data_export.py, audit
    I-06). The password is asked again: the file holds your sign-in addresses and private lists."""
    if not v.verifyPass(request.form.get("password", "")):
        return render_template("settings_security.html", v=v, error="Incorrect password"), 401
    from ruqqus.helpers import data_export
    body = data_export.as_json(data_export.build(g.db, v.id))
    resp = make_response(body)
    resp.headers["Content-Type"] = "application/json; charset=utf-8"
    resp.headers["Content-Disposition"] = f'attachment; filename="{v.username}-data.json"'
    resp.headers["Cache-Control"] = "no-store"
    return resp
