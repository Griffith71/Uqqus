"""Drafts and scheduled posts: the Save draft / Schedule / Drafts controls on
Create a post. Publishing a scheduled draft is scripts/publish_scheduled.py."""
import time

from flask import g, jsonify, request

from ruqqus.__main__ import app, limiter
from ruqqus.classes import PostDraft
from ruqqus.helpers import post_drafts as pd
from ruqqus.helpers.wrappers import auth_required, validate_formkey


def _mine(v, did):
    return g.db.query(PostDraft).filter_by(id=did, user_id=v.id).first()


def _open_drafts(v):
    return g.db.query(PostDraft).filter(
        PostDraft.user_id == v.id, PostDraft.status.in_(pd.EDITABLE + ("publishing",)))


@app.get("/api/drafts")
@auth_required
def list_drafts(v):
    """Your drafts and scheduled posts, most recently changed first."""
    rows = _open_drafts(v).order_by(PostDraft.updated_utc.desc(), PostDraft.id.desc()).all()
    return jsonify({"drafts": [row.json for row in rows], "limit": pd.DRAFT_LIMIT})


@app.post("/api/drafts")
@limiter.limit("30/minute")
@auth_required
@validate_formkey
def save_draft(v):
    """Save the composer as a draft, or schedule it.

Form data: the composer's own fields (`title`, `url`, `body`, `forward_guilds`,
`comment_permission`, `paid_partnership`, `made_with_ai`, `sensitive`), plus:
* `draft_id` - Update this draft instead of making a new one.
* `publish_utc` - Epoch seconds. When given, the draft is scheduled for then
  (5 minutes to a year ahead, and it needs a title); without it the draft is
  saved unscheduled. Saving always unschedules a draft that was scheduled.
"""
    try:
        fields = pd.clean_fields(request.form)
        if pd.is_empty(fields):
            raise pd.PostFieldError("There is nothing to save yet.")

        now = int(time.time())
        raw_when = (request.form.get("publish_utc") or "").strip()
        when = None
        if raw_when:
            pd.require_title(fields)
            when = pd.schedule_time(raw_when, now)
    except pd.PostFieldError as e:
        return jsonify({"error": str(e)}), 400

    draft = None
    raw_id = (request.form.get("draft_id") or "").strip()
    if raw_id:
        draft = _mine(v, int(raw_id)) if raw_id.isdigit() else None
        if draft is None or draft.status == "published":
            draft = None   # gone or already published: this save becomes a new draft
        elif draft.status == "publishing":
            return jsonify({"error": "That post is being published right now."}), 409

    if draft is None:
        if _open_drafts(v).count() >= pd.DRAFT_LIMIT:
            return jsonify({"error": f"You can keep {pd.DRAFT_LIMIT} drafts. Delete one to save another."}), 400
        draft = PostDraft(user_id=v.id, created_utc=now)

    draft.set_fields(fields)
    draft.status = "scheduled" if when else "draft"
    draft.publish_utc = when
    draft.attempts = 0
    draft.claimed_utc = None
    draft.error = ""
    draft.published_post_id = None
    draft.creation_ip = request.remote_addr or ""
    draft.creation_region = (request.headers.get("cf-ipcountry") or "")[:2] or None
    draft.updated_utc = now
    g.db.add(draft)
    g.db.flush()

    return jsonify({"id": draft.id, "status": draft.status, "publish_utc": draft.publish_utc})


@app.post("/api/drafts/<int:did>/unschedule")
@auth_required
@validate_formkey
def unschedule_draft(did, v):
    """Turn a scheduled post back into a plain draft."""
    draft = _mine(v, did)
    if not draft or draft.status not in ("scheduled", "failed"):
        return jsonify({"error": "That post isn't scheduled."}), 404
    draft.status = "draft"
    draft.publish_utc = None
    draft.updated_utc = int(time.time())
    g.db.add(draft)
    return jsonify(draft.json)


@app.post("/api/drafts/<int:did>/delete")
@auth_required
@validate_formkey
def delete_draft(did, v):
    """Delete a draft or cancel a scheduled post."""
    draft = _mine(v, did)
    if not draft:
        return jsonify({"error": "That draft doesn't exist."}), 404
    if draft.status == "publishing":
        return jsonify({"error": "That post is being published right now."}), 409
    g.db.delete(draft)
    return jsonify({"deleted": did})
