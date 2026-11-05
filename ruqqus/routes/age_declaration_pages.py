"""Answering the age question, and an admin changing an answer (helpers/age_declaration.py has the rules). The sign-up's
own answer is in routes/login.py. Named *_pages so the star imports never replace the helper."""
import time

from flask import abort, g, redirect, render_template, request

from ruqqus.classes import User
from ruqqus.helpers import action_log, age_declaration, muting
from ruqqus.helpers.alerts import notify
from ruqqus.helpers.get import get_user
from ruqqus.helpers.i18n_runtime import msg
from ruqqus.helpers.redirects import same_site
from ruqqus.helpers.wrappers import not_qr_limited, admin_level_required, auth_required, validate_formkey
from ruqqus.__main__ import app


@app.route("/settings/age_declaration", methods=["POST"])
@auth_required
@not_qr_limited
@validate_formkey
def settings_age_declaration(v):
    """One answer, for good. "Child" asks first what that means, on a page of its own: it can't be taken back by the
    member, and a wrong tick on a notice must not lock a grown-up's account."""
    if getattr(v, "is_bot", False):
        abort(403)
    back = same_site(request.form.get("next"), "/settings/content")

    def refused(message, status):
        return render_template("message.html", v=v, title=msg(age_declaration.ANSWER_TITLE), error=msg(message),
                               link=back, link_text=msg(age_declaration.BACK)), status

    if not age_declaration.is_asked(v):
        return refused(age_declaration.ALREADY_ANSWERED, 409)
    answer = age_declaration.parse(request.form.get("age_declaration"))
    if answer is None:
        return refused(age_declaration.NO_ANSWER, 400)
    if answer == age_declaration.CHILD and request.form.get("confirm") != "1":
        return render_template("age_confirm.html", v=v, back=back,
                               title=msg(age_declaration.CONFIRM_TITLE))

    # the check and the write are one statement, so two tabs can't both answer
    changed = g.db.query(User).filter(User.id == v.id, User.age_declared.is_(None)).update(
        age_declaration.fields_for(answer, int(time.time())), synchronize_session=False)
    g.db.commit()
    if not changed:
        return refused(age_declaration.ALREADY_ANSWERED, 409)
    g.db.refresh(v)
    if answer == age_declaration.CHILD:
        muting.clear_cached_feeds(v)
    return redirect(back, code=303)


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
    answer = age_declaration.parse(raw)
    if raw and answer is None:
        abort(400)
    if answer == target.age_declared:
        return redirect(target.permalink)
    action_log.record_quietly(g.db, "age_declaration", v.id, target_user_id=target.id, role=action_log.ADMIN,
                              reason=request.form.get("reason", ""), notice_sent=True,
                              staff_note=f"{age_declaration.LABELS[target.age_declared]} to {age_declaration.LABELS[answer]}")
    for column, value in age_declaration.fields_for(answer, int(time.time())).items():
        setattr(target, column, value)
    g.db.add(target)
    g.db.commit()
    muting.clear_cached_feeds(target)
    notify(target, age_declaration.ADMIN_NOTICES[answer])
    return redirect(target.permalink)
