"""Circles (helpers/circles.py rules, helpers/circle_store.py database side): the settings pages and the actions.

Everything that moves coins or changes who is in a circle needs a login and the form key. The owner only ever
changes their own circles (`v.id` is the owner everywhere, and circle_store checks each circle is theirs); a member
only ever changes their own subscription. Nobody is told they were put in a custom circle, and no page shows anyone
the custom circles they are in."""
import time

from flask import abort, g, jsonify, render_template, request

from ruqqus.classes import Board, ForwardRelationship, RepostRelationship, Submission, User
from ruqqus.helpers import circle_guard, circle_store, circles, coauthors, sharing
from ruqqus.helpers.alerts import send_notification
from ruqqus.helpers.get import get_guild, get_post, get_user
from ruqqus.helpers.media import attach as media_attach, cdn as media_cdn
from ruqqus.helpers.wrappers import auth_required, is_guildmaster, is_not_banned, no_negative_balance, validate_formkey
from ruqqus.__main__ import app, cache
from ruqqus.routes.front import frontlist

DAY = 24 * 60 * 60


def _target(name, v):
    """The account a name points at. An unknown or deleted account is a 404 (the same for both)."""
    user = get_user((name or "").strip().lstrip("@"), v=v, graceful=True)
    if user is None or user.is_deleted:
        abort(404)
    return user


def _fail(message, status=400):
    return jsonify({"error": message}), status


def _link(user):
    return f"[@{user.username}](/@{user.username})"


def _mine(v, cid, kinds=(circles.CUSTOM, circles.PAID)):
    """One of the member's own circles of these kinds, or a 404 (someone else's circle looks like no circle)."""
    row = circle_store.owned(g.db, v.id, cid, kinds=kinds)
    if row is None:
        abort(404)
    return row


# --- the settings pages ------------------------------------------------------------------------------------

@app.route("/settings/circle", methods=["GET"])
@auth_required
def settings_circle(v):
    now = int(time.time())
    rows = circle_store.circles_of(g.db, v.id, kinds=(circles.CUSTOM, circles.PAID))
    counts = circle_store.member_counts(g.db, [r.id for r in rows], now)
    custom = [{"id": r.id, "name": r.name, "count": counts.get(r.id, 0)} for r in rows if r.kind == circles.CUSTOM]
    paid = [{"id": r.id, "name": r.name, "price": r.price_coins, "count": counts.get(r.id, 0)} for r in rows if r.kind == circles.PAID]
    return render_template(
        "settings_circle.html",
        v=v,
        friends_count=circle_store.mutual_count(g.db, v.id),
        custom=custom,
        paid=paid,
        has_premium=v.has_premium_no_renew,
        earned_30=circle_store.earnings(g.db, v.id, now - 30 * DAY),
        earned_all=circle_store.earnings(g.db, v.id),
        custom_limit=circles.CUSTOM_LIMIT,
        paid_limit=circles.PAID_LIMIT,
        name_max=circles.NAME_MAX,
        price_max=circles.PRICE_MAX,
        describe_price=circles.describe_price,
    )


@app.route("/settings/circle/<int:cid>", methods=["GET"])
@auth_required
def settings_circle_one(cid, v):
    now = int(time.time())
    row = _mine(v, cid)
    people = [{"username": m.username, "since": circles.date_text(m.created_utc)} for m in circle_store.members(g.db, row.id)] \
        if row.kind == circles.CUSTOM else []
    subscribers = [{"username": s.username, "until": circles.date_text(s.renews_utc), "cancelled": bool(s.cancelled), "price": s.price_coins}
                   for s in circle_store.subscribers(g.db, row.id, now)] if row.kind == circles.PAID else []
    return render_template(
        "settings_circle_one.html",
        v=v,
        circle=row,
        people=people,
        subscribers=subscribers,
        earned_30=circle_store.earnings_of(g.db, row.id, now - 30 * DAY) if row.kind == circles.PAID else 0,
        earned_all=circle_store.earnings_of(g.db, row.id) if row.kind == circles.PAID else 0,
        member_limit=circles.MEMBER_LIMIT,
        name_max=circles.NAME_MAX,
        price_max=circles.PRICE_MAX,
        describe_price=circles.describe_price,
    )


@app.route("/settings/circles", methods=["GET"])
@auth_required
def settings_circles(v):
    """The paid circles and Circle guilds this account pays to be in. Never the custom circles someone put it in."""
    rows = [{"circle": m.circle_id, "username": m.username, "name": m.name, "until": circles.date_text(m.renews_utc),
             "cancelled": bool(m.cancelled), "price": m.price_coins} for m in circle_store.memberships(g.db, v.id)]
    guilds = [{"name": name, "until": circles.date_text(until), "cancelled": bool(cancelled), "price": price}
              for _, name, until, cancelled, price in circle_store.guild_memberships(g.db, v.id)]
    return render_template("settings_circles.html", v=v, memberships=rows, guilds=guilds)


# --- making and changing one's own circles -------------------------------------------------------------------

@app.route("/api/circles", methods=["POST"])
@is_not_banned
@validate_formkey
def circle_create(v):
    """Make a circle: `kind` custom (anyone, CUSTOM_LIMIT) or paid (Premium, PAID_LIMIT, with a `price`)."""
    kind = (request.values.get("kind") or circles.CUSTOM).strip()
    name, error = circles.parse_name(request.values.get("name"))
    if error:
        return _fail(error)
    price = 0
    if kind == circles.PAID:
        price, error = circles.parse_price(request.values.get("price", "0"))
        if error:
            return _fail(error)
    has_premium = v.has_premium_no_renew                 # never has_premium: reading it spends a coin to renew
    refusal = circles.refusal_to_make(kind, count=circle_store.count_kind(g.db, v.id, kind), has_premium=has_premium)
    if refusal:
        return _fail(refusal, 403 if kind == circles.PAID and not has_premium else 400)
    new_id, error = circle_store.create(g.db, v.id, kind, name, price)
    if error:
        return _fail(error, 409)
    g.db.commit()
    return jsonify({"id": new_id, "go": f"/settings/circle/{new_id}", "message": f"{name} is ready."})


@app.route("/api/circles/<int:cid>/rename", methods=["POST"])
@is_not_banned
@validate_formkey
def circle_rename(cid, v):
    _mine(v, cid)
    name, error = circles.parse_name(request.values.get("name"))
    if error:
        return _fail(error)
    error = circle_store.rename(g.db, v.id, cid, name)
    if error:
        return _fail(error, 409)
    g.db.commit()
    return jsonify({"message": f"It's called {name} now."})


@app.route("/api/circles/<int:cid>/delete", methods=["POST"])
@is_not_banned
@validate_formkey
def circle_delete(cid, v):
    row = _mine(v, cid)
    error = circle_store.delete(g.db, v.id, cid)
    if error:
        return _fail(error, 409)
    g.db.commit()
    return jsonify({"go": "/settings/circle", "message": f"{row.name} is deleted. Posts that were for it are still for your other circles they were shared with."})


@app.route("/api/circles/<int:cid>/price", methods=["POST"])
@is_not_banned
@validate_formkey
def circle_price(cid, v):
    _mine(v, cid, kinds=(circles.PAID,))
    price, error = circles.parse_price(request.values.get("price"))
    if error:
        return _fail(error)
    circle_store.set_price(g.db, v.id, cid, price)
    g.db.commit()
    if price == 0:
        return jsonify({"message": "Nobody new can subscribe. Current subscribers keep what they paid for."})
    return jsonify({"message": f"New subscribers pay {price} coins every 30 days. Current subscribers keep their price."})


@app.route("/api/circles/<int:cid>/add", methods=["POST"])
@is_not_banned
@validate_formkey
def circle_add(cid, v):
    """Put someone in a custom circle. They are not told."""
    row = _mine(v, cid, kinds=(circles.CUSTOM,))
    target = _target(request.values.get("username"), v)
    refusal = circle_store.add_member(g.db, v.id, row.id, target.id)
    if refusal:
        return _fail(refusal)
    g.db.commit()
    return jsonify({"message": f"@{target.username} is in {row.name}. They are not told."})


@app.route("/api/circles/<int:cid>/remove", methods=["POST"])
@is_not_banned
@validate_formkey
def circle_remove(cid, v):
    row = _mine(v, cid, kinds=(circles.CUSTOM,))
    target = _target(request.values.get("username"), v)
    if not circle_store.remove_member(g.db, v.id, row.id, target.id):
        return _fail(f"They aren't in {row.name}.", 404)
    g.db.commit()
    return jsonify({"message": f"@{target.username} is no longer in {row.name}."})


@app.route("/api/circles/for/<username>", methods=["GET"])
@auth_required
def circle_choices_for(username, v):
    """The member's own custom circles and whether this account is in each (the profile's "Add to circles" sheet).
    Only ever the asker's own circles: nothing here says whose circles the account is in."""
    target = _target(username, v)
    if target.id == v.id:
        return _fail("You can always see your own posts.")
    inside = circle_store.custom_circles_with(g.db, v.id, target.id)
    rows = circle_store.circles_of(g.db, v.id, kinds=(circles.CUSTOM,))
    response = jsonify({"username": target.username, "manage": "/settings/circle",
                        "circles": [{"id": r.id, "name": r.name, "in": r.id in inside} for r in rows]})
    response.headers["Cache-Control"] = "private, no-store"
    return response


# --- subscribing to someone's paid circle ------------------------------------------------------------------------

def _paid_circle(cid):
    """A paid circle of a live account, by the id in the address (its owner comes from the circle); else a 404."""
    row = circle_store.get(g.db, cid)
    if row is None or row.kind != circles.PAID:
        abort(404)
    owner = g.db.query(User).filter_by(id=row.user_id).first()
    if owner is None or owner.is_deleted:
        abort(404)
    return row, owner


@app.route("/api/circles/<int:cid>/subscribe", methods=["POST"])
@is_not_banned
@no_negative_balance("toast")
@validate_formkey
def circle_subscribe(cid, v):
    row, owner = _paid_circle(cid)
    # write the member's own pending changes first (has_premium may have renewed from coins): the payment is raw SQL
    g.db.flush()
    outcome, message = circle_store.subscribe(g.db, row.id, v.id)
    if outcome == "refused":
        g.db.rollback()
        return _fail(message, 403)
    g.db.commit()
    g.db.refresh(v)
    if outcome == "subscribed":
        send_notification(owner, f"{_link(v)} subscribed to your paid circle {circles.plain_name(row.name)}.")
    return jsonify({"message": message})


@app.route("/api/circles/<int:cid>/cancel", methods=["POST"])
@auth_required
@validate_formkey
def circle_cancel(cid, v):
    row, _ = _paid_circle(cid)
    if not circle_store.cancel(g.db, row.id, v.id):
        return _fail("You aren't subscribed to that circle.", 404)
    g.db.commit()
    return jsonify({"message": "Your subscription won't renew. You keep access until the day you paid to."})


def circle_status(owner, viewer):
    """What a profile shows of circles to this viewer, or None (a visitor, or their own profile): the owner's paid
    circles that take subscribers or that the viewer is in. Custom circles are never part of it. For templates."""
    if viewer is None or viewer.id == owner.id:
        return None
    now = int(time.time())
    paid = []
    for row in circle_store.paid_circles(g.db, owner.id):
        sub = circle_store.subscriber_row(g.db, row.id, viewer.id)
        tier = circles.tier_of(sub.tier, sub.status, sub.renews_utc, now) if sub else None
        subscribed = tier == circles.SUBSCRIBER
        cancelled = bool(subscribed and sub.cancelled)
        if row.price_coins > 0 or subscribed:
            paid.append({"id": row.id, "name": row.name, "price": row.price_coins, "subscribed": subscribed,
                         "cancelled": cancelled, "joinable": row.price_coins > 0 and (not subscribed or cancelled)})
    joinable = [p for p in paid if p["joinable"]]
    return {"paid": paid, "joinable": joinable, "subscribed": any(p["subscribed"] and not p["cancelled"] for p in paid)}


def circle_choices(viewer):
    """The member's circles as the audience controls offer them (composer, "Who can see this", stories): Friends
    first (every account has it), then their own circles, then their paid ones. Asked once per request."""
    from jinja2 import Undefined
    if viewer is None or isinstance(viewer, Undefined):
        return []
    found = g.__dict__.get("_circle_choices")
    if found is None:
        found = [{"token": circles.FRIENDS_TOKEN, "name": circles.FRIENDS_NAME, "kind": circles.FRIENDS,
                  "hint": "People you follow who follow you back"}]
        for row in circle_store.circles_of(g.db, viewer.id, kinds=(circles.CUSTOM, circles.PAID)):
            found.append({"token": str(row.id), "name": row.name, "kind": row.kind,
                          "hint": "Paid circle" if row.kind == circles.PAID else ""})
        g._circle_choices = found
    return found


app.jinja_env.globals.update(circle_status=circle_status, circle_choices=circle_choices)


# --- who can see a post that already exists ------------------------------------------------------------

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
    """The author changes who can see one of their posts: Public, or some of their circles (`audience` 4 and
    `circles`). Narrowing it takes the same refusals as posting it that way (it cannot be anonymous, co-authored or carry
    an own video). A post that was public and already shared can still be narrowed: what others did with it stays
    (their forward copies stay in their guilds; reposts follow the post), the forward copies the author made are taken
    down, and the page says so first (`confirm=1`, as when the circles reach nobody yet). Making it Public again needs
    `confirm=1` too: everyone will see it."""
    post = get_post(pid, v=v)                              # only someone who may see it gets this far
    if post.author_id != v.id:
        abort(403)
    if post.is_deleted or post.is_banned:
        abort(404)
    if post.repost_id not in (0, None):
        return _fail("Only a post on your own profile has an audience.")
    if (post.audience or 0) == circles.GUILD:
        return _fail("A post in a Circle guild keeps that guild's audience.")
    raw, chosen = request.values.get("audience"), request.values.getlist("circles")
    if (raw is None or not raw.strip()) and not chosen:
        return _fail("Choose who can see this.")
    audience, tokens, error = circles.parse_audience(raw, chosen)
    if error:
        return _fail(error)
    circle_ids = []
    if audience == circles.CIRCLES:
        circle_ids, error = circle_store.resolve(g.db, v.id, tokens)
        if error:
            return _fail(error)
    before = post.audience or 0
    before_ids = sorted(circle_guard.post_circles(g.db, [post.id]).get(post.id, ())) if before == circles.CIRCLES else []
    if audience == before and circle_ids == before_ids:
        return jsonify({"message": "Nothing changed."})

    own = []
    if audience != circles.PUBLIC:
        refusal = circles.post_refusal(
            audience, anonymous=bool(post.is_anonymous), coauthors=bool(coauthors.rows_of(g.db, post.id)),
            own_video=bool(post.url and media_attach.own_video(g.db, v.id, post.url)))
        if refusal:
            return _fail(refusal)
        notes = []
        if circle_store.reaches_nobody(g.db, v.id, circle_ids):
            notes.append("Nobody is in those circles yet, so only you would see this post.")
        if before == circles.PUBLIC:
            own, others, reposted = _shared_copies(post, v.id)
            notes.append(sharing.narrowing_note([c.board.name for c in own], others, reposted))
        notes = [n for n in notes if n]
        # say what will happen before saving (the page asks, `confirm=1` goes on)
        if notes and request.values.get("confirm") != "1":
            return jsonify({"error": " ".join(notes), "confirm": True}), 409
    elif request.values.get("confirm") != "1":
        return jsonify({"error": "This post will be visible to everyone. Confirm to make it public.", "confirm": True}), 409

    post.audience = audience
    post.post_public = not audience and not post.board.is_private
    g.db.add(post)
    circle_store.set_post_circles(g.db, post.id, circle_ids)
    _withdraw(own, v.id, int(time.time()))
    g.db.commit()
    if audience != circles.PUBLIC:
        # a picture that was public may still sit in a CDN: it must not be served from there any more
        media_cdn.purge(media_attach.attached_paths(g.db, submission_id=post.id))

    cache.delete_memoized(User.userpagelisting, v, sort="new")
    cache.delete_memoized(frontlist, sort="new")
    cache.delete_memoized(frontlist)
    if audience == circles.PUBLIC:
        return jsonify({"message": "This post is public now."})
    named = [name for _, _, name in circle_store.names_of(g.db, circle_ids)]
    return jsonify({"message": f"Shared with {circles.shared_label(named)} now."})


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
