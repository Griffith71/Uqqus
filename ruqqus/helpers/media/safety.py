"""Checks run on an upload before it may be shown.

The file is in the member's storage, so the browser sent it there without the server
seeing it. To check it the server fetches it once into a temp file, looks, and deletes
the temp file: nothing is kept.
"""
import os
import tempfile

from . import rules


def fetch_to_temp(provider, account, asset, limit):
    """The asset's bytes in a temp file (the caller deletes it). MediaError if it is
    bigger than `limit`, whatever size was claimed."""
    handle, path = tempfile.mkstemp(prefix="ruqqus_media_check_")
    written = 0
    try:
        with os.fdopen(handle, "wb") as out:
            for block in provider.open(account, asset).chunks:
                written += len(block)
                if written > limit:
                    raise rules.MediaError(f"That file is too big. The limit is {limit // rules.MIB} MB.", 413)
                out.write(block)
    except BaseException:
        discard(path)
        raise
    return path


def discard(path):
    try:
        os.remove(path)
    except OSError:
        pass


def image_size(path):
    """(width, height) of a real image, or MediaError. Guards against a small file that
    unpacks to an enormous picture."""
    from PIL import Image

    try:
        with Image.open(path) as probe:
            probe.verify()                      # the whole file parses as the image it claims to be
        with Image.open(path) as image:
            width, height = image.size
    except rules.MediaError:
        raise
    except Exception:
        raise rules.MediaError("That image could not be read. It may be damaged.")
    if width <= 0 or height <= 0 or width * height > rules.IMAGE_PIXELS_MAX:
        raise rules.MediaError("That image is too large in pixels.")
    return width, height


# --- known-bad images --------------------------------------------------------------

def banned_match(db, path):
    """The BadPic row this image matches (the site's list of banned pictures), or None."""
    from ruqqus.helpers.aws import check_phash
    return check_phash(db, path)


def ban_uploader(db, user, reason, days=0):
    """Suspend the uploader and their other accounts, as the site does for a banned image
    uploaded the old way (helpers/aws.py check_csam_url). `days` 0 is permanent."""
    import time

    until = int(time.time()) + 60 * 60 * 24 * days if days else 0
    for account in [user] + list(user.alts_threaded(db)):
        account.ban_reason = reason
        account.is_banned = 1
        account.unban_utc = until
        db.add(account)


def _scan_public(url, asset_id):
    """Fetch the public address through the CDN, which answers 451 for an image on the
    child-abuse hash lists it checks (the same signal helpers/aws.py reads for the old
    uploads). On a match: remove the file and what shows it, and ban the uploader."""
    import time

    import requests

    from ruqqus.__main__ import db_session
    from ruqqus.classes import Comment, MediaAsset, Submission, User

    status = None
    for _ in range(5):
        try:
            status = requests.get(url, headers={"User-Agent": "Ruqqus webserver"}, timeout=20, stream=True).status_code
        except Exception:
            return                        # not reachable from here (a dev machine): nothing to read
        if status in (200, 451):
            break
        time.sleep(20)
    if status != 451:
        return

    db = db_session()
    try:
        asset = db.get(MediaAsset, asset_id)
        if asset is None:
            return
        user = db.get(User, asset.user_id)
        if user is not None:
            ban_uploader(db, user, "Sexualizing Minors")
        asset.status = rules.REMOVED
        asset.updated_utc = int(time.time())
        db.add(asset)
        if asset.submission_id:
            post = db.get(Submission, asset.submission_id)
            if post is not None:
                post.is_banned = True
                db.add(post)
        if asset.comment_id:
            comment = db.get(Comment, asset.comment_id)
            if comment is not None:
                comment.is_banned = True
                db.add(comment)
        db.commit()
        path = asset.path
    finally:
        db.close()
    from . import cdn
    cdn.purge([path])


def scan_later(assets):
    """After a post or comment that shows these files is saved: check each image at its
    public address, in the background."""
    import gevent

    from . import cdn

    for asset in assets or ():
        if asset.kind == rules.IMAGE and asset.provider in rules.SERVED and asset.ext:
            gevent.spawn(_scan_public, cdn.absolute(asset.path), asset.id)
