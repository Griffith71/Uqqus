"""Tying uploaded files to the post or comment that shows them.

An asset is public only while it is part of a live post or comment (rules.access), so
every place that saves a post's link or text, or a comment's text, calls `sync` with it.
"""
import hmac
import time

from . import rules


def _models():
    from ruqqus.classes import Comment, MediaAsset, Submission
    return MediaAsset, Submission, Comment


def claimed_assets(db, author_id, texts):
    """The author's own ready assets mentioned in `texts`. A mention only counts when the
    id, the secret token and the extension all match, so an address cannot be guessed and
    someone else's file cannot be claimed."""
    MediaAsset, _, _ = _models()
    refs = rules.find_refs(*texts)
    if not refs:
        return []
    rows = {a.id: a for a in db.query(MediaAsset).filter(MediaAsset.id.in_([r[0] for r in refs])).all()}
    out = []
    for asset_id, token, ext in refs:
        asset = rows.get(asset_id)
        if (asset is not None and asset.user_id == author_id and asset.ext == ext
                and asset.status == rules.READY and hmac.compare_digest(asset.token, token)):
            out.append(asset)
    return out


def is_live(db, asset):
    """Is the asset part of a post or comment that is still up?"""
    _, Submission, Comment = _models()
    if asset.submission_id:
        post = db.query(Submission.is_banned, Submission.deleted_utc).filter(Submission.id == asset.submission_id).first()
        return bool(post) and not post.is_banned and not post.deleted_utc
    if asset.comment_id:
        comment = db.query(Comment.is_banned, Comment.deleted_utc).filter(Comment.id == asset.comment_id).first()
        return bool(comment) and not comment.is_banned and not comment.deleted_utc
    return False


def sync(db, author_id, texts, submission_id=None, comment_id=None):
    """Make the assets attached to this post (or comment) match what its link and text
    mention now: attach the newly mentioned ones, let go of the ones no longer there.
    Returns the attached assets. The caller commits."""
    MediaAsset, _, _ = _models()
    if not (submission_id or comment_id):
        return []
    now = int(time.time())
    wanted = claimed_assets(db, author_id, texts)
    wanted_ids = {a.id for a in wanted}

    column = MediaAsset.submission_id if submission_id else MediaAsset.comment_id
    target = submission_id or comment_id
    for asset in db.query(MediaAsset).filter(column == target).all():
        if asset.id not in wanted_ids:
            asset.submission_id = None
            asset.comment_id = None
            asset.updated_utc = now
            db.add(asset)

    for asset in wanted:
        here = (asset.submission_id == submission_id) if submission_id else (asset.comment_id == comment_id)
        if here:
            continue
        # one home per file: keep it where it already shows, unless that place is gone
        if (asset.submission_id or asset.comment_id) and is_live(db, asset):
            continue
        asset.submission_id = submission_id
        asset.comment_id = None if submission_id else comment_id
        asset.updated_utc = now
        db.add(asset)
    return wanted
