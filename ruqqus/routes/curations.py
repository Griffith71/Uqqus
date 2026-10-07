import time
import re
import secrets

from flask import *
from sqlalchemy import *
from sqlalchemy.orm import lazyload

from ruqqus.helpers.wrappers import *
from ruqqus.helpers.visibility import filter_posts, viewer_level
from ruqqus.helpers.get import *
from ruqqus.classes import *
from ruqqus.__main__ import app, cache
from ruqqus.routes.front import region_filter_condition, ALL_SUBCAT_IDS
from ruqqus.helpers.regions import REGION_CENTROIDS
from ruqqus.helpers.languages import LANGUAGE_NAMES
from ruqqus.helpers.base36 import base36decode


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


@cache.memoize(timeout=120)
def curation_idlist(curation_id, v=None, sort=None, page=1, t=None, filter_words='', **kwargs):
    """Posts from a curation's member guilds OR member accounts - same
    boards-or-authors union shape as User.idlist(), just sourced from
    CurationGuild/CurationUser membership instead of Subscription/Follow.
    Shorter TTL than frontlist()'s 900s since curation membership can change
    more often; membership-mutating routes below explicitly invalidate this
    so follows "live update" rather than waiting out the TTL."""

    posts = g.db.query(Submission.id).options(lazyload('*')).filter_by(
        is_banned=False, deleted_utc=0, stickied=False
    )

    posts = filter_posts(posts, v)
    if v and v.hide_bot:
        posts = posts.filter_by(is_bot=False)

    board_ids = select(CurationGuild.board_id).filter_by(curation_id=curation_id)
    user_ids = select(CurationUser.target_user_id).filter_by(curation_id=curation_id)

    # an anonymous post never matches through its author (a curation can hold one
    # account, which would name them); it still shows through a member guild
    posts = posts.filter(
        or_(
            Submission.board_id.in_(board_ids),
            and_(Submission.author_id.in_(user_ids), not_(Submission.is_anonymous))
        )
    )

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

    # Regional/Language/Categorical filters intrinsic to the curation itself
    # (never the viewing session's own personal filters - a curation is a
    # fixed, shareable feed definition, same for everyone who views it)
    curation = g.db.query(Curation).filter_by(id=curation_id).first()
    if curation:
        if curation.region_filter_list:
            posts = posts.filter(region_filter_condition(curation.region_filter_list))
        if curation.language_filter_list:
            posts = posts.filter(Submission.language_code.in_(curation.language_filter_list))
        if curation.category_filter_list:
            board_ids_in_cats = select(Board.id).where(Board.subcat_id.in_(tuple(curation.category_filter_list)))
            posts = posts.filter(Submission.board_id.in_(board_ids_in_cats))

    if filter_words:
        posts = posts.join(Submission.submission_aux)
        for word in filter_words:
            posts = posts.filter(not_(SubmissionAux.title.ilike(f'%{word}%')))

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

    if sort is None:
        sort = v.defaultsorting if v else "hot"

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
    return render_template("curations/form.html", v=v, curation=None)


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
    )
    g.db.add(curation)
    g.db.commit()

    return redirect(curation.permalink)


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
    page = int(request.args.get("page") or 1)

    ids = curation_idlist(curation.id, v=v, sort=sort, page=page, t=t,
                           filter_level=viewer_level(v),
                           filter_words=v.filter_words if v else [])
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

    return render_template("curations/form.html", v=v, curation=curation, guilds=guilds, accounts=accounts)


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
    )
    g.db.add(fork)
    g.db.flush()

    for cg in g.db.query(CurationGuild).filter_by(curation_id=original.id).all():
        g.db.add(CurationGuild(curation_id=fork.id, board_id=cg.board_id, created_utc=int(time.time())))
    for cu in g.db.query(CurationUser).filter_by(curation_id=original.id).all():
        g.db.add(CurationUser(curation_id=fork.id, target_user_id=cu.target_user_id, created_utc=int(time.time())))

    g.db.commit()

    return redirect(fork.permalink)
