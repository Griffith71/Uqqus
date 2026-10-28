"""Where a post stands among the posts made around it, for the post page's Post Info box (the owner's 2026-10-23 ask):
"#3 of 41 posts in +photography on 23 October" and "Top 5% in +photography that week" for a guild copy, "Top 8% of
posts on Laba on 23 October" for a post on a profile. Counts only, by score (`score_top`, the votes' own number); the
lines say nothing about who voted or who else posted.

The windows are the post's own UTC day and the 7 days ending with it, so a post keeps the standing it earned rather
than being compared with whatever was posted today. A line is drawn only when it says something: a day with one post,
a week of a handful, or a share in the bottom half, are left out.
"""
from dataclasses import dataclass

from ruqqus.helpers.i18n_runtime import date_text, tr, trn

DAY = 86400
RANK_MIN = 2           # a guild's day needs this many posts for "#N of M"
SHARE_MIN = 10         # a window needs this many posts for "Top N%"
SHARE_MAX = 50         # "Top 60%" is not worth saying


@dataclass(frozen=True)
class Standing:
    rank_line: str = ""          # "#3 of 41 posts in +photography on 23 October"
    share_line: str = ""         # "Top 5% in +photography that week"

    def __bool__(self):
        return bool(self.rank_line or self.share_line)


def day_window(created_utc):
    """(start, end) of the UTC day a post was made in."""
    start = int(created_utc) // DAY * DAY
    return start, start + DAY


def week_window(created_utc):
    """(start, end): the 7 UTC days ending with the post's own."""
    start, end = day_window(created_utc)
    return end - 7 * DAY, end


def day_label(created_utc):
    """"23 October" (UTC) in English; the day and month (no year, as in English) in another language."""
    from ruqqus.helpers.text import date_pattern
    return date_text(created_utc, "%d %B", date_pattern("MMMMd", "d MMMM")).lstrip("0")


def rank_line(higher, total, where, created_utc):
    """`higher` posts in the window scored more than this one, of `total`."""
    if total < RANK_MIN or higher >= total:
        return ""
    # Translators: a post's rank among the posts made in a guild that day, such as "#3 of 41 posts in +photography on
    # 23 October"
    return trn("#%(rank)s of %(num)s post in %(guild)s on %(date)s", "#%(rank)s of %(num)s posts in %(guild)s on %(date)s",
               total, rank=higher + 1, guild=where, date=day_label(created_utc))


def top_share(higher, total):
    """The smallest whole "Top N%" that holds this post, or None when it isn't worth saying."""
    if total < SHARE_MIN or higher >= total:
        return None
    share = max(1, -(-(higher + 1) * 100 // total))          # ceil, at least 1
    return share if share <= SHARE_MAX else None


def share_line(higher, total, where):
    """"Top 5% in +photography that week" (`where` is the guild, as "+photography")."""
    share = top_share(higher, total)
    # Translators: how a post did among a guild's posts of the 7 days ending with its own, such as "Top 5% in
    # +photography that week"
    return tr("Top %(share)s%% in %(guild)s that week", share=share, guild=where) if share is not None else ""


# --- the counts ------------------------------------------------------------------------------------------------------

_LIVE = "s.is_banned = false AND s.deleted_utc = 0 AND s.created_utc >= :start AND s.created_utc < :end"

GUILD_SQL = ("SELECT count(*) FILTER (WHERE s.score_top > :score), count(*) FROM submissions s "
             "WHERE s.board_id = :board AND " + _LIVE)

# a post on a profile against the site's other original public posts (not guild copies, not Spaces)
SITE_SQL = ("SELECT count(*) FILTER (WHERE s.score_top > :score), count(*) FROM submissions s "
            "WHERE s.board_id = :board AND s.post_public = true AND coalesce(s.repost_id, 0) = 0 "
            "AND coalesce(s.is_space, false) = false AND " + _LIVE)


def counts(db, sql, board, start, end, score):
    from sqlalchemy import text
    row = db.execute(text(sql), {"board": board, "start": start, "end": end, "score": score or 0}).first()
    return int(row[0] or 0), int(row[1] or 0)


def standing_for(db, p, site_name, counted=counts):
    """The Standing of post `p` (empty for a removed, deleted or Space post)."""
    if p.is_banned or p.deleted_utc or getattr(p, "is_space", False):
        return Standing()
    score = p.score_top or 0
    if p.is_profile_post:
        higher, total = counted(db, SITE_SQL, p.board_id, *day_window(p.created_utc), score)
        share = top_share(higher, total)
        # Translators: how a post did among the site's posts of its day, such as "Top 8% of posts on Laba on 23 October"
        line = (tr("Top %(share)s%% of posts on %(site)s on %(date)s", share=share, site=site_name,
                   date=day_label(p.created_utc)) if share is not None else "")
        return Standing(share_line=line)
    where = f"+{p.board.name}"
    return Standing(rank_line=rank_line(*counted(db, GUILD_SQL, p.board_id, *day_window(p.created_utc), score),
                                        where, p.created_utc),
                    share_line=share_line(*counted(db, GUILD_SQL, p.board_id, *week_window(p.created_utc), score),
                                          where))
