"""One entry of the Log, and appeals against the decisions in it (the Log, slice D2; helpers/log_appeals.py has the rules).

`GET /log/<id>` is one entry, drawn as the public Log draws it (what the notices to the person link to). `GET/POST /log/<id>/appeal` is the
appeal: only the person the decision is about may open it (anyone else, and an entry that can't be appealed, gets the same 404), once per
entry, three a day. The deciders: the site's admins at `/admin/appeals`, and a guild's guildmasters (with the permission the action
asked for) at `/+guild/mod/appeals`, never the one who made the decision while another could. A reversal runs the existing undo and writes a
NEW entry linked to the original (public when the original was); the person is told how it ended either way.

Not `log_appeals.py`: the star import of a routes package would put a module of that name over the helper."""
import time

from flask import abort, g, redirect, render_template, request
from sqlalchemy import text

from ruqqus.__main__ import app, cache, limiter
from ruqqus.classes import BanRelationship, Board, Comment, ContentEditHistory, ModAction, Submission, User
from ruqqus.classes.mod_logs import ACTIONTYPES
from ruqqus.helpers import action_log, alias_actions, log_appeals, log_notices, log_view
from ruqqus.helpers.base36 import base36encode
from ruqqus.helpers.get import get_guild
from ruqqus.helpers.i18n_runtime import msg
from ruqqus.helpers.wrappers import admin_level_required, auth_desired, auth_required, not_qr_limited, qr_limited_answer, validate_formkey

LOG_RATE = "60/minute"
UNDO_REASON = "Reversed on appeal."


def _noindex(html):
    response = app.make_response(html)
    response.headers["X-Robots-Tag"] = "noindex"
    response.headers["Cache-Control"] = "private, no-store"
    return response


def _public_row(entry_id):
    """One entry as the public Log carries it (the staff-only columns never selected; an admin's or a post author's name left out),
    whatever its audience: for the person it is about, who may be told what it says of them but never the staff's notes."""
    row = g.db.execute(
        text(f"SELECT {action_log.PUBLIC_COLUMNS} FROM log_entries WHERE id = :i"), {"i": int(entry_id)}).first()
    if row is None:
        return None
    entry = dict(row._mapping)
    if entry["actor_role"] in (action_log.ADMIN, action_log.AUTHOR):
        entry["actor_id"] = None
    if entry["redacted_utc"]:
        entry["reason"] = ""
    return entry


def _lines(entries, v):
    from ruqqus.routes.log_pages import _rows
    return _rows(entries, v)


# --- one entry ---------------------------------------------------------------------------------------------------------------

@app.route("/log/<int:entry_id>", methods=["GET"])
@limiter.limit(LOG_RATE)
@auth_desired
def log_entry(entry_id, v):
    """One entry, if this reader may read it (the same rule as the Log's own pages: a staff entry or a private guild's is a 404 to
    everyone else), with its Appeal link for the person it is about."""
    level = action_log.staff_level(v.admin_level if v else 0)
    member = action_log.member_board_ids(g.db, v.id) if v else []
    rows, _ = action_log.fetch(g.db, level=level, member_boards=member, scope=("id = :one", {"one": int(entry_id)}), limit=1)
    if not rows:
        abort(404)
    lines = _lines(rows, v)
    if not lines:
        abort(404)
    entry = log_appeals.entry_of(g.db, entry_id)
    appeal = None
    if v is not None and not getattr(v, "qr_limited", False) and entry is not None and entry["action"] in log_appeals.APPEALABLE \
            and log_appeals.subject_of(g.db, entry) == v.id:
        appeal = {"mine": _my_appeal(entry_id, v.id), "can": log_appeals.can_appeal(g.db, entry, v.id) is None}
    return _noindex(render_template("log_entry.html", v=v, rows=lines, entry_id=entry_id, appeal=appeal))


def _my_appeal(entry_id, user_id):
    row = g.db.execute(text("SELECT status, decided_utc, decision_note FROM log_appeals WHERE entry_id = :e AND user_id = :u"),
                       {"e": entry_id, "u": user_id}).first()
    return dict(row._mapping) if row else None


# --- appealing ---------------------------------------------------------------------------------------------------------------

def _appealable_or_404(entry_id, v):
    entry = log_appeals.entry_of(g.db, entry_id)
    if entry is None or entry["action"] not in log_appeals.APPEALABLE or log_appeals.subject_of(g.db, entry) != v.id:
        abort(404)                               # the same page for an entry that isn't theirs, one that can't be appealed and none
    return entry


@app.route("/log/<int:entry_id>/appeal", methods=["GET"])
@limiter.limit(LOG_RATE)
@auth_required
def log_appeal_form(entry_id, v):
    """The appeal form: a suspended account may open it (a suspension is exactly what it may be about)."""
    if getattr(v, "qr_limited", False):
        return qr_limited_answer(v)               # the member's own items are not shown to a limited sign-in until the password is given
    entry = _appealable_or_404(entry_id, v)
    lines = _lines([_public_row(entry_id)], v)
    return _noindex(render_template("log_appeal.html", v=v, rows=lines, entry_id=entry_id, mine=_my_appeal(entry_id, v.id),
                                    problem=log_appeals.can_appeal(g.db, entry, v.id), max_reason=log_appeals.REASON_MAX,
                                    error=request.args.get("error") or "", sent=bool(request.args.get("sent"))))


@app.route("/log/<int:entry_id>/appeal", methods=["POST"])
@limiter.limit("20/hour")
@auth_required
@not_qr_limited
@validate_formkey
def log_appeal_post(entry_id, v):
    _appealable_or_404(entry_id, v)
    try:
        log_appeals.file_appeal(g.db, entry_id, v.id, request.form.get("reason"))
    except log_appeals.AppealRefused as refused:
        g.db.rollback()
        return redirect(f"/log/{int(entry_id)}/appeal?error={_quote(msg(str(refused)))}", code=303)
    g.db.commit()
    return redirect(f"/log/{int(entry_id)}/appeal?sent=1", code=303)


def _quote(value):
    from urllib.parse import quote
    return quote(value, safe="")


# --- the queues --------------------------------------------------------------------------------------------------------------

def _hidden_item(row):
    """Whether the item an appeal is about is anonymous (its deciders are never told whose it is)."""
    if row["target_comment_id"]:
        found = g.db.execute(text("SELECT is_anonymous FROM comments WHERE id = :i"), {"i": row["target_comment_id"]}).first()
    elif row["target_submission_id"]:
        found = g.db.execute(text("SELECT is_anonymous FROM submissions WHERE id = :i"), {"i": row["target_submission_id"]}).first()
    else:
        return False
    return bool(found and found[0])


def _queue_rows(appeals, staff):
    """The lines a queue draws: what was decided, by id, the appeal's words and, to the site's admins always and to guildmasters only
    when the item isn't anonymous, who appealed. Never the item's words."""
    users = {u.id: u for u in g.db.query(User).filter(User.id.in_([a["user_id"] for a in appeals] or [0])).all()}
    rows = []
    for a in appeals:
        anonymous = _hidden_item(a)
        kind = ACTIONTYPES.get(a["action"], {})
        target = ""
        if a["target_submission_id"]:
            target = log_view.item_link("post", base36encode(a["target_submission_id"]))
        elif a["target_comment_id"]:
            target = log_view.item_link("comment", base36encode(a["target_comment_id"]))
        elif a["target_user_id"]:
            target = log_view.person(_name(a["target_user_id"]))
        who = log_view.site_account(app.config["SITE_NAME"]) if a["actor_role"] != action_log.GUILDMASTER else log_view.person(
            _name(a["actor_id"]))
        appellant = users.get(a["user_id"])
        rows.append({
            "id": a["id"], "entry_id": a["entry_id"], "created_utc": a["created_utc"], "reason": a["reason"],
            "sentence": log_view.sentence(a["action"], ACTIONTYPES, who, target),
            "icon": kind.get("icon", "fa-gavel"), "color": kind.get("color", "bg-info"),
            "entry_reason": a["entry_reason"], "rule": a["rule_key"],
            "who": "" if (anonymous and not staff) else (appellant.username if appellant else ""),
            "anonymous": anonymous and not staff,
            "conflicted": False,
        })
    return rows


def _name(user_id):
    found = g.db.execute(text("SELECT username FROM users WHERE id = :i"), {"i": user_id}).first() if user_id else None
    return found[0] if found else ""


@app.route("/admin/appeals", methods=["GET"])
@admin_level_required(3)
def admin_appeals(v):
    appeals = log_appeals.open_queue(g.db, None)
    rows = _queue_rows(appeals, True)
    for row, a in zip(rows, appeals):
        row["conflicted"] = log_appeals.conflicted(g.db, {**a, "board_id": None}, v.id)
    return render_template("admin/appeals.html", v=v, rows=rows, waiting=len(rows), msg=request.args.get("msg"))


def _guild_mod(board, v):
    """The guildmaster record if this account leads the guild and may decide anything about it, else a 404 (a page that says nothing about
    whether the guild has appeals)."""
    m = board.has_mod(v) if v is not None else None
    if not m or v.is_suspended:
        abort(404)
    return m


def _may_decide_guild(m, action):
    permission = log_appeals.APPEALABLE.get(action, (None, None))[1]
    return bool(permission and (m.perm_full or getattr(m, f"perm_{permission}", False)))


@app.route("/+<guildname>/mod/appeals", methods=["GET"])
@auth_required
def guild_appeals(guildname, v):
    board = get_guild(guildname, v=v)
    m = _guild_mod(board, v)
    appeals = [a for a in log_appeals.open_queue(g.db, board.id) if _may_decide_guild(m, a["action"])]
    rows = _queue_rows(appeals, False)
    for row, a in zip(rows, appeals):
        row["conflicted"] = log_appeals.conflicted(g.db, a, v.id)
    return render_template("guild/appeals.html", v=v, b=board, rows=rows, msg=request.args.get("msg"))


# --- deciding ----------------------------------------------------------------------------------------------------------------

def _bad_input(url, message):
    return redirect(f"{url}?msg={_quote(message)}", code=303)


def _undo(entry, decider):
    """Run the undo of the entry's action and return the id of the NEW entry it wrote, linked to the original (public when the original
    was). The state changes are the existing unhide, unban and unexile routes' own (routes/boards.py, comments.py, admin_api.py)."""
    action, now = entry["action"], int(time.time())
    if action == "hide_post_from_guild":
        post = g.db.query(Submission).filter_by(id=entry["target_submission_id"]).first()
        if post is None:
            return None
        post.hidden_by_guild = False
        g.db.add(post)
        cache.delete_memoized(Board.idlist, post.board)
        g.db.add(ContentEditHistory(actor_id=decider.id, target_submission_id=post.id, board_id=entry["board_id"], action="guild_unhide"))
        ma = ModAction(kind="unhide_post_from_guild", user_id=decider.id, target_submission_id=post.id, board_id=entry["board_id"])
    elif action == "hide_comment_from_guild":
        comment = g.db.query(Comment).filter_by(id=entry["target_comment_id"]).first()
        if comment is None:
            return None
        comment.hidden_by_guild = False
        g.db.add(comment)
        g.db.add(ContentEditHistory(actor_id=decider.id, target_comment_id=comment.id, board_id=entry["board_id"], action="guild_unhide"))
        ma = ModAction(kind="unhide_comment_from_guild", user_id=decider.id, board_id=entry["board_id"], target_comment_id=comment.id)
    elif action == "ban_post":
        post = g.db.query(Submission).filter_by(id=entry["target_submission_id"]).first()
        if post is None:
            return None
        post.is_banned = False
        post.is_approved = decider.id
        post.approved_utc = now
        g.db.add(post)
        ma = ModAction(kind="unban_post", user_id=decider.id, target_submission_id=post.id, board_id=post.board_id, note="admin action")
    elif action == "ban_comment":
        comment = g.db.query(Comment).filter_by(id=entry["target_comment_id"]).first()
        if comment is None:
            return None
        comment.is_banned = False
        comment.is_approved = decider.id
        comment.approved_utc = now
        g.db.add(comment)
        ma = ModAction(kind="unban_comment", user_id=decider.id, target_comment_id=comment.id,
                       board_id=comment.post.board_id if comment.post else None, note="admin action")
    elif action == "author_hide_comment":
        comment = g.db.query(Comment).filter_by(id=entry["target_comment_id"]).first()
        if comment is None:
            return None
        comment.author_hidden = False
        g.db.add(comment)
        g.db.add(ContentEditHistory(actor_id=decider.id, target_comment_id=comment.id, action="author_restore"))
        return action_log.record_quietly(g.db, "author_unhide_comment", decider.id, role=action_log.ADMIN, comment_id=comment.id,
                                         board_id=entry["board_id"], reason=UNDO_REASON, linked_entry_id=entry["id"])
    elif action == "exile_user":
        ban = g.db.query(BanRelationship).filter_by(board_id=entry["board_id"], user_id=entry["target_user_id"], is_active=True).first()
        if ban is None:
            return None
        ban.is_active = False
        g.db.add(ban)
        ma = ModAction(kind="unexile_user", user_id=decider.id, target_user_id=entry["target_user_id"], board_id=entry["board_id"])
    elif action == "exile_alias":
        # an exile made by alias is put right the same way, and the entry that says so names the number, not the account (helpers/alias_actions.py)
        ban = g.db.query(BanRelationship).filter(BanRelationship.board_id == entry["board_id"], BanRelationship.user_id == entry["subject_user_id"],
                                                 BanRelationship.is_active == True, BanRelationship.alias_n.isnot(None)).first()
        if ban is None:
            return None
        ban.is_active = False
        g.db.add(ban)
        ma = ModAction(kind="unexile_alias", user_id=decider.id, board_id=entry["board_id"], note=alias_actions.label(entry["alias_n"]))
        g.db.add(ma)
        g.db.flush()
        return action_log.annotate(g.db, ma.id, reason=UNDO_REASON, linked_entry_id=entry["id"], alias_n=entry["alias_n"],
                                   subject_user_id=entry["subject_user_id"])
    elif action in ("suspend_user", "ban_user"):
        user = g.db.query(User).filter_by(id=entry["target_user_id"]).first()
        if user is None:
            return None
        made = user.unban(admin=decider, reason=UNDO_REASON, notice_sent=True)
        action_log.link(g.db, made, entry["id"])
        return made
    else:
        return None
    g.db.add(ma)
    g.db.flush()
    return action_log.annotate(g.db, ma.id, reason=UNDO_REASON, linked_entry_id=entry["id"])


def _settle(appeal_id, outcome, note, decider, queue_url, permitted):
    """The one decision: refuses what the caller may not decide, runs the undo on `reversed`, records the outcome once and tells the person.
    `permitted` is the caller's own check of who may decide this appeal."""
    appeal = log_appeals.appeal_of(g.db, appeal_id)
    if appeal is None or not permitted(appeal):
        abort(404)
    if appeal["status"] != log_appeals.OPEN:
        return _bad_input(queue_url, "That appeal was already decided.")
    if log_appeals.conflicted(g.db, appeal, decider.id):
        return _bad_input(queue_url, "You made this decision: another can decide its appeal.")
    outcome = log_appeals.parse_outcome(outcome)
    note = log_appeals.parse_reason(note, log_appeals.NOTE_MAX)
    if outcome is None or not note:
        return _bad_input(queue_url, "Choose an outcome and say why, in a line the person will read.")
    entry = log_appeals.entry_of(g.db, appeal["entry_id"])
    reversal = None
    if outcome == log_appeals.REVERSED:
        reversal = _undo(entry, decider)
        if reversal is None:
            g.db.rollback()
            return _bad_input(queue_url, "That could not be undone (it may already have been put right by hand).")
    if not log_appeals.decide(g.db, appeal_id, outcome, decider.id, note, reversal_entry_id=reversal):
        g.db.rollback()
        return _bad_input(queue_url, "That appeal was already decided.")
    g.db.commit()
    person = g.db.query(User).filter_by(id=appeal["user_id"]).first()
    if person is not None and not person.is_deleted:
        log_notices.send(person, log_notices.outcome(outcome, entry_id=appeal["entry_id"], note=note))
    return _bad_input(queue_url, "Decided.")


@app.route("/admin/appeals/<int:appeal_id>", methods=["POST"])
@admin_level_required(3)                 # a limited sign-in has no admin level: the page asks for the password
@validate_formkey
def admin_appeal_decide(appeal_id, v):
    return _settle(appeal_id, request.form.get("outcome"), request.form.get("note"), v, "/admin/appeals",
                   lambda appeal: appeal["board_id"] is None)


@app.route("/+<guildname>/mod/appeals/<int:appeal_id>", methods=["POST"])
@auth_required
@not_qr_limited
@validate_formkey
def guild_appeal_decide(guildname, appeal_id, v):
    board = get_guild(guildname, v=v)
    m = _guild_mod(board, v)
    return _settle(appeal_id, request.form.get("outcome"), request.form.get("note"), v, f"/+{board.name}/mod/appeals",
                   lambda appeal: appeal["board_id"] == board.id and _may_decide_guild(m, appeal["action"]))


def _appeal_url(entry_id, user_id):
    """The appeal address for an entry this account may appeal now, else ''."""
    if not entry_id or getattr(g, "v", None) is not None and getattr(g.v, "qr_limited", False):
        return ""                                  # a limited sign-in is a stranger to the member's own items: it is shown none of them
    entry = log_appeals.entry_of(g.db, entry_id)
    return f"/log/{int(entry_id)}/appeal" if log_appeals.can_appeal(g.db, entry, user_id) is None else ""


def appeal_for_account(account, v):
    """Jinja global: the appeal address of the latest suspension or ban of `account`, for that account's own banned page, else ''."""
    if v is None or account is None or v.id != account.id or getattr(v, "qr_limited", False):
        return ""
    row = g.db.execute(text("SELECT id FROM log_entries WHERE target_user_id = :u AND action IN ('suspend_user', 'ban_user') "
                            "ORDER BY id DESC LIMIT 1"), {"u": account.id}).first()
    return _appeal_url(row[0] if row else None, v.id)


def appeal_for_item(item, v):
    """Jinja global: the appeal address of the latest hide or removal of a post or comment, for the person it is about, else ''. One
    small query, and only for someone looking at their own removed item."""
    if v is None or item is None or getattr(v, "qr_limited", False):
        return ""
    if isinstance(item, Comment):
        row = g.db.execute(text("SELECT id FROM log_entries WHERE target_comment_id = :i AND action IN "
                                "('hide_comment_from_guild', 'ban_comment', 'author_hide_comment') ORDER BY id DESC LIMIT 1"),
                           {"i": item.id}).first()
    else:
        row = g.db.execute(text("SELECT id FROM log_entries WHERE target_submission_id = :i AND action IN "
                                "('hide_post_from_guild', 'ban_post') ORDER BY id DESC LIMIT 1"), {"i": item.id}).first()
    return _appeal_url(row[0] if row else None, v.id)


app.jinja_env.globals.update(appeal_for_account=appeal_for_account, appeal_for_item=appeal_for_item)


def waiting_for_admins():
    """The count beside Admin > Appeals on the admin page."""
    return log_appeals.waiting_count(g.db, None)


def log_problems():
    """How many entries the Log could not write or copy lately (and how many mod-log rows still have no entry): the red note on the admin
    page. 0 when all is well."""
    return action_log.failure_count(g.db) + action_log.unmirrored_count(g.db)


app.jinja_env.globals.update(appeals_waiting=waiting_for_admins, log_problems=log_problems)
