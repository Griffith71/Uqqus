"""Community notes: the admins' side (helpers/community_notes.py).

Members ask for a note from the flag menu (routes/flagging.py). Here an admin sees what was
asked for most, writes a note or dismisses the requests, and can take a note down. A note
names the site's moderators, never the admin who wrote it, and requesters are never shown."""
import time
from urllib.parse import quote

from flask import abort, g, redirect, render_template, request
from jinja2 import Undefined
from sqlalchemy import func

from ruqqus.classes import Comment, CommunityNote, NoteRequest, Submission, User
from ruqqus.helpers import community_notes
from ruqqus.helpers.alerts import send_notification
from ruqqus.helpers.wrappers import admin_level_required, validate_formkey
from ruqqus.__main__ import app

SHOWN = 50           # most-requested items on the page
NOTIFY_LIMIT = 200   # requesters told when a note is added


def _back(message=None, error=None):
    if error:
        return redirect("/admin/note_requests?error=" + quote(error))
    return redirect("/admin/note_requests?msg=" + quote(message or "Saved."))


def _target(kind, target_id):
    """(the post or comment, the id requests and notes are kept under) or 404."""
    if kind == "post":
        post = g.db.query(Submission).filter_by(id=target_id).first()
        if not post:
            abort(404)
        return post, community_notes.primary_post_id(post)
    if kind == "comment":
        comment = g.db.query(Comment).filter_by(id=target_id).first()
        if not comment:
            abort(404)
        return comment, comment.id
    abort(404)


def _filter(kind, key):
    """Conditions picking one target's rows in note_requests or community_notes."""
    return (lambda model: model.post_id == key) if kind == "post" else (lambda model: model.comment_id == key)


def _excerpt(text, limit=160):
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


@app.get("/admin/note_requests")
@admin_level_required(3)
def admin_note_requests(v):
    """What members asked a community note for, most asked first, and the live notes."""
    rows = g.db.query(
        NoteRequest.post_id, NoteRequest.comment_id, func.count(NoteRequest.id), func.max(NoteRequest.created_utc)
    ).group_by(NoteRequest.post_id, NoteRequest.comment_id).order_by(
        func.count(NoteRequest.id).desc(), func.max(NoteRequest.created_utc).desc()
    ).limit(SHOWN).all()

    requested = []
    for post_id, comment_id, count, last in rows:
        kind, key = ("post", post_id) if post_id is not None else ("comment", comment_id)
        item = g.db.query(Submission if kind == "post" else Comment).filter_by(id=key).first()
        if item is None:
            continue
        existing = g.db.query(CommunityNote).filter(
            (CommunityNote.post_id == key) if kind == "post" else (CommunityNote.comment_id == key),
            CommunityNote.removed_utc == 0).first()
        requested.append({
            "kind": kind, "id": key, "count": count, "last": last, "link": item.permalink,
            "text": _excerpt(item.title if kind == "post" else item.body), "note": existing,
        })

    live = g.db.query(CommunityNote).filter(CommunityNote.removed_utc == 0).order_by(CommunityNote.id.desc()).limit(SHOWN).all()
    live_rows = []
    for note in live:
        kind, key = ("post", note.post_id) if note.post_id is not None else ("comment", note.comment_id)
        item = g.db.query(Submission if kind == "post" else Comment).filter_by(id=key).first()
        live_rows.append({"note": note, "kind": kind, "link": item.permalink if item else None,
                          "text": _excerpt(item.title if kind == "post" else item.body) if item else "(gone)"})

    return render_template(
        "admin/note_requests.html", v=v, requested=requested, live=live_rows, limit=community_notes.MAX_CHARS,
        msg=request.args.get("msg"), error=request.args.get("error"),
    )


@app.post("/admin/note/<kind>/<int:target_id>")
@admin_level_required(3)
@validate_formkey
def admin_note_write(kind, target_id, v):
    """Write the note for a post or comment (replacing a live one, which is kept as removed)
    and tell the people who asked."""
    item, key = _target(kind, target_id)
    try:
        body = community_notes.clean_body(request.form.get("body"))
    except community_notes.NoteError as error:
        return _back(error=error.message)

    now = int(time.time())
    where = _filter(kind, key)
    g.db.query(CommunityNote).filter(where(CommunityNote), CommunityNote.removed_utc == 0).update({"removed_utc": now})
    g.db.add(CommunityNote(
        post_id=key if kind == "post" else None, comment_id=key if kind == "comment" else None,
        body=body, admin_id=v.id, created_utc=now))
    requesters = [r for (r,) in g.db.query(NoteRequest.user_id).filter(where(NoteRequest)).limit(NOTIFY_LIMIT).all()]
    g.db.query(NoteRequest).filter(where(NoteRequest)).delete(synchronize_session=False)
    g.db.commit()
    community_notes.forget()

    # best effort and the link only: nothing about the author, and one failure tells nobody else off
    for user in g.db.query(User).filter(User.id.in_(requesters), User.is_deleted == False).all():
        try:
            send_notification(user, f"A community note was added to a {kind} you asked about: [view it]({item.permalink})")
        except Exception:
            g.db.rollback()
    return _back("Note added.")


@app.post("/admin/note_requests/dismiss/<kind>/<int:target_id>")
@admin_level_required(3)
@validate_formkey
def admin_note_dismiss(kind, target_id, v):
    """Close the requests for something without writing a note."""
    _, key = _target(kind, target_id)
    g.db.query(NoteRequest).filter(_filter(kind, key)(NoteRequest)).delete(synchronize_session=False)
    g.db.commit()
    return _back("Requests dismissed.")


@app.post("/admin/note/<int:note_id>/remove")
@admin_level_required(3)
@validate_formkey
def admin_note_remove(note_id, v):
    note = g.db.query(CommunityNote).filter_by(id=note_id, removed_utc=0).first()
    if not note:
        abort(404)
    note.removed_utc = int(time.time())
    g.db.add(note)
    g.db.commit()
    community_notes.forget()
    return _back("Note removed.")


def _note_of(item):
    return community_notes.note_of(None if isinstance(item, Undefined) else item)


app.jinja_env.globals.update(community_note_of=_note_of)
