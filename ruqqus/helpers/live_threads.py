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

Since 2026-10-16 it reads like Reddit's live threads (the owner's choice):
- **A chosen length** (`DURATIONS`, the poll lengths): it ran for 1 hour to 7 days (`ends_utc`; since 2026-10-19 no post
  starts one: a Space's live updates use this machinery, started and ended with the Space, routes/spaces.py), can be extended while live (`extend_until`, never past MAX_DAYS from its start) or
  ended early, and waits RESTART_WAIT before it can start again, so the people who follow it are not told over and
  over. A thread started before then has no `ends_utc` and keeps the old rule (`ends_at`).
- **Corrections**: the writer of an update, or the post's author, crosses it out (shown "Corrected", undone the same
  way) or deletes it (`may_change_update`).
- **A pinned summary** (Reddit's "resources"): SUMMARY_MAX characters of markdown the runners keep up to date.
- **Who is watching** is a count only, never who: a page counts while it has asked in the last WATCH_SECONDS.
"""

from ruqqus.helpers.polls import DURATIONS

MAX_DAYS = 7
IDLE_HOURS = 24
UPDATE_MAX = 2000
UPDATES_PER_DAY = 200
HOURS = tuple(hours for hours, _ in DURATIONS)
DEFAULT_HOURS = 24
RESTART_WAIT = 3600
SUMMARY_MAX = 1000
WATCH_SECONDS = 60


class Refused(ValueError):
    """Shown to the member as it is."""


def ends_at(started_utc, last_update_utc, ends_utc=0):
    """When a running thread stops by itself: its chosen end, or (a thread started before lengths were chosen) a day
    after its last update and never past a week from its start."""
    if ends_utc:
        return min(ends_utc, started_utc + MAX_DAYS * 86400)
    return min(started_utc + MAX_DAYS * 86400, max(started_utc, last_update_utc or 0) + IDLE_HOURS * 3600)


def is_live(started_utc, ended_utc, last_update_utc, now, ends_utc=0):
    if not started_utc or ended_utc:
        return False
    return now < ends_at(started_utc, last_update_utc, ends_utc)


def ended_at(started_utc, ended_utc, last_update_utc, ends_utc=0):
    """When a thread that is no longer live stopped (ended by hand, or ran out)."""
    return ended_utc or ends_at(started_utc, last_update_utc, ends_utc)


def clean_hours(raw):
    """How long a thread runs, in hours: one of HOURS (blank is the default)."""
    value = str(raw if raw is not None else "").strip()
    if not value:
        return DEFAULT_HOURS
    if value.isascii() and value.isdigit() and int(value) in HOURS:
        return int(value)
    raise Refused("Pick how long the live thread runs.")


def extend_until(started_utc, now, hours):
    """The new end of a running thread extended by `hours` from now, never past MAX_DAYS from its start."""
    return min(now + hours * 3600, started_utc + MAX_DAYS * 86400)


def may_run(viewer_id, author_id, coauthor_ids):
    return viewer_id is not None and (viewer_id == author_id or viewer_id in set(coauthor_ids or ()))


def start_refusal(may, removed, deleted, already_live, stopped_utc=0, now=0):
    """Why the thread can't start (or start again) now. `stopped_utc`: when it last stopped, if it ran before."""
    if not may:
        return "Only the post's author and co-authors can run its live updates."
    if removed or deleted:
        return "This post can't go live."
    if already_live:
        return "This post is live already."
    if stopped_utc and now - stopped_utc < RESTART_WAIT:
        minutes = max(1, (RESTART_WAIT - (now - stopped_utc) + 59) // 60)
        return f"This live thread ended a moment ago. It can go live again in {minutes} min."
    return None


def may_change_update(viewer_id, update_author_id, post_author_id):
    """Who may cross out or delete an update: its writer, and the post's author (who runs the thread)."""
    return viewer_id is not None and viewer_id in (update_author_id, post_author_id)


def clean_summary(text):
    """The pinned summary as written (empty clears it)."""
    body = (text or "").strip()
    if len(body) > SUMMARY_MAX:
        raise Refused(f"The summary can be at most {SUMMARY_MAX:,} characters.")
    return body


def runners_line(names, anonymous):
    """Who writes the updates, for the header: "Updates by @a and @b" (an anonymous post: the author, unnamed)."""
    if anonymous or not names:
        return "Updates by the author"
    names = [f"@{n}" for n in names]
    if len(names) == 1:
        return f"Updates by {names[0]}"
    return f"Updates by {', '.join(names[:-1])} and {names[-1]}"


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
