"""Give posts linking to a social site (X, Instagram, Facebook, TikTok, Bluesky, Reddit, Threads, Pinterest, Tumblr,
Mastodon) what their quote card is made from now (helpers/social_embeds.py, helpers/quote_cards.py), and YouTube Shorts
and live links their player. Posts saved before 2026-10-15 have no card (a feed showed an empty play box) or no player,
Mastodon posts saved before quote cards hold only a frame (no text, handle or date), and X posts saved before
2026-10-16 that quote another hold only a t.co link to it (now the quoted post is saved with them) or have a photo or
video and no picture (now read from the post's link-preview tags). Run once:

    PYTHONPATH=. python scripts/backfill_social_embeds.py --once

Only live posts without a feed card, Mastodon posts saved as a frame and those X posts are touched: their saved
embed is cleared and the site's own preview task (helpers/thumbs.thumbnail_thread) works it out again, as for a new post.
"""
import sys

from sqlalchemy import text

from ruqqus.__main__ import db_session
from ruqqus.helpers import social_embeds
from ruqqus.helpers.base36 import base36encode
from ruqqus.helpers.thumbs import thumbnail_thread

_CANDIDATES = text("""
    SELECT s.id, a.url, COALESCE(a.meta_title, '') = '' AS no_card, COALESCE(a.embed_url, '') LIKE '<iframe%' AS framed,
           COALESCE(a.embed_url, '') AS saved, COALESCE(a.preview_image_url, '') AS picture
    FROM submissions s JOIN submissions_aux a ON a.id = s.id
    WHERE s.deleted_utc = 0 AND NOT s.is_banned AND a.url IS NOT NULL AND a.url != ''
      AND (COALESCE(a.meta_title, '') = '' OR COALESCE(a.embed_url, '') LIKE '<iframe%'
           OR a.embed_url LIKE '%twitter-tweet%') ORDER BY s.id""")


def wanted(url, no_card=True, framed=False, saved="", picture=""):
    """A post this backfill is for: a Mastodon post saved as a frame; an X post whose saved text ends with the t.co
    link of a post it quotes but not that post, that has a photo or video and no picture, or whose quoted post was
    saved before pictures were read; or one with no card on a social site, a Mastodon post, or a YouTube Short or
    live link."""
    saved = saved or ""
    if framed and social_embeds.mastodon_post(url):
        return True
    if social_embeds.x_status_url(url):
        if "x-quoted" not in saved and social_embeds.last_tco_link(saved):
            return True
        if not picture and ("pic.twitter.com" in saved or "pic.x.com" in saved):
            return True
        if "x-quoted" in saved and "data-image=" not in saved:
            return True
    if not no_card:
        return False
    if social_embeds.site_of(url) or social_embeds.mastodon_post(url):
        return True
    return bool(social_embeds.youtube_id(url)) and ("/shorts/" in url or "/live/" in url)


def run():
    db = db_session()
    try:
        ids = [pid for pid, url, no_card, framed, saved, picture in db.execute(_CANDIDATES).fetchall()
               if wanted(url, no_card, framed, saved, picture)]
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
