"""Answering the age question, confirming an age, and an admin changing an answer (helpers/age_declaration.py has the rules,
helpers/age_assurance.py the vendor side). The sign-up's own answer is in routes/login.py. Named *_pages so the star imports never
replace the helper."""
import time

from flask import abort, g, redirect, render_template, request

from ruqqus.classes import User
from ruqqus.helpers import action_log, age_assurance, age_declaration, chat_devices, chat_signouts, child_store, muting
from ruqqus.helpers.alerts import notify
from ruqqus.helpers.get import get_user
from ruqqus.helpers.i18n_runtime import msg
from ruqqus.helpers.redirects import same_site
from ruqqus.helpers.wrappers import not_qr_limited, admin_level_required, auth_required, validate_formkey
from ruqqus.__main__ import app, limiter, r


def _settings_back():
    return same_site(request.form.get("next"), "/settings/content")


def _refused(v, message, status, back):
    return render_template("message.html", v=v, title=msg(age_declaration.ANSWER_TITLE), error=msg(message),
                           link=back, link_text=msg(age_declaration.BACK)), status


@app.route("/settings/age_declaration", methods=["POST"])
@auth_required
@not_qr_limited
@validate_formkey
def settings_age_declaration(v):
    """Two things, both once. A member who hasn't answered answers ("18 or over" from a country where a vendor has to confirm it is
    stored as the locked answer, as at sign-up). A locked account that says "18 or over" lifts its lock where a tick is accepted;
    in the UK, the EU/EEA and Australia only a vendor's check does (`age_assurance`)."""
    if getattr(v, "is_bot", False):
        abort(403)
    back = _settings_back()
    answer = age_declaration.parse(request.form.get("age_declaration"))
    if answer is None:
        return _refused(v, age_declaration.NO_ANSWER, 400, back)
    country = age_declaration.request_country()
    now = int(time.time())

    if age_declaration.is_asked(v):
        stored = age_declaration.signup_answer(answer, country)
        was_locked = age_declaration.is_locked(v)
        # the check and the write are one statement, so two tabs can't both answer
        changed = g.db.query(User).filter(User.id == v.id, User.age_declared.is_(None)).update(
            age_declaration.fields_for(stored, now), synchronize_session=False)
        if not changed:
            g.db.rollback()
            return _refused(v, age_declaration.ALREADY_ANSWERED, 409, back)
        g.db.refresh(v)                        # read back in this transaction: the answer as written, not yet committed
        if age_declaration.newly_locked(was_locked, age_declaration.is_locked(v)):
            # a child account has no chat (`messaging`): what was signed in stops, outside apps too, whose tokens never expire. Queued in the
            # answer's own transaction, so the lock and its sign-out are committed together or not at all
            chat_signouts.queue(g.db, v.id, "child_locked")
        g.db.commit()
        muting.clear_cached_feeds(v)
        return redirect(back, code=303)

    if age_declaration.is_locked(v) and answer == age_declaration.ADULT:
        if getattr(v, "age_lock", None) == age_declaration.PARENT_LOCK:
            from ruqqus.routes.children_pages import ask_parent       # a parent's sub-account asks its parent to release it
            return ask_parent(v, "release", back)
        if getattr(v, "age_lock", None) != age_declaration.REGION_LOCK:
            return _refused(v, age_declaration.TARGET_LOCKED, 403, back)
        if age_declaration.is_strict(country):
            return _refused(v, age_declaration.DECLARE_NOT_HERE, 403, back)
        if v.age_under_utc:                    # an age check found them under 18: a tick doesn't overrule it
            return _refused(v, age_declaration.UNDER_18_FOUND, 403, back)
        changed = g.db.query(User).filter(User.id == v.id, User.age_lock == age_declaration.REGION_LOCK, User.age_under_utc == 0).update(
            age_declaration.lift_fields(now, False), synchronize_session=False)
        g.db.commit()
        if not changed:
            return _refused(v, age_declaration.ALREADY_ANSWERED, 409, back)
        g.db.refresh(v)
        muting.clear_cached_feeds(v)
        return redirect(back + ("&" if "?" in back else "?") + "age=lifted", code=303)

    return _refused(v, age_declaration.ALREADY_ANSWERED, 409, back)


def _vendor_or_404(v):
    if getattr(v, "is_bot", False) or not age_assurance.configured():
        abort(404)


@app.route("/settings/age_assurance/start", methods=["POST"])
@auth_required
@not_qr_limited
@limiter.limit("10/hour")
@validate_formkey
def settings_age_assurance_start(v):
    """Send the member to the age-check service. 404 while no service is set up. Not offered to someone already confirmed, or whom
    a check found under 18 (their account stays locked and the option is gone)."""
    _vendor_or_404(v)
    back = _settings_back()
    if age_declaration.assured(v) or (v.age_under_utc or 0) > 0:
        return _refused(v, age_declaration.ALREADY_ANSWERED, 409, back)
    state = age_assurance.new_state(r, v.id)
    callback = request.url_root.rstrip("/") + "/settings/age_assurance/callback"
    return redirect(age_assurance.provider().start(v, callback, state), code=303)


@app.route("/settings/age_assurance/callback", methods=["GET"])
@auth_required
@not_qr_limited
def settings_age_assurance_callback(v):
    """Where the service sends the member back. The one-time state must be this account's own; the result is the provider's."""
    _vendor_or_404(v)
    if not age_assurance.take_state(r, request.args.get("state"), v.id):
        return _refused(v, age_declaration.ALREADY_ANSWERED, 400, "/settings/content")
    status = age_assurance.provider().finish(v, request.args)
    now = int(time.time())
    if status == age_assurance.ADULT:
        was_sub = getattr(v, "age_lock", None) == age_declaration.PARENT_LOCK
        g.db.query(User).filter(User.id == v.id).update(age_declaration.assure_fields(v, now), synchronize_session=False)
        parent_id = child_store.release(g.db, v.id) if was_sub else None          # an age check releases a sub-account: its parent's access ends
        # the Log, for the staff only: an age lock must never mark a person as a minor in public
        action_log.record_staff(g.db, "age_lock", None, "age check: confirmed 18 or over, the lock is lifted", target_user_id=v.id)
        g.db.commit()
        g.db.refresh(v)
        muting.clear_cached_feeds(v)
        if was_sub:
            from ruqqus.routes.children_pages import tell_parent_released
            tell_parent_released(parent_id, v)
        return redirect("/settings/content?age=confirmed", code=303)
    if status == age_assurance.MINOR:
        g.db.query(User).filter(User.id == v.id).update({"age_under_utc": now}, synchronize_session=False)
        action_log.record_staff(g.db, "age_lock", None, "age check: under 18, it stays locked", target_user_id=v.id)
        g.db.commit()
        return redirect("/settings/content?age=under", code=303)
    if status == age_assurance.UNDER_MINIMUM:
        from ruqqus.routes.settings import close_own_account
        # read before the accounts close: the page clears this account's chat here (and any child closed with it)
        deleted_mxid, deleted_children = chat_devices.closing_with(g.db, v.id)
        close_own_account(v, "age check: under the youngest age where they are")
        action_log.record_staff(g.db, "age_lock", None, "age check: under the youngest age allowed here, the account was closed",
                                target_user_id=v.id)
        g.db.commit()
        return render_template("account_deleted.html", v=None, deleted_mxid=deleted_mxid, deleted_children=deleted_children, title=msg(age_declaration.UNDER_AGE_CLOSED_TITLE),
                               message=msg(age_declaration.UNDER_AGE_CLOSED))
    return redirect("/settings/content?age=failed", code=303)


@app.route("/admin/age_declaration", methods=["POST"])
@admin_level_required(3)
@validate_formkey
def admin_age_declaration(v):
    """Change an account's answer (`answer`: 1, 2, or empty to ask again). Only an account below the admin's own level,
    the bar for banning, and never a bot, which is not asked. Written to the Log for the staff, and the account is told.
    Taking an account off "child" leaves its filter where it is: the member lowers it, with their password."""
    target = get_user(request.form.get("username", ""), graceful=True)
    if target is None or target.is_deleted or target.is_bot or v.admin_level <= (target.admin_level or 0):
        abort(404)
    raw = request.form.get("answer", "")
    confirmed = raw == "confirmed"
    answer = age_declaration.ADULT if confirmed else age_declaration.parse(raw)
    if raw and answer is None:
        abort(400)
    now = int(time.time())
    was = age_declaration.CONFIRMED if age_declaration.assured(target) else target.age_declared
    if confirmed and age_declaration.assured(target):
        return redirect(target.permalink)
    if not confirmed and answer == target.age_declared:
        return redirect(target.permalink)
    notice = age_declaration.CONFIRMED if confirmed else answer
    was_sub = target.age_lock == age_declaration.PARENT_LOCK
    was_locked = age_declaration.is_locked(target)
    action_log.record_quietly(g.db, "age_declaration", v.id, target_user_id=target.id, role=action_log.ADMIN,
                              reason=request.form.get("reason", ""), notice_sent=True,
                              staff_note=f"{age_declaration.LABELS[was]} to {age_declaration.LABELS[notice]}")
    # an admin's confirmation is how staff (who must see what they moderate) and anyone verified some other way are cleared
    fields = age_declaration.lift_fields(now, True, reset_filter=False) if confirmed else age_declaration.fields_for(answer, now)
    for column, value in fields.items():
        setattr(target, column, value)
    g.db.add(target)
    # an admin who takes the lock off a parent's sub-account ends the parent's access with it (the switches and the link go; the parent is told)
    parent_id = child_store.release(g.db, target.id) if was_sub and target.age_lock != age_declaration.PARENT_LOCK else None
    if age_declaration.newly_locked(was_locked, age_declaration.is_locked(target)):
        chat_signouts.queue(g.db, target.id, "child_locked")       # a child account has no chat: what was signed in stops
    g.db.commit()
    muting.clear_cached_feeds(target)
    notify(target, age_declaration.ADMIN_NOTICES[notice])
    if parent_id:
        from ruqqus.routes.children_pages import tell_parent_released
        tell_parent_released(parent_id, target)
    return redirect(target.permalink)
