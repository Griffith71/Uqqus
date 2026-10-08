"""Stories (rules: helpers/stories.py, database side: helpers/story_store.py).

Every answer is built from `story_store.watchable` / `highlight_stories`, the one place that decides what a viewer may watch
(audience, blocks, word filter), and is `private, no-store`. Stories are for signed-in members. Making, deleting and
reporting need the form key; the author is always the signed-in member (`v.id`), never a field of the request."""
import time

from flask import abort, g, jsonify, redirect, render_template, request

from ruqqus.classes import MediaAsset
from ruqqus.helpers import circles, stories as rules, story_store
from ruqqus.helpers.get import get_user
from ruqqus.helpers.media import attach as media_attach, safety as media_safety
from ruqqus.helpers.visibility import user_hidden
from ruqqus.helpers.word_filter_store import get_filter
from ruqqus.helpers.wrappers import admin_level_required, auth_required, is_not_banned, validate_formkey
from ruqqus.__main__ import app


def _fail(message, status=400):
    return jsonify({"error": message}), status


def _private(data, status=200):
    response = jsonify(data)
    response.status_code = status
    response.headers["Cache-Control"] = "private, no-store"
    return response


def _owner(username, v):
    """The account whose stories are asked for. Unknown, deleted, banned or word-filtered is a 404 (one answer for all)."""
    owner = get_user(username or "", v=v, graceful=True)
    if owner is None or owner.is_deleted or (owner.is_banned and not owner.unban_utc) or user_hidden(owner, v):
        abort(404)
    return owner


def _story(row, viewer, seen, counts, now):
    data = {
        "id": row.id, "kind": row.kind, "text": row.body, "background": row.background,
        "picture": story_store.picture_of(g.db, row.id) if row.kind == rules.IMAGE else None,
        "video": row.video_ref or None, "audience": circles.NAMES.get(row.audience, "Public"),
        "created": row.created_utc, "left": rules.describe_left(row.expires_utc, now),
        "live": rules.is_live(row.expires_utc, row.deleted_utc, now), "seen": row.id in seen,
    }
    if viewer is not None and viewer.id == row.user_id:
        data["views"] = counts.get(row.id, 0)             # only ever a number: nobody is named
    return data


def _stories(rows, viewer, now):
    seen = story_store.seen_ids(g.db, viewer.id, [r.id for r in rows])
    counts = story_store.view_counts(g.db, [r.id for r in rows if r.user_id == viewer.id])
    return [_story(r, viewer, seen, counts, now) for r in rows]


def _user(owner):
    return {"username": owner.username, "avatar": owner.profile_url, "permalink": owner.permalink}


# ------------------------------------------------------------------ watching

@app.route("/api/stories/<username>", methods=["GET"])
@auth_required
def stories_of(username, v):
    owner = _owner(username, v)
    rows = story_store.watchable(g.db, owner.id, v)
    return _private({"user": _user(owner), "mine": owner.id == v.id, "stories": _stories(rows, v, int(time.time()))})


@app.route("/api/stories/<int:sid>/view", methods=["POST"])
@auth_required
@validate_formkey
def story_view(sid, v):
    row = story_store.get(g.db, sid)
    if row is None:
        abort(404)
    if row.user_id == v.id:
        return _private({"ok": True})
    if not any(r.id == sid for r in story_store.watchable(g.db, row.user_id, v)):
        abort(404)
    story_store.mark_seen(g.db, sid, v.id)
    g.db.commit()
    return _private({"ok": True})


@app.route("/api/highlights/<int:hid>", methods=["GET"])
@auth_required
def highlight_of(hid, v):
    found = story_store.highlight_stories(g.db, hid, v)
    if found is None:
        abort(404)
    highlight, rows = found
    if not rows:
        abort(404)
    owner = _owner_by_id(highlight.user_id, v)
    return _private({"id": highlight.id, "title": highlight.title, "user": _user(owner), "mine": owner.id == v.id,
                     "stories": _stories(rows, v, int(time.time()))})


def _owner_by_id(user_id, v):
    from ruqqus.classes import User
    owner = g.db.query(User).filter_by(id=user_id).first()
    if owner is None or owner.is_deleted or user_hidden(owner, v):
        abort(404)
    return owner


# ------------------------------------------------------------------ making, deleting, reporting

@app.route("/api/stories", methods=["POST"])
@is_not_banned
@validate_formkey
def story_create(v):
    kind, error = rules.parse_kind(request.form.get("kind"))
    if error:
        return _fail(error)
    audience, error = circles.parse_audience(request.form.get("audience"))
    if error:
        return _fail(error)
    body, error = rules.clean_text(request.form.get("text"))
    if error:
        return _fail(error)
    video_ref = rules.parse_video(request.form.get("video")) if kind == rules.VIDEO else ""
    picture = media_attach.own_asset(g.db, v.id, request.form.get("media"), kinds=("image",)) if kind == rules.IMAGE else None
    refusal = rules.refusal(kind, audience, body, picture is not None, video_ref)
    if refusal:
        return _fail(refusal)
    if story_store.count_today(g.db, v.id) >= rules.DAILY_MAX:
        return _fail(f"You can share {rules.DAILY_MAX} stories in a day. Try again later.", 429)

    severity = get_filter(g.db).severity(body) if body else 0           # the words go through the word filter like any text
    story_id = story_store.create(g.db, v.id, kind, body, rules.parse_background(request.form.get("background")), video_ref, audience, severity)
    if picture is not None and not story_store.attach_picture(g.db, story_id, picture.id, v.id):
        g.db.rollback()
        return _fail("That picture is already used somewhere else. Upload it again.")
    g.db.commit()
    if picture is not None:
        media_safety.scan_later([g.db.query(MediaAsset).filter_by(id=picture.id).first()])
    return _private({"id": story_id, "message": "Your story is up for 24 hours."})


@app.route("/api/stories/<int:sid>/delete", methods=["POST"])
@auth_required
@validate_formkey
def story_delete(sid, v):
    if not story_store.delete(g.db, sid, v.id):
        abort(404)
    g.db.commit()
    return _private({"message": "Story deleted."})


@app.route("/api/stories/<int:sid>/report", methods=["POST"])
@auth_required
@validate_formkey
def story_report(sid, v):
    row = story_store.get(g.db, sid)
    if row is None:
        abort(404)
    if row.user_id == v.id:
        return _fail("That's your own story.")
    if not any(r.id == sid for r in story_store.watchable(g.db, row.user_id, v)):
        abort(404)
    if not story_store.report(g.db, sid, v.id, rules.parse_reason(request.form.get("reason"))):
        return _fail("You already reported this story.", 409)
    g.db.commit()
    return _private({"message": "Thanks. A moderator will look at it."})


@app.route("/api/stories/archive", methods=["GET"])
@auth_required
def story_archive(v):
    rows = story_store.archive(g.db, v.id)
    return _private({"stories": _stories(rows, v, int(time.time()))})


# ------------------------------------------------------------------ highlights

@app.route("/api/highlights", methods=["POST"])
@is_not_banned
@validate_formkey
def highlight_create(v):
    title, error = rules.parse_title(request.form.get("title"))
    if error:
        return _fail(error)
    hid, error = story_store.create_highlight(g.db, v.id, title, request.form.getlist("story"))
    if error:
        g.db.rollback()
        return _fail(error)
    g.db.commit()
    return _private({"id": hid, "message": "Highlight saved."})


@app.route("/api/highlights/<int:hid>/rename", methods=["POST"])
@auth_required
@validate_formkey
def highlight_rename(hid, v):
    title, error = rules.parse_title(request.form.get("title"))
    if error:
        return _fail(error)
    if not story_store.rename_highlight(g.db, v.id, hid, title):
        abort(404)
    g.db.commit()
    return _private({"message": "Renamed."})


@app.route("/api/highlights/<int:hid>/delete", methods=["POST"])
@auth_required
@validate_formkey
def highlight_delete(hid, v):
    if not story_store.delete_highlight(g.db, v.id, hid):
        abort(404)
    g.db.commit()
    return _private({"message": "Highlight deleted. Its stories are still in your archive."})


# ------------------------------------------------------------------ what a profile draws

def story_ring(owner, viewer):
    """The ring around an avatar for this viewer: "unseen", "seen", "own" (the member's own live stories) or None."""
    if viewer is None:
        return None
    if viewer.id == owner.id:
        return "own" if story_store.watchable(g.db, owner.id, viewer) else None
    return story_store.rings(g.db, [owner.id], viewer).get(owner.id)


def profile_highlights(owner, viewer):
    """The Highlights under a bio that this viewer may see anything of: id, name and how to draw the cover."""
    if viewer is None:
        return []
    out = []
    for hid, title, rows in story_store.highlights(g.db, owner.id, viewer):
        first = rows[0]
        out.append({"id": hid, "title": title, "picture": story_store.picture_of(g.db, first.id) if first.kind == rules.IMAGE else None,
                    "background": first.background if first.kind == rules.TEXT else ""})
    return out


app.jinja_env.globals.update(story_ring=story_ring, profile_highlights=profile_highlights, story_backgrounds=rules.BACKGROUNDS)


# ------------------------------------------------------------------ the moderators' queue

@app.route("/admin/story_reports", methods=["GET"])
@admin_level_required(3)
def admin_story_reports(v):
    reports = [dict(row._mapping, picture=story_store.picture_of(g.db, row.story_id) if row.kind == rules.IMAGE else None)
               for row in story_store.open_reports(g.db)]
    return render_template("admin/story_reports.html", v=v, reports=reports)


@app.route("/admin/story_reports/<int:sid>/remove", methods=["POST"])
@admin_level_required(3)
@validate_formkey
def admin_story_remove(sid, v):
    if story_store.get(g.db, sid) is None:
        abort(404)
    story_store.remove(g.db, sid)
    story_store.resolve_reports(g.db, sid)
    g.db.commit()
    return redirect("/admin/story_reports")


@app.route("/admin/story_reports/<int:sid>/dismiss", methods=["POST"])
@admin_level_required(3)
@validate_formkey
def admin_story_dismiss(sid, v):
    if story_store.get(g.db, sid) is None:
        abort(404)
    story_store.resolve_reports(g.db, sid)
    g.db.commit()
    return redirect("/admin/story_reports")
