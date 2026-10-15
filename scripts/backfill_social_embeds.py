"""Give posts linking to a social site (X, Instagram, Facebook, TikTok, Bluesky, Reddit, Threads, Pinterest, Tumblr,
Mastodon) the widget and feed card they get now (helpers/social_embeds.py), and YouTube Shorts and live links their
player. Posts saved before 2026-10-15 have no card (a feed showed an empty play box) or no player. Run once:

    PYTHONPATH=. python scripts/backfill_social_embeds.py --once

Only live posts without a feed card are touched: their saved embed is cleared and the site's own preview task
(helpers/thumbs.thumbnail_thread) works it out again, as for a new post.
"""
import sys

from sqlalchemy import text

from ruqqus.__main__ import db_session
from ruqqus.helpers import social_embeds
from ruqqus.helpers.base36 import base36encode
from ruqqus.helpers.thumbs import thumbnail_thread

_CANDIDATES = text("""
    SELECT s.id, a.url FROM submissions s JOIN submissions_aux a ON a.id = s.id
    WHERE s.deleted_utc = 0 AND NOT s.is_banned AND a.url IS NOT NULL AND a.url != ''
      AND COALESCE(a.meta_title, '') = '' ORDER BY s.id""")


def wanted(url):
    """A link this backfill is for: a social site, a Mastodon post, or a YouTube Short or live link."""
    if social_embeds.site_of(url) or social_embeds.mastodon_post(url):
        return True
    return bool(social_embeds.youtube_id(url)) and ("/shorts/" in url or "/live/" in url)


def run():
    db = db_session()
    try:
        ids = [pid for pid, url in db.execute(_CANDIDATES).fetchall() if wanted(url)]
        if ids:
            db.execute(text("UPDATE submissions_aux SET embed_url = '', embed_type = NULL WHERE id = ANY(:ids)"), {"ids": ids})
        db.commit()
    finally:
        db_session.remove()
    for pid in ids:
        print(f"post {pid}: {thumbnail_thread(base36encode(pid))}", flush=True)
    print(f"re-worked {len(ids)} posts", flush=True)


if __name__ == "__main__":
    if "--once" not in sys.argv:
        print(__doc__)
        sys.exit(1)
    run()
