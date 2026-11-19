"""The signup guard's admin pages: /admin/signups (level 3) lists the accounts it is holding (helpers/quarantine.py) with
why, what each tried since, and the accounts it shares a network or a name pattern with; admins approve or ban them one
by one, ticked, or a whole cluster at once. Level 4 changes the threshold and the surge switch. The rules are
helpers/signup_risk.py, the counts helpers/signup_signals.py, the rows helpers/signup_review_store.py. English, as every
admin page; what a released member is told goes through `notify` in their language. Not `signup_guard.py`: a route
module named like a helper would be replaced by it in files that star-import both."""
import time

from flask import abort, g, redirect, render_template, request
from sqlalchemy import bindparam, text

from ruqqus.classes import User
from ruqqus.helpers import action_log, captcha, signup_review_store, signup_risk, signup_signals
from ruqqus.helpers.alerts import notify
from ruqqus.helpers.i18n_runtime import N_
from ruqqus.helpers.wrappers import admin_level_required, page_number, validate_formkey
from ruqqus.__main__ import app, r

BAN_REASON = "Bot account (signup guard)"
BULK_MAX = 200
RECENT_DAYS = 7
APPROVED_TOLD = N_("Your account has been checked and is ready: you can now post, comment, vote and follow. Welcome!")

ACTION_LABELS = {
    "post": "post", "edit_post": "edit a post", "comment": "comment", "vote": "vote", "forward": "forward",
    "repost": "repost", "tip": "tip", "follow": "follow", "join_guild": "join a guild", "chat": "start a chat",
    "group": "make a group", "report": "report", "poll_vote": "vote in a poll", "story": "share a story",
    "edit_comment": "edit a comment", "moderate": "moderate (hide a comment, act as a guildmaster)", "wiki": "change a guild's wiki",
    "event": "make or change a guild event", "space": "make a Space or write in one", "live_update": "write a live update",
    "channel": "write in a channel", "collab": "work on a shared draft", "coauthor": "invite or join as a co-author",
    "guild": "start or take over a guild", "curation": "make or change a curation", "region": "propose a region's name",
    "circle": "make or change a circle", "other": "other",
}


def _since():
    return int(time.time()) - signup_review_store.CLUSTER_DAYS * 86400


def _alt_counts(ids):
    if not ids:
        return {}
    statement = text(
        "SELECT u, count(*) FROM (SELECT user1 AS u FROM alts WHERE user1 IN :ids "
        "UNION ALL SELECT user2 AS u FROM alts WHERE user2 IN :ids) a GROUP BY u"
    ).bindparams(bindparam("ids", expanding=True))
    return dict(g.db.execute(statement, {"ids": list(ids)}).all())


def _dress(rows):
    """Reasons as labels, cluster sizes, alts and what each tried, for the template."""
    since = _since()
    networks = signup_review_store.cluster_sizes(g.db, "network", [row["network"] for row in rows], since)
    shapes = signup_review_store.cluster_sizes(g.db, "name_shape", [row["name_shape"] for row in rows], since)
    alts = _alt_counts({row["user_id"] for row in rows})
    for row in rows:
        row["labels"] = [signup_risk.label(key) for key in signup_risk.parse_reasons(row["reasons"])]
        row["network_size"] = networks.get(row["network"], 0)
        row["shape_size"] = shapes.get(row["name_shape"], 0) if signup_risk.shape_counts(row["name_shape"]) else 0
        row["alts"] = alts.get(row["user_id"], 0)
        row["tried"] = [(ACTION_LABELS.get(k, k), n) for k, n in
                        sorted(signup_signals.attempts(r, row["user_id"]).items(), key=lambda kv: -kv[1])]
    return rows


def _tally_rows(hours):
    counts = signup_signals.tallies(r, hours)
    made = counts.get("allow", 0) + counts.get("quarantine", 0)
    refused = sorted(((signup_risk.label(k), n) for k, n in counts.items() if k not in ("allow", "quarantine")),
                     key=lambda kv: -kv[1])
    return {"allowed": counts.get("allow", 0), "held": counts.get("quarantine", 0), "made": made,
            "refused": refused, "refused_total": sum(n for _, n in refused)}


@app.get("/admin/signups")
@admin_level_required(3)
def admin_signups(v):
    show = request.args.get("show", "waiting")
    show = show if show in ("waiting", "recent") else "waiting"
    page = page_number()
    if show == "waiting":
        rows, next_exists = signup_review_store.waiting(g.db, page)
    else:
        rows, next_exists = signup_review_store.recent(g.db, int(time.time()) - RECENT_DAYS * 86400, limit=200), False
    return render_template(
        "admin/signups.html", v=v, show=show, page=page, next_exists=next_exists, rows=_dress(rows),
        waiting=signup_review_store.waiting_count(g.db), day=_tally_rows(24), week=_tally_rows(7 * 24),
        settings=signup_signals.settings(r), captcha_provider=captcha.chosen(app.config),
        site_hour=signup_signals.recent(r, "all", "site"), msg=request.args.get("msg"),
        weights=sorted(((signup_risk.label(k), w) for k, w in signup_risk.WEIGHTS.items() if w), key=lambda kw: -kw[1]),
        refusals=[signup_risk.label(k) for k in signup_risk.REFUSALS],
    )


def _ids_from_form():
    ids = []
    for raw in request.form.getlist("uid")[:BULK_MAX]:
        if raw.isascii() and raw.isdigit():
            ids.append(int(raw))
    return ids


def _act(v, ids, decision, with_alts=False):
    """Approve or ban the held accounts among `ids`; returns how many."""
    now = int(time.time())
    if decision == "approve":
        done = signup_review_store.decide(g.db, ids, "approved", v.id, now)
        g.db.commit()
        for uid in done:
            signup_signals.forget_attempts(r, uid)
            user = g.db.query(User).filter_by(id=uid).first()
            if user:
                action_log.record_staff(g.db, "admin_signup", v, "approved a held sign-up", target_user_id=uid)
                g.db.commit()
                notify(user, APPROVED_TOLD)
        return len(done)
    if decision == "ban":
        # only the accounts this admin outranks: the review was closed as "banned" for an account the ban then skipped (the audit, S6)
        bannable = [uid for (uid,) in g.db.query(User.id).filter(User.id.in_(ids or [0]), User.admin_level < (v.admin_level or 0)).all()]
        done = signup_review_store.decide(g.db, bannable, "banned", v.id, now)
        for uid in done:
            user = g.db.query(User).filter_by(id=uid).first()
            if not user:
                continue
            user.ban(admin=v, reason=BAN_REASON)
            if with_alts:
                for alt in user.alts:
                    if not alt.is_deleted and (alt.admin_level or 0) < (v.admin_level or 0):
                        alt.ban(admin=v, reason=BAN_REASON, staff_note=f"alt of @{user.username}", floor=action_log.STAFF)
        g.db.commit()
        return len(done)
    abort(400)


@app.post("/admin/signups/decide")
@admin_level_required(3)
@validate_formkey
def admin_signups_decide(v):
    count = _act(v, _ids_from_form(), request.form.get("decision", ""), request.form.get("with_alts") == "1")
    return redirect(f"/admin/signups?msg={count}+account{'s' if count != 1 else ''}+done.")


@app.post("/admin/signups/cluster")
@admin_level_required(3)
@validate_formkey
def admin_signups_cluster(v):
    # the button that sent the form names the cluster ("network:<hash>" or "name_shape:<shape>") and what to do with it
    decision, _, cluster = (request.form.get("cluster") or "").partition("|")
    column, _, value = cluster.partition(":")
    if column not in ("network", "name_shape") or not value:
        abort(400)
    ids = signup_review_store.cluster_waiting(g.db, column, value, _since())[:BULK_MAX]
    count = _act(v, ids, decision, request.form.get("with_alts") == "1")
    return redirect(f"/admin/signups?msg={count}+account{'s' if count != 1 else ''}+in+the+cluster+done.")


@app.post("/admin/signups/settings")
@admin_level_required(4)
@validate_formkey
def admin_signups_settings(v):
    try:
        quarantine_at = int(request.form.get("quarantine_at", ""))
        surge_ceiling = int(request.form.get("surge_ceiling", ""))
    except ValueError:
        abort(400)
    signup_signals.save_settings(r, quarantine_at, request.form.get("surge") == "1", surge_ceiling)
    action_log.record_staff(g.db, "admin_signup", v, f"signup guard: hold at {quarantine_at}, surge {'on' if request.form.get('surge') == '1' else 'off'}, "
                                                     f"ceiling {surge_ceiling}")
    g.db.commit()
    return redirect("/admin/signups?msg=Settings+saved.")


def signups_waiting():
    """The count beside Admin > Held sign-ups on the admin page."""
    return signup_review_store.waiting_count(g.db)


app.jinja_env.globals.update(signups_waiting=signups_waiting)
