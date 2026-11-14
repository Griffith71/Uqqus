"""The Report button on a Log entry that shows a person's words, and the admins' queue (the Log, slice D3a; helpers/log_reports.py has the rules).

`POST /log/<id>/report`: a signed-in member reports an entry they can read and that shows words (a deleted item's, or a reason), once, with a reason
of up to 200 characters, 20 a day. **Nobody is ever shown who reported.** `/admin/log_reports` (level 3) is the queue: **Purge** erases the words
through the existing purge path (`routes/admin_api.py purge_post_text` / `purge_comment_text`, which empties every copy in the Log), **Redact** (level
4) hides a reason from everyone below staff as a new linked entry, **Dismiss** closes it. Guildmasters have no tool for deleted items.

Not `log_reports.py`: the star import of a routes package would put a module of that name over the helper."""
import time

from flask import abort, g, redirect, render_template, request
from sqlalchemy import text

from ruqqus.__main__ import app, limiter
from ruqqus.classes import Comment, Submission
from ruqqus.helpers import action_log, log_reports, log_view
from ruqqus.helpers.get import get_post
from ruqqus.helpers.i18n_runtime import tr
from ruqqus.helpers.redirects import same_site
from ruqqus.helpers.wrappers import admin_level_required, auth_required, validate_formkey

REPORT_RATE = "30/hour"


def _entry_words(entry):
    """Does this entry show words a person wrote: a reason, or the text of a deleted item (a public copy)? Only those can be reported."""
    if entry["reason"] and not entry["redacted_utc"]:
        return True
    if entry["log_type"] != action_log.DELETION:
        return False
    return bool(action_log.deleted_texts(g.db, [entry["id"]]).get(entry["id"], {}).get("public"))


@app.route("/log/<int:entry_id>/report", methods=["POST"])
@limiter.limit(REPORT_RATE)
@auth_required
@validate_formkey
def log_report(entry_id, v):
    level = action_log.staff_level(v.admin_level)
    member = action_log.member_board_ids(g.db, v.id)
    rows, _ = action_log.fetch(g.db, level=level, member_boards=member, scope=("id = :one", {"one": int(entry_id)}), limit=1)
    if not rows or not _entry_words(rows[0]):
        abort(404)                                           # an entry they can't read, or one with no words in it: nothing to report
    if not log_reports.parse_reason(request.form.get("reason")):
        return render_template("message.html", v=v, title=tr("Give a reason"), message=tr("Say why it should come down, in a few words.")), 400
    answer = log_reports.file_report(g.db, entry_id, v.id, request.form.get("reason"), int(time.time()))
    g.db.commit()
    if answer == "limit":
        return render_template("message.html", v=v, title=tr("Too many reports today"), message=tr("Try again tomorrow.")), 429
    back = same_site(request.referrer or "", f"/log/{entry_id}")
    title = tr("You already reported this") if answer == "again" else tr("Thank you")
    message = (tr("The site's admins have it already.") if answer == "again"
               else tr("The site's admins will read it. They are never told who reported."))
    return render_template("message.html", v=v, title=title, message=message, link=back, link_text=tr("Back")), 200


# --- the admins' queue ------------------------------------------------------------------------------------------------------------

def _queue(limit=100):
    """The reported entries, most reported first, with what an admin needs to decide: the line, the rule and reason, and the deleted words
    unfiltered (an admin reads what a reader's filter would hide). Never who reported."""
    waiting = log_reports.waiting(g.db, limit)
    ids = [w["entry_id"] for w in waiting]
    if not ids:
        return []
    from ruqqus.routes.log_pages import _rows
    entries, _ = action_log.fetch(g.db, level=4, scope=(f"id IN ({', '.join(str(int(i)) for i in ids)})", {}), limit=len(ids))
    by_id = {e["id"]: e for e in entries}
    lines = {r["id"]: r for r in _rows([by_id[i] for i in ids if i in by_id], None)}
    texts = action_log.deleted_texts(g.db, ids)
    out = []
    for item in waiting:
        entry = by_id.get(item["entry_id"])
        if entry is None:
            continue
        out.append({**item, "entry": entry, "line": lines.get(item["entry_id"]), "words": texts.get(item["entry_id"]),
                    "can_purge": bool(texts.get(item["entry_id"]) and (texts[item["entry_id"]]["post_id"] or texts[item["entry_id"]]["comment_id"])),
                    "can_redact": bool(entry["reason"]) and not entry["redacted_utc"]})
    return out


def _plain_words(html_):
    """A deleted item's text for the queue: its links and pictures taken out, whatever the admin's own word filter says (they read it to decide),
    and cleaned again with the site's own cleaner, so what is drawn is as safe as any member text."""
    from markupsafe import Markup
    from ruqqus.helpers import sanitize as sanitize_rules
    return Markup(sanitize_rules._clean_wo_links.clean(log_view.without_links(log_view.without_media(html_))))


@app.route("/admin/log_reports", methods=["GET"])
@admin_level_required(3)
def admin_log_reports(v):
    return render_template("admin/log_reports.html", v=v, queue=_queue(), level=action_log.staff_level(v.admin_level), msg=request.args.get("msg") or "",
                           words=_plain_words)


@app.route("/admin/log_reports/<int:entry_id>", methods=["POST"])
@admin_level_required(3)
@validate_formkey
def admin_log_report_decide(entry_id, v):
    """Purge the words, hide the reason (level 4) or dismiss: one decision for every open report of the entry."""
    decision = request.form.get("decision")
    if decision not in log_reports.DECISIONS:
        abort(400)
    now = int(time.time())
    rows, _ = action_log.fetch(g.db, level=4, scope=("id = :one", {"one": int(entry_id)}), limit=1)
    if not rows:
        abort(404)
    entry = rows[0]
    if decision == log_reports.PURGED:
        copy = action_log.deleted_texts(g.db, [entry_id]).get(entry_id)
        if not copy or not (copy["post_id"] or copy["comment_id"]):
            return redirect("/admin/log_reports?msg=There+are+no+words+here+to+purge.", code=303)
        from ruqqus.routes.admin_api import purge_comment_text, purge_post_text
        if copy["post_id"]:
            post = g.db.query(Submission).filter_by(id=copy["post_id"]).first()
            if post is not None:
                purge_post_text(post, v, rule=None, reason="Taken down after a report.")
        else:
            comment = g.db.query(Comment).filter_by(id=copy["comment_id"]).first()
            if comment is not None:
                purge_comment_text(comment, v, rule=None, reason="Taken down after a report.")
        action_log.scrub_for(g.db, post_ids=[copy["post_id"]] if copy["post_id"] else (), comment_ids=[copy["comment_id"]] if copy["comment_id"] else ())
    elif decision == log_reports.REDACTED:
        if v.admin_level < 4:
            abort(403)
        if not entry["reason"] or entry["redacted_utc"]:
            return redirect("/admin/log_reports?msg=There+is+no+reason+to+hide.", code=303)
        action_log.correct(g.db, entry_id, v, "Hidden after a report.")
    closed = log_reports.decide(g.db, entry_id, decision, v.id, now)
    g.db.commit()
    return redirect(f"/admin/log_reports?msg={'Done.' if closed else 'Someone decided this a moment ago.'}".replace(" ", "+"), code=303)


def waiting_reports():
    return log_reports.waiting_count(g.db)


app.jinja_env.globals.update(log_reports_waiting=waiting_reports)
