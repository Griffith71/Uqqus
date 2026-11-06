"""Settings > Children: an age-confirmed adult makes and manages child sub-accounts (helpers/child_accounts.py has the rules, helpers/child_store.py the SQL,
helpers/child_activity.py what a parent may see, CLAUDE.md "Age declaration and the child lock" the whole design). Named *_pages so the star imports never
replace a helper.

A parent only ever reaches ONE of their own children, by name: `_child_or_404` asks `child_store.child_of`, which lists the signed-in parent's links and
nobody else's, so another account's name is a 404 here whatever it is. Everything here is a POST with the form key except the two pages; making an account
and answering a request are held for a limited QR sign-in (`@not_qr_limited`: they act on other people's accounts and outlast a log out), and so is the
password a parent sets for a child. Deleting an account, setting its password and downloading its data ask the parent's own password again."""
import calendar
import time
from urllib.parse import quote

from flask import abort, g, jsonify, make_response, redirect, render_template, request
from sqlalchemy import text

from ruqqus.classes import User
from ruqqus.helpers import account_sessions, age_declaration, child_accounts as rules, child_activity, child_store, chat_signouts, circle_store, creator_fund_store, data_export, mfa_backup_store, sign_in_history
from ruqqus.helpers.alerts import notify
from ruqqus.helpers.get import get_user
from ruqqus.helpers.i18n_runtime import N_, msg, tr
from ruqqus.helpers.regions import COUNTRY_NAMES, resolve_region
from ruqqus.helpers.word_filter_store import apply_user_severity
from ruqqus.helpers.wrappers import auth_required, is_not_banned, not_qr_limited, not_quarantined, validate_formkey
from ruqqus.routes.login import valid_password_regex, valid_username_regex
from ruqqus.__main__ import app, limiter

# notices, stored in English and drawn in each reader's language (helpers/alerts.py notify)
WELCOME = N_("Your parent or guardian made this account for you. They can see what you do on the site (your posts, comments, votes, bookmarks, follows, guilds, "
             "who you chat with, that your account was reported and your recent sign-ins; the site never shows them what your chats say) and they choose which features "
             "are on. You can see which in [Settings > Content](/settings/content). They chose your password: change it in [Settings > Security](/settings/security) "
             "so that only you know it, because anyone who knows it can sign in as you and read your chats.")
ASKED_PRIVATE = N_("@%(name)s asked you to turn Private posting off for their account. You can answer in [Settings > Children](/settings/children).")
ASKED_RELEASE = N_("@%(name)s says they are 18 or over and asked you to release their account. You can answer in [Settings > Children](/settings/children).")
PRIVATE_ALLOWED = N_("Your parent or guardian turned Private posting off for your account.")
PRIVATE_DENIED = N_("Your parent or guardian wants Private posting to stay on.")
RELEASED = N_("Your account is your own now. It no longer has the child lock and your parent or guardian can no longer see it. They chose your password: change it in "
              "[Settings > Security](/settings/security), or they can still sign in as you.")
RELEASE_DENIED = N_("Your parent or guardian didn't agree to release your account.")
STOPPED = N_("Your parent or guardian stopped managing your account. It keeps the child lock until you confirm your age in Settings > Content. They chose your password: "
             "change it in [Settings > Security](/settings/security), or they can still sign in as you.")
NEW_PASSWORD = N_("Your parent or guardian set a new password for your account and you were signed out. Sign in with the new password, then change it in "
                  "[Settings > Security](/settings/security) so that only you know it.")
RELEASED_FOR_PARENT = N_("@%(name)s's age was confirmed as 18 or over, so their account is their own now and you can no longer see it.")

# (number, a moment in that month): the page writes each month's name in the reader's language from the moment (`date_text`)
MONTHS = tuple((number, calendar.timegm((2001, number, 15, 12, 0, 0))) for number in range(1, 13))


def _now():
    return int(time.time())


def _say(message, country=None):
    """A marked message in the reader's language, with the numbers these messages carry filled in (`msg` alone doesn't format)."""
    num = rules.MAX_CHILDREN if message == rules.TOO_MANY else rules.PER_WINDOW if message == rules.RECENT else rules.PER_DAY
    return msg(message) % {"num": num, "days": rules.WINDOW_DAYS, "age": rules.child_min_age(country)}


def _back(message=None, error=None):
    suffix = ("?msg=" + quote(message)) if message else ("?error=" + quote(error)) if error else ""
    return redirect("/settings/children" + suffix, code=303)


def _child_or_404(v, username):
    """One of the signed-in parent's own children, or a 404. A parent who is no longer fit to manage (suspended, under the child lock, a bot) is refused first:
    being a parent is checked when a child is made, and again every time one is touched."""
    if age_declaration.is_locked(v) or v.is_suspended or getattr(v, "is_bot", False):
        abort(403)
    child = child_store.child_of(g.db, v.id, username, _now())
    if child is None:
        abort(404)
    return child


def _child_row(child):
    row = g.db.query(User).filter_by(id=child["id"]).first()
    if row is None or row.is_deleted:
        abort(404)
    return row


def _money_off(child_id):
    """Money was switched off (or the account stopped being managed, which locks it): what was set up before stops by itself, since the routes refuse new spending
    but a job would renew it. Premium's auto-renew goes off, the creator fund is left, and every paid subscription the account is in or takes from stops renewing
    (the access already paid for stays). The renewal jobs also refuse such an account (child_store.money_locked): this is what the page says, that is the guard."""
    g.db.execute(text("UPDATE users SET premium_auto_renew = :no WHERE id = :u"), {"no": False, "u": child_id})
    creator_fund_store.set_opt_in(g.db, child_id, False)
    circle_store.stop_renewals_of(g.db, child_id)


def _sign_out_of_chat(child_id, reason):
    """Chat already set up keeps working until its devices are signed out (Synapse knows nothing of switches): every Matrix device of the child goes, apps included,
    through the durable queue (helpers/chat_signouts.py), worked once this is committed. The chat session can't be opened again without chat (`messaging` is chat),
    so this is final until chat is switched on. Called BEFORE the route's one commit: the switch (or the release) and its sign-out are written together or not at
    all, so a worker that dies in between can't leave chat off with its devices still signed in."""
    chat_signouts.queue(g.db, child_id, reason)


def _wrong_proof(v):
    """Is the parent's own proof wrong? Their password, and their authenticator or a backup code when they have two-factor on: what is secret or can't be
    undone here (a child's password, their data, their account) asks it as Settings does for the parent's own."""
    if not v.verifyPass(request.form.get("parent_password") or ""):
        return True
    if v.mfa_secret:
        code = request.form.get("twofactor", "")
        return not (v.accept_2fa(code) or mfa_backup_store.spend(g.db, v.id, code, _now()))
    return False


def parent_view(v):
    """What the page and its form need to know: the parent's children and the reason (if any) they may not make another."""
    region = age_declaration.current_region_locked(v)
    now = _now()
    children = child_store.children_of(g.db, v.id, now)
    made_today = child_store.made_since(g.db, v.id, now - 86400)
    made_recent = child_store.made_since(g.db, v.id, now - rules.WINDOW_DAYS * 86400)
    return children, rules.parent_refusal(v, region, len(children), made_today, made_recent), rules.eligible_parent(v, region)


@app.get("/settings/children")
@auth_required
@not_qr_limited
def settings_children(v):
    children, refusal, eligible = parent_view(v)
    year = time.gmtime().tm_year
    return render_template("settings_children.html", v=v, children=children, refusal=_say(refusal, request.headers.get("cf-ipcountry")) if refusal and not eligible else None,
                           full_message=_say(refusal) if refusal and eligible else None, eligible=eligible, controls=rules.CONTROLS,
                           text_of=rules.CONTROL_TEXT, max_children=rules.MAX_CHILDREN, months=MONTHS, this_year=year,
                           msg=request.args.get("msg"), error=request.args.get("error"))


@app.post("/settings/children/new")
@is_not_banned
@not_qr_limited
@limiter.limit("5/hour")
@not_quarantined("child")
@validate_formkey
def children_new(v):
    """Make a child account: a username, a password, and the month and year they were born (checked here and not kept). The parent agrees to the
    Terms for them. At most `MAX_CHILDREN` a parent and `PER_DAY` in a day, counted again under a lock so two requests can't both slip in."""
    child_store.lock_parent(g.db, v.id)          # two requests of one parent make one account at a time, and never while the parent is being closed
    g.db.refresh(v)                       # what the parent is NOW (closing an account takes the same lock), not what this request loaded
    children, refusal, _eligible = parent_view(v)
    if refusal:
        return _back(error=_say(refusal))
    form = request.form
    now = _now()
    stamp = time.gmtime(now)
    country = request.headers.get("cf-ipcountry")
    problem = rules.birth_refusal(form.get("birth_year"), form.get("birth_month"), stamp.tm_year, stamp.tm_mon, country)
    if problem:
        return _back(error=_say(problem, country))
    if form.get("terms") != "1":
        return _back(error=msg(rules.TERMS_NEEDED))
    username = (form.get("username") or "").strip()
    password = form.get("password") or ""
    if password != form.get("password_confirm"):
        return _back(error=tr("Passwords did not match. Please try again."))
    if not valid_username_regex.fullmatch(username):
        return _back(error=tr("Invalid username"))
    if not valid_password_regex.fullmatch(password):
        return _back(error=tr("Password must be between 8 and 100 characters."))
    if get_user(username, graceful=True):
        return _back(error=tr("An account with that username or email already exists."))

    child = User(username=username, original_username=username, password=password, email=None, created_utc=now, creation_ip=request.remote_addr,
                 tos_agreed_utc=now, creation_region=country, display_region=resolve_region(country), region_settled_utc=now, score_display=0,
                 # a child's lists start private (an adult's open to everyone): the parent sees them anyway, other people don't need to
                 following_visibility=2, followers_visibility=2, subscribers_visibility=2, subscriptions_visibility=2,
                 **age_declaration.sub_account_fields(now))
    apply_user_severity(child)
    g.db.add(child)
    g.db.flush()
    child_store.link(g.db, child.id, v.id, now)
    g.db.commit()
    notify(child, WELCOME)
    return _back(message=tr("The account @%(name)s is made. They sign in with the username and password you gave.", name=username))


@app.post("/settings/children/<username>/controls")
@auth_required
@not_qr_limited
@validate_formkey
def children_controls(username, v):
    """Switch one control for one child (`control`, `on` = "true"): chat, spaces, money, anonymous_guilds, posting, commenting, or `private_posting`
    (the parent decides it; any change answers the child's open request about it)."""
    child = _child_or_404(v, username)
    name = request.values.get("control")
    on = request.values.get("on") == "true"
    if name == "private_posting":
        row = _child_row(child)
        row.private_posting = on
        g.db.add(row)
        child_store.clear_ask(g.db, child["id"], "private")
    elif not child_store.set_control(g.db, child["id"], name, on, _now()):
        return jsonify({"error": msg(rules.UNKNOWN_CONTROL)}), 400
    if name == "money" and not on:
        _money_off(child["id"])
    if name == "chat" and not on:
        _sign_out_of_chat(child["id"], "child_chat_off")       # in the switch's own transaction
    g.db.commit()
    return jsonify({"message": tr("Saved.")})


@app.post("/settings/children/<username>/answer")
@auth_required
@not_qr_limited
@validate_formkey
def children_answer(username, v):
    """Answer a child's open request: `request` is `private` (turn Private posting off) or `release` (they say they are 18 or over and ask to be released),
    `answer` is `allow` or `deny`. A release lifts the child lock and ends the parent's access; it is refused where only an age check lifts a lock."""
    child = _child_or_404(v, username)
    kind, allow = request.form.get("request"), request.form.get("answer") == "allow"
    if kind not in ("private", "release") or request.form.get("answer") not in ("allow", "deny"):
        abort(400)
    if not child_store.asked(g.db, child["id"], kind):
        return _back(error=msg(rules.NO_REQUEST))
    if allow and _wrong_proof(v):                 # loosening or ending the parent's authority asks for their own proof, as ending it by 'Stop' does
        return _back(error=msg(rules.WRONG_PASSWORD))
    row = _child_row(child)
    if kind == "release" and allow:
        # A release lifts the lock by a tick, so it is refused where only an age check lifts a lock: by where the CHILD asked from (the parent may answer from
        # anywhere), and never for a child an age check found under 18.
        if age_declaration.is_strict(child_store.asked_country(g.db, child["id"])):
            return _back(error=msg(age_declaration.DECLARE_NOT_HERE))
        if row.age_under_utc:
            return _back(error=msg(age_declaration.UNDER_18_FOUND))
    if not child_store.take_ask(g.db, child["id"], kind):          # another request answered it a moment ago and did the work: this one does none
        return _back(message=tr("Saved."))
    if kind == "private":
        if allow:
            row.private_posting = False
            g.db.add(row)
        g.db.commit()
        notify(row, PRIVATE_ALLOWED if allow else PRIVATE_DENIED)
        return _back(message=tr("Saved."))
    if allow:
        release_child(row, _now())
        g.db.commit()
        notify(row, RELEASED)
        return _back(message=tr("Saved."))
    g.db.commit()
    notify(row, RELEASE_DENIED)
    return _back(message=tr("Saved."))


@app.post("/settings/children/<username>/stop")
@auth_required
@not_qr_limited
@validate_formkey
def children_stop(username, v):
    """Stop managing an account: it stays a child account with the child lock (a standalone one), the parent's access ends and the link can't be remade."""
    child = _child_or_404(v, username)
    if _wrong_proof(v):                           # irreversible: the link can't be made again
        return _back(error=msg(rules.WRONG_PASSWORD))
    row = _child_row(child)
    # conditional, in the statement that writes: an age check or an admin may have lifted the lock a moment ago, and a lock may only sit on a child account
    changed = g.db.query(User).filter(User.id == child["id"], User.age_lock == age_declaration.PARENT_LOCK).update(
        {"age_lock": age_declaration.REGION_LOCK}, synchronize_session=False)
    if not changed:
        g.db.rollback()
        return _back(error=msg(rules.NO_CHILD))
    child_store.release(g.db, child["id"])
    _money_off(child["id"])                       # a standalone locked account has no parent to allow money
    _sign_out_of_chat(child["id"], "child_stopped")   # and none to allow chat: what was open stops (in the release's own transaction)
    g.db.commit()
    notify(row, STOPPED)
    return _back(message=tr("Saved."))


@app.post("/settings/children/<username>/password")
@auth_required
@not_qr_limited
@limiter.limit("10/hour")
@validate_formkey
def children_password(username, v):
    """Set a new password for a child (the parent's own password first). It signs the child out everywhere, chat apps included. The chat key the child's
    browser locked with the old password is not reachable from here: if they use chat they are asked for their recovery code on their next new device."""
    child = _child_or_404(v, username)
    if _wrong_proof(v):
        return _back(error=msg(rules.WRONG_PASSWORD))
    new = request.form.get("new_password") or ""
    if new != request.form.get("new_password_confirm"):
        return _back(error=tr("Passwords did not match. Please try again."))
    if not valid_password_regex.fullmatch(new):
        return _back(error=tr("Password must be between 8 and 100 characters."))
    row = _child_row(child)
    row.passhash = row.hash_password(new)
    account_sessions.revoke(g.db, row, "child_password")      # every sign-in and chat device of the child, none spared; never the parent's
    g.db.add(row)
    g.db.commit()
    notify(row, NEW_PASSWORD)             # the child is told: a changed password is never silent
    return _back(message=tr("@%(name)s has a new password and is signed out everywhere.", name=row.username))


@app.post("/settings/children/<username>/delete")
@auth_required
@not_qr_limited
@limiter.limit("10/hour")
@validate_formkey
def children_delete(username, v):
    """Delete a child's account (the parent's own password first). The same steps as when a member deletes theirs, run for the child: it isn't this
    browser's account, so this browser stays signed in."""
    child = _child_or_404(v, username)
    if _wrong_proof(v):
        return _back(error=msg(rules.WRONG_PASSWORD))
    row = _child_row(child)
    if (row.negative_balance_cents or 0) > 0:             # an account that owes money isn't deleted to escape it, by anyone (delete_account asks the same)
        return _back(error=msg(rules.OWES))
    rules.close_account(row, "deleted by its parent")          # the one set of steps that closes an account (routes/settings.py)
    g.db.commit()
    return _back(message=tr("The account @%(name)s is deleted.", name=child["username"]))


@app.post("/settings/children/<username>/export")
@limiter.limit("3/hour")
@auth_required
@not_qr_limited
@validate_formkey
def children_export(username, v):
    """Download what the site holds about a child, as a member can download their own data (the parent's own password first). No chat content: there is none."""
    child = _child_or_404(v, username)
    if _wrong_proof(v):
        return _back(error=msg(rules.WRONG_PASSWORD))
    response = make_response(data_export.as_json(data_export.build_for_parent(g.db, child["id"])))
    response.headers["Content-Type"] = "application/json; charset=utf-8"
    response.headers["Content-Disposition"] = f'attachment; filename="{child["username"]}-data.json"'
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/settings/children/<username>")
@auth_required
@not_qr_limited
def children_activity(username, v):
    """What a parent may see of one child's account (helpers/child_activity.py): activity and the names of who they chat with, never a chat."""
    child = _child_or_404(v, username)
    row = _child_row(child)
    return render_template("settings_child.html", v=v, child=child, activity=child_activity.everything(g.db, child["id"]),
                           sign_ins=sign_in_history.page_context(g.db, row, COUNTRY_NAMES)["sign_ins"])


@app.post("/settings/parent/ask")
@auth_required
@not_qr_limited
@validate_formkey
def settings_parent_ask(v):
    """A child asks their parent: `kind` private (turn Private posting off) or release (I'm 18 or over, release my account). Asked once while it is open."""
    return ask_parent(v, request.form.get("kind"), request.form.get("next"))


def ask_parent(v, kind, back="/settings/content"):
    """The child's request, for the route above and for the age answer in Settings (routes/age_declaration_pages.py). A release is only asked where a tick is
    accepted: in the UK, the EU/EEA and Australia an age check is what releases an account."""
    from ruqqus.helpers.redirects import same_site
    back = same_site(back, "/settings/content")
    if v.age_lock != age_declaration.PARENT_LOCK or kind not in ("private", "release"):
        abort(404)
    country = age_declaration.request_country()
    if kind == "release":
        refusal = age_declaration.DECLARE_NOT_HERE if age_declaration.is_strict(country) else age_declaration.UNDER_18_FOUND if v.age_under_utc else None
        if refusal:
            return render_template("message.html", v=v, title=msg(age_declaration.ANSWER_TITLE), error=msg(refusal), link=back,
                                   link_text=msg(age_declaration.BACK)), 403
    parent_id = child_store.parent_of(g.db, v.id)
    if parent_id is None:                  # a lock held by a parent who isn't there (a lost link): nobody to ask, and it doesn't lift itself
        return render_template("message.html", v=v, title=msg(age_declaration.ANSWER_TITLE), error=msg(age_declaration.TARGET_LOCKED), link=back,
                               link_text=msg(age_declaration.BACK)), 403
    # opened by the one request that changes the row (asking again while it is open changes nothing), so the parent is told once however many arrive
    if child_store.ask(g.db, v.id, kind, _now(), age_declaration._country(country) if kind == "release" else None):
        g.db.commit()
        parent = g.db.query(User).filter_by(id=parent_id).first()
        if parent is not None and not parent.is_deleted:
            notify(parent, ASKED_PRIVATE if kind == "private" else ASKED_RELEASE, name=v.username)
    return redirect(back + ("&" if "?" in back else "?") + "age=asked_" + kind, code=303)


def release_child(row, now):
    """End a sub-account's link and lift its child lock: it says (and its parent agrees, or an age check found) it is 18 or over. One statement's worth of
    columns (`age_declaration.lift_fields`), so the CHECKs hold in between; the parent's access ends with it. Returns the parent's id."""
    for column, value in age_declaration.lift_fields(now, False).items():
        setattr(row, column, value)
    g.db.add(row)
    return child_store.release(g.db, row.id)


def tell_parent_released(parent_id, child):
    """An age check (or an admin) released a sub-account: its parent is told and their access has already ended."""
    parent = g.db.query(User).filter_by(id=parent_id).first() if parent_id else None
    if parent is not None and not parent.is_deleted:
        notify(parent, RELEASED_FOR_PARENT, name=child.username)
