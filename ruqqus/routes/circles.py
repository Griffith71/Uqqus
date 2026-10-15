"""Circles (helpers/circles.py rules, helpers/circle_store.py database side): the settings pages and the actions.

Everything that moves coins or changes who is in a Circle needs a login and the form key. The owner only ever
changes their own Circle (`v.id` is the owner everywhere); a member only ever changes their own subscription."""
import time

from flask import abort, g, jsonify, render_template, request

from ruqqus.classes import Board, ForwardRelationship, RepostRelationship, Submission, User
from ruqqus.helpers import circle_store, circles, coauthors, sharing
from ruqqus.helpers.alerts import send_notification
from ruqqus.helpers.get import get_guild, get_post, get_user
from ruqqus.helpers.media import attach as media_attach, cdn as media_cdn
from ruqqus.helpers.wrappers import auth_required, is_guildmaster, is_not_banned, no_negative_balance, validate_formkey
from ruqqus.__main__ import app, cache
from ruqqus.routes.front import frontlist

DAY = 24 * 60 * 60


def _target(name, v):
    """The account a name points at. An unknown or deleted account is a 404 (the same for both)."""
    user = get_user(name or "", v=v, graceful=True)
    if user is None or user.is_deleted:
        abort(404)
    return user


def _fail(message, status=400):
    return jsonify({"error": message}), status


def _link(user):
    return f"[@{user.username}](/@{user.username})"


@app.route("/settings/circle", methods=["GET"])
@auth_required
def settings_circle(v):
    now = int(time.time())
    circle = circle_store.circle_of(g.db, v.id)
    friends = [{"username": f.username, "since": circles.date_text(f.created_utc)} for f in circle_store.friends(g.db, v.id)]
    subscribers = [{"username": s.username, "until": circles.date_text(s.renews_utc), "cancelled": bool(s.cancelled), "price": s.price_coins}
                   for s in circle_store.subscribers(g.db, v.id, now)]
    return render_template(
        "settings_circle.html",
        v=v,
        price=circle.price_coins if circle else 0,
        friends=friends,
        subscribers=subscribers,
        earned_30=circle_store.earnings(g.db, v.id, now - 30 * DAY),
        earned_all=circle_store.earnings(g.db, v.id),
        price_max=circles.PRICE_MAX,
        friend_limit=circles.FRIEND_LIMIT,
        describe_price=circles.describe_price,
    )


@app.route("/settings/circles", methods=["GET"])
@auth_required
def settings_circles(v):
    rows = [{"username": name, "tier": tier, "until": circles.date_text(until), "cancelled": bool(cancelled), "price": price}
            for _, name, tier, until, cancelled, price in circle_store.memberships(g.db, v.id)]
    guilds = [{"name": name, "until": circles.date_text(until), "cancelled": bool(cancelled), "price": price}
              for _, name, until, cancelled, price in circle_store.guild_memberships(g.db, v.id)]
    return render_template("settings_circles.html", v=v, memberships=rows, guilds=guilds)


@app.route("/settings/circle/price", methods=["POST"])
@is_not_banned
@validate_formkey
def settings_circle_price(v):
    price, error = circles.parse_price(request.values.get("price"))
    if error:
        return _fail(error)
    circle_store.set_price(g.db, v.id, price)
    g.db.commit()
    if price == 0:
        return jsonify({"message": "Your Circle is for Close Friends only. Current subscribers keep what they paid for."})
    return jsonify({"message": f"New subscribers pay {price} coins every 30 days. Current subscribers keep their price."})


@app.route("/api/circle/friends/add", methods=["POST"])
@is_not_banned
@validate_formkey
def circle_friend_add(v):
    target = _target(request.values.get("username"), v)
    refusal = circle_store.add_friend(g.db, v.id, target.id)
    if refusal:
        return _fail(refusal)
    g.db.commit()
    return jsonify({"message": f"@{target.username} is one of your Close Friends."})


@app.route("/api/circle/friends/remove", methods=["POST"])
@is_not_banned
@validate_formkey
def circle_friend_remove(v):
    target = _target(request.values.get("username"), v)
    if not circle_store.remove_friend(g.db, v.id, target.id):
        return _fail("They aren't one of your Close Friends.", 404)
    g.db.commit()
    return jsonify({"message": f"@{target.username} is no longer one of your Close Friends."})


@app.route("/api/circle/<username>/subscribe", methods=["POST"])
@is_not_banned
@no_negative_balance("toast")
@validate_formkey
def circle_subscribe(username, v):
    owner = _target(username, v)
    # write the member's own pending changes first (has_premium may have renewed from coins): the payment is raw SQL
    g.db.flush()
    outcome, message = circle_store.subscribe(g.db, owner.id, v.id)
    if outcome == "refused":
        g.db.rollback()
        return _fail(message, 403)
    g.db.commit()
    g.db.refresh(v)
    if outcome == "subscribed":
        send_notification(owner, f"{_link(v)} joined your Circle.")
    return jsonify({"message": message})


@app.route("/api/circle/<username>/cancel", methods=["POST"])
@auth_required
@validate_formkey
def circle_cancel(username, v):
    owner = _target(username, v)
    if not circle_store.cancel(g.db, owner.id, v.id):
        return _fail("You aren't subscribed to that Circle.", 404)
    g.db.commit()
    return jsonify({"message": "Your subscription won't renew. You keep access until the day you paid to."})


def circle_status(owner, viewer):
    """What a profile shows of Circles to this viewer, or None (a visitor, or their own profile): the price, their
    place in the owner's Circle, whether the owner is one of their own Close Friends. For templates."""
    if viewer is None or viewer.id == owner.id:
        return None
    now = int(time.time())
    circle = circle_store.circle_of(g.db, owner.id)
    price = circle.price_coins if circle else 0
    row = circle_store.member_row(g.db, owner.id, viewer.id)
    tier = circles.tier_of(row.tier, row.status, row.renews_utc, now) if row else None
    cancelled = bool(row and row.cancelled)
    mine = circle_store.member_row(g.db, viewer.id, owner.id)
    return {
        "price": price,
        "tier": tier,
        "cancelled": cancelled,
        "joinable": price > 0 and (tier is None or (tier == circles.SUBSCRIBER and cancelled)),
        "you_added": bool(mine and mine.tier == circles.FRIEND and mine.status == circles.ACTIVE),
    }


app.jinja_env.globals.update(circle_status=circle_status)


# --- who can see a post that already exists ------------------------------------------------------------

def _empty_audience(owner_id, audience):
    """A sentence when nobody but the author could see a post made for `audience` (their Circle has nobody it
    would reach yet), else None. Close Friends see Subscribers' posts too; Subscribers do not see Close Friends'."""
    friends = circle_store.friends(g.db, owner_id)
    if audience == circles.FRIENDS:
        return None if friends else "You have no Close Friends yet, so only you would see this post."
    if friends or circle_store.subscribers(g.db, owner_id):
        return None
    return "You have no Close Friends or subscribers yet, so only you would see this post."


def _shared_copies(post, author_id):
    """(the author's own live forward copies, how many live copies others forwarded, whether others reposted it)."""
    copies = g.db.query(Submission).filter(Submission.repost_id == post.id, Submission.deleted_utc == 0).all()
    mine = {rel.forward_submission_id for rel in g.db.query(ForwardRelationship).filter_by(
        primary_submission_id=post.id, forwarded_by_id=author_id)}
    own = [c for c in copies if c.id in mine]
    reposted = g.db.query(RepostRelationship.id).filter(RepostRelationship.submission_id == post.id,
                                                       RepostRelationship.user_id != author_id).first()
    return own, len(copies) - len(own), bool(reposted)


def _withdraw(own, author_id, now):
    """Take down the forward copies the author made, as deleting the post does, and forget that they were forwarded,
    so the post can be forwarded there again if it is made public again. Copies others made are not touched."""
    for copy in own:
        copy.deleted_utc = now
        copy.is_pinned = False
        copy.stickied = False
        g.db.add(copy)
        cache.delete_memoized(Board.idlist, copy.board)
        cache.delete_memoized(Board.idlist, copy.board, sort="new")
    if own:
        g.db.query(ForwardRelationship).filter(ForwardRelationship.forwarded_by_id == author_id,
                                               ForwardRelationship.forward_submission_id.in_([c.id for c in own])
                                               ).delete(synchronize_session=False)


@app.route("/api/post/<pid>/audience", methods=["POST"])
@is_not_banned
@validate_formkey
def circle_post_audience(pid, v):
    """The author changes who can see one of their posts. Narrowing it to their Circle takes the same refusals as posting
    it there (it cannot be anonymous, co-authored or carry an own video). A post that was public and already shared
    can still be narrowed: what others did with it stays (their forward copies stay in their guilds; reposts follow
    the post), the forward copies the author made are taken down, and the page says so first (`confirm=1`, as for
    an empty Circle). Making it Public again needs `confirm=1` too: everyone will see it."""
    post = get_post(pid, v=v)                              # only someone who may see it gets this far
    if post.author_id != v.id:
        abort(403)
    if post.is_deleted or post.is_banned:
        abort(404)
    if post.repost_id not in (0, None):
        return _fail("Only a post on your own profile has an audience.")
    if (post.audience or 0) == circles.GUILD:
        return _fail("A post in a Circle guild keeps that guild's audience.")
    raw = request.values.get("audience")
    if raw is None or not raw.strip():
        return _fail("Choose who can see this.")
    audience, error = circles.parse_audience(raw)
    if error:
        return _fail(error)
    before = post.audience or 0
    if audience == before:
        return jsonify({"message": "Nothing changed."})

    own = []
    if audience != circles.PUBLIC:
        refusal = circles.post_refusal(
            audience, anonymous=bool(post.is_anonymous), coauthors=bool(coauthors.rows_of(g.db, post.id)),
            own_video=bool(post.url and media_attach.own_video(g.db, v.id, post.url)))
        if refusal:
            return _fail(refusal)
        own, others, reposted = _shared_copies(post, v.id)
        # say what will happen before saving (the page asks, `confirm=1` goes on): only the author would see it in an
        # empty Circle, and copies made while it was public are dealt with as `sharing.narrowing_note` says
        notes = [n for n in (_empty_audience(v.id, audience),
                             sharing.narrowing_note([c.board.name for c in own], others, reposted)) if n]
        if notes and request.values.get("confirm") != "1":
            return jsonify({"error": " ".join(notes), "confirm": True}), 409
    elif request.values.get("confirm") != "1":
        return jsonify({"error": "This post will be visible to everyone. Confirm to make it public.", "confirm": True}), 409

    post.audience = audience
    post.post_public = not audience and not post.board.is_private
    g.db.add(post)
    _withdraw(own, v.id, int(time.time()))
    g.db.commit()
    if audience != circles.PUBLIC:
        # a picture that was public may still sit in a CDN: it must not be served from there any more
        media_cdn.purge(media_attach.attached_paths(g.db, submission_id=post.id))

    cache.delete_memoized(User.userpagelisting, v, sort="new")
    cache.delete_memoized(frontlist, sort="new")
    cache.delete_memoized(frontlist)
    return jsonify({"message": f"{circles.NAMES[audience]} can see this post now." if audience else "This post is public now."})


def may_change_audience(post, viewer):
    """Offer "Who can see this" in a post's menu: the author, on a post of their own profile, not anonymous, still up."""
    from jinja2 import Undefined
    viewer = None if isinstance(viewer, Undefined) else viewer
    return bool(viewer and isinstance(post, Submission) and post.author_id == viewer.id and not post.is_anonymous
                and not post.is_deleted and not post.is_banned and post.repost_id in (0, None))


app.jinja_env.globals.update(may_change_audience=may_change_audience)


# --- Circle guilds -----------------------------------------------------------------------------------------

def _circle_guild(name):
    """A live Circle guild by name; anything else is a 404."""
    board = get_guild(name or "", graceful=True)
    if board is None or not board.is_circle or board.is_banned:
        abort(404)
    return board


@app.route("/+<guildname>/mod/circle", methods=["GET"])
@auth_required
@is_guildmaster("full")
def board_circle_settings(guildname, board, v):
    if not board.is_circle:
        abort(404)
    now = int(time.time())
    circle = circle_store.circle_of_board(g.db, board.id)
    subscribers = [{"username": s.username, "until": circles.date_text(s.renews_utc), "cancelled": bool(s.cancelled), "price": s.price_coins}
                   for s in circle_store.guild_subscribers(g.db, board.id, now)]
    founder = g.db.query(User).filter_by(id=board.creator_id).first()
    earned_30, earned_all = circle_store.guild_earnings(g.db, board.id, now - 30 * DAY), circle_store.guild_earnings(g.db, board.id)
    return render_template("guild/circle.html", v=v, b=board, price=circle.price_coins if circle else 0, subscribers=subscribers,
                           is_founder=board.creator_id == v.id, founder=founder, earned_30=earned_30, earned_all=earned_all,
                           price_max=circles.PRICE_MAX, describe_price=circles.describe_price)


@app.route("/+<guildname>/mod/circle/price", methods=["POST"])
@auth_required
@is_guildmaster("full")
@validate_formkey
def board_circle_price(guildname, board, v):
    if not board.is_circle:
        abort(404)
    if board.creator_id != v.id:
        return _fail("Only the guild's founder can set the price.", 403)
    price, error = circles.parse_price(request.values.get("price"))
    if error:
        return _fail(error)
    circle_store.set_board_price(g.db, board.id, price)
    g.db.commit()
    if price == 0:
        return jsonify({"message": "Nobody can pay to join now: the guild is by invitation only. Current members keep what they paid for."})
    return jsonify({"message": f"New members pay {price} coins every 30 days. Current members keep their price."})


@app.route("/api/circle/guild/<guildname>/subscribe", methods=["POST"])
@is_not_banned
@no_negative_balance("toast")
@validate_formkey
def circle_guild_subscribe(guildname, v):
    board = _circle_guild(guildname)
    g.db.flush()                                   # the payment is raw SQL: write the member's own pending changes first
    outcome, message = circle_store.subscribe_guild(g.db, board.id, v.id)
    if outcome == "refused":
        g.db.rollback()
        return _fail(message, 403)
    g.db.commit()
    g.db.refresh(v)
    if outcome == "subscribed":
        founder = g.db.query(User).filter_by(id=board.creator_id).first()
        if founder is not None and founder.id != v.id:
            send_notification(founder, f"{_link(v)} joined [+{board.name}](/+{board.name}).")
    return jsonify({"message": message})


@app.route("/api/circle/guild/<guildname>/cancel", methods=["POST"])
@auth_required
@validate_formkey
def circle_guild_cancel(guildname, v):
    board = _circle_guild(guildname)
    if not circle_store.cancel_guild(g.db, board.id, v.id):
        return _fail("You aren't a paying member of that guild.", 404)
    g.db.commit()
    return jsonify({"message": "Your membership won't renew. You keep access until the day you paid to."})


def circle_guild_status(board, viewer):
    """What a Circle guild's page shows a signed-in member: the price, whether they pay to be in it, and whether Join is
    offered (a price, not already a member, not exiled; or a cancelled paying member who can resume). None otherwise."""
    if viewer is None or not getattr(board, "is_circle", False):
        return None
    now = int(time.time())
    circle = circle_store.circle_of_board(g.db, board.id)
    price = circle.price_coins if circle else 0
    paid = next((m for m in circle_store.guild_memberships(g.db, viewer.id, now) if m.board_id == board.id), None)
    member = circle_store.is_guild_member(g.db, board.id, viewer.id)
    exiled = circle_store.exiled_from(g.db, board.id, viewer.id)
    resumable = bool(paid and paid.cancelled)
    return {"price": price, "paid": bool(paid), "member": member,
            "joinable": price > 0 and not exiled and (resumable or not member)}


app.jinja_env.globals.update(circle_guild_status=circle_guild_status)
