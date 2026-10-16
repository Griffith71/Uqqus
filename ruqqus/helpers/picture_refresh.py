"""Quote card pictures whose address expires: Instagram signs its pictures with an end date (`oe=`, about five days)
and TikTok with `x-expires=`, after which the picture is gone from a card. A post's picture is read again a day before
that (scripts/recomputes.py, every cycle), through the same handler that read it when the post was made
(helpers/embed.known_provider_embed), so it stays the site's own current address. The rules are pure
(`due`); `refresh` takes the database and the reader (tests/test_quote_card_sites.py)."""
from sqlalchemy import text

from ruqqus.helpers import social_embeds

WINDOW = 60 * 60 * 24          # read again when a picture has less than a day left
PER_CYCLE = 30                 # at most this many posts a cycle (a few requests each)
RECENT_DAYS = 30               # posts older than this keep what they have (a broken picture is simply hidden)
RETRY = 60 * 60                # a site may hand out the same address until nearer the end: one try an hour per post

# post id -> when it was last read again, in this process (scripts/recomputes.py runs for good)
_tried = {}

_CANDIDATES = text("""
    SELECT s.id, a.url, a.preview_image_url FROM submissions s JOIN submissions_aux a ON a.id = s.id
    WHERE s.deleted_utc = 0 AND NOT s.is_banned AND s.created_utc > :since
      AND (a.preview_image_url LIKE '%oe=%' OR a.preview_image_url LIKE '%x-expires=%')
    ORDER BY s.id DESC LIMIT 2000""")
_SAVE = text("UPDATE submissions_aux SET preview_image_url = :picture, embed_url = :embed WHERE id = :id")
_DROP = text("UPDATE submissions_aux SET preview_image_url = '' WHERE id = :id")


def due(rows, now, tried=None):
    """The (post id, link) of the rows (id, link, picture) whose picture expires within WINDOW, soonest first, at most
    PER_CYCLE, leaving out those read again less than RETRY ago (`tried`: post id -> when). A picture that does not
    expire is never due."""
    tried = tried or {}
    soon = []
    for post_id, url, picture in rows:
        ends = social_embeds.picture_expires(picture)
        if ends is not None and ends - now < WINDOW and url and now - tried.get(post_id, 0) >= RETRY:
            soon.append((ends, post_id, url))
    return [(post_id, url) for _ends, post_id, url in sorted(soon)[:PER_CYCLE]]


def refresh(db, now, read):
    """Read again the pictures that are about to expire. `read(url)` answers as embed.known_provider_embed does:
    (kind, snippet, picture, card). A post whose site no longer gives a picture loses it (it was about to break)."""
    rows = db.execute(_CANDIDATES, {"since": now - RECENT_DAYS * 60 * 60 * 24}).fetchall()
    count = 0
    for post_id, url in due(rows, now, _tried):
        _tried[post_id] = now
        try:
            kind, snippet, picture, _card = read(url)
        except Exception:
            kind, snippet, picture = None, None, None
        if kind and snippet and picture:
            db.execute(_SAVE, {"picture": str(picture)[:1024], "embed": snippet, "id": post_id})
        else:
            db.execute(_DROP, {"id": post_id})
        count += 1
    db.commit()
    for post_id in [key for key, when in _tried.items() if now - when > WINDOW]:
        del _tried[post_id]                               # it has expired or been renewed by then
    return count
