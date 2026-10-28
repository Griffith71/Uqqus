"""Similar guilds (a guild's right sidebar): guilds like this one, from public signals only.

* Tier A, members in common: a guild that many of this guild's members also joined. No page lists a guild's members
  and membership has no per-person visibility setting, so a small overlap could name one person's other guilds: it
  counts only from MIN_SHARED members, and only between guilds of at least MIN_MEMBERS members each (3 shared members
  of a 3-member guild would name all of them). Only members whose account is not banned, deleted or in private mode
  are counted, the size floors are counted live (`stored_subscriber_count` is stale outside the top 1,000 guilds),
  and nothing here ever returns a number: the reason says "shares members with +guild" and no more.
  The score is the cosine of the two member sets, shared / sqrt(size * size), so a mega-guild does not win by size.
* Tier B, the same topic: guilds in the same subcategory, then in the same category (including the guilds filed under
  the category alone). Public, cheap and it works for a new or small guild; a guild that is Uncategorized gets none.

Forwards of the same posts are deliberately not a signal: no index leads with `board_id` and one person forwarding a
post to ten guilds would make them "similar".

Plain text() SQL, tested on SQLite (tests/test_similar_guilds_store.py). The live rules (the viewer's joins, mutes,
exiles, word filter, sensitivity) are applied when the list is read, in helpers/suggestions.py similar_guilds_for.
"""
import math
from collections import namedtuple

from sqlalchemy import bindparam, text

from ruqqus.helpers.suggestions import SAME_TOPIC, SHARES_MEMBERS, Scores

MIN_SHARED = 5                # members in common that count
MIN_MEMBERS = 25              # and the least either guild may have (the guild insights' MIN_CHART_MEMBERS)
SAMPLE_MEMBERS = 5000         # a bigger guild is read through its newest members only
PER_TIER = 100
TOPIC_PER_STEP = 25
PROFILE_BOARD = "systemprofile"
UNLISTED_SUBCAT = 108         # never a topic (as /browse and the guild lists)

# What a guild is filed under, for tier B: `subcat_id` / `subcat_name` (None when it is filed under a category alone or
# not at all), `cat_id` / `cat_name` and `sibling_subcats`, the ids of every subcategory of that category.
Topic = namedtuple("Topic", "subcat_id subcat_name cat_id cat_name sibling_subcats")

# an account that is counted as a member
_COUNTED = ("COALESCE(u.is_banned, 0) = 0 AND COALESCE(u.is_deleted, :no) = :no "
            "AND COALESCE(u.is_private, :no) = :no")

_CO_MEMBERS = text(f"""
SELECT s2.board_id, COUNT(*) AS n
FROM (
    SELECT s.user_id FROM subscriptions s JOIN users u ON u.id = s.user_id
    WHERE s.board_id = :anchor AND s.is_active = :yes AND {_COUNTED}
    ORDER BY s.id DESC
    LIMIT :sample
) m
JOIN subscriptions s2 ON s2.user_id = m.user_id AND s2.board_id <> :anchor AND s2.is_active = :yes
JOIN boards b ON b.id = s2.board_id
WHERE COALESCE(b.is_private, :no) = :no AND COALESCE(b.is_banned, :no) = :no AND COALESCE(b.is_circle, :no) = :no
  AND b.name <> :profile_board
GROUP BY s2.board_id
HAVING COUNT(*) >= :least
ORDER BY n DESC, s2.board_id
LIMIT :cap
""")

_SIZES = text(f"""
SELECT s.board_id, COUNT(*) AS n
FROM subscriptions s JOIN users u ON u.id = s.user_id
WHERE s.board_id IN :ids AND s.is_active = :yes AND {_COUNTED}
GROUP BY s.board_id
""").bindparams(bindparam("ids", expanding=True))

_PUBLIC_GUILD = ("COALESCE(b.is_private, :no) = :no AND COALESCE(b.is_banned, :no) = :no "
                 "AND COALESCE(b.is_circle, :no) = :no AND COALESCE(b.all_opt_out, :no) = :no "
                 "AND b.name <> :profile_board AND COALESCE(b.subcat_id, 0) <> :unlisted AND b.id <> :anchor")

_SAME_SUBCAT = text(f"""
SELECT b.id, COALESCE(b.stored_subscriber_count, 0) FROM boards b
WHERE b.subcat_id = :subcat AND {_PUBLIC_GUILD}
ORDER BY COALESCE(b.stored_subscriber_count, 0) DESC, b.id
LIMIT :cap
""")

_SAME_CATEGORY = text(f"""
SELECT b.id, COALESCE(b.stored_subscriber_count, 0) FROM boards b
WHERE (b.cat_id = :cat OR b.subcat_id IN :siblings) AND {_PUBLIC_GUILD}
ORDER BY COALESCE(b.stored_subscriber_count, 0) DESC, b.id
LIMIT :cap
""").bindparams(bindparam("siblings", expanding=True))


def _flags(**more):
    return {"yes": True, "no": False, **more}


def cosine(shared, size_a, size_b):
    """shared / sqrt(size_a * size_b), 0 when either set is empty."""
    if shared <= 0 or size_a <= 0 or size_b <= 0:
        return 0.0
    return shared / math.sqrt(size_a * size_b)


def co_members(db, anchor_id):
    """[(guild id, cosine)], best first: the guilds that at least MIN_SHARED of the anchor's counted members also joined,
    each with at least MIN_MEMBERS counted members, the anchor too. Never returns a count."""
    shared = db.execute(_CO_MEMBERS, _flags(anchor=anchor_id, sample=SAMPLE_MEMBERS, least=MIN_SHARED, cap=PER_TIER,
                                            profile_board=PROFILE_BOARD)).fetchall()
    if not shared:
        return []
    ids = [anchor_id] + [row[0] for row in shared]
    sizes = {board: n for board, n in db.execute(_SIZES, _flags(ids=ids)).fetchall()}
    mine = sizes.get(anchor_id, 0)
    if mine < MIN_MEMBERS:
        return []
    found = []
    for board, n in shared:
        theirs = sizes.get(board, 0)
        if theirs >= MIN_MEMBERS:
            found.append((board, cosine(n, mine, theirs)))
    found.sort(key=lambda row: (-row[1], row[0]))
    return found


def same_topic(db, anchor_id, topic):
    """[(guild id, members, 'subcategory' | 'category')]: public guilds in the anchor's subcategory, then in its category,
    the biggest first. Nothing for a guild filed under nothing."""
    if topic is None:
        return []
    out = []
    flags = _flags(anchor=anchor_id, profile_board=PROFILE_BOARD, unlisted=UNLISTED_SUBCAT, cap=TOPIC_PER_STEP)
    if topic.subcat_id and topic.subcat_id != UNLISTED_SUBCAT:
        for board, n in db.execute(_SAME_SUBCAT, {**flags, "subcat": topic.subcat_id}).fetchall():
            out.append((board, n, "subcategory"))
    if topic.cat_id:
        siblings = list(topic.sibling_subcats) or [0]
        for board, n in db.execute(_SAME_CATEGORY, {**flags, "cat": topic.cat_id, "siblings": siblings}).fetchall():
            out.append((board, n, "category"))
    return out


def candidates(db, anchor_id, anchor_name, topic, members=None):
    """[(guild id, score, reason)], best first, unfiltered (see the module's note on what is applied later). `members` is
    the result of `co_members` when the caller ran it under a time limit; else it is run here. A reason is data
    (`SHARES_MEMBERS, the anchor's name` or `SAME_TOPIC, the topic's English name`), drawn in each reader's language."""
    s = Scores()
    for board, score in (co_members(db, anchor_id) if members is None else members):
        s.add(board, 3.0 + 6.0 * score, (SHARES_MEMBERS, anchor_name))     # always above any topic weight
    for board, n, step in same_topic(db, anchor_id, topic):
        # a little popularity breaks ties between guilds of one topic; the subcategory outweighs the category
        tie = min(math.log1p(max(n, 0)) * 0.01, 0.1)
        if step == "subcategory":
            s.add(board, 1.0 + tie, (SAME_TOPIC, topic.subcat_name))
        else:
            s.add(board, 0.5 + tie, (SAME_TOPIC, topic.cat_name))
    return [row for row in s.rows() if row[0] != anchor_id][:300]
