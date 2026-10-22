"""Tying uploaded files to the post or comment that shows them.

An asset is public only while it is part of a live post or comment (rules.access), so
every place that saves a post's link or text, or a comment's text, calls `sync` with it.
"""
import hmac
import time
from urllib.parse import urlparse

from sqlalchemy import text

from ruqqus.helpers import circles
from . import rules


def _models():
    from ruqqus.classes import Comment, MediaAsset, Submission
    return MediaAsset, Submission, Comment


def claimed_assets(db, author_id, texts):
    """The author's own ready assets mentioned in `texts`. A mention only counts when the
    id, the secret token and the extension all match, so an address cannot be guessed and
    someone else's file cannot be claimed. `author_id` may be a list: a collaborative post's
    authors (routes/collab.py), each claiming only their own files."""
    owners = set(author_id) if isinstance(author_id, (list, tuple, set, frozenset)) else {author_id}
    MediaAsset, _, _ = _models()
    refs = rules.find_refs(*texts)
    if not refs:
        return []
    rows = {a.id: a for a in db.query(MediaAsset).filter(MediaAsset.id.in_([r[0] for r in refs])).all()}
    out = []
    for asset_id, token, ext in refs:
        asset = rows.get(asset_id)
        if (asset is not None and asset.user_id in owners and asset.ext == ext
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


def attach_image_sizes(db, posts, server_name):
    """Set `post._image_size` to the stored (width, height) of a post whose picture is one of this site's own
    media files, in one query for a page of posts. The template gives the <img> that size, so the browser keeps
    the picture's room before it has loaded and the card does not jump when it arrives."""
    MediaAsset, _, _ = _models()
    wanted = {}
    for post in posts:
        url = getattr(post, "url", None)
        if not url or not is_media_url(url, server_name):
            continue
        asset_id, _token, _ext = rules.parse_path(urlparse(url).path)
        wanted.setdefault(asset_id, []).append(post)
    if not wanted:
        return
    for asset_id, width, height in db.query(MediaAsset.id, MediaAsset.width, MediaAsset.height).filter(
            MediaAsset.id.in_(list(wanted))).all():
        if width and height:
            for post in wanted[asset_id]:
                post._image_size = (width, height)


def own_video(db, author_id, url):
    """The author's own uploaded video this link points at, or None. Such a video plays
    with the author's channel name on it (YouTube or Dailymotion), so it cannot go on an
    anonymous post."""
    MediaAsset, _, _ = _models()
    found = rules.video_ref(url)
    if not found:
        return None
    provider, video_id = found
    return db.query(MediaAsset).filter_by(user_id=author_id, provider=provider, provider_ref=video_id).first()


ANONYMOUS_VIDEO = ("A video uploaded to your YouTube or Dailymotion channel shows your channel's name, "
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


def board_paths(db, board_id, post_id=None):
    """The addresses of the files on a guild's posts and their comments that become private with the guild (never
    public there, not pinned; audience_context): what a CDN must forget when the guild turns private, since it kept
    them as public files. `post_id` limits it to one post (one unpinned in a private guild)."""
    MediaAsset, Submission, Comment = _models()
    posts = db.query(Submission.id).filter(Submission.board_id == board_id, Submission.post_public == False,
                                           Submission.is_pinned == False)
    if post_id is not None:
        posts = posts.filter(Submission.id == post_id)
    comments = db.query(Comment.id).filter(Comment.parent_submission.in_(posts))
    rows = db.query(MediaAsset).filter((MediaAsset.submission_id.in_(posts)) | (MediaAsset.comment_id.in_(comments))).all()
    return [a.path for a in rows if a.provider in rules.SERVED and a.ext]


def is_live(db, asset):
    """Is the asset part of a post or comment that is still up, or of a story that is up or kept in a Highlight?"""
    _, Submission, Comment = _models()
    if asset.story_id:
        row = db.execute(text("SELECT deleted_utc, expires_utc FROM stories WHERE id = :i"), {"i": asset.story_id}).fetchone()
        if row is None or row.deleted_utc:
            return False
        return row.expires_utc > int(time.time()) or bool(
            db.execute(text("SELECT 1 FROM highlight_stories WHERE story_id = :i LIMIT 1"), {"i": asset.story_id}).fetchone())
    if asset.submission_id:
        post = db.query(Submission.is_banned, Submission.deleted_utc).filter(Submission.id == asset.submission_id).first()
        return bool(post) and not post.is_banned and not post.deleted_utc
    if asset.comment_id:
        comment = db.query(Comment.is_banned, Comment.deleted_utc).filter(Comment.id == asset.comment_id).first()
        return bool(comment) and not comment.is_banned and not comment.deleted_utc
    return False


def audience_context(db, asset):
    """(audience, author id, board id, circle ids) of the story, the post - or the post a comment is on - this asset
    is part of: what circle_guard.may_see needs. (0, None, None, ()) when it is part of nothing or of something public:
    the file is then shown to everyone. Anything else is for an audience."""
    _, Submission, Comment = _models()
    if asset.story_id:
        # a story's picture is never public-cached: a public story is shown per viewer (circles.STORY_OPEN), one for
        # circles to the people in them
        story = db.execute(text("SELECT id, audience, user_id FROM stories WHERE id = :i"), {"i": asset.story_id}).fetchone()
        if story is None:
            return circles.GUILD, None, None, ()             # a story that is gone: nobody (fail closed)
        if story.audience == circles.CIRCLES:
            found = db.execute(text("SELECT circle_id FROM story_circles WHERE story_id = :i"), {"i": story.id}).fetchall()
            return circles.CIRCLES, story.user_id, None, tuple(sorted(r[0] for r in found))
        return (story.audience or circles.STORY_OPEN), story.user_id, None, ()
    columns = (Submission.id, Submission.audience, Submission.author_id, Submission.board_id, Submission.post_public,
               Submission.is_pinned)
    if asset.submission_id:
        row = db.query(*columns).filter(Submission.id == asset.submission_id).first()
    elif asset.comment_id:
        row = (db.query(*columns)
               .join(Comment, Comment.parent_submission == Submission.id).filter(Comment.id == asset.comment_id).first())
    else:
        row = None
    if (row and not row.audience and not row.post_public and not row.is_pinned and row.board_id
            and _in_private_guild(db, row.board_id)):
        # bug hunt L5: these were public, cached for a year for anyone holding the address. A copy that was public
        # there, or a pinned one, stays readable by anyone (as the post page says) and so does its picture
        return circles.PRIVATE_GUILD, row.author_id, row.board_id, ()
    if not row or not row.audience:
        return 0, None, None, ()
    found = ()
    if row.audience == circles.CIRCLES:
        found = tuple(sorted(r[0] for r in db.execute(text("SELECT circle_id FROM post_circles WHERE post_id = :i"), {"i": row.id}).fetchall()))
    return row.audience, row.author_id, row.board_id, found


def _in_private_guild(db, board_id):
    """Is this a private guild that isn't a Circle guild (a Circle guild's posts carry their own audience, 3)?"""
    found = db.execute(text("SELECT is_private, is_circle FROM boards WHERE id = :b"), {"b": board_id}).fetchone()
    return bool(found and found[0] and not found[1])


def audience_of(db, asset):
    """(audience, author id) of what this asset is part of (`audience_context` without the guild and the circles)."""
    return audience_context(db, asset)[:2]


def sync(db, author_id, texts, submission_id=None, comment_id=None):
    """Make the assets attached to this post (or comment) match what its link and text
    mention now: attach the newly mentioned ones, let go of the ones no longer there.
    Returns the attached assets. The caller commits, then calls safety.scan_later on them.
    `author_id` may be a list of owners (a collaborative post's authors)."""
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
