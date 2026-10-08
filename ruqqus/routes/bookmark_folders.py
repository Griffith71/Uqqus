"""Bookmark folders (helpers/bookmark_folders.py): the routes.

A member's folders for what they bookmarked, private to them. A bookmark is a `SaveRelationship`
(post) or `CommentSaveRelationship` (comment) and sits in one folder (`folder_id`) or none.
Everything here is the caller's own: a folder or bookmark of someone else is a 404."""
import re
import time

from flask import abort, g, jsonify, request
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from ruqqus.classes import BookmarkFolder, CommentSaveRelationship, SaveRelationship
from ruqqus.helpers import bookmark_folders as rules
from ruqqus.helpers.get import get_comment, get_post
from ruqqus.helpers.visibility import comment_hidden, post_hidden
from ruqqus.helpers.wrappers import auth_required, validate_formkey
from ruqqus.__main__ import app

BASE36 = re.compile(r"^[0-9a-z]{1,10}$")


def _folders(v):
    return g.db.query(BookmarkFolder).filter_by(user_id=v.id).order_by(func.lower(BookmarkFolder.name)).all()


def _folder_or_404(v, folder_id):
    folder = g.db.query(BookmarkFolder).filter_by(id=folder_id, user_id=v.id).first()
    if not folder:
        abort(404)
    return folder


def _target(v, kind, ident):
    """The post or comment `ident` names, as `v` may see it, or a 404."""
    if kind not in ("post", "comment") or not BASE36.match(ident or ""):
        abort(404)
    if kind == "post":
        item = get_post(ident, v=v, graceful=True)
        if not item or post_hidden(item, v):
            abort(404)
    else:
        item = get_comment(ident, v=v, graceful=True)
        if not item or comment_hidden(item, v):
            abort(404)
    return item


def _bookmark(v, kind, item):
    """v's existing bookmark of `item`, or None."""
    if kind == "post":
        return g.db.query(SaveRelationship).filter_by(user_id=v.id, submission_id=item.id).first()
    return g.db.query(CommentSaveRelationship).filter_by(user_id=v.id, comment_id=item.id).first()


def _name_from_request():
    try:
        return rules.clean_name(request.values.get("name")), None
    except rules.FolderError as error:
        return None, (jsonify({"error": error.message}), 400)


@app.route("/bookmarks/folders", methods=["GET"])
@auth_required
def bookmark_folders_list(v):
    """The member's folders, for the picker; with `?kind=post|comment&id=<id>` also where that
    item is now (`current`: a folder id or null) and whether it is bookmarked at all."""
    out = {"folders": [{"id": f.id, "name": f.name} for f in _folders(v)], "limit": rules.MAX_FOLDERS}

    kind, ident = request.args.get("kind"), request.args.get("id")
    if kind and ident:
        row = _bookmark(v, kind, _target(v, kind, ident))
        out["bookmarked"] = bool(row)
        out["current"] = row.folder_id if row else None

    return jsonify(out)


@app.route("/bookmarks/folders", methods=["POST"])
@auth_required
@validate_formkey
def bookmark_folders_create(v):
    name, error = _name_from_request()
    if error:
        return error

    existing = [f.name for f in _folders(v)]
    problem = rules.check_new(name, existing, len(existing))
    if problem:
        return jsonify({"error": problem}), 409

    folder = BookmarkFolder(user_id=v.id, name=name, created_utc=int(time.time()))
    g.db.add(folder)
    try:
        g.db.flush()
    except IntegrityError:
        g.db.rollback()
        return jsonify({"error": f"You already have a folder called {name}."}), 409

    return jsonify({"id": folder.id, "name": folder.name, "message": f"Folder {folder.name} made."})


@app.route("/bookmarks/folders/<int:folder_id>/rename", methods=["POST"])
@auth_required
@validate_formkey
def bookmark_folders_rename(folder_id, v):
    folder = _folder_or_404(v, folder_id)
    name, error = _name_from_request()
    if error:
        return error

    problem = rules.check_rename(name, [f.name for f in _folders(v) if f.id != folder.id])
    if problem:
        return jsonify({"error": problem}), 409

    folder.name = name
    g.db.add(folder)
    try:
        g.db.flush()
    except IntegrityError:
        g.db.rollback()
        return jsonify({"error": f"You already have a folder called {name}."}), 409

    return jsonify({"id": folder.id, "name": folder.name, "message": "Folder renamed."})


@app.route("/bookmarks/folders/<int:folder_id>/delete", methods=["POST"])
@auth_required
@validate_formkey
def bookmark_folders_delete(folder_id, v):
    """Deleting a folder never deletes a bookmark: they go back to unsorted."""
    folder = _folder_or_404(v, folder_id)

    for model in (SaveRelationship, CommentSaveRelationship):
        g.db.query(model).filter_by(user_id=v.id, folder_id=folder.id).update(
            {"folder_id": None}, synchronize_session=False)
    g.db.delete(folder)

    return jsonify({"message": "Folder deleted. Its bookmarks are unsorted now."})


@app.route("/bookmarks/move", methods=["POST"])
@auth_required
@validate_formkey
def bookmark_move(v):
    """Put a bookmark in one of the caller's folders, or back to unsorted (empty `folder_id`).
    An item that is not bookmarked yet is bookmarked as it is filed, as with any bookmark: only
    something the caller may see, and not a removed or deleted post or comment."""
    kind = request.values.get("kind")
    item = _target(v, kind, (request.values.get("id") or "").strip())

    raw = (request.values.get("folder_id") or "").strip()
    folder = None
    if raw:
        folder_id = rules.parse_id(raw)
        if folder_id is None:
            abort(404)
        folder = _folder_or_404(v, folder_id)

    row = _bookmark(v, kind, item)
    if not row:
        if item.is_banned or item.deleted_utc:
            abort(404)
        if kind == "post":
            row = SaveRelationship(user_id=v.id, submission_id=item.id, created_utc=int(time.time()))
        else:
            row = CommentSaveRelationship(user_id=v.id, comment_id=item.id, created_utc=int(time.time()))

    row.folder_id = folder.id if folder else None
    g.db.add(row)
    try:
        g.db.flush()
    except IntegrityError:
        g.db.rollback()
        abort(422)

    where = f"Moved to {folder.name}." if folder else "Moved to unsorted."
    return jsonify({"message": where, "saved": True, "kind": kind, "id": request.values.get("id"),
                    "folder": {"id": folder.id, "name": folder.name} if folder else None})
