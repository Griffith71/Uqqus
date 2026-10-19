"""Comment spaces of a post (the owner's "where to post", 2026-10).

A post made for guilds has one row on its author's profile (the hub: author, edits, polls, co-authors) and one row per
guild (a copy: its own votes and comments, `repost_id` pointing at the hub). Each guild's comment section is a *space*.
The hub's own space is the profile's, which a post made for guilds only does not have
(`comment_permission.GUILDS`). On the hub's page the spaces are listed, ranked by the sort the page is using, and one
is opened at a time, or all the guilds' comments together; a guild's own page shows only its own.

Pure (no I/O): routes/post_spaces.py loads the rows and these functions order and label them.
"""

# the comment sorts the post page offers (routes/posts.py `post_base36id_with_comments`), and what each ranks by
SORTS = ("top", "hot", "new", "old", "disputed")
DEFAULT_SORT = "top"
LISTED = 10                   # guilds shown before "more"


def sort_of(raw):
    raw = str(raw or "").strip().lower()
    return raw if raw in SORTS else DEFAULT_SORT


def _number(value):
    return float(value or 0)


def post_key(post, sort):
    """What ranks a guild's copy under a sort (lower comes first). The copies' own ranking numbers are the ones the
    guild's feed uses, so the list reads like a feed of the guilds."""
    sort = sort_of(sort)
    if sort == "hot":
        return -_number(post.score_hot)
    if sort == "new":
        return -_number(post.created_utc)
    if sort == "old":
        return _number(post.created_utc)
    if sort == "disputed":
        return -_number(post.score_disputed)
    return -_number(post.score_top)


def ranked(copies, sort):
    """The copies in order for a sort; ties go to the newer copy."""
    return sorted(copies, key=lambda p: (post_key(p, sort), -_number(p.created_utc)))


def comment_key(comment, sort):
    """What ranks a top-level comment when guilds' comments are shown together (lower comes first)."""
    sort = sort_of(sort)
    if sort == "hot":
        return -_number(comment.score_hot)
    if sort == "new":
        return -_number(comment.created_utc)
    if sort == "old":
        return _number(comment.created_utc)
    if sort == "disputed":
        return -_number(comment.score_disputed)
    return -_number(getattr(comment, "score_top", None) if getattr(comment, "score_top", None) is not None
                    else getattr(comment, "score", 0))


def merge_threads(per_copy, sort):
    """One list of `(copy, thread)` from every copy's top-level threads. A guildmaster's pinned comment stays first
    in its own guild's turn; every thread keeps its comments under it (they belong to one guild's space) and is ordered
    against the others by the same sort that ordered it inside its guild. `per_copy` is [(copy, [top-level comments])]."""
    entries = []
    for copy, threads in per_copy:
        for thread in threads:
            entries.append((copy, thread))
    return sorted(entries, key=lambda e: (not getattr(e[1], "is_pinned", False), comment_key(e[1], sort),
                                          -_number(e[1].created_utc)))


def listed(copies):
    """(shown, more): the first LISTED copies and the rest."""
    return copies[:LISTED], copies[LISTED:]


def label(copy):
    """How a space is named in the list."""
    return f"+{copy.board.name}"
