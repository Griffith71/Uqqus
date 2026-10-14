"""Who can comment on a post that already exists.

The author picks it when writing the post (helpers/comment_permission.py has the rule, `post_options.html` the field)
and can change it later, from the post's "..." menu (`partials/comment_permission_modal.html`,
`assets/js/comment_permission.js`) or from the Edit form. Only the post on the author's own profile has the setting:
a forwarded copy follows its guild's rules.
"""
from flask import abort, g, jsonify, request
from jinja2 import Undefined

from ruqqus.classes import Submission
from ruqqus.helpers import comment_permission as cperm
from ruqqus.helpers.get import get_post
from ruqqus.helpers.wrappers import is_not_banned, validate_formkey
from ruqqus.__main__ import app

PERMISSION_CHANGED = {
    cperm.EVERYONE: "Everyone can comment on this post now.",
    cperm.FOLLOWED: "Only accounts you follow can comment on this post now.",
    cperm.PREMIUM: "Only Premium accounts can comment on this post now.",
    cperm.NOBODY: cperm.CLOSED_NOTICE,
}


def _fail(message, status=400):
    return jsonify({"error": message}), status


@app.route("/api/post/<pid>/comment_permission", methods=["POST"])
@is_not_banned
@validate_formkey
def post_comment_permission(pid, v):
    """The author changes who can comment on one of their posts. Existing comments stay as they are; the rule applies
    from the next comment (`api_comment` asks `comment_permission.restriction`). Like the audience change, it is not an
    edit: no history row and no `edited_utc`."""
    post = get_post(pid, v=v)                              # only someone who may see it gets this far
    if post.author_id != v.id:
        abort(403)
    if post.is_deleted or post.is_banned:
        abort(404)
    if post.repost_id not in (0, None) or not post.is_profile_post:
        return _fail("Only a post on your own profile has this setting. A copy in a guild follows that guild's rules.")

    raw = request.values.get("comment_permission")
    mode = cperm.parse(raw) if raw is not None else None
    if mode is None:
        return _fail("Choose who can comment.")
    refused = cperm.refusal(mode, bool(post.is_anonymous))
    if refused:
        return _fail(refused)
    if mode == cperm.mode_of(post):
        return jsonify({"message": "Nothing changed."})

    post.comment_permission = mode
    g.db.add(post)
    g.db.commit()
    return jsonify({"message": PERMISSION_CHANGED[mode]})


def may_change_comment_permission(post, viewer):
    """Offer "Who can comment" in a post's menu: the author, on the post of their own profile, still up."""
    viewer = None if isinstance(viewer, Undefined) else viewer
    return bool(viewer and isinstance(post, Submission) and post.author_id == viewer.id
                and not post.is_deleted and not post.is_banned
                and post.repost_id in (0, None) and post.is_profile_post)


app.jinja_env.globals.update(may_change_comment_permission=may_change_comment_permission)
