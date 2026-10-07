"""Trending topics as the app uses them: which posts count, the lists that are kept, and
reading the list a viewer gets. The rules that find and score a topic are in
helpers/trending.py (pure); scripts/compute_trending.py calls `compute` every few minutes.

Lists are kept per scope and per word-filter level:
    scope         "all", "lang:<language code>", "region:<region code>"
    filter_level  0 Off, 1 Standard, 2 Child (only posts that level may see are counted,
                  and a topic whose own words the level hides is left out)

Rules this module keeps (CLAUDE.md):
* a region list never counts an anonymous post: its region is its author's, and the region
  filter does not match anonymous posts through their author either;
* a topic's posts are read back through helpers/visibility.py and the viewer's blocks;
* authors are only ever counted here, never shown.
"""
import json
import os
import time

from sqlalchemy import and_, func, or_, select

from ruqqus.helpers import trending as tr
from ruqqus.helpers.visibility import filter_posts
from ruqqus.helpers.muting import hide_muted
from ruqqus.helpers.wordfilter import is_hidden

SITE = "all"
LEVELS = (0, 1, 2)
MAX_POSTS = 20000            # newest posts read per run (the day itself plus the week before)
SCOPE_MIN_TOPICS = 3         # a region or language with fewer topics falls back to the site's list
STALE_SECONDS = 3600         # a list this old is not shown: the job is not running
KEEP_POST_IDS = 300
HIDDEN_SUBCATS = (44, 108)   # as frontlist(): not on All for a viewer with the word filter on
PAGE = 25


def min_authors():
    """How many different accounts a topic needs. One account can never be enough."""
    try:
        return max(2, int(os.environ.get("TRENDING_MIN_AUTHORS", tr.MIN_AUTHORS)))
    except ValueError:
        return tr.MIN_AUTHORS


def blocked_keys(db):
    from ruqqus.classes.trending import TrendingBlocked
    return {key for (key,) in db.query(TrendingBlocked.key).all()}


# --- computing ---------------------------------------------------------------------------

def _load(db, since, now):
    """The posts that count: live, public, on the site's open pages, by accounts in good
    standing. A forwarded copy is not counted beside its original."""
    from ruqqus.classes import Board, Submission, SubmissionAux, User

    return db.query(
        Submission.id, Submission.author_id, Submission.created_utc, Submission.language_code,
        Submission.is_anonymous, Submission.upvotes, Submission.downvotes, SubmissionAux.title,
        func.left(SubmissionAux.body, tr.TEXT_CHARS).label("text"), SubmissionAux.url,
        SubmissionAux.meta_title, User.display_region, Board.subcat_id,
    ).join(
        SubmissionAux, SubmissionAux.id == Submission.id
    ).join(
        User, User.id == Submission.author_id
    ).join(
        Board, Board.id == Submission.board_id
    ).filter(
        Submission.created_utc >= since,
        Submission.created_utc <= now,
        Submission.is_banned == False,
        Submission.deleted_utc == 0,
        Submission.post_public == True,
        Submission.is_bot == False,
        func.coalesce(Submission.repost_id, 0) == 0,
        Submission.board_id != 1,              # as frontlist()
        Board.is_banned == False,
        Board.is_private == False,
        Board.all_opt_out == False,
        User.is_deleted == False,
        or_(User.is_banned == 0, and_(User.unban_utc > 0, User.unban_utc < now)),
    ).order_by(Submission.id.desc()).limit(MAX_POSTS).all()


def _comment_counts(db, post_ids):
    from ruqqus.classes import Comment

    if not post_ids:
        return {}
    rows = db.query(Comment.parent_submission, func.count(Comment.id)).filter(
        Comment.parent_submission.in_(post_ids), Comment.is_banned == False, Comment.deleted_utc == 0
    ).group_by(Comment.parent_submission).all()
    return dict(rows)


def _visible_ids(db, since, level):
    from ruqqus.classes import Submission

    query = db.query(Submission.id).filter(Submission.created_utc >= since)
    return {pid for (pid,) in filter_posts(query, None, level=level).all()}


def scopes_of(row):
    """The lists a post counts for."""
    scopes = [SITE]
    if row.language_code:
        scopes.append(f"lang:{row.language_code}")
    if row.display_region and not row.is_anonymous:
        scopes.append(f"region:{row.display_region}")
    return scopes


def compute(db, now=None, own_hosts=(), log=None):
    """Work out every list and replace the stored rows. Returns {scope: {level: topics}}."""
    from ruqqus.classes.trending import TrendingTopic
    from ruqqus.helpers.word_filter_store import get_filter

    now = int(now or time.time())
    start = now - tr.WINDOW_SECONDS
    since = start - tr.BASELINE_DAYS * 86400
    need = min_authors()

    rows = _load(db, since, now)
    comments = _comment_counts(db, [r.id for r in rows if r.created_utc >= start])

    def as_post(r):
        return tr.Post(r.id, r.author_id, r.created_utc, title=r.title, text=r.text, url=r.url, link_title=r.meta_title,
                       language=r.language_code, votes=(r.upvotes or 0) - (r.downvotes or 0), comments=comments.get(r.id, 0))

    window = [(r, tr.Entry(p, tr.terms_of(p, own_hosts))) for r, p in ((r, as_post(r)) for r in rows if r.created_utc >= start)]
    # last week's posts only matter for terms that could trend at all
    interesting = tr.candidates([e for _, e in window], need)
    baseline = []
    for r in rows:
        if r.created_utc < start:
            p = as_post(r)
            baseline.append((r, tr.Entry(p, frozenset(k for k in tr.terms_of(p, own_hosts) if k in interesting))))

    blocked = blocked_keys(db)
    word_filter = get_filter(db)
    severity = {}

    def label_hidden(level, label):
        if label not in severity:
            severity[label] = word_filter.severity(label)
        return is_hidden(level, severity[label])

    out, result = [], {}
    for level in LEVELS:
        visible = _visible_ids(db, since, level)

        def counts(r):
            return r.id in visible and (level == 0 or r.subcat_id not in HIDDEN_SUBCATS)

        by_scope = {}
        for group, index in ((window, 0), (baseline, 1)):
            for r, entry in group:
                if counts(r):
                    for scope in scopes_of(r):
                        by_scope.setdefault(scope, ([], []))[index].append(entry)

        for scope, (now_entries, before) in sorted(by_scope.items()):
            if scope != SITE and len({e.post.author_id for e in now_entries}) < need:
                continue
            topics = tr.rank(now_entries, before, now, min_authors=need, blocked=blocked, top=tr.TOP * 2)
            topics = [t for t in topics if not label_hidden(level, t.label)][:tr.TOP]
            if scope != SITE and len(topics) < SCOPE_MIN_TOPICS:
                continue
            result.setdefault(scope, {})[level] = topics
            for position, topic in enumerate(topics, 1):
                out.append(TrendingTopic(
                    scope=scope, filter_level=level, rank=position, key=topic.key[:320], slug=topic.slug,
                    kind=topic.kind, label=topic.label[:120], score=topic.score, post_count=len(topic.post_ids),
                    author_count=topic.authors, post_ids=json.dumps(topic.post_ids[:KEEP_POST_IDS]), computed_utc=now))

    db.query(TrendingTopic).delete()
    db.add_all(out)
    db.commit()
    if log:
        log(f"{len(rows)} posts read, {len(out)} rows in {len(result)} lists")
    return result


# --- reading -----------------------------------------------------------------------------

def scopes_for(regions=(), languages=()):
    """The lists a viewer's region and language filters name, regions first."""
    return [f"region:{code}" for code in regions or ()] + [f"lang:{code}" for code in languages or ()]


def current(db, level, regions=(), languages=(), now=None):
    """(scopes the list came from, its rows best first) for a viewer browsing at `level`.
    The viewer's own regions and languages when they have a list, otherwise the whole site."""
    from ruqqus.classes.trending import TrendingTopic

    fresh = int(now or time.time()) - STALE_SECONDS
    blocked = blocked_keys(db)

    def read(scopes):
        rows = db.query(TrendingTopic).filter(
            TrendingTopic.scope.in_(scopes), TrendingTopic.filter_level == level, TrendingTopic.computed_utc >= fresh
        ).order_by(TrendingTopic.rank.asc()).all()
        return [r for r in rows if not tr.is_blocked(r.key, blocked)]

    wanted = scopes_for(regions, languages)
    rows = read(wanted) if wanted else []
    if rows:
        return [s for s in wanted if any(r.scope == s for r in rows)], tr.merge([rows])
    return [SITE], tr.merge([read([SITE])])


def find(db, slug, level, regions=(), languages=()):
    """(label, post ids newest first) of a topic on one of the viewer's lists, or (None, [])."""
    from ruqqus.classes.trending import TrendingTopic

    blocked = blocked_keys(db)
    rows = db.query(TrendingTopic).filter(
        TrendingTopic.slug == slug, TrendingTopic.filter_level == level,
        TrendingTopic.scope.in_(scopes_for(regions, languages) + [SITE])
    ).order_by(TrendingTopic.score.desc()).all()
    rows = [r for r in rows if not tr.is_blocked(r.key, blocked)]
    if not rows:
        return None, []
    ids = sorted({pid for r in rows for pid in r.post_id_list}, reverse=True)
    return rows[0].label, ids


def visible_post_ids(db, v, post_ids, sort="new", page=1):
    """One page (plus one, to know whether there is a next) of a topic's posts that this
    viewer may see: the same rules as any feed."""
    from ruqqus.classes import BoardBlock, Submission, UserBlock

    if not post_ids:
        return []
    posts = db.query(Submission.id).filter(
        Submission.id.in_(post_ids),
        Submission.is_banned == False,
        Submission.deleted_utc == 0,
        Submission.post_public == True,
    )
    posts = filter_posts(posts, v)
    posts = hide_muted(posts, v, Submission)
    if v:
        if v.hide_bot:
            posts = posts.filter(Submission.is_bot == False)
        posts = posts.filter(
            Submission.author_id.notin_(select(UserBlock.target_id).filter_by(user_id=v.id)),
            Submission.board_id.notin_(select(BoardBlock.board_id).filter_by(user_id=v.id)),
        )
    order = Submission.score_top.desc() if sort == "top" else Submission.created_utc.desc()
    return [pid for (pid,) in posts.order_by(order, Submission.id.desc()).offset(PAGE * (page - 1)).limit(PAGE + 1).all()]


# --- admin -------------------------------------------------------------------------------

def overview(db):
    """Every stored list, for /admin/trending: [(scope, level, rows)]."""
    from ruqqus.classes.trending import TrendingTopic

    rows = db.query(TrendingTopic).order_by(
        TrendingTopic.scope.asc(), TrendingTopic.filter_level.asc(), TrendingTopic.rank.asc()).all()
    groups = {}
    for row in rows:
        groups.setdefault((row.scope != SITE, row.scope, row.filter_level), []).append(row)
    return [(scope, level, group) for (_, scope, level), group in sorted(groups.items())]


def block(db, key, label="", admin_id=None):
    from ruqqus.classes.trending import TrendingBlocked

    key = " ".join((key or "").split())[:320]
    if not key.startswith("link:"):
        key = key.casefold()
    if not key or db.query(TrendingBlocked).filter_by(key=key).first():
        return False
    db.add(TrendingBlocked(key=key, label=(label or key)[:120], admin_id=admin_id, created_utc=int(time.time())))
    db.commit()
    return True


def unblock(db, blocked_id):
    from ruqqus.classes.trending import TrendingBlocked

    db.query(TrendingBlocked).filter_by(id=blocked_id).delete()
    db.commit()
