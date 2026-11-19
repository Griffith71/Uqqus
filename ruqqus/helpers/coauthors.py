"""Co-authored posts: one post that several accounts share, as an Instagram or YouTube "collab".

The author invites up to MAX_COAUTHORS accounts (from the composer, or later from the post's
menu). Each accepts or declines; once accepted the one post carries their name, is listed on their
profile and in the Following feed of the people who follow them. It stays ONE post: votes, comments
and forwards are shared, and only the author edits, deletes or forwards it. A co-author can step off;
the author can take one off or cancel an invitation.

Rules this module keeps (CLAUDE.md):
* **a credit can be anonymous** (slice D3f, the Log plan's decision 21): a post made anonymous from the start gives every co-author an anonymous credit, and any co-author may
  hide theirs ("Remove my name", or by accepting that way) or have it hidden by deleting their account. `post_coauthors.is_anonymous` is the flag; the post's own
  author's credit stays `submissions.is_anonymous`. An anonymous credit is shown only to admins, to the co-author and to the post's author (who invited them), and
  everyone else is shown its alias ("Anonymous #2", helpers/anon_aliases.py) wherever a credit is drawn or counted;
* only a post on the author's own profile (the primary post) has co-authors; a forwarded copy
  resolves to it through `repost_id`;
* nobody is invited across a block, and the refusal does not say who blocked whom.

The pure rules (`parse_names`, `refusal`) are tested without a database; the rest reads and writes
`post_coauthors`.
"""
import re
import time

from ruqqus.helpers.i18n_runtime import N_, tr, trn

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
        raise CoauthorError(tr("That list of co-authors is too long."))
    names, seen = [], set()
    for token in re.split(r"[\s,;]+", text.strip()):
        name = token.lstrip("@")
        if not name:
            continue
        if not _NAME.match(name):
            raise CoauthorError(tr("\"%(name)s\" is not a username.", name=name[:30]))
        if name.lower() not in seen:
            seen.add(name.lower())
            names.append(name)
    if len(names) > MAX_NAMES or len(names) > MAX_COAUTHORS:
        raise CoauthorError(_too_many())
    return names


def _too_many():
    return trn("A post can have %(num)s co-author at most.", "A post can have %(num)s co-authors at most.", MAX_COAUTHORS)


def refusal(name, *, primary, author_id, candidate, count, blocked, already):
    """Why this account cannot be invited, or None. `count` is how many are invited already
    (pending or accepted), `blocked` whether a block exists either way."""
    if not primary:
        return tr("Only a post on your profile can have co-authors.")
    if candidate is None:
        return tr("There is no account named @%(name)s.", name=name)
    if candidate.id == author_id:
        return tr("You are already the author.")
    from ruqqus.helpers import age_declaration
    if candidate.is_deleted or candidate.is_banned or blocked or age_declaration.target_refusal(candidate, "chat"):
        return tr("@%(name)s can't be invited.", name=candidate.username)
    if already:
        return tr("@%(name)s was already invited.", name=candidate.username)
    if count >= MAX_COAUTHORS:
        return _too_many()
    return None


# --- reading and writing ----------------------------------------------------------------------

def _not_banned_now(User, now):
    """The accounts that are not banned or suspended AT THIS MOMENT: `is_banned` is set by a timed suspension too and nothing clears it when the suspension
    lapses, so `is_banned == 0` dropped an account from every byline for good after one short suspension (the second review, C25)."""
    from sqlalchemy import and_, or_
    return or_(User.is_banned == 0, and_(User.unban_utc > 0, User.unban_utc <= now))


def _classes():
    from ruqqus.classes.coauthors import PostCoauthor
    from ruqqus.classes.user import User
    return PostCoauthor, User


def rows_of(db, post_id):
    PostCoauthor, _ = _classes()
    return db.query(PostCoauthor).filter_by(post_id=post_id).order_by(PostCoauthor.id.asc()).all()


def check_names(db, author, names):
    """The first reason a field of names cannot be used on a NEW post, or None. Run before the
    post is made, so that a name that cannot be invited stops the post instead of vanishing."""
    from ruqqus.helpers.get import get_user

    for index, name in enumerate(names):
        candidate = get_user(name, graceful=True)
        message = refusal(name, primary=True, author_id=author.id, candidate=candidate, count=index,
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
        candidate_name, primary=post.repost_id in (0, None) and post.id == primary_id,
        author_id=author.id, candidate=candidate, count=len(existing),
        blocked=bool(candidate and author.any_block_exists(candidate)),
        already=bool(candidate and any(r.user_id == candidate.id for r in existing)))
    if message:
        return None, message
    # a post anonymous from the start gives every credit the same (decision 21), and its invitation never names its author: the author can tell them
    row = PostCoauthor(post_id=primary_id, user_id=candidate.id, invited_by_id=author.id, status=PENDING, created_utc=int(time.time()),
                       is_anonymous=bool(post.is_anonymous))
    db.add(row)
    db.flush()
    if post.is_anonymous:
        _notify(candidate, N_("You were invited to co-author an anonymous post. [See the invitation](%(link)s)"), link=f"/coauthor/{post.base36id}")
    else:
        _notify(candidate, N_("@%(name)s invited you to co-author a post. [See the invitation](%(link)s)"),
                name=author.username, link=f"/coauthor/{post.base36id}")
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


def _notify(user, message, **values):
    """A notification marked for translation (helpers/alerts.notify), best effort like _tell."""
    from flask import g
    from ruqqus.helpers.alerts import notify

    try:
        notify(user, message, **values)
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
            PostCoauthor.post_id == key, PostCoauthor.status == ACCEPTED, User.is_deleted == False, _not_banned_now(User, int(time.time()))
        ).order_by(PostCoauthor.accepted_utc.asc(), PostCoauthor.id.asc()).all()
    return [u for u in memo[key] if not user_hidden(u, v)]


def is_coauthor(post, v):
    return bool(v) and any(u.id == v.id for u in accepted_of(post, v))


# --- credits: what a byline and the pages that draw a co-author show (slice D3f) ----------------------------------------------------------

def sees_credit(user, v):
    """May this viewer be told whose this credit is? Admins, and the co-author themselves (a limited QR sign-in is a stranger to its own anonymous
    credit until the password is given, as to its own anonymous items)."""
    if v is None:
        return False
    if (getattr(v, "admin_level", 0) or 0) >= 3:
        return True
    return v.id == user.id and not getattr(v, "qr_limited", False)


def _credit_rows(post):
    """[(user, is_anonymous)] for every accepted co-author, in the order they accepted, banned accounts left out: a deleted account is here only when its credit
    is anonymous (an account's deletion makes it so), because a named credit of an account that is gone is drawn as nobody."""
    from flask import g, has_request_context

    if post is None or not has_request_context() or not hasattr(g, "db"):
        return []
    key = primary_post_id(post)
    if key not in _live_ids():
        return []
    PostCoauthor, User = _classes()
    memo = g.__dict__.setdefault("_coauthor_credits", {})
    if key not in memo:
        from sqlalchemy import or_
        # an ANONYMOUS credit stays whatever happens to its account: it disappeared from the byline at the moment its holder was publicly suspended, which
        # said who "Anonymous #3" was (the second review, C25). A named one of a banned account is still left out
        found = g.db.query(User, PostCoauthor.is_anonymous).join(PostCoauthor, PostCoauthor.user_id == User.id).filter(
            PostCoauthor.post_id == key, PostCoauthor.status == ACCEPTED,
            or_(_not_banned_now(User, int(time.time())), PostCoauthor.is_anonymous == True)
        ).order_by(PostCoauthor.accepted_utc.asc(), PostCoauthor.id.asc()).all()
        memo[key] = [(u, bool(hidden)) for u, hidden in found if hidden or not u.is_deleted]
    return memo[key]


def credits_of(post, v=None):
    """What to draw for this post's co-authors for this viewer, in order: the account (a `User`) for a named credit, or for an anonymous one the viewer may be
    told; otherwise a stand-in with the credit's alias ("Anonymous #2", the plain stand-in when none was given). A named credit whose name the viewer's word filter
    hides is left out, as before; an anonymous one is never judged by a name it doesn't show."""
    from flask import g
    from ruqqus.helpers import anon_aliases, anonymity
    from ruqqus.helpers.visibility import user_hidden

    rows = _credit_rows(post)
    if not rows:
        return []
    numbers = anon_aliases.numbers_cached(g.db, anon_aliases.scope_key(post)) if any(hidden for _, hidden in rows) else {}
    out = []
    for user, hidden in rows:
        if hidden and not sees_credit(user, v):
            number = numbers.get(user.id)
            out.append(anonymity.AliasedAnonymousAuthor(number) if number else anonymity.ANONYMOUS)
        elif hidden or not user_hidden(user, v):
            out.append(user)
    return out


def named_of(post):
    """The accounts whose credit on this post is not anonymous, in order: for a page that names the people running something (a live thread's header)
    and has no alias to show in their place."""
    return [user for user, hidden in _credit_rows(post) if not hidden and not user.is_deleted]


def hide_credit(db, post, user_id, now=None):
    """Make this co-author's credit anonymous, for good, and give them their number in the post's thread now ("Remove my name", the decision 21's per-person
    rule). Returns whether a credit changed."""
    from ruqqus.helpers import anon_aliases

    PostCoauthor, _ = _classes()
    row = db.query(PostCoauthor).filter_by(post_id=primary_post_id(post), user_id=user_id, status=ACCEPTED).first()
    if row is None or row.is_anonymous:
        return False
    row.is_anonymous = True
    db.add(row)
    anon_aliases.give(db, anon_aliases.scope_key(post), user_id, now)
    forget()
    return True
