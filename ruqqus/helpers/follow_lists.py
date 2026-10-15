"""A profile's four lists and their counts: Following, Followers, Subscribers (paying members of the account's
Circle) and Subscriptions (the Circles it pays for). Each member chooses who may open each of their lists
(users.<list>_visibility: 0 everyone, 1 their followers, 2 only them).

Two rules, both enforced here:
* **Opening a list** (`may_open`): the owner always; otherwise the owner's setting for that list.
* **Every row needs the other account's consent too**: a row also says something about the account it shows (that it
  follows the owner, is followed by them, pays for their Circle...), so it is listed only when the viewer passes that
  account's matching setting as well (OTHER_SIDE). This is in the SQL, so pages are full and counts of what is shown
  are right. The owner gets no exception: a follower who keeps who they follow to themselves is not listed on the
  owner's own Followers list either (the count still includes them; counts are public, as they always were).

Never listed: deleted or banned accounts, accounts blocked either way with the viewer, names the viewer's word filter
hides. Close Friends are never counted or listed (only active subscribers of a person's Circle are; a guild's Circle,
which is private membership, never). Plain text() SQL, tested on SQLite (tests/test_follow_lists.py).
"""
from sqlalchemy import text

EVERYONE, FOLLOWERS, ONLY_ME = 0, 1, 2
CHOICES = {EVERYONE: "Everyone", FOLLOWERS: "Followers", ONLY_ME: "Only me"}

LISTS = ("following", "followers", "subscribers", "subscriptions")
TITLES = {"following": "Following", "followers": "Followers", "subscribers": "Subscribers", "subscriptions": "Subscriptions"}
SETTING = {name: f"{name}_visibility" for name in LISTS}
# the other account's setting that covers the same fact
OTHER_SIDE = {"following": "followers_visibility", "followers": "following_visibility",
              "subscribers": "subscriptions_visibility", "subscriptions": "subscribers_visibility"}

PAGE_SIZE = 25
NO_VIEWER = -1          # a visitor matches no account and follows no one
NO_FILTER = 99          # a name severity cut that hides nothing (word filter Off)


def parse_choice(raw):
    """A visibility from a form ("0", "1", "2"), or None."""
    return int(raw) if str(raw) in ("0", "1", "2") else None


def may_open(setting, *, is_owner, follows_owner):
    """May this viewer open a list whose owner chose `setting`? An unknown setting is treated as Only me."""
    if is_owner:
        return True
    if setting == EVERYONE:
        return True
    if setting == FOLLOWERS:
        return bool(follows_owner)
    return False


# --- SQL -----------------------------------------------------------------------------------------------------

# each list: the rows (`o` is the account shown, `at` orders it) for one owner
_SOURCES = {
    "following": "FROM follows r JOIN users o ON o.id = r.target_id WHERE r.user_id = :owner",
    "followers": "FROM follows r JOIN users o ON o.id = r.user_id WHERE r.target_id = :owner",
    # paid circles only (never a custom circle: nobody is told they are in one), one row per account however many of
    # the owner's paid circles it pays for
    "subscribers": ("FROM (SELECT m.user_id AS uid, MIN(m.started_utc) AS started_utc FROM circle_members m "
                    "JOIN circles c ON c.id = m.circle_id WHERE c.user_id = :owner AND c.kind = 'paid' "
                    "AND m.tier = 'subscriber' AND m.status = 'active' AND m.renews_utc > :now GROUP BY m.user_id) r "
                    "JOIN users o ON o.id = r.uid WHERE 1 = 1"),
    "subscriptions": ("FROM (SELECT c.user_id AS uid, MIN(m.started_utc) AS started_utc FROM circle_members m "
                      "JOIN circles c ON c.id = m.circle_id WHERE m.user_id = :owner AND c.kind = 'paid' "
                      "AND m.tier = 'subscriber' AND m.status = 'active' AND m.renews_utc > :now GROUP BY c.user_id) r "
                      "JOIN users o ON o.id = r.uid WHERE 1 = 1"),
}
_ORDER = {"following": "r.created_utc", "followers": "r.created_utc",
          "subscribers": "r.started_utc", "subscriptions": "r.started_utc"}

_LIVE = " AND o.is_deleted = :no AND COALESCE(o.is_banned, 0) = 0 AND o.id <> :owner"


def _shown(name):
    """The conditions a row needs to be shown to the viewer: not blocked either way, a name the filter allows, and the
    other account's own setting for the same fact."""
    other = OTHER_SIDE[name]
    return f"""
      AND COALESCE(o.name_severity, 0) < :cut
      AND NOT EXISTS (SELECT 1 FROM userblocks b
                      WHERE (b.user_id = :viewer AND b.target_id = o.id) OR (b.user_id = o.id AND b.target_id = :viewer))
      AND (o.{other} = 0 OR o.id = :viewer
           OR (o.{other} = 1 AND EXISTS (SELECT 1 FROM follows x WHERE x.user_id = :viewer AND x.target_id = o.id)))"""


def _params(owner_id, viewer_id, cut, now):
    return {"owner": owner_id, "viewer": NO_VIEWER if viewer_id is None else viewer_id,
            "cut": NO_FILTER if cut is None else cut, "now": int(now), "no": False}


def counts(db, owner_id, now):
    """{list name: how many}, the full numbers (live accounts only)."""
    out = {}
    for name in LISTS:
        sql = text(f"SELECT COUNT(DISTINCT o.id) {_SOURCES[name]}{_LIVE}")
        out[name] = db.execute(sql, _params(owner_id, None, None, now)).scalar() or 0
    return out


def page(db, name, owner_id, viewer_id, cut, now, page_no=1):
    """(ids on this page, whether there is a next page, how many are shown in all). `cut` is the lowest name severity
    the viewer's word filter hides (None: Off)."""
    params = _params(owner_id, viewer_id, cut, now)
    where = f"{_SOURCES[name]}{_LIVE}{_shown(name)}"
    offset = (max(1, int(page_no)) - 1) * PAGE_SIZE
    rows = db.execute(text(f"SELECT o.id {where} ORDER BY {_ORDER[name]} DESC, o.id DESC "
                           f"LIMIT {PAGE_SIZE + 1} OFFSET {offset}"), params).fetchall()
    shown = db.execute(text(f"SELECT COUNT(DISTINCT o.id) {where}"), params).scalar() or 0
    ids = [r[0] for r in rows]
    return ids[:PAGE_SIZE], len(ids) > PAGE_SIZE, shown


def follows(db, follower_id, target_id):
    if follower_id is None:
        return False
    return db.execute(text("SELECT 1 FROM follows WHERE user_id = :a AND target_id = :b LIMIT 1"),
                      {"a": follower_id, "b": target_id}).first() is not None


def circle_price(db, owner_id):
    """The lowest price of the owner's paid circles that take subscribers (0: none does)."""
    row = db.execute(text("SELECT MIN(price_coins) FROM circles WHERE user_id = :u AND kind = 'paid' AND price_coins > 0"),
                     {"u": owner_id}).first()
    return int(row[0]) if row and row[0] else 0
