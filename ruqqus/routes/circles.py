"""Circles (helpers/circles.py rules, helpers/circle_store.py database side): the settings pages and the actions.

Everything that moves coins or changes who is in a Circle needs a login and the form key. The owner only ever
changes their own Circle (`v.id` is the owner everywhere); a member only ever changes their own subscription."""
import time

from flask import abort, g, jsonify, render_template, request

from ruqqus.classes import RepostRelationship, Submission, User
from ruqqus.helpers import circle_store, circles, coauthors
from ruqqus.helpers.alerts import send_notification
from ruqqus.helpers.get import get_post, get_user
from ruqqus.helpers.media import attach as media_attach, cdn as media_cdn
from ruqqus.helpers.wrappers import auth_required, is_not_banned, no_negative_balance, validate_formkey
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
    return render_template("settings_circles.html", v=v, memberships=rows)


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

@app.route("/api/post/<pid>/audience", methods=["POST"])
@is_not_banned
@validate_formkey
def circle_post_audience(pid, v):
    """The author changes who can see one of their posts. Narrowing it to their Circle takes the same refusals as posting
    it there (it cannot be anonymous, co-authored, forwarded, reposted or carry an own video); making it Public again
    needs `confirm=1`, because a Circle post becomes visible to everyone (the page asks first)."""
    post = get_post(pid, v=v)                              # only someone who may see it gets this far
    if post.author_id != v.id:
        abort(403)
    if post.is_deleted or post.is_banned:
        abort(404)
    if post.repost_id not in (0, None):
        return _fail("Only a post on your own profile has an audience.")
    raw = request.values.get("audience")
    if raw is None or not raw.strip():
        return _fail("Choose who can see this.")
    audience, error = circles.parse_audience(raw)
    if error:
        return _fail(error)
    before = post.audience or 0
    if audience == before:
        return jsonify({"message": "Nothing changed."})

    if audience != circles.PUBLIC:
        shared = bool(g.db.query(Submission.id).filter(Submission.repost_id == post.id).first()
                      or g.db.query(RepostRelationship.id).filter_by(submission_id=post.id).first())
        refusal = circles.post_refusal(
            audience, anonymous=bool(post.is_anonymous), coauthors=bool(coauthors.rows_of(g.db, post.id)),
            own_video=bool(post.url and media_attach.own_video(g.db, v.id, post.url)), shared=shared)
        if refusal:
            return _fail(refusal)
    elif request.values.get("confirm") != "1":
        return jsonify({"error": "This post will be visible to everyone. Confirm to make it public.", "confirm": True}), 409

    post.audience = audience
    post.post_public = not audience and not post.board.is_private
    g.db.add(post)
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
