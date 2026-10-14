"""Similar accounts (a profile's right sidebar): accounts like this one, from public signals only.

* Followed by the same people: an account that at least MIN_SHARED_FOLLOWERS of this account's followers also
  follow. Follower lists are not public, so a signal from fewer people never counts (one follower's follows would
  be readable from the box).
* Active in the same guilds: accounts posting in the public guilds this account posted in lately. Anonymous posts
  never count, on either side, so the box can never tie an anonymous post to its author; nor do posts that are not
  public (Circle posts, private and Circle guilds) or posts on profiles.

Plain text() SQL, tested on SQLite (tests/test_similar_store.py). The live rules (blocks, mutes, private accounts,
the viewer's word filter) are applied when the list is read, in helpers/suggestions.py similar_for.
"""
from sqlalchemy import text

from ruqqus.helpers.suggestions import Scores, plural

MIN_SHARED_FOLLOWERS = 3
RECENT_SECONDS = 90 * 86400
PROFILE_BOARD = "systemprofile"

_CO_FOLLOWED = text("""
SELECT f2.target_id, COUNT(DISTINCT f1.user_id) AS n
FROM follows f1 JOIN follows f2 ON f2.user_id = f1.user_id
WHERE f1.target_id = :owner AND f2.target_id <> :owner
GROUP BY f2.target_id
HAVING COUNT(DISTINCT f1.user_id) >= :least
ORDER BY n DESC, f2.target_id
LIMIT 200
""")

# a post that counts, as `s`, in a guild that counts, as `b`
_LIVE_PUBLIC = """
    s.is_banned = :no AND s.deleted_utc = 0 AND s.is_anonymous = :no AND s.post_public = :yes
    AND s.created_utc > :since
    AND COALESCE(b.is_private, :no) = :no AND COALESCE(b.is_banned, :no) = :no AND b.is_circle = :no
    AND b.name <> :profile_board
"""

_SAME_GUILDS = text(f"""
SELECT s.author_id, b.name, COUNT(*) AS n
FROM submissions s JOIN boards b ON b.id = s.board_id
WHERE s.author_id <> :owner AND {_LIVE_PUBLIC}
  AND s.board_id IN (
      SELECT s.board_id FROM submissions s JOIN boards b ON b.id = s.board_id
      WHERE s.author_id = :owner AND {_LIVE_PUBLIC})
GROUP BY s.author_id, b.name
ORDER BY n DESC, s.author_id
LIMIT 500
""")


def candidates(db, owner_id, owner_name, now):
    """[(account id, score, reason)], best first, unfiltered (see the module's note on what is applied later)."""
    s = Scores()
    for uid, n in db.execute(_CO_FOLLOWED, {"owner": owner_id, "least": MIN_SHARED_FOLLOWERS}).fetchall():
        s.add(uid, 3 * n, f"Followed by {plural(n, 'person', 'people')} who follow @{owner_name}")
    for uid, guild, n in db.execute(_SAME_GUILDS, {
            "owner": owner_id, "no": False, "yes": True, "since": int(now) - RECENT_SECONDS,
            "profile_board": PROFILE_BOARD}).fetchall():
        s.add(uid, min(n, 5) * 0.8, f"Also active in +{guild}")
    return [row for row in s.rows() if row[0] != owner_id][:300]
