"""The ban reveal's pages (the Log, slice D3d; helpers/log_evidence.py has the rules and the SQL).

`GET /admin/evidence_items/<user id>`: the ban form's picker, the account's own items (anonymous ones, ones made anonymous and deleted accounts' included),
paginated, as JSON the form draws with `textContent`. `GET /admin/log_evidence` lists what is waiting for a second admin and `POST
/admin/log_evidence/<entry id>` approves or declines it: **never the admin who proposed it**, except the level-4 owner when no other admin exists (decision 27,
recorded as a sole-admin approval). Every decision is a staff-only entry. The ban form itself (`ban_user`, routes/admin_api.py) proposes the evidence.

Not `log_evidence.py`: the star import of a routes package would put a module of that name over the helper."""
import time

from flask import abort, g, jsonify, redirect, render_template, request
from sqlalchemy import text

from ruqqus.__main__ import app
from ruqqus.classes import User
from ruqqus.helpers import action_log, log_evidence, mass_edit
from ruqqus.helpers.base36 import base36encode
from ruqqus.helpers.i18n_runtime import msg
from ruqqus.helpers.wrappers import admin_level_required, page_number, validate_formkey


@app.route("/admin/evidence_items/<int:user_id>", methods=["GET"])
@admin_level_required(3)
def admin_evidence_items(user_id, v):
    """The account's items for the ban form's picker. An admin who couldn't ban the account (the same rule as the ban: only below their own level) is
    told nothing about it."""
    user = g.db.query(User).filter_by(id=user_id).first()
    if user is None or v.admin_level <= user.admin_level:
        abort(404)
    rows, more, private = log_evidence.items_of(g.db, user_id, page_number())
    return jsonify({
        "rows": [{"token": mass_edit.token(r["kind"], base36encode(r["id"])), "kind": r["kind"], "title": r["title"], "anonymous": r["anonymous"],
                  "deleted": r["deleted"], "created": r["created"], "citable": r["citable"], "why": msg(r["why"]) if r["why"] else ""} for r in rows],
        "more": more, "private": private, "max": log_evidence.MAX_ITEMS})


def _waiting():
    """The proposals waiting for a second admin, each with what this reader may do about it and the items by their short ids."""
    out = []
    for w in log_evidence.waiting(g.db):
        w["items"] = [(kind, base36encode(item)) for kind, item in w["items"]]
        out.append(w)
    return out


@app.route("/admin/log_evidence", methods=["GET"])
@admin_level_required(3)
def admin_log_evidence(v):
    """What admins cited for their bans, waiting for a second admin to approve or decline naming the authors in the Log."""
    waiting = _waiting()
    others = log_evidence.another_admin_exists(g.db, v.id)
    for w in waiting:
        w["can_decide"] = log_evidence.approval(proposer_id=w["proposed_by"], admin_id=v.id, admin_level=v.admin_level, another_admin_exists=others)[0]
        w["mine"] = w["proposed_by"] == v.id
    return render_template("admin/log_evidence.html", v=v, waiting=waiting, msg=request.args.get("done", ""))


@app.route("/admin/log_evidence/<int:entry_id>", methods=["POST"])
@admin_level_required(3)
@validate_formkey
def admin_log_evidence_decide(entry_id, v):
    """Approve or decline the reveal for one ban. One conditional update, so two admins deciding at once decide once."""
    approve = request.form.get("decision") == "approve"
    now = int(time.time())
    target = g.db.execute(text("SELECT target_user_id FROM log_evidence WHERE entry_id = :e AND status = 'pending' LIMIT 1"), {"e": entry_id}).scalar()
    changed, why = log_evidence.decide(g.db, entry_id, v.id, v.admin_level, approve, now)
    if not changed:
        g.db.rollback()
        return render_template("message.html", v=v, title=msg("Nothing was changed"), message=msg(why) if why else msg(log_evidence.NOTHING_TO_DECIDE)), 403
    sole = bool(g.db.execute(text("SELECT sole_admin FROM log_evidence WHERE entry_id = :e AND decided_by = :a ORDER BY id DESC LIMIT 1"),
                             {"e": entry_id, "a": v.id}).scalar())
    action_log.record_staff(g.db, "admin_evidence", v, f"{'approved' if approve else 'declined'} naming the author of {changed} cited item(s) for entry {entry_id}"
                            + (" (a sole-admin approval: no other admin exists)" if sole else ""), target_user_id=target)
    g.db.commit()
    return redirect("/admin/log_evidence?done=" + ("approved" if approve else "declined"))


def waiting_evidence():
    """How many bans have evidence waiting for a second admin (the count beside the link on /admin); 0 where the table isn't there yet."""
    try:
        return len(log_evidence.waiting(g.db))
    except Exception:
        g.db.rollback()
        return 0


app.jinja_env.globals.update(log_evidence_waiting=waiting_evidence)
