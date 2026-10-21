from ruqqus.classes import *
from ruqqus.helpers.wrappers import *
from ruqqus.helpers.visibility import (comment_thread_hidden, filter_boards, filter_comments, filter_posts,
                                       filter_users, post_hidden, viewer_level)
from ruqqus.helpers.muting import hide_muted
from ruqqus.helpers.blocks import hide_blocked
from urllib.parse import quote
import re

from sqlalchemy import *

from flask import *
from ruqqus.classes.domains import reasons as REASONS
from ruqqus.__main__ import app, cache
from ruqqus.helpers import search_comments, search_filters, search_query
from ruqqus.helpers.get import LIKE_ESCAPE, like_literal
from ruqqus.helpers import topics as topic_rules
from ruqqus.helpers.curation_feed import FeedTooSlow, limited
from ruqqus.helpers.languages import LANGUAGE_NAMES
from ruqqus.helpers.profile_about import shown_curations
from ruqqus.helpers.word_filter_store import name_severity
from ruqqus.helpers.wordfilter import is_hidden



# the post search's operators (from:, in:, before:, has:, lang:, ...) are helpers/search_query.py,
# their conditions helpers/search_filters.py; this module keeps who may see what
SORTS = ("hot", "top", "new", "old", "disputed", "activity")
TIME_LIMIT_MS = 5000


def listable_boards(boards, v):
    """A query over Board, narrowed to the guilds this viewer may be shown in a list: not word-filtered, banned only for
    admins, and a Circle guild only for its members (contributors and guildmasters), as everywhere else."""
    boards = filter_boards(boards, v)

    if not (v and v.admin_level >= 3):
        boards = boards.filter_by(is_banned=False)

    if not (v and v.admin_level >= 4):
        member_of = select(ContributorRelationship.board_id).where(
            ContributorRelationship.user_id == (v.id if v else 0), ContributorRelationship.is_active == True
        ).union(select(ModRelationship.board_id).where(
            ModRelationship.user_id == (v.id if v else 0), ModRelationship.accepted == True,
            ModRelationship.invite_rescinded == False))
        boards = boards.filter(or_(Board.is_circle == False, Board.id.in_(member_of)))

    return boards


@app.get("/api/guild_search")
@auth_required
def guild_search(v):
    """Guilds a signed-in member could post to, for the "Where to post" box in the composers: name part, up to 8,
    exact name first, then guilds they joined, then the biggest. A Circle guild has its own composer, a guild the member
    is exiled from or may not forward to is left out, and the visibility rules are the guild list's (`listable_boards`)."""
    term = (request.args.get("q") or "").strip().lstrip("+")[:40]
    term = term.replace('\\', '').replace('_', r'\_').replace('%', '')

    boards = g.db.query(Board).filter(
        Board.name.ilike(f"%{term}%"),
        func.lower(Board.name) != PROFILE_BOARD_NAME,
        Board.is_circle == False)
    boards = listable_boards(boards, v)
    joined = select(Subscription).filter_by(user_id=v.id, is_active=True).subquery()
    boards = boards.join(joined, joined.c.board_id == Board.id, isouter=True).order_by(
        Board.name.ilike(term).desc(), joined.c.id.is_(None).asc(), Board.stored_subscriber_count.desc())

    found = []
    for board in boards.limit(30):
        if board.has_ban(v):
            continue
        if (board.restricted_forwarding or board.is_private) and not board.can_forward(v):
            continue
        found.append({"name": board.name, "members": max(board.stored_subscriber_count or 0, 0)})
        if len(found) == 8:
            break
    response = jsonify({"guilds": found})
    response.headers["Cache-Control"] = "private, no-store"
    return response


def sort_of(raw, default):
    raw = (raw or "").lower()
    return raw if raw in SORTS else default


@cache.memoize(300)
def searchlisting(query, v=None, page=1, t="None", sort="top", b=None, filter_level=None):

    posts = g.db.query(Submission).options(
                lazyload('*')
            ).join(
                Submission.submission_aux,
            ).join(
                Submission.author
            ).join(
                Submission.board
            )

    posts = search_filters.apply(posts, query, v)

    if b:
        posts=posts.filter(Submission.board_id==b.id)

    posts = filter_posts(posts, v)
    # a search for one author is not filtered by mutes: going to their posts is a choice
    if not query.for_one_author:
        posts = hide_muted(posts, v, Submission)
		
    if v and v.hide_bot:
        posts = posts.filter(Submission.is_bot == False)

    if not(v and v.admin_level >= 3):
        posts = posts.filter(
            Submission.deleted_utc == 0,
            Submission.is_banned == False,
            )

    if v and v.admin_level >= 4:
        pass
    elif v:
        m = select(ModRelationship.board_id).filter_by(
            user_id=v.id, invite_rescinded=False
        ).scalar_subquery()
        c = select(
            ContributorRelationship.board_id
        ).filter_by(
            user_id=v.id
        ).scalar_subquery()
        posts = posts.filter(
            or_(
                Submission.author_id == v.id,
                # public when shared, and not in a guild that has since turned private (audit A-01)
                and_(Submission.post_public == True, Board.is_private == False),
                Submission.board_id.in_(m),
                Submission.board_id.in_(c)
            )
        )

        # blocked either way (helpers/blocks.py), and the guilds the viewer filtered out, as All does
        posts = hide_blocked(posts, v, Submission).filter(Board.is_banned==False)
        if not b:
            posts = posts.filter(Submission.board_id.notin_(select(BoardBlock.board_id).filter_by(user_id=v.id)))
    else:
        posts = posts.filter(
            Submission.post_public == True,
            Board.is_private == False,
            Board.is_banned==False,
            )

    if t:
        now = int(time.time())
        if t == 'day':
            cutoff = now - 86400
        elif t == 'week':
            cutoff = now - 604800
        elif t == 'month':
            cutoff = now - 2592000
        elif t == 'year':
            cutoff = now - 31536000
        else:
            cutoff = 0
        posts = posts.filter(Submission.created_utc >= cutoff)

    posts=posts.options(
        contains_eager(Submission.submission_aux),
        contains_eager(Submission.author),
        contains_eager(Submission.board)
        )

    if sort == "hot":
        posts = posts.order_by(Submission.score_hot.desc())
    elif sort == "new":
        posts = posts.order_by(Submission.created_utc.desc())
    elif sort == "old":
        posts = posts.order_by(Submission.created_utc.asc())
    elif sort == "disputed":
        posts = posts.order_by(Submission.score_disputed.desc())
    elif sort == "top":
        posts = posts.order_by(Submission.score_top.desc())
    elif sort == "activity":
        posts = posts.order_by(Submission.score_activity.desc())

    def run():
        return posts.count(), [x.id for x in posts.offset(25 * (page - 1)).limit(26).all()]

    # what a member types can ask for a slow query: it runs under a time limit (FeedTooSlow)
    return limited(g.db, run, TIME_LIMIT_MS)


def post_search(raw, v, page, t, sort, b=None):
    """(total, ids, query, notices) for the post search: the problems of the query (an unknown
    name, a bad date, ...) are notices and the search does not run."""
    query, notes = search_filters.resolve(search_query.parse(raw), v, b=b)
    if query.problems:
        return 0, [], query, list(query.problems) + notes
    try:
        total, ids = searchlisting(query, v=v, page=page, t=t, sort=sort, b=b, filter_level=viewer_level(v))
    except FeedTooSlow:
        return 0, [], query, [search_query.TOO_SLOW] + notes
    return total, ids, query, notes


COMMENT_SORTS = ("top", "new", "old")


@cache.memoize(300)
def comment_searchlisting(query, v=None, page=1, sort="top", b=None, filter_level=None):
    """(total, ids) of comments matching a resolved, prepared query. Who may see what follows the site's comment list
    (routes/front.py `comment_idlist`) and the post search above: the word filter, mutes (not for a search of one
    author's comments), blocks either way (the comment's author and its post's), removed and deleted comments and
    posts, banned guilds, a guild the viewer muted, private guilds and Circles (a post that is not public is only for
    its author, the guild's mods and contributors). `filter_level` is part of the cache key."""
    comments = g.db.query(Comment.id).join(
        CommentAux, CommentAux.id == Comment.id
    ).join(
        Submission, Submission.id == Comment.parent_submission      # a system message has no post: it is never found
    ).join(
        Board, Board.id == Submission.board_id
    )

    comments = search_comments.apply(comments, query, v)

    if b:
        comments = comments.filter(Submission.board_id == b.id)

    comments = filter_comments(comments, v)
    if not query.for_one_author:
        comments = hide_muted(comments, v, Comment)
    if v and v.hide_bot:
        comments = comments.filter(Comment.is_bot == False)

    # removed and deleted items belong on the logs, not in a search, admins included
    comments = comments.filter(Comment.is_banned == False, Comment.deleted_utc == 0,
                               Submission.is_banned == False, Submission.deleted_utc == 0,
                               Board.is_banned == False)

    if v and v.admin_level >= 4:
        pass
    elif v:
        m = select(ModRelationship.board_id).filter_by(user_id=v.id, invite_rescinded=False).scalar_subquery()
        c = select(ContributorRelationship.board_id).filter_by(user_id=v.id).scalar_subquery()
        comments = comments.filter(or_(
            Submission.author_id == v.id,
            and_(Submission.post_public == True, Board.is_private == False),
            Submission.board_id.in_(m),
            Submission.board_id.in_(c)))
        comments = hide_blocked(comments, v, Comment)
        comments = hide_blocked(comments, v, Submission)
        if not b:
            comments = comments.filter(Submission.board_id.notin_(select(BoardBlock.board_id).filter_by(user_id=v.id)))
    else:
        comments = comments.filter(Submission.post_public == True, Board.is_private == False)

    if sort == "new":
        comments = comments.order_by(Comment.created_utc.desc())
    elif sort == "old":
        comments = comments.order_by(Comment.created_utc.asc())
    else:
        comments = comments.order_by(Comment.score_top.desc())

    def run():
        return comments.count(), [x[0] for x in comments.offset(25 * (page - 1)).limit(26).all()]

    return limited(g.db, run, TIME_LIMIT_MS)


def comment_search(raw, v, page, sort, b=None):
    """(total, ids, query, notices) for the comment search, as `post_search`: a problem with the query, or one that
    would read every comment, is a notice and the search does not run."""
    query, notes = search_filters.resolve(search_query.parse(raw), v, b=b)
    if query.problems:
        return 0, [], query, list(query.problems) + notes
    query, dropped = search_comments.prepare(query)
    notes = notes + dropped
    broad = search_comments.problem(query)
    if broad:
        return 0, [], query, [broad] + notes
    try:
        total, ids = comment_searchlisting(query, v=v, page=page, sort=sort, b=b, filter_level=viewer_level(v))
    except FeedTooSlow:
        return 0, [], query, [search_query.TOO_SLOW] + notes
    return total, ids, query, notes


def comments_for(ids, v):
    """The comments of a page of results, in the order found. One whose ancestor the viewer's filter hides (it can't be
    checked in SQL), or whose post they may not open, is left out, so a page can come up short."""
    found = {c.id: c for c in get_comments(ids, v=v)}
    shown = []
    for cid in ids:
        comment = found.get(cid)
        if comment is None or comment_thread_hidden(comment, v):
            continue
        post = comment.post
        if post is None or post_hidden(post, v) or post.in_private_guild_for(v):
            continue
        shown.append(comment)
    return shown


def comment_search_response(query, v, page, b=None):
    """The results page (or JSON) of a comment search, for the site's search and a guild's."""
    sort = sort_of(request.args.get("sort"), "top")
    sort = sort if sort in COMMENT_SORTS else "top"
    total, ids, parsed, notices = comment_search(query, v, page, sort, b=b)
    next_exists = len(ids) == 26
    comments = comments_for(ids[0:25], v)
    return {"html": lambda: render_template("search_comments.html",
                                            v=v,
                                            query=query,
                                            total=total,
                                            page=page,
                                            listing=comments,
                                            standalone=True,
                                            sort_method=sort,
                                            time_filter="all",
                                            next_exists=next_exists,
                                            notices=notices,
                                            b=b,
                                            comment_search=True,
                                            **search_page(parsed)),
            "api": lambda: jsonify({"data": [x.json for x in comments], "next_exists": next_exists,
                                    "notices": notices})}


def search_page(query):
    """What the results page shows about the query: a chip per operator with the query
    without it (its remove link), and the advanced form filled in."""
    chips = [(search_query.label(p), search_query.without(query, i))
             for i, p in enumerate(query.parts) if p.kind not in search_query.TEXT]
    return {"chips": chips, "form": search_query.form_fields(query), "post_search": True,
            "languages": sorted(LANGUAGE_NAMES.items(), key=lambda x: x[1]),
            "has_kinds": search_query.FORM_HAS, "has_labels": search_query.HAS_LABELS}


def advanced_redirect(base):
    """The advanced search form, sent back as one search in the box."""
    a = request.args
    q = search_query.compose(words=a.get("words", ""), author=a.get("from", ""), guild=a.get("in", ""),
                             since=a.get("since", ""), until=a.get("until", ""), has=a.getlist("has"),
                             lang=a.get("lang", ""), no_forwards=bool(a.get("no_forwards")))
    sort = request.args.get("sort", "")
    return redirect(f"{base}?q={quote(q)}" + (f"&sort={quote(sort)}" if sort in SORTS else ""))


@app.route("/search", methods=["GET"])
@app.route("/api/v1/search", methods=["GET"])
@app.route("/api/vue/search")
@auth_desired
@api("read")
def search(v, search_type="posts"):

    if request.args.get("adv"):
        return advanced_redirect("/search")

    query = request.args.get("q", '').lstrip().rstrip()

    page = page_number()

    if query.startswith("+"):

        # guild search stuff here
        sort = request.args.get("sort", "subs").lower()
    
        term=query.lstrip('+')
        term=term.replace('\\','')
        term=term.replace('_', r'\_')

        boards = g.db.query(Board).filter(
            Board.name.ilike(f'%{term}%'),
            func.lower(Board.name) != PROFILE_BOARD_NAME)
        boards = listable_boards(boards, v)

        if v:
            joined = select(Subscription).filter_by(user_id=v.id, is_active=True).subquery()

            boards=boards.join(
                joined,
                joined.c.board_id==Board.id,
                isouter=True
                )

            boards=boards.order_by(
                Board.name.ilike(term).desc(),
                joined.c.id.is_(None).asc(),
                Board.stored_subscriber_count.desc(),
                )



        else:

            boards = boards.order_by(
                Board.name.ilike(term).desc(), 
                Board.stored_subscriber_count.desc()
                )

        total = boards.count()

        boards = [x for x in boards.offset(25 * (page - 1)).limit(26)]
        next_exists = (len(boards) == 26)
        boards = boards[0:25]

        return {"html":lambda:render_template("search_boards.html",
                               v=v,
                               query=query,
                               total=total,
                               page=page,
                               boards=boards,
                               sort_method=sort,
                               next_exists=next_exists
                               ),
                "api":lambda:jsonify({"data":[x.json for x in boards]})
                }

    elif query.startswith("#"):

        # topic search: `#gam` is the topics whose name holds "gam" (a category or a subcategory, helpers/topics.py), and
        # the results are the guilds in them
        found = topic_rules.matching(query)
        subcat_ids = sorted({i for t in found for i in t.subcat_ids})

        boards = g.db.query(Board).filter(
            Board.subcat_id.in_(subcat_ids) if subcat_ids else false(),
            func.lower(Board.name) != PROFILE_BOARD_NAME)
        boards = listable_boards(boards, v).order_by(Board.stored_subscriber_count.desc(), Board.name.asc())

        total = boards.count()

        boards = [x for x in boards.offset(25 * (page - 1)).limit(26)]
        next_exists = (len(boards) == 26)
        boards = boards[0:25]

        return {"html":lambda:render_template("search_boards.html",
                               v=v,
                               query=query,
                               total=total,
                               page=page,
                               boards=boards,
                               topics_found=found,
                               sort_method=request.args.get("sort", "subs").lower(),
                               next_exists=next_exists
                               ),
                "api":lambda:jsonify({"topics": [{"name": t.label, "slug": t.slug, "permalink": t.url} for t in found],
                                      "data":[x.json for x in boards]})
                }

    elif query.startswith("&"):

        # curation search: public curations whose name or slug holds the text. A private curation is never found,
        # its owner included (they have their own list); a name the viewer's word filter hides is left out as it is drawn
        term = query.lstrip('&')
        term = term.replace('\\', '')
        term = term.replace('_', r'\_')

        found = g.db.query(Curation).join(User, User.id == Curation.owner_id).filter(
            Curation.is_private == False,
            User.is_deleted == False,
            User.is_banned == 0,
            or_(Curation.name.ilike(f'%{term}%'), Curation.slug.ilike(f'%{term}%')))
        found = found.order_by(Curation.slug.ilike(term).desc(), Curation.created_utc.desc())

        total = found.count()

        rows = [x for x in found.offset(25 * (page - 1)).limit(26)]
        next_exists = (len(rows) == 26)
        level = viewer_level(v)
        curations, _ = shown_curations(rows[0:25], lambda name: is_hidden(level, name_severity(name or "", g.db)), limit=25)

        followed_ids = set()
        if v and curations:
            followed_ids = {r[0] for r in g.db.query(CurationFollow.curation_id).filter(
                CurationFollow.user_id == v.id, CurationFollow.curation_id.in_([c.id for c in curations])).all()}

        return {"html":lambda:render_template("search_curations.html",
                               v=v,
                               query=query,
                               total=total,
                               page=page,
                               curations=curations,
                               followed_ids=followed_ids,
                               sort_method="new",
                               next_exists=next_exists
                               ),
                "api":lambda:jsonify({"data":[{"name": c.name, "slug": c.slug, "description": c.description,
                                               "owner": c.owner.username, "permalink": c.permalink}
                                              for c in curations]})
                }

    elif query.startswith("@"):
            
        # a handle OR a display name (helpers/display_names.py): what a person types is a name to find, so `%` and `_`
        # in it are letters, not wildcards (the older code escaped `_` and let `%` through)
        term = like_literal(query.lstrip('@'))

        now=int(time.time())
        users=g.db.query(User).filter(or_(
            User.username.ilike(f'%{term}%', escape=LIKE_ESCAPE),
            User.display_name.ilike(f'%{term}%', escape=LIKE_ESCAPE)))
        users=filter_users(users, v)
        
        
        if not (v and v.admin_level >= 3):
            users=users.filter(
            User.is_private==False,
            User.is_deleted==False,
            or_(
              User.is_banned==0,
              and_(
                User.is_banned>0,
		User.unban_utc>0,
                User.unban_utc<now
              )
            )
        )
        # the exact handle first, then an exact display name, then the most followed
        users=users.order_by(User.username.ilike(term, escape=LIKE_ESCAPE).desc(),
                             User.display_name.ilike(term, escape=LIKE_ESCAPE).desc(),
                             User.stored_subscriber_count.desc())
        
        total=users.count()
        
        users=[x for x in users.offset(25 * (page-1)).limit(26)]
        next_exists=(len(users)==26)
        users=users[0:25]
        
        
        
        return {"html":lambda:render_template("search_users.html",
                       v=v,
                       query=query,
                       total=total,
                       page=page,
                       users=users,
                       next_exists=next_exists
                      ),
                "api":lambda:jsonify({"data":[x.json for x in users]})
                }
                   
    

    elif request.args.get("type") == "comments":
        return comment_search_response(query, v, page)

    else:
        sort = sort_of(request.args.get("sort"), "top")
        t = request.args.get('t', 'all').lower()

        # posts search

        total, ids, parsed, notices = post_search(query, v, page, t, sort)

        next_exists = (len(ids) == 26)
        ids = ids[0:25]

        posts = get_posts(ids, v=v)

        site = parsed.first("domain")
        if v and v.admin_level>3 and site:
            domain=site.value
            domain_obj=get_domain(domain)
        else:
            domain=None
            domain_obj=None

        return {"html":lambda:render_template("search.html",
                               v=v,
                               query=query,
                               total=total,
                               page=page,
                               listing=posts,
                               sort_method=sort,
                               time_filter=t,
                               next_exists=next_exists,
                               notices=notices,
                               domain=domain,
                               domain_obj=domain_obj,
                               reasons=REASONS,
                               **search_page(parsed)
                               ),
                "api":lambda:jsonify({"data":[x.json for x in posts], "next_exists": next_exists,
                                      "notices": notices})
                }


@app.route("/+<name>/search", methods=["GET"])
@app.get("/api/v2/guilds/<name>/search")
@auth_desired
@api("read")
def search_guild(name, v, search_type="posts"):


    if request.args.get("adv"):
        return advanced_redirect(f"/+{quote(name)}/search")

    query=request.args.get("q","").lstrip().rstrip()

    if query.startswith(("+","@")):
        return redirect(f"/search?q={quote(query)}")

    b = get_guild(name, graceful=True)
    if not b:
        abort(404)

    if b.is_banned:
        return render_template("board_banned.html", v=v, b=b)

    page=page_number()

    if request.args.get("type") == "comments":
        return comment_search_response(query, v, page, b=b)

    sort=sort_of(request.args.get("sort"), "hot")
    
    
    t = request.args.get('t', 'all').lower()

    #posts search

    total, ids, parsed, notices = post_search(query, v, page, t, sort, b=b)

    next_exists=(len(ids)==26)
    ids=ids[0:25]

    posts=get_posts(ids, v=v)

    return {"html": lambda: render_template("search.html",
                   v=v,
                   query=query,
                   total=total,
                   page=page,
                   listing=posts,
                       sort_method=sort,
                           next_exists=next_exists,
               time_filter=t,
                           notices=notices,
                           b=b,
                           **search_page(parsed)
                   ),
            "api": lambda: jsonify({"data": [x.json for x in posts], "next_exists": next_exists, "notices": notices})
            }
