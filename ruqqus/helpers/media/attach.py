"""Tying uploaded files to the post or comment that shows them.

An asset is public only while it is part of a live post or comment (rules.access), so
every place that saves a post's link or text, or a comment's text, calls `sync` with it.
"""
import hmac
import time
from urllib.parse import urlparse

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


def own_asset(db, author_id, raw_id, kinds=(rules.IMAGE, rules.AUDIO)):
    """The author's own ready, site-served asset with this (base 36) id, or None. For a
    post's main attachment, which is named by id (the `media` form field)."""
    MediaAsset, _, _ = _models()
    try:
        asset_id = int((raw_id or "").strip(), 36)
    except ValueError:
        return None
    asset = db.query(MediaAsset).filter_by(id=asset_id, user_id=author_id).first()
    if asset is None or asset.status != rules.READY or asset.kind not in kinds or asset.provider not in rules.SERVED:
        return None
    return asset


def is_media_url(url, server_name):
    """Is this link one of this site's own media addresses?"""
    if not url:
        return False
    parsed = urlparse(url)
    return parsed.netloc in ("", server_name) and rules.parse_path(parsed.path) is not None


def own_video(db, author_id, url):
    """The author's own uploaded video this link points at, or None. Such a video plays
    with the author's channel name on it, so it cannot go on an anonymous post."""
    MediaAsset, _, _ = _models()
    video_id = rules.youtube_id(url)
    if not video_id:
        return None
    return db.query(MediaAsset).filter_by(user_id=author_id, provider="youtube", provider_ref=video_id).first()


ANONYMOUS_VIDEO = ("A video uploaded to your YouTube channel shows your channel's name, "
                   "so it cannot be added to an anonymous post.")


def link_refusal(db, author_id, hides_author, url):
    """Why this link may not go on the post, or None. `hides_author`: the post is one that
    does not say who wrote it."""
    if hides_author and own_video(db, author_id, url):
        return ANONYMOUS_VIDEO
    return None


def link_refusal_for(db, post, url):
    """The same question for a link being put on an existing post (an edit)."""
    return link_refusal(db, post.author_id, bool(post.is_anonymous), url)


def attached_paths(db, submission_id=None, comment_id=None):
    """The addresses of the files a post (or comment) shows: what a CDN must forget when
    the post is removed."""
    MediaAsset, _, _ = _models()
    if not (submission_id or comment_id):
        return []
    column = MediaAsset.submission_id if submission_id else MediaAsset.comment_id
    rows = db.query(MediaAsset).filter(column == (submission_id or comment_id)).all()
    return [a.path for a in rows if a.provider in rules.SERVED and a.ext]


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
    Returns the attached assets. The caller commits, then calls safety.scan_later on them."""
    MediaAsset, _, _ = _models()
    if not (submission_id or comment_id):
        return []
    now = int(time.time())
    wanted = claimed_assets(db, author_id, texts)
    wanted_ids = {a.id for a in wanted}

    column = MediaAsset.submission_id if submission_id else MediaAsset.comment_id
    target = submission_id or comment_id
    dropped = []
    for asset in db.query(MediaAsset).filter(column == target).all():
        if asset.id not in wanted_ids:
            asset.submission_id = None
            asset.comment_id = None
            asset.updated_utc = now
            db.add(asset)
            if asset.provider in rules.SERVED and asset.ext:
                dropped.append(asset.path)
    if dropped:
        from . import cdn
        cdn.purge(dropped)          # no longer public: the CDN must not keep serving it

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
