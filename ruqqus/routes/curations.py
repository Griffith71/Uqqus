import time
import re
import secrets

from flask import *
from sqlalchemy import *
from sqlalchemy.orm import lazyload

from ruqqus.helpers.wrappers import *
from ruqqus.helpers.visibility import filter_posts, viewer_level
from ruqqus.helpers.muting import hide_muted
from ruqqus.helpers.get import *
from ruqqus.classes import *
from ruqqus.__main__ import app, cache
from ruqqus.routes.front import region_filter_condition, ALL_SUBCAT_IDS
from ruqqus.helpers.regions import REGION_CENTROIDS
from ruqqus.helpers.languages import LANGUAGE_NAMES
from ruqqus.helpers.base36 import base36decode
from ruqqus.helpers import curation_feed, feed_algorithm, feed_server, safe_fetch
from urllib.parse import quote

PREVIEW_POSTS = 10
SERVER_SPARE = 76       # ids asked of a feed server beyond the page, for the ones a viewer may not see


def _generate_curation_slug(name):
    """Auto-generated once at creation time from `name`, URL-safe (\\w only,
    matching the &mention regex's character class), globally unique (real DB
    constraint - see Curation.slug), immutable afterward. Collisions get a
    numeric suffix rather than being rejected, since `name` itself is free
    text with no uniqueness requirement."""

    base = re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')[:25]
    if len(base) < 3:
        base = (base + secrets.token_hex(4))[:25]

    slug = base
    n = 2
    while g.db.query(Curation).filter(Curation.slug.ilike(slug)).first():
        suffix = f"_{n}"
        slug = base[:25 - len(suffix)] + suffix
        n += 1

    return slug


def _viewable(posts, v):
    """Who may see what, for every curation whatever its algorithm: live posts, the word
    filter, private guilds, the viewer's blocks. `posts` is a query over Submission."""

    posts = posts.filter(
        Submission.is_banned == False,
        Submission.deleted_utc == 0,
        Submission.stickied == False
    )

    posts = filter_posts(posts, v)
    posts = hide_muted(posts, v, Submission)
    if v and v.hide_bot:
        posts = posts.filter(Submission.is_bot == False)

    if v and v.admin_level >= 4:
        board_blocks = select(BoardBlock.board_id).filter_by(user_id=v.id)
        posts = posts.filter(Submission.board_id.notin_(board_blocks))
    elif v:
        m = select(ModRelationship.board_id).filter_by(user_id=v.id, invite_rescinded=False)
        c = select(ContributorRelationship.board_id).filter_by(user_id=v.id)
        posts = posts.filter(
            or_(
                Submission.author_id == v.id,
                Submission.post_public == True,
                Submission.board_id.in_(m),
                Submission.board_id.in_(c)
            )
        )
        blocking = select(UserBlock.target_id).filter_by(user_id=v.id)
        posts = posts.filter(Submission.author_id.notin_(blocking))
        board_blocks = select(BoardBlock.board_id).filter_by(user_id=v.id)
        posts = posts.filter(Submission.board_id.notin_(board_blocks))
    else:
        posts = posts.filter(Submission.post_public == True)

    return posts


def _own_filters(posts, curation):
    """Regional/Language/Categorical filters intrinsic to the curation itself
    (never the viewing session's own personal filters - a curation is a
    fixed, shareable feed definition, same for everyone who views it)."""

    if curation.region_filter_list:
        posts = posts.filter(region_filter_condition(curation.region_filter_list))
    if curation.language_filter_list:
        posts = posts.filter(Submission.language_code.in_(curation.language_filter_list))
    if curation.category_filter_list:
        board_ids_in_cats = select(Board.id).where(Board.subcat_id.in_(tuple(curation.category_filter_list)))
        posts = posts.filter(Submission.board_id.in_(board_ids_in_cats))
    return posts


def _curation_ids(curation, spec, v=None, sort=None, page=1, t=None, filter_words='', **kwargs):
    """Post ids of one page (plus one) of a curation's feed.

    Who may see what is `_viewable`, the same for every curation. Which posts the
    curation wants and how it orders them comes from its algorithm, `spec`
    (helpers/feed_algorithm.py, applied by helpers/curation_feed.py): by default the
    posts of its member guilds OR member accounts - the same boards-or-authors union
    shape as User.idlist(), just sourced from CurationGuild/CurationUser membership
    instead of Subscription/Follow."""

    now = int(time.time())

    posts = g.db.query(Submission.id).options(lazyload('*')).join(
        SubmissionAux, SubmissionAux.id == Submission.id
    )
    posts = _viewable(posts, v)

    if spec["source"] == feed_algorithm.SITE:
        posts = curation_feed.site_pool(posts, v, viewer_level(v))
    else:
        posts = posts.filter(curation_feed.member_condition(curation.id))

    posts = _own_filters(posts, curation)
    posts = curation_feed.apply(posts, spec, now)

    if filter_words:
        for word in filter_words:
            posts = posts.filter(not_(SubmissionAux.title.ilike(f'%{word}%')))

    if t:
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

    # a sort the viewer asked for wins; otherwise the curation's own order, and a
    # curation with no algorithm of its own keeps the viewer's usual sort
    if sort is None:
        if not feed_algorithm.is_default(spec):
            sort = spec["rank"]
        else:
            sort = v.defaultsorting if v else "hot"

    if sort == feed_algorithm.MIX_RANK:
        ids = curation_feed.mix_ids(g.db, posts, spec, curation.id, now)
        return ids[25 * (page - 1):25 * (page - 1) + 26]

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
        abort(422)

    return [x[0] for x in posts.offset(25 * (page - 1)).limit(26).all()]


@cache.memoize(timeout=120)
def curation_idlist(curation_id, v=None, sort=None, page=1, t=None, filter_words='', **kwargs):
    """One page of a curation's feed, cached. Shorter TTL than frontlist()'s 900s since
    curation membership can change more often; the routes below that change a curation
    explicitly invalidate this so follows "live update" rather than waiting out the TTL.
    A curation with rules of its own runs under a time limit (FeedTooSlow)."""

    curation = g.db.query(Curation).filter_by(id=curation_id).first()
    if not curation:
        return []
    spec = curation.algorithm_spec

    def run():
        return _curation_ids(curation, spec, v=v, sort=sort, page=page, t=t, filter_words=filter_words, **kwargs)

    if feed_algorithm.is_default(spec):
        return run()
    return curation_feed.limited(g.db, run)


def _seen_among(curation, ids, v):
    """Which of these post ids this viewer may see in this curation."""
    if not ids:
        return set()
    posts = g.db.query(Submission.id).options(lazyload('*')).filter(Submission.id.in_(ids))
    posts = _own_filters(_viewable(posts, v), curation)
    return {x[0] for x in posts.all()}


def _server_ids(curation, spec, v=None, page=1):
    """(post ids of one page plus one, a notice or None) for a curation ranked by an
    outside feed server. The server only orders: every id it names goes through
    `_viewable` before anything is shown, and nothing about the viewer is sent to it
    (helpers/feed_server.py keeps one list for everyone)."""

    if not feed_server.enabled():
        return [], feed_server.OFF

    # some of what the server names will be hidden from this viewer: ask for spare
    ids, notice = feed_server.ids_for(curation.id, spec["server"], 25 * page + SERVER_SPARE)
    ordered = feed_server.keep_order(ids, _seen_among(curation, ids, v))
    return ordered[25 * (page - 1):25 * (page - 1) + 26], notice


def _invalidate_curation_feed(curation_id):
    # Deliberately broad invalidation (clears every curation's cached feed,
    # not just this one) rather than trying to match cache.memoize's exact
    # positional/keyword call signature - safer than the precedent at
    # routes/boards.py's subscribe_board()/unsubscribe_board(), which calls
    # cache.delete_memoized(User.idlist, v, kind="board") with args that don't
    # match idlist()'s real call site and so likely never actually invalidate
    # anything, silently relying on the TTL to expire instead. At this
    # project's scale, clearing all of curation_idlist's cache on any
    # membership edit is cheap and correct, not a performance concern.
    cache.delete_memoized(curation_idlist)


def _owned_curation_or_404(slug, v):
    curation = get_curation(slug, graceful=True)
    if not curation:
        abort(404)
    if curation.owner_id != v.id:
        abort(403)
    return curation


@app.route("/curations", methods=["GET"])
@auth_desired
def curations_browse(v):

    public_curations = g.db.query(Curation).filter_by(is_private=False).order_by(Curation.created_utc.desc()).all()

    owned = []
    followed = []
    if v:
        owned = g.db.query(Curation).filter_by(owner_id=v.id).order_by(Curation.created_utc.desc()).all()
        followed_ids = select(CurationFollow.curation_id).filter_by(user_id=v.id)
        followed = g.db.query(Curation).filter(Curation.id.in_(followed_ids)).all()

    return render_template(
        "curations/browse.html",
        v=v,
        public_curations=public_curations,
        owned=owned,
        followed=followed,
    )


@app.route("/curations/create", methods=["GET"])
@auth_required
def curations_create_get(v):
    return render_template("curations/form.html", v=v, curation=None, presets=feed_algorithm.PRESETS)


@app.route("/curations/create", methods=["POST"])
@auth_required
@validate_formkey
def curations_create_post(v):

    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    is_private = request.form.get("is_private", "true") == "true"

    if not name or len(name) > 100:
        abort(400)

    curation = Curation(
        owner_id=v.id,
        name=name,
        slug=_generate_curation_slug(name),
        description=description[:500],
        is_private=is_private,
        created_utc=int(time.time()),
        # "Start from": one of the ready-made algorithms, or none (the usual behaviour)
        algorithm=feed_algorithm.dump(feed_algorithm.preset(request.form.get("preset", ""))),
    )
    g.db.add(curation)
    g.db.commit()

    # the guilds, accounts and algorithm are added on the edit page
    return redirect(curation.permalink + "/edit")


@app.route("/curation/<b36id>", methods=["GET"])
def curation_legacy_redirect(b36id):
    """Old ID-based URL, kept as a thin redirect to the current /&<slug>
    form so any existing links/bookmarks from before curations moved to
    name-based URLs still work."""
    try:
        cid = base36decode(b36id)
    except Exception:
        abort(404)
    curation = g.db.query(Curation).filter_by(id=cid).first()
    if not curation:
        abort(404)
    return redirect(curation.permalink)


@app.route("/&<slug>", methods=["GET"])
@auth_desired
def curation_detail(slug, v):

    curation = get_curation(slug, graceful=True)
    if not curation:
        abort(404)

    is_owner = bool(v and v.id == curation.owner_id)
    if curation.is_private and not is_owner:
        abort(403)

    sort = request.args.get("sort")
    t = request.args.get("t")
    try:
        page = max(1, int(request.args.get("page") or 1))
    except ValueError:
        abort(400)

    feed_notice = None
    spec = curation.algorithm_spec
    if feed_algorithm.is_server(spec):
        # the server's order is the feed: a sort or a time filter in the address does not apply
        ids, feed_notice = _server_ids(curation, spec, v=v, page=page)
        sort = None
    else:
        try:
            ids = curation_idlist(curation.id, v=v, sort=sort, page=page, t=t,
                                   filter_level=viewer_level(v),
                                   filter_words=v.filter_words if v else [])
        except curation_feed.FeedTooSlow as slow:
            ids, feed_notice = [], str(slow)
    next_exists = (len(ids) == 26)
    ids = ids[0:25]
    posts = get_posts(ids, v=v, sort=sort or "hot")

    is_following = False
    if v and not is_owner:
        is_following = bool(g.db.query(CurationFollow).filter_by(
            curation_id=curation.id, user_id=v.id).first())

    guilds = g.db.query(Board).join(
        CurationGuild, CurationGuild.board_id == Board.id
    ).filter(CurationGuild.curation_id == curation.id).all()

    accounts = g.db.query(User).join(
        CurationUser, CurationUser.target_user_id == User.id
    ).filter(CurationUser.curation_id == curation.id).all()

    return render_template(
        "curations/detail.html",
        v=v,
        curation=curation,
        is_owner=is_owner,
        is_following=is_following,
        guilds=guilds,
        accounts=accounts,
        listing=posts,
        next_exists=next_exists,
        page=page,
        sort_method=sort or "",
        feed_notice=feed_notice,
    )


@app.route("/&<slug>/edit", methods=["GET"])
@auth_required
def curation_edit_get(slug, v):
    curation = _owned_curation_or_404(slug, v)

    guilds = g.db.query(Board).join(
        CurationGuild, CurationGuild.board_id == Board.id
    ).filter(CurationGuild.curation_id == curation.id).all()

    accounts = g.db.query(User).join(
        CurationUser, CurationUser.target_user_id == User.id
    ).filter(CurationUser.curation_id == curation.id).all()

    return render_template(
        "curations/form.html", v=v, curation=curation, guilds=guilds, accounts=accounts,
        spec=curation.algorithm_spec, algorithm=feed_algorithm, presets=feed_algorithm.PRESETS,
        preset_specs={name: feed_algorithm.preset(name) for name in feed_algorithm.PRESETS},
        alg_msg=request.args.get("alg_msg"), alg_error=request.args.get("alg_error"),
        feed_servers=feed_server.enabled(),
    )


@app.route("/&<slug>/edit", methods=["POST"])
@auth_required
@validate_formkey
def curation_edit_post(slug, v):

    curation = _owned_curation_or_404(slug, v)

    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    is_private = request.form.get("is_private", "true") == "true"

    if not name or len(name) > 100:
        abort(400)

    curation.name = name
    curation.description = description[:500]
    curation.is_private = is_private
    g.db.add(curation)
    g.db.commit()

    return redirect(curation.permalink)


@app.route("/&<slug>/delete", methods=["POST"])
@auth_required
@validate_formkey
def curation_delete(slug, v):

    curation = _owned_curation_or_404(slug, v)

    g.db.query(CurationGuild).filter_by(curation_id=curation.id).delete()
    g.db.query(CurationUser).filter_by(curation_id=curation.id).delete()
    g.db.query(CurationFollow).filter_by(curation_id=curation.id).delete()
    # forks are independent copies: they stay, and only forget where they came from
    # (the database refuses to delete a curation a fork still points at)
    g.db.query(Curation).filter_by(forked_from_id=curation.id).update({"forked_from_id": None})
    g.db.delete(curation)
    g.db.commit()

    _invalidate_curation_feed(curation.id)

    return redirect("/curations")


@app.route("/&<slug>/add_guild", methods=["POST"])
@auth_required
@validate_formkey
def curation_add_guild(slug, v):

    curation = _owned_curation_or_404(slug, v)
    guildname = request.form.get("guildname", "").strip()
    board = get_guild(guildname, graceful=True)
    if not board:
        abort(404)

    exists = g.db.query(CurationGuild).filter_by(curation_id=curation.id, board_id=board.id).first()
    if not exists:
        g.db.add(CurationGuild(curation_id=curation.id, board_id=board.id, created_utc=int(time.time())))
        g.db.commit()
        _invalidate_curation_feed(curation.id)

    return redirect(curation.permalink + "/edit")


@app.route("/&<slug>/remove_guild/<int:board_id>", methods=["POST"])
@auth_required
@validate_formkey
def curation_remove_guild(slug, board_id, v):

    curation = _owned_curation_or_404(slug, v)
    g.db.query(CurationGuild).filter_by(curation_id=curation.id, board_id=board_id).delete()
    g.db.commit()
    _invalidate_curation_feed(curation.id)

    return redirect(curation.permalink + "/edit")


@app.route("/&<slug>/add_user", methods=["POST"])
@auth_required
@validate_formkey
def curation_add_user(slug, v):

    curation = _owned_curation_or_404(slug, v)
    username = request.form.get("username", "").strip()
    target = get_user(username, graceful=True)
    if not target:
        abort(404)

    exists = g.db.query(CurationUser).filter_by(curation_id=curation.id, target_user_id=target.id).first()
    if not exists:
        g.db.add(CurationUser(curation_id=curation.id, target_user_id=target.id, created_utc=int(time.time())))
        g.db.commit()
        _invalidate_curation_feed(curation.id)

    return redirect(curation.permalink + "/edit")


@app.route("/&<slug>/remove_user/<int:target_user_id>", methods=["POST"])
@auth_required
@validate_formkey
def curation_remove_user(slug, target_user_id, v):

    curation = _owned_curation_or_404(slug, v)
    g.db.query(CurationUser).filter_by(curation_id=curation.id, target_user_id=target_user_id).delete()
    g.db.commit()
    _invalidate_curation_feed(curation.id)

    return redirect(curation.permalink + "/edit")


@app.route("/&<slug>/set_region_filter", methods=["POST"])
@auth_required
@validate_formkey
def curation_set_region_filter(slug, v):

    curation = _owned_curation_or_404(slug, v)
    requested = request.form.get("region", "")
    codes = [x for x in requested.split(",") if x]
    for code in codes:
        if code not in REGION_CENTROIDS:
            abort(404)

    curation.region_filter = ",".join(codes)
    g.db.add(curation)
    g.db.commit()
    _invalidate_curation_feed(curation.id)

    return redirect(curation.permalink + "/edit")


@app.route("/&<slug>/set_language_filter", methods=["POST"])
@auth_required
@validate_formkey
def curation_set_language_filter(slug, v):

    curation = _owned_curation_or_404(slug, v)
    requested = request.form.get("codes", "")
    codes = [x for x in requested.split(",") if x]
    for code in codes:
        if code not in LANGUAGE_NAMES:
            abort(404)

    curation.language_filter = ",".join(codes)
    g.db.add(curation)
    g.db.commit()
    _invalidate_curation_feed(curation.id)

    return redirect(curation.permalink + "/edit")


@app.route("/&<slug>/set_category_filter", methods=["POST"])
@auth_required
@validate_formkey
def curation_set_category_filter(slug, v):

    curation = _owned_curation_or_404(slug, v)
    requested = request.form.get("cats", "")
    ids = [x for x in requested.split(",") if x]
    for i in ids:
        if not i.isdigit() or int(i) not in ALL_SUBCAT_IDS:
            abort(404)

    curation.category_filter = ",".join(ids)
    g.db.add(curation)
    g.db.commit()
    _invalidate_curation_feed(curation.id)

    return redirect(curation.permalink + "/edit")


def _posted_algorithm():
    """The algorithm a posted curation form describes (AlgorithmError when it cannot be)."""
    spec = feed_algorithm.from_form(request.form)
    if feed_algorithm.is_server(spec):
        if not feed_server.enabled():
            raise feed_algorithm.AlgorithmError(feed_server.OFF)
        try:
            # the address itself and where its name leads (helpers/safe_fetch.py)
            safe_fetch.check(spec["server"])
        except safe_fetch.FetchError as error:
            raise feed_algorithm.AlgorithmError(error.message)
    return spec


@app.route("/&<slug>/set_algorithm", methods=["POST"])
@auth_required
@validate_formkey
def curation_set_algorithm(slug, v):
    """Save the curation's algorithm from the form's fields. Nothing the member typed is
    stored as it came: feed_algorithm.clean keeps fixed names, bounded numbers and plain words."""

    curation = _owned_curation_or_404(slug, v)
    try:
        spec = _posted_algorithm()
    except feed_algorithm.AlgorithmError as error:
        return redirect(f"{curation.permalink}/edit?alg_error={quote(error.message)}#algorithm")

    before = curation.algorithm_spec
    if feed_algorithm.is_server(before) and before["server"] != spec["server"]:
        feed_server.forget(curation.id, before["server"])

    curation.algorithm = feed_algorithm.dump(spec)
    g.db.add(curation)
    g.db.commit()
    _invalidate_curation_feed(curation.id)

    return redirect(f"{curation.permalink}/edit?alg_msg={quote('Algorithm saved.')}#algorithm")


@app.route("/&<slug>/preview_algorithm", methods=["POST"])
@auth_required
@validate_formkey
def curation_preview_algorithm(slug, v):
    """What the form's algorithm would show, without saving it: its plain-language summary
    and the first posts, through the same query as the real feed."""

    curation = _owned_curation_or_404(slug, v)
    try:
        spec = _posted_algorithm()
    except feed_algorithm.AlgorithmError as error:
        return jsonify({"error": error.message}), 400

    note = None
    if feed_algorithm.is_server(spec):
        # asks the server once, now, so the owner sees what it really answers
        try:
            named = feed_server.try_server(spec["server"])
        except safe_fetch.FetchError as error:
            return jsonify({"error": error.message, "summary": feed_algorithm.describe(spec)}), 400
        ids = feed_server.keep_order(named, _seen_among(curation, named, v))
        note = f"The server named {len(named)} post{'' if len(named) == 1 else 's'}; you can see {len(ids)} of them."
    else:
        try:
            ids = curation_feed.limited(g.db, lambda: _curation_ids(
                curation, spec, v=v, sort=spec["rank"], page=1, filter_words=v.filter_words))
        except curation_feed.FeedTooSlow as slow:
            return jsonify({"error": str(slow), "summary": feed_algorithm.describe(spec)}), 400

    posts = get_posts(ids[:PREVIEW_POSTS], v=v)
    return jsonify({
        "summary": feed_algorithm.describe(spec),
        "count": len(posts),
        "note": note,
        "html": render_template("curations/preview.html", v=v, listing=posts),
    })


@app.route("/&<slug>/remove_feed_server", methods=["POST"])
@admin_level_required(4)
@validate_formkey
def curation_remove_feed_server(slug, v):
    """An admin takes a curation off its outside server. Its own rules are kept, so it
    goes back to them."""

    curation = get_curation(slug, graceful=True)
    if not curation:
        abort(404)

    spec = curation.algorithm_spec
    if feed_algorithm.is_server(spec):
        feed_server.forget(curation.id, spec["server"])
        try:
            curation.algorithm = feed_algorithm.dump(dict(spec, mode=feed_algorithm.RULES, server=""))
        except feed_algorithm.AlgorithmError:
            # the rules kept beside the server were never complete: the usual behaviour then
            curation.algorithm = feed_algorithm.dump({})
        g.db.add(curation)
        g.db.commit()
        _invalidate_curation_feed(curation.id)

    return redirect(curation.permalink)


@app.route("/&<slug>/follow", methods=["POST"])
@auth_required
@validate_formkey
def curation_follow(slug, v):

    curation = get_curation(slug, graceful=True)
    if not curation or curation.is_private:
        abort(404)

    exists = g.db.query(CurationFollow).filter_by(curation_id=curation.id, user_id=v.id).first()
    if not exists:
        g.db.add(CurationFollow(curation_id=curation.id, user_id=v.id, created_utc=int(time.time())))
        g.db.commit()

    return redirect(curation.permalink)


@app.route("/api/follow_curation/<slug>", methods=["POST"])
@auth_required
@validate_formkey
def curation_follow_inline(slug, v):
    """The same follow as /&<slug>/follow for buttons that stay on the page
    (Who to follow): answers JSON instead of redirecting."""

    curation = get_curation(slug, graceful=True)
    if not curation or curation.is_private:
        abort(404)

    exists = g.db.query(CurationFollow).filter_by(curation_id=curation.id, user_id=v.id).first()
    if not exists:
        g.db.add(CurationFollow(curation_id=curation.id, user_id=v.id, created_utc=int(time.time())))
        g.db.commit()

    return jsonify({"following": True})


@app.route("/&<slug>/unfollow", methods=["POST"])
@auth_required
@validate_formkey
def curation_unfollow(slug, v):

    curation = get_curation(slug, graceful=True)
    if not curation:
        abort(404)

    g.db.query(CurationFollow).filter_by(curation_id=curation.id, user_id=v.id).delete()
    g.db.commit()

    return redirect(curation.permalink)


@app.route("/&<slug>/fork", methods=["POST"])
@auth_required
@validate_formkey
def curation_fork(slug, v):

    original = get_curation(slug, graceful=True)
    if not original or original.is_private:
        abort(404)

    fork_name = f"{original.name} (forked)"
    fork = Curation(
        owner_id=v.id,
        name=fork_name,
        slug=_generate_curation_slug(fork_name),
        description=original.description,
        is_private=True,
        created_utc=int(time.time()),
        forked_from_id=original.id,
        region_filter=original.region_filter,
        language_filter=original.language_filter,
        category_filter=original.category_filter,
        algorithm=feed_algorithm.dump(original.algorithm_spec),
    )
    g.db.add(fork)
    g.db.flush()

    for cg in g.db.query(CurationGuild).filter_by(curation_id=original.id).all():
        g.db.add(CurationGuild(curation_id=fork.id, board_id=cg.board_id, created_utc=int(time.time())))
    for cu in g.db.query(CurationUser).filter_by(curation_id=original.id).all():
        g.db.add(CurationUser(curation_id=fork.id, target_user_id=cu.target_user_id, created_utc=int(time.time())))

    g.db.commit()

    return redirect(fork.permalink)
