"""One vote per person across the copies of a post (the vote route is routes/votes.py).

A forwarded post is an independent copy in a guild (`repost_id` points at the original) with its own votes
and comments. So that one person can't vote on the original and on every copy, a person may hold an ACTIVE
vote (up or down) in at most one live member of a *family*: the original post plus every row whose
`repost_id` points at it (forward copies, and the legacy pre-Forward resubmits that also set `repost_id`).
The original counts as one of the copies. A forwarded comment becomes a new, unrelated post and has no family.

- Removing a vote (`x=0`) is always allowed and frees the person to vote on another copy.
- Members that are removed, deleted or in a banned guild don't count (their votes are out of every feed).
  Archived members do count: their votes are frozen but real.
- The post's author is exempt: they get an automatic upvote on the original and on every copy, and keep voting
  on all of them.
- Votes cast before this rule are left as they are; the rule only refuses to add or change a vote while another
  active one exists in the family (`scripts/report_cross_copy_votes.py` counts them).

The database parts are plain SQL (`sqlalchemy.text`) so they run on Postgres and, in tests, on SQLite. The
route calls `refusal`, which takes a per-person, per-family advisory lock first: two simultaneous votes on two
copies by one account queue up, and the second one sees the first.
"""
from sqlalchemy import bindparam, text

from ruqqus.helpers.base36 import base36encode
from ruqqus.helpers.coauthors import primary_post_id

ARCHIVE_AFTER_SECONDS = 60 * 60 * 24 * 180      # the same age as Submission.is_archived


# --- the rules, without a database ---------------------------------------------------------------------

def exempt(viewer_id, author_id):
    """The post's author: automatic upvote on every copy, free to vote on all of them."""
    return viewer_id == author_id


def is_archived(created_utc, now):
    return int(now) - int(created_utc) > ARCHIVE_AFTER_SECONDS


def place_label(guild_name, profile_name):
    """Where a vote is, as a person reads it: "the original post" (it sits on the author's profile) or "+guild"."""
    return "the original post" if (guild_name or "").lower() == profile_name.lower() else f"+{guild_name}"


def message(label, archived):
    if archived:
        return f"You already voted on this post, in {label}, and that vote can't be changed any more."
    return f"You already voted on this post, in {label}. Remove that vote to vote here."


# --- the database --------------------------------------------------------------------------------------

# "live": not removed, not deleted, and not in a banned guild
_LIVE = ("COALESCE(s.is_banned, :no) = :no AND COALESCE(s.deleted_utc, 0) = 0 "
         "AND COALESCE(b.is_banned, :no) = :no")

_OTHER = text(f"""
    SELECT v.submission_id AS id, b.name AS guild, s.created_utc AS created
    FROM votes v
    JOIN submissions s ON s.id = v.submission_id
    JOIN boards b ON b.id = s.board_id
    WHERE v.user_id = :user
      AND v.vote_type <> 0
      AND v.submission_id <> :post
      AND (s.id = :family OR s.repost_id = :family)
      AND {_LIVE}
    ORDER BY v.created_utc, v.id
    LIMIT 1
""")

_PAGE = text(f"""
    SELECT COALESCE(NULLIF(s.repost_id, 0), s.id) AS family, s.id AS id, b.name AS guild, s.created_utc AS created
    FROM votes v
    JOIN submissions s ON s.id = v.submission_id
    JOIN boards b ON b.id = s.board_id
    WHERE v.user_id = :user
      AND v.vote_type <> 0
      AND (s.id IN :families OR s.repost_id IN :families)
      AND {_LIVE}
    ORDER BY v.created_utc, v.id
""").bindparams(bindparam("families", expanding=True))


def _profile_name():
    from ruqqus.classes.boards import PROFILE_BOARD_NAME
    return PROFILE_BOARD_NAME


def lock(db, user_id, family_id):
    """Queue behind any other vote by this person in this family until the transaction ends (the route's commit).
    Postgres only: the lock is what makes the check below safe against two simultaneous votes."""
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:user, :family)"), {"user": int(user_id), "family": int(family_id)})


def other_vote(db, user_id, post):
    """The person's earliest active vote in another live member of `post`'s family, or None."""
    return db.execute(_OTHER, {"user": user_id, "post": post.id, "family": primary_post_id(post), "no": False}).first()


def refusal(db, viewer, post, now):
    """None when `viewer` may vote on `post`; otherwise the JSON to answer with (the message, and where the vote is)."""
    if exempt(viewer.id, post.author_id):
        return None

    lock(db, viewer.id, primary_post_id(post))
    row = other_vote(db, viewer.id, post)
    if row is None:
        return None

    label = place_label(row.guild, _profile_name())
    return {"error": message(label, is_archived(row.created, now)),
            "voted_in": {"id": base36encode(row.id), "guild": row.guild, "label": label}}


def attach(db, posts, viewer, now):
    """Give each post of a page `_voted_elsewhere`: None, or where the viewer's vote in its family is, so the page
    can lock the arrows. One query for the whole page. A post the viewer has voted on themselves is never locked
    (they must be able to change or remove that vote), and neither is their own post."""
    posts = list(posts)
    for post in posts:
        post._voted_elsewhere = None
    if viewer is None or not posts:
        return

    mine = [post for post in posts if not exempt(viewer.id, post.author_id) and not getattr(post, "_voted", 0)]
    if not mine:
        return

    families = sorted({primary_post_id(post) for post in mine})
    votes = {}
    for row in db.execute(_PAGE, {"user": viewer.id, "families": families, "no": False}):
        votes.setdefault(row.family, []).append(row)

    profile = _profile_name()
    for post in mine:
        elsewhere = [row for row in votes.get(primary_post_id(post), []) if row.id != post.id]
        if elsewhere:
            row = elsewhere[0]
            label = place_label(row.guild, profile)
            post._voted_elsewhere = {"id": base36encode(row.id), "guild": row.guild, "label": label,
                                     "message": message(label, is_archived(row.created, now))}
