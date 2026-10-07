"""Community notes: context an admin adds under a post or comment after people asked for it.

Someone picks "Request a community note" in the flag menu (a NoteRequest). Admins see what
was asked for most (/admin/note_requests) and may write a note (a CommunityNote), which is
then shown under the post or comment to everyone who can see it, with the words "added by
the moderators". Declining is just dismissing the requests.

* A note belongs to the PRIMARY post: a forwarded copy resolves to the post it was copied
  from (`primary_post_id`), so the note is on every copy and a request on a copy counts.
* Nothing here says who wrote or requested anything: notes name the site's moderators, never
  an admin, and the requesters are never shown. The author of an anonymous post is not touched.
* Which items have a live note is kept in this process for `TTL` seconds (a single cheap query),
  so drawing a page of posts costs nothing extra when none of them has a note.
"""
import re
import time

MAX_CHARS = 600
TTL = 30                        # seconds a worker keeps the list of annotated items
DAILY_REQUESTS = 20             # requests one member may make in a day

_CONTROL = re.compile(r"[\x00-\x09\x0b-\x1f\x7f​-‍  ﻿]")
_cache = {"at": 0.0, "posts": frozenset(), "comments": frozenset()}


class NoteError(ValueError):
    """Something the admin can fix; the message is shown to them."""

    def __init__(self, message):
        super().__init__(message)
        self.message = message


# --- pure rules --------------------------------------------------------------------------

def primary_post_id(post):
    """The id a post's requests and note are kept under: the post it was copied from, or itself."""
    return post.repost_id or post.id


def clean_body(text):
    """A note's text: plain, at most MAX_CHARS, a few paragraphs. NoteError when it cannot be."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    text = _CONTROL.sub("", text)
    text = "\n".join(line.rstrip() for line in text.split("\n")).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if not text:
        raise NoteError("Write the note.")
    if len(text) > MAX_CHARS:
        raise NoteError(f"A note can be {MAX_CHARS} characters at most (this one is {len(text)}).")
    return text


# --- which items have a note --------------------------------------------------------------

def _is_comment(item):
    from ruqqus.classes.comment import Comment
    return isinstance(item, Comment)


def _live_ids():
    from flask import g
    from ruqqus.classes.community_notes import CommunityNote

    now = time.time()
    if now - _cache["at"] > TTL:
        rows = g.db.query(CommunityNote.post_id, CommunityNote.comment_id).filter(CommunityNote.removed_utc == 0).all()
        _cache["posts"] = frozenset(p for p, c in rows if p is not None)
        _cache["comments"] = frozenset(c for p, c in rows if c is not None)
        _cache["at"] = now
    return _cache


def forget():
    """The list changed: this worker re-reads it at once (the others within TTL seconds)."""
    _cache["at"] = 0.0


def note_of(item):
    """The live CommunityNote under this post or comment, or None."""
    from flask import g, has_request_context
    from ruqqus.classes.community_notes import CommunityNote

    if item is None or not has_request_context() or not hasattr(g, "db"):
        return None
    comment = _is_comment(item)
    key = item.id if comment else primary_post_id(item)
    if key not in (_live_ids()["comments"] if comment else _live_ids()["posts"]):
        return None

    memo = g.__dict__.setdefault("_community_notes", {})
    if (comment, key) not in memo:
        column = CommunityNote.comment_id if comment else CommunityNote.post_id
        memo[(comment, key)] = g.db.query(CommunityNote).filter(column == key, CommunityNote.removed_utc == 0).first()
    return memo[(comment, key)]


def text_of(item):
    """The note's words for the JSON of a post or comment, or None."""
    note = note_of(item)
    return note.body if note else None
