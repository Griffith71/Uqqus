"""Co-authored posts: one post that several accounts share, as an Instagram or YouTube "collab".

The author invites up to MAX_COAUTHORS accounts (from the composer, or later from the post's
menu). Each accepts or declines; once accepted the one post carries their name, is listed on their
profile and in the Following feed of the people who follow them. It stays ONE post: votes, comments
and forwards are shared, and only the author edits, deletes or forwards it. A co-author can step off;
the author can take one off or cancel an invitation.

Rules this module keeps (CLAUDE.md):
* never on an anonymous post: names are shown, so it would undo the anonymity (`is_anonymous` is
  set at creation only, and a post with co-authors cannot be made anonymous);
* only a post on the author's own profile (the primary post) has co-authors; a forwarded copy
  resolves to it through `repost_id`;
* nobody is invited across a block, and the refusal does not say who blocked whom.

The pure rules (`parse_names`, `refusal`) are tested without a database; the rest reads and writes
`post_coauthors`.
"""
import re
import time

MAX_COAUTHORS = 5              # pending and accepted together
MAX_NAMES = 20                 # names read from one field before it is refused
FIELD_CHARS = 200
PENDING, ACCEPTED = "pending", "accepted"
TTL = 30                       # seconds a worker keeps the list of posts that have co-authors

_NAME = re.compile(r"^[A-Za-z0-9_]{1,25}$")
_cache = {"at": 0.0, "ids": frozenset()}


class CoauthorError(ValueError):
    """Something the member can fix; the message is shown to them."""

    def __init__(self, message):
        super().__init__(message)
        self.message = message


# --- pure rules ---------------------------------------------------------------------------

def primary_post_id(post):
    """The post invitations are kept under: the post a copy was forwarded from, or itself."""
    return post.repost_id or post.id


def parse_names(text):
    """The usernames in a field ("@ann, bob carol"), each once, in order. CoauthorError for
    something that is not a username, too many, or a field that is too long."""
    text = text or ""
    if len(text) > FIELD_CHARS:
        raise CoauthorError("That list of co-authors is too long.")
    names, seen = [], set()
    for token in re.split(r"[\s,;]+", text.strip()):
        name = token.lstrip("@")
        if not name:
            continue
        if not _NAME.match(name):
            raise CoauthorError(f"\"{name[:30]}\" is not a username.")
        if name.lower() not in seen:
            seen.add(name.lower())
            names.append(name)
    if len(names) > MAX_NAMES or len(names) > MAX_COAUTHORS:
        raise CoauthorError(f"A post can have {MAX_COAUTHORS} co-authors at most.")
    return names


def refusal(name, *, anonymous, primary, author_id, candidate, count, blocked, already):
    """Why this account cannot be invited, or None. `count` is how many are invited already
    (pending or accepted), `blocked` whether a block exists either way."""
    if anonymous:
        return "A co-authored post can't be anonymous."
    if not primary:
        return "Only a post on your profile can have co-authors."
    if candidate is None:
        return f"There is no account named @{name}."
    if candidate.id == author_id:
        return "You are already the author."
    if candidate.is_deleted or candidate.is_banned or blocked:
        return f"@{candidate.username} can't be invited."
    if already:
        return f"@{candidate.username} was already invited."
    if count >= MAX_COAUTHORS:
        return f"A post can have {MAX_COAUTHORS} co-authors at most."
    return None


# --- reading and writing ----------------------------------------------------------------------

def _classes():
    from ruqqus.classes.coauthors import PostCoauthor
    from ruqqus.classes.user import User
    return PostCoauthor, User


def rows_of(db, post_id):
    PostCoauthor, _ = _classes()
    return db.query(PostCoauthor).filter_by(post_id=post_id).order_by(PostCoauthor.id.asc()).all()


def check_names(db, author, names, *, anonymous):
    """The first reason a field of names cannot be used on a NEW post, or None. Run before the
    post is made, so that a name that cannot be invited stops the post instead of vanishing."""
    from ruqqus.helpers.get import get_user

    for index, name in enumerate(names):
        candidate = get_user(name, graceful=True)
        message = refusal(name, anonymous=anonymous, primary=True, author_id=author.id, candidate=candidate, count=index,
                          blocked=bool(candidate and author.any_block_exists(candidate)), already=False)
        if message:
            return message
    return None


def invite(db, author, post, candidate_name):
    """Invite one account to the post. (row, None) or (None, why not)."""
    from ruqqus.helpers.get import get_user

    PostCoauthor, _ = _classes()
    primary_id = primary_post_id(post)
    candidate = get_user(candidate_name, graceful=True)
    existing = rows_of(db, primary_id)
    message = refusal(
        candidate_name, anonymous=bool(post.is_anonymous), primary=post.repost_id in (0, None) and post.id == primary_id,
        author_id=author.id, candidate=candidate, count=len(existing),
        blocked=bool(candidate and author.any_block_exists(candidate)),
        already=bool(candidate and any(r.user_id == candidate.id for r in existing)))
    if message:
        return None, message
    row = PostCoauthor(post_id=primary_id, user_id=candidate.id, invited_by_id=author.id, status=PENDING, created_utc=int(time.time()))
    db.add(row)
    db.flush()
    _tell(candidate, f"@{author.username} invited you to co-author a post. [See the invitation](/coauthor/{post.base36id})")
    return row, None


def invite_names(db, author, post, names):
    """Invite each name; (invited usernames, [reasons]). Used right after a post is made."""
    invited, errors = [], []
    for name in names:
        row, why = invite(db, author, post, name)
        (errors.append(why) if why else invited.append(name))
    db.commit()
    return invited, errors


def _tell(user, text):
    """A notification, best effort: a failure to tell must never undo an invitation."""
    from flask import g
    from ruqqus.helpers.alerts import send_notification

    try:
        send_notification(user, text)
    except Exception:
        g.db.rollback()


def tell(user, text):
    _tell(user, text)


# --- which posts have co-authors (for bylines, without a query per card) -------------------------

def _live_ids():
    from flask import g
    PostCoauthor, _ = _classes()

    now = time.time()
    if now - _cache["at"] > TTL:
        _cache["ids"] = frozenset(i for (i,) in g.db.query(PostCoauthor.post_id).filter_by(status=ACCEPTED).distinct().all())
        _cache["at"] = now
    return _cache["ids"]


def forget():
    """The accepted list changed: this worker re-reads it at once (the others within TTL seconds)."""
    _cache["at"] = 0.0


def accepted_of(post, v=None):
    """The accounts that co-author this post, in the order they accepted, as shown to `v`: not
    deleted or banned accounts, and not a name the viewer's word filter hides."""
    from flask import g, has_request_context
    from ruqqus.helpers.visibility import user_hidden

    if post is None or not has_request_context() or not hasattr(g, "db"):
        return []
    key = primary_post_id(post)
    if key not in _live_ids():
        return []
    PostCoauthor, User = _classes()
    memo = g.__dict__.setdefault("_coauthors", {})
    if key not in memo:
        memo[key] = g.db.query(User).join(PostCoauthor, PostCoauthor.user_id == User.id).filter(
            PostCoauthor.post_id == key, PostCoauthor.status == ACCEPTED, User.is_deleted == False, User.is_banned == 0
        ).order_by(PostCoauthor.accepted_utc.asc(), PostCoauthor.id.asc()).all()
    return [u for u in memo[key] if not user_hidden(u, v)]


def is_coauthor(post, v):
    return bool(v) and any(u.id == v.id for u in accepted_of(post, v))
