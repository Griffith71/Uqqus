"""Reporting an account, and the admins' queue. Rules and SQL in helpers/user_reports.py; the sheet is
partials/report_account_modal.html, opened from a profile's "⋯" menu."""
from flask import abort, g, jsonify, redirect, render_template, request

from ruqqus.helpers import user_reports
from ruqqus.helpers.get import get_user
from ruqqus.helpers.visibility import user_hidden
from ruqqus.helpers.wrappers import admin_level_required, auth_required, validate_formkey
from ruqqus.__main__ import app


@app.post("/api/user/<username>/report")
@auth_required
@validate_formkey
def report_account(username, v):
    target = get_user(username, v=v, graceful=True)
    if not target or user_hidden(target, v):
        abort(404)
    refused = user_reports.refusal(v.id, target.id, target.is_deleted)
    if refused:
        return jsonify({"error": refused[0]}), refused[1]
    reason, error = user_reports.parse_reason(request.form.get("reason"))
    if error:
        return jsonify({"error": error}), 400
    if user_reports.filed_today(g.db, v.id) >= user_reports.DAILY_LIMIT:
        return jsonify({"error": "You've sent a lot of reports today. Try again tomorrow."}), 429
    if not user_reports.report(g.db, target.id, v.id, reason):
        return jsonify({"error": "You already reported this account."}), 409
    g.db.commit()
    return jsonify({"message": "Thanks. An admin will look at it."})


@app.get("/admin/user_reports")
@admin_level_required(3)
def admin_user_reports(v):
    return render_template("admin/user_reports.html", v=v, reports=user_reports.open_reports(g.db))


@app.post("/admin/user_reports/<int:uid>/dismiss")
@admin_level_required(3)
@validate_formkey
def admin_user_reports_dismiss(uid, v):
    if not user_reports.resolve(g.db, uid):
        abort(404)
    g.db.commit()
    return redirect("/admin/user_reports")
