"""Admin > Chat health (/admin/chat): whether the site's database and Synapse's still belong together
(helpers/chat_health.py), and giving a chat account back to the member it belongs to. English, like every admin page.
Named `chat_health_pages.py`, not `chat_health.py`: `from ruqqus.routes import *` would put the module where the helper
of the same name is expected."""
from flask import abort, g, redirect, render_template, request

from ruqqus.classes import ChatIdentity, User
from ruqqus.helpers import action_log, chat_health, chat_signouts
from ruqqus.helpers.wrappers import admin_level_required, validate_formkey
import ruqqus.helpers.matrix_client as matrix
from ruqqus.__main__ import app, r


def chat_health_problem():
    """For the admin page's link: True while the last check found something to put right (no check made: False)."""
    report = chat_health.cached(r)
    return bool(report and not report.get("ok"))


app.jinja_env.globals.update(chat_health_problem=chat_health_problem)


@app.route("/admin/chat", methods=["GET"])
@admin_level_required(3)
def admin_chat_health(v):
    report = chat_health.run(g.db, r, fix=True)
    refused = []
    if report.get("refused"):
        refused = g.db.query(User).filter(User.id.in_(report["refused"])).order_by(User.id).all()
    return render_template("admin/chat_health.html", v=v, report=report, refused=refused,
                           signouts=chat_signouts.pending_summary(g.db), late_minutes=chat_signouts.LATE_SECONDS // 60,
                           server_name=app.config["MATRIX_SERVER_NAME"])


@app.route("/admin/chat/give_back", methods=["POST"])
@admin_level_required(4)
@validate_formkey
def admin_chat_give_back(v):
    """The chat account Synapse holds for this id is the same person's (an admin judged it): the site records it as
    theirs. Only for an account that exists on the chat server and that the site has no record of."""
    try:
        user_id = int(request.form.get("user_id", ""))
    except ValueError:
        abort(400)
    user = g.db.query(User).filter_by(id=user_id).first()
    if not user or user.is_deleted:
        abort(404)
    if g.db.query(ChatIdentity).filter_by(user_id=user_id).first():
        return redirect("/admin/chat")
    mxid = matrix.mxid_for(user_id)
    if not matrix.account_exists(mxid):
        abort(409)
    g.db.add(ChatIdentity(user_id=user_id, matrix_user_id=mxid))
    action_log.record_quietly(g.db, "chat_account_returned", v.id, target_user_id=user_id, floor=action_log.STAFF,
                              staff_note=f"{mxid} was on the chat server with no record on the site; given back to @{user.username}.")
    g.db.commit()
    if r is not None:
        r.srem(chat_health.REFUSED_KEY, user_id)
    return redirect("/admin/chat")
