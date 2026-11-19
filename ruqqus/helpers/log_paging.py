"""How far the Log's pages read for one page (the second review's C56): what a page of the log of an item or an account, and a page of a deleted post's
thread, ask the database for, so that a page costs what it shows and not what came before it.

* **The log of one post, comment or account** (`/post/<id>/log`, `/comment/<id>/log`, `/@name/log`): page N reads entries (N-1)*25 to N*25 of the newest-first
  list with `LIMIT 26 OFFSET (N-1)*25` (`about_window`), where it used to read the first N*25 and draw the last 25, so page 40 hydrated 1,000 entries to
  show 25. Forty pages is as far back as it reads (1,000 lines): the offset is at most 975, which is the bound.
* **The thread of a deleted post** (`/log/post/<id>`): the whole shape of the thread is read as ids alone (`comment_index`: id and parent, one statement,
  oldest first, at most `THREAD_MAX`, the same comments and the same order as before), the page's 25 top-level subtrees are cut from it (`thread_page`) and
  only the comments THOSE hold are loaded as rows. The cap and the depth are the thread's old ones: a thread is a record, not a feed.

Plain `text()` SQL and pure rules, no Flask, tested on SQLite (tests/test_log_paging.py). Nothing here commits. A question about a block is
`helpers/log_thread.py blocked_among`, which is also one statement for the page."""
from sqlalchemy import text

from ruqqus.helpers import log_view

ABOUT_MAX_PAGE = 40         # how far back an item's or account's log reads: 40 pages of 25, 1,000 lines
THREAD_PER_PAGE = 25        # top-level comments a page of a deleted post's thread
THREAD_MAX = 2000           # comments read (as ids) for one thread: a thread is a record, not a feed
THREAD_DEPTH = 8            # a comment this deep is drawn, what is under it is not

_INDEX = text("SELECT id, parent_comment_id FROM comments WHERE parent_submission = :post ORDER BY created_utc ASC, id ASC LIMIT :limit")


def about_window(page):
    """(page, offset) for a page of an item's or an account's log: the page held to 1..`ABOUT_MAX_PAGE` and how many entries come before it."""
    page = max(1, min(int(page), ABOUT_MAX_PAGE))
    return page, (page - 1) * log_view.PER_PAGE


def comment_index(db, post_id, limit=THREAD_MAX):
    """[(comment id, parent comment id or None)] of this post's comments, oldest first (ties by id), at most `limit`: one statement over
    `comment_post_id_index`, however big the thread. What a page of the thread is cut from (`thread_page`)."""
    return [(int(i), int(parent) if parent else None)
            for i, parent in db.execute(_INDEX, {"post": int(post_id), "limit": int(limit)})]


def thread_page(index, page, per_page=THREAD_PER_PAGE, depth=THREAD_DEPTH):
    """(tree, ids, more, total) for page `page` of a thread read as `comment_index`: the page's `per_page` top-level comments, each with its subtree as
    nested {"id", "depth", "children"} nodes, walked oldest first and not deeper than `depth`; `ids` is every id in the tree, parents before their children
    (what to load); `more` is whether top-level comments are left for a later page; `total` is every comment read, which the heading counts. A comment whose
    parent was not read (beyond the cap) is in no tree, as it never was."""
    children, top = {}, []
    for comment_id, parent in index:
        if parent:
            children.setdefault(parent, []).append(comment_id)
        else:
            top.append(comment_id)
    ids = []

    def walk(comment_id, level):
        ids.append(comment_id)
        return {"id": comment_id, "depth": level,
                "children": [walk(child, level + 1) for child in children.get(comment_id, ())] if level < depth else []}

    tree = [walk(comment_id, 0) for comment_id in top[(page - 1) * per_page:page * per_page]]
    return tree, ids, len(top) > page * per_page, len(index)
