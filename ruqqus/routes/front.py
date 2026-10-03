import time
from flask import *
from sqlalchemy import *
from sqlalchemy.orm import lazyload
import random

from ruqqus.helpers.wrappers import *
from ruqqus.helpers.get import *
from flask import session as flask_session

from ruqqus.__main__ import app, cache
from ruqqus.classes.submission import Submission
from ruqqus.classes.categories import CATEGORIES
from ruqqus.helpers.languages import LANGUAGE_NAMES
from ruqqus.helpers.regions import REGION_CENTROIDS


@app.route("/post/", methods=["GET"])
def slash_post():
    return redirect("/")


@app.get("/notifications")
@app.get("/notifications/all")
@app.get("/notifications/mentions")
@app.get("/notifications/replies")
@app.get("/notifications/system")
@app.route("/api/v1/notifications", methods=["GET"])
@auth_required
@api("read")
def notifications(v):

    page = int(request.args.get('page', 1))
    all_ = request.args.get('all', False)

    cids = v.notification_commentlisting(page=page,
                                         all_=request.path=="/notifications/all",
                                         mentions_only=request.path=="/notifications/mentions",
                                         replies_only=request.path=="/notifications/replies",
                                         system_only=request.path=="/notifications/system"
                                         )
    next_exists = (len(cids) == 26)
    cids = cids[0:25]

    comments = get_comments(cids, v=v, sort_type="new", load_parent=True)

    listing = []
    for c in comments:
        c._is_blocked = False
        c._is_blocking = False
        c.replies = []
        if c.author_id == 1:
            c._is_system = True
            listing.append(c)
        elif c.level > 1 and c.parent_comment and c.parent_comment.author_id == v.id:
            c._is_comment_reply = True
            parent = c.parent_comment

            if parent in listing:
                parent.replies = parent.replies + [c]
            else:
                parent.replies = [c]
                listing.append(parent)

        elif c.level == 1 and c.post.author_id == v.id:
            c._is_post_reply = True
            listing.append(c)
        else:
            c._is_username_mention = True
            listing.append(c)

    return {'html': lambda: render_template("notifications.html",
                            v=v,
                            notifications=listing,
                            next_exists=next_exists,
                            page=page,
                            standalone=True,
                            render_replies=True,
                            is_notification_page=True),
            'api': lambda: jsonify({"data": [x.json for x in listing]})}

@app.get("/notifications/posts")
@auth_required
@api("read")
def notifications_posts(v):

    page=int(request.args.get("page", 1))

    pids=v.notification_postlisting(
        page=page,
        all_=request.args.get("all")
        )

    next_exists=(len(pids)==26)
    pids=pids[0:25]

    posts=get_posts(pids, v=v, sort="new")

    return {'html': lambda: render_template("notifications_posts.html",
                            v=v,
                            notifications=posts,
                            next_exists=next_exists,
                            page=page,
                            is_notification_page=True),
            'api': lambda: jsonify({"data": [x.json for x in listing]})
            }

@cache.memoize(timeout=900)
def frontlist(v=None, sort=None, page=1,
              t=None, categories=[], filter_words='', region=None, language=None, **kwargs):

    # cutoff=int(time.time())-(60*60*24*30)

    if sort == None:
        if v: sort = v.defaultsorting
        else: sort = "hot"

    if sort == "hot":
        sort_func = Submission.score_hot.desc
    elif sort == "new":
        sort_func = Submission.created_utc.desc
    elif sort == "old":
        sort_func = Submission.created_utc.asc
    elif sort == "disputed":
        sort_func = Submission.score_disputed.desc
    elif sort == "top":
        sort_func = Submission.score_top.desc
    elif sort == "activity":
        sort_func = Submission.score_activity.desc
    else:
        abort(400)

    posts = g.db.query(
        Submission
        ).options(
            lazyload('*'),
            Load(Board).lazyload('*')
        ).filter_by(
            is_banned=False,
            stickied=False
        ).filter(Submission.deleted_utc == 0)

    if (v and v.hide_offensive) or not v:
        posts = posts.filter_by(is_offensive=False)
    
    if v and v.hide_bot:
        posts = posts.filter(Submission.is_bot==False)

    if v and v.admin_level >= 4:
        board_blocks = select(BoardBlock.board_id).filter_by(
            user_id=v.id
        ).subquery()

        posts = posts.filter(Submission.board_id.notin_(board_blocks))
    elif v:
        m = select(ModRelationship.board_id).filter_by(
            user_id=v.id, invite_rescinded=False
        ).subquery()
        c = select(ContributorRelationship.board_id).filter_by(
            user_id=v.id
        ).subquery()

        posts = posts.filter(
            or_(
                Submission.author_id == v.id,
                Submission.post_public == True,
                Submission.board_id.in_(m),
                Submission.board_id.in_(c)
            )
        )

        blocking = select(UserBlock.target_id).filter_by(
            user_id=v.id
        ).subquery()
        # blocked = g.db.query(
        #     UserBlock.user_id).filter_by(
        #     target_id=v.id).subquery()
        posts = posts.filter(
            Submission.author_id.notin_(blocking) #,
        #    Submission.author_id.notin_(blocked)
        )

        board_blocks = select(BoardBlock.board_id).filter_by(
            user_id=v.id
        ).subquery()

        posts = posts.filter(Submission.board_id.notin_(board_blocks))
    else:
        posts = posts.filter(Submission.post_public==True)

    # board opt out of all
    if v:
        posts = posts.join(Submission.board).filter(
            or_(
                Board.all_opt_out == False,
                Submission.board_id.in_(
                    select(Subscription.board_id).filter_by(
                        user_id=v.id,
                        is_active=True
                    ).subquery()
                )
            )
        )
    else:

        posts = posts.join(
            Submission.board).filter_by(
            all_opt_out=False)

    
    if categories:
        posts=posts.filter(Board.subcat_id.in_(tuple(categories)))

    if region:
        region_list = region if isinstance(region, (list, tuple)) else [region]
        posts = posts.filter(region_filter_condition(region_list))

    if language:
        language_list = language if isinstance(language, (list, tuple)) else [language]
        posts = posts.filter(Submission.language_code.in_(language_list))


    if (v and v.hide_offensive) or not v:
        posts=posts.filter(
            Board.subcat_id.notin_([44, 108]) 
            )

    posts=posts.filter(Submission.board_id!=1)

    posts=posts.options(contains_eager(Submission.board))


    #custom filter
    #print(filter_words)
    if v and filter_words:
        posts=posts.join(Submission.submission_aux)
        for word in filter_words:
            #print(word)
            posts=posts.filter(not_(SubmissionAux.title.ilike(f'%{word}%')))

    if t == None and v: t = v.defaulttime
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

    gt = kwargs.get("gt")
    lt = kwargs.get("lt")

    if gt:
        posts = posts.filter(Submission.created_utc > gt)

    if lt:
        posts = posts.filter(Submission.created_utc < lt)

    if sort == "hot":
        posts = posts.order_by(Submission.score_best.desc())
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
    else:
        abort(400)

    return [x.id for x in posts.offset(25 * (page - 1)).limit(26).all()]


@cache.memoize(timeout=3600)
def sidebar_regions():
    """All regions ordered by display name, for the left-sidebar Regional
    filter panel. Cached since region renames (ruqqus/routes/regions.py's
    proposal/voting system) are rare, but this needs to run on every page
    view that renders sidebar-left.html - i.e. virtually every page."""
    return g.db.query(Region).order_by(Region.current_name).all()


# All (name, id, parent-category-name) subcategory rows, including the
# synthetic "Uncategorized" = 0, computed once at import time like CATEGORIES
# itself - the single source of truth for the Categorical catalog and for
# resolving ids back to display names for the "Showing posts in X" notice.
ALL_SUBCATS = sorted(
    [(subcat.name, subcat.id, cat.name) for cat in CATEGORIES for subcat in cat.subcats]
    + [("Uncategorized", 0, "")],
    key=lambda x: x[0]
)
SUBCAT_NAME_BY_ID = {id_: name for name, id_, catname in ALL_SUBCATS}

# Used to detect and self-heal sessions saved before "Show all" meant "empty
# filter" (it used to check every single box and save the full id list
# explicitly).
ALL_SUBCAT_IDS = set(SUBCAT_NAME_BY_ID.keys())


def _resolve_active_filters(v):
    """The combined set of active Regional/Language/Categorical selections,
    shared by all three filter routes so that landing on any one of them
    applies all three dimensions together, not just its own."""
    region_codes = flask_session.get('selected_regions') or ([v.display_region] if v and v.display_region else [])
    language_codes = flask_session.get('langcodes') or []
    subcat_ids = flask_session.get('catids') or []
    return region_codes, language_codes, subcat_ids


def _filter_display_context(region_codes, language_codes, subcat_ids):
    """The display-name context vars the 'Showing posts from/detected as/in
    X' notices in home.html need, shared by All/For You/Following now that
    region/language/category are ambient modifiers on all three rather than
    dedicated pages of their own."""
    region_objs = g.db.query(Region).filter(Region.code.in_(region_codes)).all() if region_codes else []
    return {
        "current_regions": region_codes,
        "region_names": [r.current_name for r in region_objs],
        "current_languages": language_codes,
        "language_names_list": [LANGUAGE_NAMES.get(c) for c in language_codes],
        "current_subcats": subcat_ids,
        "subcat_names": [SUBCAT_NAME_BY_ID[i] for i in subcat_ids if i in SUBCAT_NAME_BY_ID],
    }


def region_filter_condition(region_list):
    """A Submission matches a region filter if ANY of these is true:
    - its own author is in one of region_list (the common case - also
      covers Forward copies, which keep the ORIGINAL author's author_id);
    - it was forwarded by someone in region_list (ForwardRelationship;
      tracked separately from authorship - see create_forward_post());
    - it was reposted (Twitter-retweet-style, via RepostRelationship - no
      new Submission row, any number of distinct users) by anyone in
      region_list.
    Pure filter expression (correlated EXISTS subqueries) - adds no joins
    to the caller's query, so it composes safely regardless of what's
    already been joined."""

    author_match = Submission.author_id.in_(
        select(User.id).where(User.display_region.in_(region_list))
    )
    forwarder_match = exists(
        select(ForwardRelationship.id)
        .join(User, User.id == ForwardRelationship.forwarded_by_id)
        .where(
            ForwardRelationship.forward_submission_id == Submission.id,
            User.display_region.in_(region_list)
        )
    )
    reposter_match = exists(
        select(RepostRelationship.id)
        .join(User, User.id == RepostRelationship.user_id)
        .where(
            RepostRelationship.submission_id == Submission.id,
            User.display_region.in_(region_list)
        )
    )
    return or_(author_match, forwarder_match, reposter_match)


@app.context_processor
def inject_sidebar_filter_data():
    """Makes CATEGORIES/the three filter catalogs/the three active-selection
    sets available to every rendered template without requiring every route
    across the whole codebase to remember to pass them explicitly -
    sidebar-left.html (desktop) and the mobile filter modals are included
    unconditionally, and both need the exact same data. Flask only invokes
    context processors when a template is actually rendered, so this has
    zero cost for pure-JSON/API responses. Explicit render_template() kwargs
    of the same name still take precedence over these defaults."""

    stored_catids = flask_session.get('catids')
    if stored_catids and set(stored_catids) == ALL_SUBCAT_IDS:
        flask_session['catids'] = []
        flask_session.modified = True

    categorical_catalog = [
        {"value": id_, "label": name, "search": f"{name} {catname}".strip()}
        for name, id_, catname in ALL_SUBCATS
    ]

    regions_catalog = [
        {"value": r.code, "label": r.current_name, "search": r.current_name}
        for r in sidebar_regions()
    ]

    sorted_languages = sorted(LANGUAGE_NAMES.items(), key=lambda x: x[1])
    languages_catalog = [
        {"value": code, "label": name, "search": name}
        for code, name in sorted_languages
    ]

    v = getattr(g, 'v', None)
    active_regions, active_languages, active_subcats = _resolve_active_filters(v)

    return {
        "CATEGORIES": CATEGORIES,
        "regions_catalog": regions_catalog,
        "languages_catalog": languages_catalog,
        "categorical_catalog": categorical_catalog,
        "active_regions": active_regions,
        "active_languages": active_languages,
        "active_subcats": active_subcats,
    }


@app.route("/following", methods=["GET"])
@app.route("/api/v1/front/listing", methods=["GET"])
@app.route("/api/v2/me/submissions")
@auth_desired
@api("read")
def following(v):
    """
Get the Following feed: posts from subscribed guilds and followed accounts,
narrowed by whichever Regional/Language/Categorical filters are currently
active (ambient modifiers shared with All and For You - see
_resolve_active_filters()).

Pre-existing quirk, preserved as-is: only engages when the viewer has at
least one *active guild subscription* specifically (not just follows) and
otherwise falls through to All.

Optional query parameters:
* `sort` - One of `hot`, `new`, `top`, `disputed`, `activity`. Default `hot`.
* `t` - One of `day`, `week`, `month`, `year`, `all`. Default `all`.
* `page` - Page of results to return. Default `1`.
"""

    if not request.path.startswith(('/api/', '/inpage/')):
        flask_session['base_feed'] = 'following'
        flask_session.modified = True

    if v and [i for i in v.subscriptions if i.is_active]:

        only=request.args.get("only",None)

        if v:
            defaultsorting = v.defaultsorting
            defaulttime = v.defaulttime
        else:
            defaultsorting = "hot"
            defaulttime = "all"

        sort=request.args.get("sort",defaultsorting)
        t=request.args.get('t', defaulttime)
        page=max(int(request.args.get("page",1)),0)
        ignore_pinned = bool(request.args.get("ignore_pinned", False))

        region_codes, language_codes, subcat_ids = _resolve_active_filters(v)

        ids=v.idlist(sort=sort,
                     page=page,
                     only=only,
                     t=t,
                     filter_words=v.filter_words,

                     # these arguments don't really do much but they exist for
                     # cache memoization differentiation
                     hide_offensive=v.hide_offensive,
                     hide_bot=v.hide_bot,

                     #greater/less than
                     gt=int(request.args.get("utc_greater_than",0)),
                     lt=int(request.args.get("utc_less_than",0)),

                     region=region_codes,
                     language=language_codes,
                     categories=subcat_ids,

                     )

        next_exists=(len(ids)==26)
        ids=ids[0:25]

        # If page 1, check for sticky
        if page == 1 and not ignore_pinned:
            sticky = g.db.query(Submission.id).filter_by(stickied=True).first()


            if sticky:
                ids=[sticky.id]+ids


        posts = get_posts(ids, sort=sort, v=v)

        return {'html': lambda: render_template("subscriptions.html",
                                                v=v,
                                                listing=posts,
                                                next_exists=next_exists,
                                                sort_method=sort,
                                                time_filter=t,
                                                page=page,
                                                only=only,
                                                **_filter_display_context(region_codes, language_codes, subcat_ids)
                                                ),
                'api': lambda: jsonify({"data": [x.json for x in posts],
                                        "next_exists": next_exists
                                        }
                                       )
                }
    else:
        return front_all()


@app.route("/", methods=["GET"])
@app.route("/for_you", methods=["GET"])
@auth_desired
@api("read")
def for_you(v):
    """
Get the For You feed: a heuristic, non-ML personalized feed based on
category affinity from the viewer's subscriptions/follows/upvote history
(see User.for_you_idlist()), narrowed by whichever Regional/Language/
Categorical filters are currently active (ambient modifiers shared with All
and Following - see _resolve_active_filters()). Anonymous visitors, and
logged-in viewers with no affinity signals yet, see the same content as All.

Optional query parameters:
* `sort` - One of `hot`, `new`, `top`, `disputed`, `activity`. Default `hot`.
* `t` - One of `day`, `week`, `month`, `year`, `all`. Default `all`.
* `page` - Page of results to return. Default `1`.
"""

    if not request.path.startswith(('/api/', '/inpage/')):
        flask_session['base_feed'] = 'for_you'
        flask_session.modified = True

    if v:
        defaultsorting = v.defaultsorting
        defaulttime = v.defaulttime
    else:
        defaultsorting = "hot"
        defaulttime = "all"

    sort = request.args.get("sort", defaultsorting)
    t = request.args.get('t', defaulttime)
    page = max(int(request.args.get("page", 1)), 1)
    ignore_pinned = bool(request.args.get("ignore_pinned", False))

    region_codes, language_codes, subcat_ids = _resolve_active_filters(v)

    if v and v.interest_subcats():
        ids = v.for_you_idlist(
            sort=sort, page=page, t=t,
            filter_words=v.filter_words,
            gt=int(request.args.get("utc_greater_than", 0)),
            lt=int(request.args.get("utc_less_than", 0)),
            region=region_codes,
            language=language_codes,
            categories=subcat_ids,
        )
    else:
        # zero-signal accounts (and anonymous visitors) fall back to All
        ids = frontlist(
            sort=sort, page=page, t=t, v=v,
            hide_offensive=(v and v.hide_offensive) or not v,
            hide_bot=(v and v.hide_bot),
            filter_words=v.filter_words if v else [],
            gt=int(request.args.get("utc_greater_than", 0)),
            lt=int(request.args.get("utc_less_than", 0)),
            region=region_codes,
            language=language_codes,
            categories=subcat_ids,
        )

    next_exists = (len(ids) == 26)
    ids = ids[0:25]

    if page == 1 and not ignore_pinned:
        sticky = g.db.query(Submission.id).filter_by(stickied=True).first()
        if sticky:
            ids = [sticky.id] + ids

    posts = get_posts(ids, sort=sort, v=v)

    return {'html': lambda: render_template("home.html",
                                            v=v,
                                            listing=posts,
                                            next_exists=next_exists,
                                            sort_method=sort,
                                            time_filter=t,
                                            page=page,
                                            CATEGORIES=CATEGORIES,
                                            **_filter_display_context(region_codes, language_codes, subcat_ids)
                                            ),
            'api': lambda: jsonify({"data": [x.json for x in posts],
                                    "next_exists": next_exists
                                    }
                                   )
            }


def default_cat_cookie():

    output=[]
    for cat in CATEGORIES:
        for subcat in cat.subcats:
            if subcat.visible:
                output.append(subcat.id)

    output += [0]
    return output

@app.route("/categories", methods=["GET"])
@auth_desired
def categories_select(v):
    return render_template(
        "categorylisting.html",
        v=v,
        categories=CATEGORIES
        )


@app.route("/all", methods=["GET"])
@app.route("/api/v1/all/listing", methods=["GET"])
@app.route("/inpage/all")
@app.get("/api/v2/submissions")
@auth_desired
@api("read")
def front_all(v):
    """
Get all posts, minus filtered content based on personal settings. Genuinely
sitewide/unfiltered by category - see categorical() for the category-scoped
equivalent of what this route used to do before the nav restructure.

Optional query parameters:
* `sort` - One of `hot`, `new`, `top`, `disputed`, `activity`. Default `hot`.
* `t` - One of `day`, `week`, `month`, `year`, `all`. Default `all`.
* `page` - Page of results to return. Default `1`.
"""

    if not request.path.startswith(('/api/', '/inpage/')):
        flask_session['base_feed'] = 'all'
        flask_session.modified = True

    page = int(request.args.get("page") or 1)

    # prevent invalid paging
    page = max(page, 1)

    if v:
        defaultsorting = v.defaultsorting
        defaulttime = v.defaulttime
    else:
        defaultsorting = "hot"
        defaulttime = "all"

    sort=request.args.get("sort",defaultsorting)
    t=request.args.get('t', defaulttime)
    ignore_pinned = bool(request.args.get("ignore_pinned", False))

    region_codes, language_codes, subcat_ids = _resolve_active_filters(v)

    ids = frontlist(sort=sort,
                    page=page,
                    t=t,
                    v=v,
                    hide_offensive=(v and v.hide_offensive) or not v,
                    hide_bot=(v and v.hide_bot),
                    gt=int(request.args.get("utc_greater_than", 0)),
                    lt=int(request.args.get("utc_less_than", 0)),
                    filter_words=v.filter_words if v else [],
                    categories=[] if request.path.startswith("/api/") else subcat_ids,
                    region=region_codes,
                    language=language_codes,
                    )

    # check existence of next page
    next_exists = (len(ids) == 26)
    ids = ids[0:25]

   # If page 1, check for sticky
    if page == 1 and not ignore_pinned:
        sticky = []
        sticky = g.db.query(Submission.id).filter_by(stickied=True).first()
        if sticky:
            ids = [sticky.id] + ids
    # check if ids exist
    posts = get_posts(ids, sort=sort, v=v)

    return {'html': lambda: render_template("home.html",
                                            v=v,
                                            listing=posts,
                                            next_exists=next_exists,
                                            sort_method=sort,
                                            time_filter=t,
                                            page=page,
                                            CATEGORIES=CATEGORIES,
                                            **_filter_display_context(region_codes, language_codes, subcat_ids)
                                            ),
            'inpage': lambda: render_template("submission_listing.html",
                                              v=v,
                                              listing=posts
                                              ),
            'api': lambda: jsonify({"data": [x.json for x in posts],
                                    "next_exists": next_exists
                                    }
                                   )
            }


@app.route("/categorical", methods=["GET"])
@app.route("/inpage/categorical")
@auth_desired
@api("read")
def categorical(v):
    """
Set the viewer's Categorical filter (session key "catids") - Categorical is
no longer its own destination, it's an ambient modifier shared by All/For
You/Following (see _resolve_active_filters()). Once resolved, redirects back
to whichever of those three the viewer was last using. Same first-time
picker-page gating as before when nothing has ever been chosen.

/inpage/categorical keeps rendering a partial directly (no redirect) for any
future AJAX-refresh use.

Optional query parameters:
* `cats` - Comma-separated subcategory ids to select/remember.
* `sort` - One of `hot`, `new`, `top`, `disputed`, `activity`. Default `hot`.
* `t` - One of `day`, `week`, `month`, `year`, `all`. Default `all`.
* `page` - Page of results to return. Default `1`.
"""

    page = int(request.args.get("page") or 1)
    page = max(page, 1)

    if v:
        defaultsorting = v.defaultsorting
        defaulttime = v.defaulttime
    else:
        defaultsorting = "hot"
        defaulttime = "all"

    sort=request.args.get("sort",defaultsorting)
    t=request.args.get('t', defaulttime)
    ignore_pinned = bool(request.args.get("ignore_pinned", False))

    cats_param_present = 'cats' in request.args
    new_cats=request.args.get('cats','')
    ever_chosen = 'catids' in flask_session
    cats = flask_session.get('catids', [])

    if not ever_chosen and not cats_param_present and not request.path.startswith('/api/'):
        return make_response(
            render_template(
                "categorylisting.html",
                v=v,
                categories=CATEGORIES
                )
            )

    if cats_param_present:
        cats = [int(x) for x in new_cats.split(',') if x] if new_cats else []
        flask_session['catids']=cats
        flask_session.modified=True

    groups = request.args.get("groups")
    if groups:
        flask_session['groupids']=[int(x) for x in groups.split(',')]
        flask_session.modified=True

    if request.path == "/categorical":
        base_feed = flask_session.get('base_feed', 'for_you')
        return redirect({'all': '/all', 'for_you': '/for_you', 'following': '/following'}.get(base_feed, '/for_you'))

    region_codes, language_codes, _ = _resolve_active_filters(v)
    cats_for_filter = [] if request.path.startswith("/api/") else cats

    ids = frontlist(sort=sort,
                    page=page,
                    t=t,
                    v=v,
                    hide_offensive=(v and v.hide_offensive) or not v,
                    hide_bot=(v and v.hide_bot),
                    gt=int(request.args.get("utc_greater_than", 0)),
                    lt=int(request.args.get("utc_less_than", 0)),
                    filter_words=v.filter_words if v else [],
                    categories=cats_for_filter,
                    region=region_codes,
                    language=language_codes,
                    )

    next_exists = (len(ids) == 26)
    ids = ids[0:25]

    if page == 1 and not ignore_pinned:
        sticky = g.db.query(Submission.id).filter_by(stickied=True).first()
        if sticky:
            ids = [sticky.id] + ids
    posts = get_posts(ids, sort=sort, v=v)

    # only /inpage/categorical (partial refresh) and /api/ variants ever
    # reach this point - the plain page always redirects above
    return {'inpage': lambda: render_template("submission_listing.html",
                                              v=v,
                                              listing=posts
                                              ),
            'api': lambda: jsonify({"data": [x.json for x in posts],
                                    "next_exists": next_exists
                                    }
                                   )
            }


@app.route("/regional", methods=["GET"])
def regional():
    """
Set the viewer's Regional filter (session key "selected_regions") - Regional
is no longer its own destination, it's an ambient modifier shared by All/For
You/Following (see _resolve_active_filters()). Redirects back to whichever
of those three the viewer was last using.

Optional query parameters:
* `region` - Comma-separated region code(s) to switch to/remember (see
  REGION_CENTROIDS, or the human-readable list at GET /regions). An explicit
  empty value (`?region=`) clears the region filter entirely. 404s if any
  code is unrecognized.
"""

    if "region" in request.args:
        # checked for presence, not truthiness - an explicit ?region= (empty)
        # means "clear my region selection", not "leave it as-is"
        requested_region = request.args.get("region", "")
        requested_codes = [x for x in requested_region.split(",") if x]
        for code in requested_codes:
            if code not in REGION_CENTROIDS:
                abort(404)
        flask_session['selected_regions'] = requested_codes
        flask_session.modified = True

    base_feed = flask_session.get('base_feed', 'for_you')
    return redirect({'all': '/all', 'for_you': '/for_you', 'following': '/following'}.get(base_feed, '/for_you'))


@app.route("/language/<code>", methods=["GET"])
def language_feed_legacy_redirect(code):
    """Old path-style language URL (/language/en,fr) - kept as a thin
    redirect to the current query-param form so any existing links/bookmarks
    from before this route shape changed still work."""
    return redirect(f"/language?codes={code}")


@app.route("/language", methods=["GET"])
@auth_desired
def language_feed(v):
    """
Set the viewer's Language filter (session key "langcodes") - Language is no
longer its own destination, it's an ambient modifier shared by All/For You/
Following (see _resolve_active_filters()). Same first-time picker-page
gating as before when nothing has ever been chosen; once resolved,
redirects back to whichever of the three base feeds the viewer was last
using.

Optional query parameters:
* `codes` - Comma-separated language code(s) to select/remember. An explicit
  empty value (`?codes=`) clears the language filter entirely. 404s if any
  code is unrecognized.
"""

    codes_param_present = 'codes' in request.args
    new_codes = request.args.get('codes', '')
    ever_chosen = 'langcodes' in flask_session

    if not ever_chosen and not codes_param_present:
        return render_template("language_picker.html", v=v, languages=LANGUAGE_NAMES)

    if codes_param_present:
        codes = [x for x in new_codes.split(',') if x]
        for c in codes:
            if c not in LANGUAGE_NAMES:
                abort(404)
        flask_session['langcodes'] = codes
        flask_session.modified = True

    base_feed = flask_session.get('base_feed', 'for_you')
    return redirect({'all': '/all', 'for_you': '/for_you', 'following': '/following'}.get(base_feed, '/for_you'))


@app.route("/subcat/<name>", methods=["GET"])
@auth_desired
@api("read")
def subcat(name, v):

    if v:
        defaultsorting = v.defaultsorting
        defaulttime = v.defaulttime
    else:
        defaultsorting = "hot"
        defaulttime = "all"

    sort=request.args.get("sort",defaultsorting)
    t=request.args.get('t', defaulttime)

    page = int(request.args.get("page") or 1)

    # prevent invalid paging
    page = max(page, 1)
    
    if "+" in name:
        ids = []
        for name in name.split("+"):
            ids += frontlist(sort=sort,
                            page=page,
                            t=t,
                            v=v,
                            hide_offensive=(v and v.hide_offensive) or not v,
                            hide_bot=(v and v.hide_bot),
                            gt=int(request.args.get("utc_greater_than", 0)),
                            lt=int(request.args.get("utc_less_than", 0)),
                            filter_words=v.filter_words if v else [],
                            categories=[name]
                            )
    else:
        ids = frontlist(sort=sort,
                        page=page,
                        t=t,
                        v=v,
                        hide_offensive=(v and v.hide_offensive) or not v,
                        hide_bot=(v and v.hide_bot),
                        gt=int(request.args.get("utc_greater_than", 0)),
                        lt=int(request.args.get("utc_less_than", 0)),
                        filter_words=v.filter_words if v else [],
                        categories=[name]
                        )

    # check existence of next page
    next_exists = (len(ids) == 26)
    ids = ids[0:25]

    # check if ids exist
    posts = get_posts(ids, sort=sort, v=v)

    return {'html': lambda: render_template("home.html",
                                            v=v,
                                            listing=posts,
                                            next_exists=next_exists,
                                            sort_method=sort,
                                            time_filter=t,
                                            page=page,
                                            CATEGORIES=CATEGORIES
                                            ),
            'inpage': lambda: render_template("submission_listing.html",
                                              v=v,
                                              listing=posts
                                              ),
            'api': lambda: jsonify({"data": [x.json for x in posts],
                                    "next_exists": next_exists})}


@cache.memoize(600)
def guild_ids(sort="subs", page=1, cats=[]):
    # cutoff=int(time.time())-(60*60*24*30)

    guilds = g.db.query(Board).filter_by(is_banned=False).filter(
        Board.subcat_id != 108
    )

    if cats:
        guilds=guilds.filter(Board.subcat.in_(tuple(cats)))

    if sort == "subs":
        guilds = guilds.order_by(Board.stored_subscriber_count.desc())
    elif sort == "new":
        guilds = guilds.order_by(Board.created_utc.desc())
    elif sort == "trending":
        guilds = guilds.order_by(Board.rank_trending.desc())

    else:
        abort(400)

    guilds = [x.id for x in guilds.offset(25 * (page - 1)).limit(26).all()]

    return guilds


@app.route("/browse", methods=["GET"])
@app.get("/api/v1/guilds")
@app.get("/api/v2/guilds")
@auth_desired
@api("read")
def browse_guilds(v):
    """
Get a listing of guilds

Optional query parameters:
* `sort` - One of `trending`, `new`, or `subs`. Default `trending`.
* `page` - Page of results to return. Defualt `1`.
"""


    page = int(request.args.get("page", 1))

    # prevent invalid paging
    page = max(page, 1)

    sort_method = request.args.get("sort", "trending")

    # get list of ids
    ids = guild_ids(
        sort=sort_method,
        page=page,
        cats=request.args.get("cats").split(',') if request.args.get("cats") else None
        )

    # check existence of next page
    next_exists = (len(ids) == 26)
    ids = ids[0:25]

    # check if ids exist
    if ids:

        boards = get_boards(ids, v=v)
    else:
        boards = []

    return {"html": lambda: render_template("boards.html",
                                            v=v,
                                            boards=boards,
                                            page=page,
                                            next_exists=next_exists,
                                            sort_method=sort_method
                                            ),
            "api": lambda: jsonify({"data": [board.json for board in boards]})
            }


@app.route('/mine/guilds', methods=["GET"])
@app.route("/api/v1/mine/guilds", methods=["GET"])
@app.get("/api/v2/me/guilds")
@auth_required
@api("read")
def my_guilds(v, kind=None):

    """
Get guilds with which the user has a connection

Optional query parameters:
`page` - Page of results to return. Default `1`

"""
    page = max(int(request.args.get("page", 1)), 1)


    b = g.db.query(Board)

    contribs = select(ContributorRelationship.board_id).filter_by(user_id=v.id, is_active=True).subquery()
    m = select(ModRelationship.board_id).filter_by(user_id=v.id, accepted=True).subquery()
    s = select(Subscription.board_id).filter_by(user_id=v.id, is_active=True).subquery()

    content = b.filter(
        or_(
            Board.id.in_(contribs),
            Board.id.in_(m),
            Board.id.in_(s)
            )
        )
    content = content.order_by(Board.name.asc())

    content = [x for x in content.offset(25 * (page - 1)).limit(26)]
    next_exists = (len(content) == 26)
    content = content[0:25]
    
    for board in content:
        board._is_subscribed=True

    return {"html": lambda: render_template("mine/boards.html",
                           v=v,
                           boards=content,
                           next_exists=next_exists,
                           page=page,
                           kind="guilds"),
            "api": lambda: jsonify({"data": [x.json for x in content]})}




@app.route('/mine', methods=["GET"])
def mine_redirect():
    return redirect("/mine/guilds")

@app.get("/mine/users")
@app.route("/api/v1/mine", methods=["GET"])
@app.get("/api/v2/me/users")
@auth_required
@api("read")
def my_subs(v, kind=None):

    """
Get users that the authenticated user is following

Optional query parameters:
`page` - Page of results to return. Default `1`

"""
    page = max(int(request.args.get("page", 1)), 1)

    u = g.db.query(User).filter_by(is_banned=0, is_deleted=False)

    follows = g.db.query(Follow).filter_by(user_id=v.id).subquery()

    content = u.join(follows,
                     User.id == follows.c.target_id,
                     isouter=False)

    content = content.order_by(User.stored_subscriber_count.desc())

    content = [x for x in content.offset(25 * (page - 1)).limit(26)]
    next_exists = (len(content) == 26)
    content = content[0:25]

    return {"html": lambda: render_template("mine/users.html",
                           v=v,
                           users=content,
                           next_exists=next_exists,
                           page=page,
                           kind="users"),
            "api": lambda: jsonify({"data": [x.json for x in content]})}


@app.route("/random/post", methods=["GET"])
@auth_desired
def random_post(v):

    x = g.db.query(Submission).options(
        lazyload(Submission.board)).filter_by(
        is_banned=False,
        ).filter(Submission.deleted_utc == 0)

    now = int(time.time())
    cutoff = now - (60 * 60 * 24 * 180)
    x = x.filter(Submission.created_utc >= cutoff)

    if v and v.hide_offensive:
        x = x.filter_by(is_offensive=False)
        
    if v and v.hide_bot:
        x = x.filter_by(is_bot=False)

    if v:
        bans = g.db.query(
            BanRelationship.board_id).filter_by(
            user_id=v.id).subquery()
        x = x.filter(Submission.board_id.notin_(bans))

    x=x.join(Submission.board).filter(Board.is_banned==False)

    total = x.count()
    n = random.randint(0, total - 1)

    post = x.order_by(Submission.id.asc()).offset(n).limit(1).first()
    return redirect(post.permalink)


@app.route("/random/guild", methods=["GET"])
@auth_desired
def random_guild(v):

    x = g.db.query(Board).filter_by(
        is_banned=False,
        is_private=False,
        is_nsfl=False)

    if v:
        bans = g.db.query(BanRelationship.id).filter_by(user_id=v.id).all()
        x = x.filter(Board.id.notin_([i[0] for i in bans]))

    total = x.count()
    n = random.randint(0, total - 1)

    board = x.order_by(Board.id.asc()).offset(n).limit(1).first()

    return redirect(board.permalink)


@app.route("/random/comment", methods=["GET"])
@auth_desired
def random_comment(v):

    x = g.db.query(Comment).filter_by(is_banned=False,
                                      is_offensive=False,
                                      is_bot=False).filter(Comment.parent_submission.isnot(None))
    if v:
        bans = g.db.query(BanRelationship.id).filter_by(user_id=v.id).all()
        x = x.filter(Comment.board_id.notin_([i[0] for i in bans]))

    total = x.count()
    n = random.randint(0, total - 1)
    comment = x.order_by(Comment.id.asc()).offset(n).limit(1).first()

    return redirect(comment.permalink)


@app.route("/random/user", methods=["GET"])
@auth_desired
def random_user(v):
    x = g.db.query(User).filter(or_(User.is_banned == 0, and_(
        User.is_banned > 0, User.unban_utc < int(time.time()))))

    x = x.filter_by(is_private=False)

    total = x.count()
    n = random.randint(0, total - 1)

    user = x.offset(n).limit(1).first()

    return redirect(user.permalink)


@cache.memoize(600)
def comment_idlist(page=1, v=None, **kwargs):

    posts = g.db.query(Submission).options(
        lazyload('*')).join(Submission.board)

    if v and v.admin_level >= 4:
        pass
    elif v:
        m = g.db.query(ModRelationship.board_id).filter_by(
            user_id=v.id, invite_rescinded=False).subquery()
        c = g.db.query(
            ContributorRelationship.board_id).filter_by(
            user_id=v.id).subquery()

        posts = posts.filter(
            or_(
                Submission.author_id == v.id,
                Submission.post_public == True,
                Submission.board_id.in_(m),
                Submission.board_id.in_(c),
                Board.is_private == False
            )
        )
    else:
        posts = posts.filter(or_(Submission.post_public ==
                                 True, Board.is_private == False))

    posts = posts.subquery()

    comments = g.db.query(Comment).options(lazyload('*'))

    if v and v.hide_offensive:
        comments = comments.filter_by(is_offensive=False)
        
    if v and v.hide_bot:
        comments = comments.filter_by(is_bot=False)

    if v and v.admin_level <= 3:
        # blocks
        blocking = g.db.query(
            UserBlock.target_id).filter_by(
            user_id=v.id).subquery()
        blocked = g.db.query(
            UserBlock.user_id).filter_by(
            target_id=v.id).subquery()

        comments = comments.filter(
            Comment.author_id.notin_(blocking),
            Comment.author_id.notin_(blocked)
        )

    if not v or not v.admin_level >= 3:
        comments = comments.filter_by(is_banned=False).filter(Comment.deleted_utc == 0)

    comments = comments.join(posts, Comment.parent_submission == posts.c.id)

    comments = comments.order_by(Comment.created_utc.desc()).offset(
        25 * (page - 1)).limit(26).all()

    return [x.id for x in comments]


@app.route("/all/comments", methods=["GET"])
@app.route("/api/v1/front/comments", methods=["GET"])
@app.get("/api/v2/comments")
@auth_desired
@api("read")
def all_comments(v):
    """
Get all comments

Optional query parameters:
* `page` - Page of results to return. Default `1`
"""

    page = int(request.args.get("page", 1))

    idlist = comment_idlist(v=v,
                            page=page,
                            hide_offensive=v and v.hide_offensive,
                            hide_bot=v and v.hide_bot)

    comments = get_comments(idlist, v=v)

    next_exists = len(idlist) == 26

    idlist = idlist[0:25]

    return {"html": lambda: render_template("home_comments.html",
                                            v=v,
                                            page=page,
                                            comments=comments,
                                            standalone=True,
                                            next_exists=next_exists),
            "api": lambda: jsonify({"data": [x.json for x in comments]})}


@app.route("/api/v1/categories", methods=["GET"])
@auth_desired
@api()
def categories(v):

    return make_response(
        jsonify(
            {"data":[x.json for x in CATEGORIES]}
            )
        )
