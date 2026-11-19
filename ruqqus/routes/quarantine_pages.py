"""What follows an automatic quarantine: the staff queue and the person's way to ask for a review (the owner, 2026-11-17 and 18).

The site's own checks only quarantine an account (helpers/auto_quarantine.py: the one place a hold is set or cleared); **a real admin decides what follows**.

* `GET /admin/quarantine` (level 3, English like the other admin pages) lists the accounts held by an automatic check: since when, which check and what it saw, whether
  an appeal waits. **Release** lifts the hold (`auto_quarantine.release`: a new staff entry, the person told; the public line that names nobody follows an hour or more later from the cycle, `auto_quarantine.publish_due`) and settles any appeal
  of it still waiting as reversed. **It lifts the hold the admin was shown**: the form posts the staff entry's id and the route compares it with the account's current one
  under a row lock, so a hold that was lifted and placed again since the page was drawn is told apart from the one that was read. **A ban** is the existing admin ban
  route on the account's profile (its second-admin approval of any reveal of an anonymous poster is unchanged); a banned account leaves this queue. **A hold whose staff
  entry is legal-level (a link about minors) is a level-4 admin's, as its appeal is: it is not in a level-3 admin's list or count at all** (as the appeals queue leaves it
  out), and a level-3 release of one is a 404.
* `GET/POST /appeal/quarantine` is the person's appeal. The staff's entry about the hold is staff-only (a 404 to the person), so this page **finds the account's own
  current entry server-side** (`auto_quarantine.current_entry(v.id)`): no id from the browser is ever trusted, and an account that isn't held by an automatic check is
  a 404. One appeal per quarantine, three a day, 500 characters (helpers/log_appeals.py), decided at `/admin/appeals` by the site's admins, never the system; a reversal
  releases the hold and writes new linked entries (`routes/log_appeal_pages.py` `_undo`). It is the way out of a hold, so it does not carry `@not_quarantined`; a limited
  QR sign-in is a stranger to it (its POST is held, its GET asks for the password).

Not `quarantine.py`: a route module named like a helper would be replaced by it in files that star-import both."""
from urllib.parse import quote

from flask import abort, g, redirect, render_template, request
from sqlalchemy.exc import IntegrityError

from ruqqus.__main__ import app, limiter
from ruqqus.helpers import auto_quarantine, log_appeals, quarantine_review
from ruqqus.helpers.i18n_runtime import msg
from ruqqus.helpers.quarantine import HELD_AUTOMATIC
from ruqqus.helpers.wrappers import admin_level_required, auth_required, not_qr_limited, page_number, qr_limited_answer, validate_formkey

READ_RATE = "60/minute"


def _noindex(html):
    response = app.make_response(html)
    response.headers["X-Robots-Tag"] = "noindex"
    response.headers["Cache-Control"] = "private, no-store"
    return response


# --- the staff queue -----------------------------------------------------------------------------------------------------------

@app.get("/admin/quarantine")
@admin_level_required(3)
def admin_quarantine(v):
    page = page_number()
    level = quarantine_review.reader_level(v.admin_level)
    rows, more = quarantine_review.queue(g.db, page, level=level)
    for row in rows:
        row["check"] = quarantine_review.check_line(row)
        row["can_release"] = row["user_id"] != v.id and quarantine_review.release_refusal(row["audience"], level) is None
    return render_template("admin/quarantine.html", v=v, rows=rows, page=page, next_exists=more, waiting=quarantine_review.queue_count(g.db, level),
                           msg=request.args.get("msg"))


def _back(message):
    return redirect(f"/admin/quarantine?msg={quote(message)}", code=303)


STALE = "That account's hold changed after you opened the page: it was lifted and placed again. Look at it again before you lift it."


@app.post("/admin/quarantine/<int:user_id>/release")
@admin_level_required(3)                 # a limited sign-in has no admin level: the page asks for the password
@validate_formkey
def admin_quarantine_release(user_id, v):
    if user_id == v.id:
        abort(404)
    held = quarantine_review.lock_hold(g.db, user_id)       # the account's row is ours until the commit: a check can't hold it again between the read and the lift
    current = auto_quarantine.current_entry(g.db, user_id)
    if current is not None and quarantine_review.release_refusal(current["audience"], v.admin_level):
        abort(404)                       # a level-4 hold: it is not in level 3's queue, and a release of it finds nothing
    if held and quarantine_review.is_stale(quarantine_review.parse_entry(request.form.get("entry")), current):
        g.db.rollback()
        return _back(STALE)              # the hold on the account is not the one this form was drawn for
    note = log_appeals.parse_reason(request.form.get("note"), log_appeals.NOTE_MAX)
    if not note:
        g.db.rollback()
        return _back("Say why you are lifting it, in a line: it goes in the Log, for the staff.")
    lifted = auto_quarantine.release(g.db, user_id, admin_id=v.id, note=note)
    if not lifted:
        g.db.rollback()
        return _back("That account isn't on hold any more.")
    if current is not None:
        quarantine_review.close_appeals(g.db, current["id"], v.id, lifted if lifted is not True else None)
    g.db.commit()
    auto_quarantine.send_pending(g.db)
    return _back("Lifted. The person has been told.")


def quarantine_waiting(viewer=None):
    """The count beside Admin > Quarantine on the admin page, as the admin it is drawn for may read the queue (the template passes `v`): a legal-level hold is in level 4's
    number only. With no viewer the narrowest admin's number is the answer."""
    return quarantine_review.queue_count(g.db, quarantine_review.reader_level(getattr(viewer, "admin_level", 0)))


app.jinja_env.globals.update(quarantine_waiting=quarantine_waiting)


# --- the person's appeal -------------------------------------------------------------------------------------------------------

def _own_entry(v):
    """The staff entry of THIS account's current automatic quarantine, found here and never named by the browser, or a 404 (not held by an automatic check, held but
    banned since: a ban has its own appeal)."""
    if v.is_suspended or not getattr(v, "is_auto_quarantined", False):
        abort(404)
    entry = auto_quarantine.current_entry(g.db, v.id)
    if entry is None:
        abort(404)
    return entry


@app.get("/appeal/quarantine")
@limiter.limit(READ_RATE)
@auth_required
def quarantine_appeal_form(v):
    if getattr(v, "qr_limited", False):
        return qr_limited_answer(v)               # the account's standing is not shown to a limited sign-in until the password is given
    entry = _own_entry(v)
    return _noindex(render_template(
        "quarantine_appeal.html", v=v, held=msg(HELD_AUTOMATIC), shown=quarantine_review.shown_to_person(entry),
        mine=quarantine_review.appeal_of(g.db, entry["id"], v.id), problem=log_appeals.can_appeal(g.db, log_appeals.entry_of(g.db, entry["id"]), v.id),
        max_reason=log_appeals.REASON_MAX, error=request.args.get("error") or "", sent=bool(request.args.get("sent"))))


@app.post("/appeal/quarantine")
@limiter.limit("20/hour")
@auth_required
@not_qr_limited
@validate_formkey
def quarantine_appeal_post(v):
    entry = _own_entry(v)
    try:
        log_appeals.file_appeal(g.db, entry["id"], v.id, request.form.get("reason"))
    except log_appeals.AppealRefused as refused:
        g.db.rollback()
        return redirect(f"/appeal/quarantine?error={quote(msg(str(refused)), safe='')}", code=303)
    except IntegrityError:                    # two tabs at once past the lock: the unique index says it was already made
        g.db.rollback()
        return redirect(f"/appeal/quarantine?error={quote(msg(log_appeals.ALREADY_APPEALED), safe='')}", code=303)
    g.db.commit()
    return redirect("/appeal/quarantine?sent=1", code=303)
