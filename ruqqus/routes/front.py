import time
from flask import *
from sqlalchemy import *
from sqlalchemy.orm import lazyload
import random

from ruqqus.helpers.wrappers import *
from ruqqus.helpers.visibility import filter_posts, filter_comments, filter_boards, filter_users, viewer_level
from ruqqus.helpers.sensitive import hide_sensitive
from ruqqus.helpers.muting import hide_muted
from ruqqus.helpers.feed_family import one_per_family
from ruqqus.helpers.post_feedback import hide_fewer
from ruqqus.helpers.blocks import hide_blocked
from ruqqus.helpers.get import *
from flask import session as flask_session

from ruqqus.__main__ import app, cache
from ruqqus.classes.submission import Submission
from ruqqus.classes.categories import CATEGORIES
from ruqqus.classes.votes import Vote
from ruqqus.helpers import topics as topic_rules
from ruqqus.helpers import feed_filters as feed_filter_rules
from ruqqus.helpers import feed_filter_sql
from ruqqus.helpers.languages import LANGUAGE_NAMES, known_codes
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

    page = page_number()
    all_ = request.args.get('all', False)

    cids = v.notification_commentlisting(page=page,
                                         all_=request.path=="/notifications/all",
                                         mentions_only=request.path=="/notifications/mentions",
                                         comments_only=request.path=="/notifications/replies",
                                         system_only=request.path=="/notifications/system"
                                         )
    next_exists = (len(cids) == 26)
    cids = cids[0:25]

    comments = get_comments(cids, v=v, sort_type="new", load_parent=True)

    listing = []
    for c in comments:
        # comments by accounts blocked either way are left out of the list (User.notification_commentlisting)
        c.nested_comments = []
        if c.author_id == 1:
            c._is_system = True
            listing.append(c)
        elif c.level > 1 and c.parent_comment and c.parent_comment.author_id == v.id:
            c._is_comment_on_comment = True
            parent = c.parent_comment

            if parent in listing:
                parent.nested_comments = parent.nested_comments + [c]
            else:
                parent.nested_comments = [c]
                listing.append(parent)

        elif c.level == 1 and c.post.author_id == v.id:
            c._is_comment_on_post = True
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
                            render_child_comments=True,
                            is_notification_page=True),
            'api': lambda: jsonify({"data": [x.json for x in listing]})}

@app.get("/notifications/posts")
@auth_required
@api("read")
def notifications_posts(v):

    page=page_number()

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
            'api': lambda: jsonify({"data": [x.json for x in posts]})
            }

def viewer_sticky(v):
    """The site-wide sticky post, if this viewer's word filter, mutes and blocks let them see it."""
    q = g.db.query(Submission.id).filter_by(stickied=True, is_banned=False).filter(Submission.deleted_utc == 0)
    return hide_blocked(hide_muted(filter_posts(q, v), v, Submission), v, Submission).first()


@cache.memoize(timeout=900)
def frontlist(v=None, sort=None, page=1,
              t=None, categories=[], filter_words='', region=None, language=None, feed_filter=None, **kwargs):

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

    posts = filter_posts(posts, v)
    posts = hide_muted(posts, v, Submission)
    # posts the viewer asked to see fewer of leave All (helpers/post_feedback.py)
    posts = hide_fewer(posts, v, Submission)
    # All is everything except sensitive content, unless the viewer chose to see it (helpers/sensitive.py)
    posts = hide_sensitive(posts, v, Submission)
    # one card per post: a guild copy leaves when its original is in All too (helpers/feed_family.py, audit F-01)
    posts = one_per_family(posts, Submission)
    
    if v and v.hide_bot:
        posts = posts.filter(Submission.is_bot==False)

    if v and v.admin_level >= 4:
        board_blocks = select(BoardBlock.board_id).filter_by(
            user_id=v.id
        ).scalar_subquery()

        posts = posts.filter(Submission.board_id.notin_(board_blocks))
    elif v:
        m = select(ModRelationship.board_id).filter_by(
            user_id=v.id, invite_rescinded=False
        ).scalar_subquery()
        c = select(ContributorRelationship.board_id).filter_by(
            user_id=v.id, is_active=True
        ).scalar_subquery()

        posts = posts.filter(
            or_(
                Submission.author_id == v.id,
                Submission.post_public == True,
                Submission.board_id.in_(m),
                Submission.board_id.in_(c)
            )
        )

        # blocked either way (helpers/blocks.py)
        posts = hide_blocked(posts, v, Submission)

        board_blocks = select(BoardBlock.board_id).filter_by(
            user_id=v.id
        ).scalar_subquery()

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
                    ).scalar_subquery()
                )
            )
        )
        if v.admin_level < 4:
            # a copy in a guild that has since turned private stays out of All for everyone but its members and the
            # author (audit F-02: a post's public stamp is made when it is posted, for the sharing rule, and kept)
            posts = posts.filter(or_(Board.is_private == False, Submission.author_id == v.id,
                                     Submission.board_id.in_(m), Submission.board_id.in_(c)))
    else:

        posts = posts.join(
            Submission.board).filter_by(
            all_opt_out=False, is_private=False)

    # a banned guild's posts leave every feed (search, trending and Following already do this)
    posts = posts.filter(Board.is_banned == False)

    if categories:
        posts=posts.filter(Board.subcat_id.in_(tuple(categories)))

    if region:
        region_list = region if isinstance(region, (list, tuple)) else [region]
        posts = posts.filter(region_filter_condition(region_list))

    if language:
        language_list = language if isinstance(language, (list, tuple)) else [language]
        posts = posts.filter(Submission.language_code.in_(language_list))


    posts=posts.options(contains_eager(Submission.board))

    # SubmissionAux is joined once, for the word filter and the feed filters alike
    if (v and filter_words) or feed_filter_sql.needs_aux(feed_filter):
        posts = posts.join(Submission.submission_aux)

    # Post type and the Advanced filter only narrow (helpers/feed_filter_sql.py)
    posts = feed_filter_sql.apply(posts, feed_filter, v)

    #custom filter
    #print(filter_words)
    if v and filter_words:
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


def _session_filters():
    """The five feed filters exactly as this session holds them (the saved default's shape)."""
    return {"regions": list(flask_session.get('selected_regions') or []),
            "languages": known_codes(flask_session.get('langcodes')),
            "categories": list(flask_session.get('catids') or []),
            "types": feed_filter_rules.parse_types(flask_session.get('feed_post_types') or []),
            "advanced": feed_filter_rules.compact(flask_session.get('feed_filter') or {})}


def _saved_defaults(v):
    """A member's saved default filters, every part checked again, or None."""
    if v is None:
        return None
    return feed_filter_rules.read_defaults(v.feed_defaults or "", known_regions=REGION_CENTROIDS,
                                           known_languages=LANGUAGE_NAMES, known_categories=ALL_SUBCAT_IDS)


def _load_into_session(saved):
    flask_session['selected_regions'] = saved["regions"]
    flask_session['langcodes'] = saved["languages"]
    flask_session['catids'] = saved["categories"]
    flask_session['feed_post_types'] = saved["types"]
    if saved["advanced"]:
        flask_session['feed_filter'] = saved["advanced"]
    else:
        flask_session.pop('feed_filter', None)
    flask_session.modified = True


def _apply_saved_defaults(v):
    """Once per session and member: their saved default filters become the session's. After that the sidebar's
    changes last for the session only, until "Reset to my default" or a new sign-in."""
    if v is None or flask_session.get('feed_defaults_for') == v.id:
        return
    flask_session['feed_defaults_for'] = v.id
    flask_session.modified = True
    saved = _saved_defaults(v)
    if saved is not None:
        _load_into_session(saved)


def _feed_defaults_state(v):
    """For the sidebar: 'none' (nothing saved), 'same' (the session is the saved default) or 'differs'."""
    saved = _saved_defaults(v)
    if saved is None:
        return "none"
    return "same" if saved == _session_filters() else "differs"


def _resolve_active_filters(v):
    """The combined set of active Regional/Language/Categorical selections,
    shared by all three filter routes so that landing on any one of them
    applies all three dimensions together, not just its own."""
    _apply_saved_defaults(v)
    region_codes = flask_session.get('selected_regions') or ([v.display_region] if v and v.display_region else [])
    language_codes = known_codes(flask_session.get('langcodes'))
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


def _stored_feed_filters():
    """(post types, Advanced filter spec) as the session holds them, cleaned again on the way out."""
    _apply_saved_defaults(getattr(g, 'v', None))
    types = feed_filter_rules.parse_types(flask_session.get('feed_post_types') or [])
    spec = feed_filter_rules.clean(flask_session.get('feed_filter') or {})
    return types, spec


def _feed_filter_for(v):
    """(FeedFilter or None, the notices' context) for All, For You and Following. The API's listings are never
    narrowed by what a browser session chose (All already leaves the category filter out there)."""
    types, spec = _stored_feed_filters()
    if request.path.startswith("/api/"):
        return None, {}
    feed_filter, notices = feed_filter_sql.prepare(spec, types, v)
    return feed_filter, {
        "post_type_names": feed_filter_rules.type_labels(types),
        "feed_filter_chips": feed_filter_rules.chips(feed_filter.query if feed_filter else None, spec),
        "feed_filter_notices": notices,
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

    # an anonymous post does not match through its author, or the filter would tell where they live
    author_match = and_(
        Submission.author_id.in_(select(User.id).where(User.display_region.in_(region_list))),
        not_(Submission.is_anonymous)
    )
    forwarder_match = exists(
        select(ForwardRelationship.id)
        .join(User, User.id == ForwardRelationship.forwarded_by_id)
        .where(
            ForwardRelationship.forward_submission_id == Submission.id,
            User.display_region.in_(region_list),
            # an author who forwarded their own anonymous post is not a region match either
            or_(not_(Submission.is_anonymous), ForwardRelationship.forwarded_by_id != Submission.author_id)
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

    # an error page can render before before_request opened g.db (the rate
    # limiter answers first); it gets an empty region list instead of a 500
    regions_catalog = [
        {"value": r.code, "label": r.current_name, "search": r.current_name}
        for r in sidebar_regions()
    ] if getattr(g, 'db', None) is not None else []

    sorted_languages = sorted(LANGUAGE_NAMES.items(), key=lambda x: x[1])
    languages_catalog = [
        {"value": code, "label": name, "search": name}
        for code, name in sorted_languages
    ]

    v = getattr(g, 'v', None)
    active_regions, active_languages, active_subcats = _resolve_active_filters(v)
    active_post_types, feed_filter_spec = _stored_feed_filters()

    return {
        "CATEGORIES": CATEGORIES,
        "regions_catalog": regions_catalog,
        "languages_catalog": languages_catalog,
        "categorical_catalog": categorical_catalog,
        "active_regions": active_regions,
        "active_languages": active_languages,
        "active_subcats": active_subcats,
        # Post type and the Advanced filter (helpers/feed_filters.py)
        "post_type_catalog": feed_filter_rules.POST_TYPES,
        "post_type_groups": feed_filter_rules.GROUPS,
        "active_post_types": active_post_types,
        "post_type_summary": feed_filter_rules.type_summary(active_post_types),
        "feed_filter_form": feed_filter_rules.form_values(feed_filter_spec),
        "feed_filter_count": feed_filter_rules.field_count(feed_filter_spec),
        # "Save as my default" (members only): none / same / differs
        "feed_defaults_state": _feed_defaults_state(v) if v else None,
    }


@app.route("/following", methods=["GET"])
@app.route("/api/v1/front/listing", methods=["GET"])
@app.route("/api/v2/me/submissions")
@auth_desired
@api("read")
def following(v):
    """
Get the Following feed: posts by the accounts you follow (and posts they
co-author) and posts in the guilds you joined, narrowed by whichever
Regional/Language/Categorical filters are currently active (ambient modifiers
shared with All and For You - see _resolve_active_filters()). Nothing else:
a member who follows nothing yet, or a visitor, gets an empty feed (the page
says how to fill it), never All.

Optional query parameters:
* `sort` - One of `hot`, `new`, `top`, `disputed`, `activity`. Default `hot`.
* `t` - One of `day`, `week`, `month`, `year`, `all`. Default `all`.
* `page` - Page of results to return. Default `1`.
"""

    if not request.path.startswith(('/api/', '/inpage/')):
        flask_session['base_feed'] = 'following'
        flask_session.modified = True

    only = request.args.get("only", None)
    sort = request.args.get("sort", v.defaultsorting if v else "hot")
    t = request.args.get('t', v.defaulttime if v else "all")
    page = max(page_number(), 0)
    ignore_pinned = flag_arg("ignore_pinned")

    if v is None:
        # a visitor follows nothing: the page asks them to sign in (it used to show All, labelled Following)
        region_codes, language_codes, subcat_ids = [], [], []
        feed_filter_context = {}
        posts, next_exists = [], False
    else:
        region_codes, language_codes, subcat_ids = _resolve_active_filters(v)
        feed_filter, feed_filter_context = _feed_filter_for(v)

        ids=v.idlist(sort=sort,
                     page=page,
                     only=only,
                     t=t,
                     filter_words=v.filter_words,

                     # these arguments don't really do much but they exist for
                     # cache memoization differentiation
                     filter_level=viewer_level(v),
                     hide_bot=v.hide_bot,

                     #greater/less than
                     gt=whole_arg("utc_greater_than"),
                     lt=whole_arg("utc_less_than"),

                     region=region_codes,
                     language=language_codes,
                     categories=subcat_ids,
                     feed_filter=feed_filter,

                     )

        next_exists=(len(ids)==26)
        ids=ids[0:25]

        # If page 1, check for sticky
        if page == 1 and not ignore_pinned and feed_filter is None and ids:
            sticky = viewer_sticky(v)
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
                                            following_feed=True,
                                            **_filter_display_context(region_codes, language_codes, subcat_ids),
                                            **feed_filter_context
                                            ),
            'api': lambda: jsonify({"data": [x.json for x in posts],
                                    "next_exists": next_exists
                                    }
                                   )
            }


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
    page = max(page_number(), 1)
    ignore_pinned = flag_arg("ignore_pinned")

    region_codes, language_codes, subcat_ids = _resolve_active_filters(v)
    feed_filter, feed_filter_context = _feed_filter_for(v)

    if v and v.interest_subcats():
        ids = v.for_you_idlist(
            sort=sort, page=page, t=t,
            filter_level=viewer_level(v),
            filter_words=v.filter_words,
            gt=whole_arg("utc_greater_than"),
            lt=whole_arg("utc_less_than"),
            region=region_codes,
            language=language_codes,
            categories=subcat_ids,
            feed_filter=feed_filter,
        )
    else:
        # zero-signal accounts (and anonymous visitors) fall back to All
        ids = frontlist(
            sort=sort, page=page, t=t, v=v,
            filter_level=viewer_level(v),
            hide_bot=(v and v.hide_bot),
            filter_words=v.filter_words if v else [],
            gt=whole_arg("utc_greater_than"),
            lt=whole_arg("utc_less_than"),
            region=region_codes,
            language=language_codes,
            categories=subcat_ids,
            feed_filter=feed_filter,
        )

    next_exists = (len(ids) == 26)
    ids = ids[0:25]

    if page == 1 and not ignore_pinned and feed_filter is None:
        sticky = viewer_sticky(v)
        if sticky:
            ids = [sticky.id] + ids

    posts = get_posts(ids, sort=sort, v=v)

    if v:
        friend_ids = v.friend_ids
        followed_ids = v.following_ids - friend_ids
        page_ids = [p.id for p in posts]

        def _like_counts(id_set):
            if not id_set or not page_ids:
                return {}
            return dict(g.db.query(Vote.submission_id, func.count(Vote.user_id)).filter(
                Vote.submission_id.in_(page_ids),
                Vote.vote_type == 1,
                Vote.user_id.in_(id_set)
            ).group_by(Vote.submission_id).all())

        friend_counts = _like_counts(friend_ids)
        followed_counts = _like_counts(followed_ids)

        for p in posts:
            if followed_counts.get(p.id, 0) >= 3:
                p.__dict__['_social_proof_label'] = 'followed'
            elif friend_counts.get(p.id, 0) >= 3:
                p.__dict__['_social_proof_label'] = 'friends'

    return {'html': lambda: render_template("home.html",
                                            v=v,
                                            listing=posts,
                                            next_exists=next_exists,
                                            sort_method=sort,
                                            time_filter=t,
                                            page=page,
                                            CATEGORIES=CATEGORIES,
                                            **_filter_display_context(region_codes, language_codes, subcat_ids),
                                            **feed_filter_context
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

    page = page_number()

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
    ignore_pinned = flag_arg("ignore_pinned")

    region_codes, language_codes, subcat_ids = _resolve_active_filters(v)
    feed_filter, feed_filter_context = _feed_filter_for(v)

    ids = frontlist(sort=sort,
                    page=page,
                    t=t,
                    v=v,
                    filter_level=viewer_level(v),
                    hide_bot=(v and v.hide_bot),
                    gt=whole_arg("utc_greater_than"),
                    lt=whole_arg("utc_less_than"),
                    filter_words=v.filter_words if v else [],
                    categories=[] if request.path.startswith("/api/") else subcat_ids,
                    region=region_codes,
                    language=language_codes,
                    feed_filter=feed_filter,
                    )

    # check existence of next page
    next_exists = (len(ids) == 26)
    ids = ids[0:25]

   # If page 1, check for sticky
    if page == 1 and not ignore_pinned and feed_filter is None:
        sticky = []
        sticky = viewer_sticky(v)
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
                                            **_filter_display_context(region_codes, language_codes, subcat_ids),
                                            **feed_filter_context
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
You/Following (see _resolve_active_filters()). The sidebar's Categorical
panel is the only place to choose; this saves the choice and redirects back
to whichever of those three the viewer was last using.

/inpage/categorical keeps rendering a partial directly (no redirect) for any
future AJAX-refresh use.

Optional query parameters:
* `cats` - Comma-separated subcategory ids to select/remember.
* `sort` - One of `hot`, `new`, `top`, `disputed`, `activity`. Default `hot`.
* `t` - One of `day`, `week`, `month`, `year`, `all`. Default `all`.
* `page` - Page of results to return. Default `1`.
"""

    page = page_number()
    page = max(page, 1)

    if v:
        defaultsorting = v.defaultsorting
        defaulttime = v.defaulttime
    else:
        defaultsorting = "hot"
        defaulttime = "all"

    sort=request.args.get("sort",defaultsorting)
    t=request.args.get('t', defaulttime)
    ignore_pinned = flag_arg("ignore_pinned")

    cats_param_present = 'cats' in request.args
    new_cats=request.args.get('cats','')
    cats = flask_session.get('catids', [])

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
                    filter_level=viewer_level(v),
                    hide_bot=(v and v.hide_bot),
                    gt=whole_arg("utc_greater_than"),
                    lt=whole_arg("utc_less_than"),
                    filter_words=v.filter_words if v else [],
                    categories=cats_for_filter,
                    region=region_codes,
                    language=language_codes,
                    )

    next_exists = (len(ids) == 26)
    ids = ids[0:25]

    if page == 1 and not ignore_pinned:
        sticky = viewer_sticky(v)
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


@app.route("/language", methods=["GET"])
@auth_desired
def language_feed(v):
    """
Set the viewer's Language filter (session key "langcodes") - Language is no
longer its own destination, it's an ambient modifier shared by All/For You/
Following (see _resolve_active_filters()). The sidebar's Language panel is
the only place to choose; this just saves the choice and redirects back to
whichever of the three base feeds the viewer was last using.

Optional query parameters:
* `codes` - Comma-separated language code(s) to select/remember. An explicit
  empty value (`?codes=`) clears the language filter entirely. 404s if any
  code is unrecognized.
"""

    codes_param_present = 'codes' in request.args
    new_codes = request.args.get('codes', '')

    if codes_param_present:
        codes = [x for x in new_codes.split(',') if x]
        for c in codes:
            if c not in LANGUAGE_NAMES:
                abort(404)
        flask_session['langcodes'] = codes
        flask_session.modified = True

    base_feed = flask_session.get('base_feed', 'for_you')
    return redirect({'all': '/all', 'for_you': '/for_you', 'following': '/following'}.get(base_feed, '/for_you'))


@app.route("/post_type", methods=["GET"])
def post_type_filter():
    """
Set the viewer's Post type filter (session key "feed_post_types", helpers/feed_filters.py): All, For You and
Following then show only posts of the ticked types. Redirects back to whichever of the three the viewer was last
using.

Optional query parameters:
* `types` - Comma-separated post types (`POST_TYPES`). An explicit empty value (`?types=`) clears the filter;
  unknown types are left out.
"""

    if "types" in request.args:
        flask_session['feed_post_types'] = feed_filter_rules.parse_types(request.args.get("types", ""))
        flask_session.modified = True

    base_feed = flask_session.get('base_feed', 'for_you')
    return redirect({'all': '/all', 'for_you': '/for_you', 'following': '/following'}.get(base_feed, '/for_you'))


@app.post("/settings/feed_defaults")
@auth_required
@validate_formkey
def feed_defaults(v):
    """
The member's default feed filters (users.feed_defaults). `action`:
* `save` - the five filters this session has now become the default, loaded into every new session.
* `reset` - the saved default becomes this session's filters again.
* `clear` - nothing is saved any more (this session keeps its filters).
Redirects back to whichever of All, For You and Following the member was last using.
"""
    action = request.form.get("action", "")
    if action == "save":
        now = _session_filters()
        v.feed_defaults = feed_filter_rules.defaults_payload(now["regions"], now["languages"], now["categories"],
                                                             now["types"], now["advanced"])
        g.db.add(v)
        g.db.commit()
    elif action == "reset":
        saved = _saved_defaults(v)
        if saved is not None:
            _load_into_session(saved)
    elif action == "clear":
        v.feed_defaults = ""
        g.db.add(v)
        g.db.commit()
    else:
        abort(400)

    base_feed = flask_session.get('base_feed', 'for_you')
    return redirect({'all': '/all', 'for_you': '/for_you', 'following': '/following'}.get(base_feed, '/for_you'))


@app.route("/feed_filter", methods=["GET"])
def advanced_feed_filter():
    """
Set the viewer's Advanced filter (session key "feed_filter", helpers/feed_filters.py): X's Advanced search for All,
For You and Following. The popup's form sends its fields here; whatever a field doesn't accept is left out. Redirects
back to whichever of the three the viewer was last using.

Optional query parameters:
* `clear` - Any value clears the filter.
* the popup's fields: `all_words`, `phrase`, `any_words`, `none_words`, `topics`, `from`, `not_from`, `mentioning`,
  `in_guilds`, `not_in_guilds`, `domain`, `ai`, `partnership`, `forwards`, `min_votes`, `min_comments`, `since`,
  `until`.
"""

    if request.args.get("clear"):
        flask_session.pop('feed_filter', None)
    else:
        # only the fields that are set: the session is a cookie that also holds the sign-in (helpers/feed_filters.py)
        spec = feed_filter_rules.compact(request.args)
        if spec:
            flask_session['feed_filter'] = spec
        else:
            flask_session.pop('feed_filter', None)
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

    page = page_number()

    # prevent invalid paging
    page = max(page, 1)

    # subcategories are numeric ids, joined with "+"
    if not all(x.isascii() and x.isdigit() for x in name.split("+")):
        abort(404)

    if "+" in name:
        ids = []
        for name in name.split("+"):
            ids += frontlist(sort=sort,
                            page=page,
                            t=t,
                            v=v,
                            filter_level=viewer_level(v),
                            hide_bot=(v and v.hide_bot),
                            gt=whole_arg("utc_greater_than"),
                            lt=whole_arg("utc_less_than"),
                            filter_words=v.filter_words if v else [],
                            categories=[name]
                            )
    else:
        ids = frontlist(sort=sort,
                        page=page,
                        t=t,
                        v=v,
                        filter_level=viewer_level(v),
                        hide_bot=(v and v.hide_bot),
                        gt=whole_arg("utc_greater_than"),
                        lt=whole_arg("utc_less_than"),
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
def guild_ids(sort="subs", page=1, cats=[], filter_level=1):
    # cutoff=int(time.time())-(60*60*24*30)

    # a Circle guild is found by invitation, never by browsing (its existence names its members' circle)
    guilds = g.db.query(Board).filter_by(is_banned=False, is_circle=False).filter(
        Board.subcat_id != topic_rules.UNLISTED_SUBCAT,
        func.lower(Board.name) != PROFILE_BOARD_NAME
    )
    guilds = filter_boards(guilds, None, level=filter_level)

    if cats:
        guilds=guilds.filter(Board.subcat_id.in_(tuple(cats)))

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


    page = page_number()

    # prevent invalid paging
    page = max(page, 1)

    sort_method = request.args.get("sort", "trending")

    # the chips are the categories as topics (helpers/topics.py); `topic` can also name a subcategory topic, reached from
    # a `#name` link or /topic/<name>. `cat` (a category id) and `cats` (subcategory ids) are the older ways in.
    chips = []
    for category in CATEGORIES:
        chip = None if category.is_nsfw or not category.visible else topic_rules.find(category.name)
        if chip and chip not in chips:
            chips.append(chip)
    topic = topic_rules.find(request.args.get("topic", ""))
    if topic is None and request.args.get("cat", type=int):
        category = next((c for c in CATEGORIES if c.id == request.args.get("cat", type=int)), None)
        topic = topic_rules.find(category.name) if category else None
    if topic:
        cats = list(topic.subcat_ids)
    else:
        cats = [int(x) for x in request.args.get("cats", "").split(",") if x.strip().isdigit()]

    # get list of ids
    ids = guild_ids(
        sort=sort_method,
        page=page,
        filter_level=viewer_level(v),
        cats=cats or None
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
                                            sort_method=sort_method,
                                            topics=chips,
                                            topic=topic
                                            ),
            "api": lambda: jsonify({"data": [board.json for board in boards]})
            }


@app.route("/topic/<name>", methods=["GET"])
def topic_page(name):
    """What `#name` links to: the guilds of that topic (a category or subcategory, helpers/topics.py), on Explore."""
    topic = topic_rules.find(name)
    if topic is None:
        abort(404)
    return redirect(f"/browse?topic={topic.slug}")


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
    page = max(page_number(), 1)


    b = g.db.query(Board)

    contribs = select(ContributorRelationship.board_id).filter_by(user_id=v.id, is_active=True).scalar_subquery()
    m = select(ModRelationship.board_id).filter_by(user_id=v.id, accepted=True).scalar_subquery()
    s = select(Subscription.board_id).filter_by(user_id=v.id, is_active=True).scalar_subquery()

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
    page = max(page_number(), 1)

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


@app.route("/random", methods=["GET"])
@auth_desired
def random_anything(v):
    """
The navbar's Random button: a random guild, post or comment, a third of the time each. The three kinds are tried in a
shuffled order and the first that has anything wins, so an empty kind (no public comments yet) never ends in a 404
while the others have something. What each may land on is decided by its own route below, unchanged.
"""
    from werkzeug.exceptions import NotFound

    pick = {"guild": random_guild, "post": random_post, "comment": random_comment}
    for kind in random.sample(list(pick), 3):
        try:
            return pick[kind]()
        except NotFound:
            continue
    abort(404)


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

    x = filter_posts(x, v)
        
    if v and v.hide_bot:
        x = x.filter_by(is_bot=False)

    if v:
        bans = g.db.query(
            BanRelationship.board_id).filter_by(
            user_id=v.id).scalar_subquery()
        x = x.filter(Submission.board_id.notin_(bans))

    # only what anyone may open: no Circle or private-guild posts (the redirect itself names them)
    x=x.join(Submission.board).filter(
        Board.is_banned==False,
        Board.is_private==False,
        Submission.post_public==True,
        Submission.audience==0)

    total = x.count()
    if not total:
        abort(404)
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
    x = filter_boards(x, v)

    if v:
        bans = g.db.query(BanRelationship.board_id).filter_by(user_id=v.id).all()
        x = x.filter(Board.id.notin_([i[0] for i in bans]))

    total = x.count()
    if not total:
        abort(404)
    n = random.randint(0, total - 1)

    board = x.order_by(Board.id.asc()).offset(n).limit(1).first()

    return redirect(board.permalink)


@app.route("/random/comment", methods=["GET"])
@auth_desired
def random_comment(v):

    x = g.db.query(Comment).filter_by(is_banned=False,
                                      is_bot=False).filter(Comment.parent_submission.isnot(None),
                                                           Comment.deleted_utc == 0)
    x = filter_comments(x, v)
    if v:
        bans = g.db.query(BanRelationship.board_id).filter_by(user_id=v.id).all()
        x = x.filter(Comment.board_id.notin_([i[0] for i in bans]))

    # only comments on posts anyone may open (see random_post)
    open_posts = select(Submission.id).join(Submission.board).where(
        Submission.is_banned == False,
        Submission.deleted_utc == 0,
        Submission.post_public == True,
        Submission.audience == 0,
        Board.is_banned == False,
        Board.is_private == False)
    x = x.filter(Comment.parent_submission.in_(open_posts))

    total = x.count()
    if not total:
        abort(404)
    n = random.randint(0, total - 1)
    comment = x.order_by(Comment.id.asc()).offset(n).limit(1).first()

    return redirect(comment.permalink)


@app.route("/random/user", methods=["GET"])
@auth_desired
def random_user(v):
    x = g.db.query(User).filter(or_(User.is_banned == 0, and_(
        User.is_banned > 0, User.unban_utc < int(time.time()))))

    x = x.filter_by(is_private=False, is_deleted=False)
    x = filter_users(x, v)

    total = x.count()
    if not total:
        abort(404)
    n = random.randint(0, total - 1)

    user = x.offset(n).limit(1).first()

    return redirect(user.permalink)


@cache.memoize(600)
def comment_idlist(page=1, v=None, **kwargs):

    # never a banned guild or a post the site's admins removed, nor a guild the viewer filtered out (as All)
    posts = g.db.query(Submission).options(
        lazyload('*')).join(Submission.board).filter(
        Board.is_banned == False, Submission.is_banned == False)
    if v:
        posts = posts.filter(Submission.board_id.notin_(select(BoardBlock.board_id).filter_by(user_id=v.id)))

    if v and v.admin_level >= 4:
        pass
    elif v:
        m = g.db.query(ModRelationship.board_id).filter_by(
            user_id=v.id, invite_rescinded=False).scalar_subquery()
        c = g.db.query(
            ContributorRelationship.board_id).filter_by(
            user_id=v.id, is_active=True).scalar_subquery()

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

    comments = filter_comments(comments, v)
    comments = hide_muted(comments, v, Comment)
        
    if v and v.hide_bot:
        comments = comments.filter_by(is_bot=False)

    if v and v.admin_level <= 3:
        # blocks
        blocking = g.db.query(
            UserBlock.target_id).filter_by(
            user_id=v.id).scalar_subquery()
        blocked = g.db.query(
            UserBlock.user_id).filter_by(
            target_id=v.id).scalar_subquery()

        comments = comments.filter(
            or_(Comment.author_id.notin_(blocking), Comment.is_anonymous == True),
            or_(Comment.author_id.notin_(blocked), Comment.is_anonymous == True)
        )

    # removed and deleted comments belong on the logs, not here, for admins too
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

    page = page_number()

    idlist = comment_idlist(v=v,
                            page=page,
                            filter_level=viewer_level(v),
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
