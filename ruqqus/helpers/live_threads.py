"""Live threads: a post its author marks Live, with timestamped updates above its comments, as Reddit's live threads
(pure rules, no I/O; SQL in helpers/live_thread_store.py, routes in routes/live_threads.py, the panel
partials/live_panel.html with assets/js/live_thread.js).

- **Who runs it**: the post's author and its accepted co-authors (helpers/coauthors.py), on a live post (not removed
  or deleted). Everyone who may read the post reads its updates: they have the post's audience, through the same
  rules as opening it (routes/live_comments.readable_post).
- **How long**: up to MAX_DAYS from going live, and it ends by itself after IDLE_HOURS without an update, or when the
  author ends it. It belongs to the primary post, so every forwarded copy shows the same updates.
- **Updates** are markdown up to UPDATE_MAX characters, rendered and sanitized like a comment, rated by the word filter
  (a reader's own filter hides a rated one) and pushed to everyone reading the post (`live.update`, only its id: each
  reader asks for it drawn for them). UPDATES_PER_DAY a post. An update on an anonymous post never names who wrote it.
- **Comments** stay as they are; a reader of a live post is told when new ones come (routes/live_comments.py).
- When it goes live, the people who follow the post (helpers/post_follows.py) are told, if they may still read it.
"""

MAX_DAYS = 7
IDLE_HOURS = 24
UPDATE_MAX = 2000
UPDATES_PER_DAY = 200


class Refused(ValueError):
    """Shown to the member as it is."""


def is_live(started_utc, ended_utc, last_update_utc, now):
    if not started_utc or ended_utc:
        return False
    if now - started_utc > MAX_DAYS * 86400:
        return False
    return now - max(started_utc, last_update_utc or 0) <= IDLE_HOURS * 3600


def may_run(viewer_id, author_id, coauthor_ids):
    return viewer_id is not None and (viewer_id == author_id or viewer_id in set(coauthor_ids or ()))


def start_refusal(may, removed, deleted, already_live):
    if not may:
        return "Only the post's author and co-authors can run its live updates."
    if removed or deleted:
        return "This post can't go live."
    if already_live:
        return "This post is live already."
    return None


def clean_update(text):
    body = (text or "").strip()
    if not body:
        raise Refused("Write the update first.")
    if len(body) > UPDATE_MAX:
        raise Refused(f"An update can be at most {UPDATE_MAX:,} characters.")
    return body


def update_refusal(may, live, today):
    if not may:
        return "Only the post's author and co-authors can post updates."
    if not live:
        return "This post isn't live."
    if today >= UPDATES_PER_DAY:
        return f"A post can have at most {UPDATES_PER_DAY} updates a day."
    return None
