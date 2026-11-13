"""Reporting a message in a private chat (rules in helpers/chat_reports.py, SQL in helpers/chat_report_store.py; named
`_pages` so the star import of ruqqus.classes can't replace the helpers). A member of the chat sends the reported message
and the few before it as their browser shows them; the site checks through its bot that each is a message of that chat
and takes who sent it and when from the chat server. Admins (level 3) read them at /admin/chat_reports, in English."""
import secrets
import time

from flask import *

from ruqqus.helpers.wrappers import *
from ruqqus.helpers.get import *
from ruqqus.classes import *
import ruqqus.helpers.matrix_client as matrix
from ruqqus.__main__ import app, limiter, member_limit_key

from ruqqus.helpers import chat_rooms
from ruqqus.helpers import chat_report_store as report_store
from ruqqus.helpers import chat_reports as report_rules
from ruqqus.helpers.i18n_runtime import tr

_HOUR, _DAY = 3600, 86400


def _error(message, status=400):
    return jsonify({"error": message}), status


def _user_ids(mxids):
    rows = g.db.query(ChatIdentity.matrix_user_id, ChatIdentity.user_id).filter(ChatIdentity.matrix_user_id.in_(list(mxids))).all() \
        if mxids else []
    return dict(rows)


@app.route("/api/chat/report", methods=["POST"])
@limiter.limit("20/hour", key_func=member_limit_key)
@auth_required
@not_qr_limited
@not_child_locked("chat")
@validate_formkey
def chat_report(v):
    room_id = str(request.values.get("room") or "")
    event_id = str(request.values.get("event_id") or "")
    if not report_rules.ROOM_ID.match(room_id) or not report_rules.EVENT_ID.match(event_id):
        abort(404)
    kind = chat_rooms.kind_for(g.db, room_id, v.id)
    if kind is None:
        abort(404)                              # only the chat's own members report its messages
    try:
        given = report_rules.parse_context(request.values.get("messages"), event_id)
    except report_rules.Refused as e:
        return _error(str(e))
    now = int(time.time())
    try:
        found = [(eid, words, report_rules.checked(matrix.event(room_id, eid), room_id)) for eid, words in given]
    except matrix.MatrixError:
        return _error(tr("Chat isn't answering right now. Try again in a moment."), 502)
    reported = found[-1][2]
    me = matrix.mxid_for(v.id)
    refusal = report_rules.refusal(me, reported[0] if reported else None,
                                   report_store.sent_since(g.db, v.id, now - _HOUR), report_store.sent_since(g.db, v.id, now - _DAY),
                                   report_store.already(g.db, v.id, event_id))
    if refusal:
        return _error(refusal, 409)
    kept = [(eid, words, info) for eid, words, info in found if info is not None]   # a message deleted since is left out
    ids = _user_ids({info[0] for _, _, info in kept})
    report_store.create(g.db, v.id, room_id, kind, event_id, ids.get(reported[0]), report_rules.clean_reason(request.values.get("reason")),
                        [(eid, ids.get(info[0]), info[0], info[1], words) for eid, words, info in kept], now)
    g.db.commit()
    return jsonify({"ok": True})


# --- admins (English) ---------------------------------------------------------------------------------------------

@app.route("/admin/chat_reports", methods=["GET"])
@admin_level_required(3)
def admin_chat_reports(v):
    reports = []
    rows = report_store.open_reports(g.db)
    people = set()
    for row in rows:
        people.add(row[5])
    shown = []
    for row in rows:
        messages = report_store.messages_of(g.db, row[0])
        people.update(m[2] for m in messages)
        shown.append((row, messages))
    users = {u.id: u for u in g.db.query(User).filter(User.id.in_([p for p in people if p])).all()} if people else {}
    for row, messages in shown:
        reports.append({
            "id": row[0], "kind": row[3], "reason": row[6], "created_utc": row[7],
            "reported": users.get(row[5]),
            "messages": [{"reported": m[1] == row[4], "sender": users.get(m[2]), "sender_mxid": m[3], "sent_utc": m[4] // 1000,
                          "body": m[5]} for m in messages],
        })
    return render_template("admin/chat_reports.html", v=v, reports=reports)


@app.route("/admin/chat_reports/<int:rid>/<action>", methods=["POST"])
@admin_level_required(3)
@validate_formkey
def admin_chat_report_act(rid, action, v):
    row = report_store.report(g.db, rid)
    if row is None or action not in ("remove", "done", "dismiss"):
        abort(404)
    if row[8] != report_rules.OPEN:
        return redirect("/admin/chat_reports")
    if action == "remove":
        try:
            matrix.redact_as_bot(row[2], row[4], secrets.token_hex(12), "Removed by the site's admins")
        except matrix.MatrixError:
            return _error("Chat isn't answering right now. Try again in a moment.", 502)     # an admin page: English
    status = report_rules.DISMISSED if action == "dismiss" else report_rules.ACTIONED
    report_store.resolve(g.db, rid, status, v.id, int(time.time()))
    g.db.commit()
    return redirect("/admin/chat_reports")
