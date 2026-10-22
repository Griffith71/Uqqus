from urllib.parse import urlparse
import mistletoe
from sqlalchemy import func
from bs4 import BeautifulSoup
import pyotp
import qrcode
import io
import gevent

from ruqqus.helpers.wrappers import *
from ruqqus.helpers.visibility import viewer_level, user_hidden, hidden_notice
from ruqqus.helpers import subscriber_badges
from ruqqus.helpers.base36 import *
from ruqqus.helpers.sanitize import *
from ruqqus.helpers.filters import *
from ruqqus.helpers.embed import *
from ruqqus.helpers.markdown import *
from ruqqus.helpers.get import *
from ruqqus.classes import *
from ruqqus.mail import *
from flask import *
from ruqqus.__main__ import app, cache, limiter, db_session

BAN_REASONS = ['',
               "Deceptive URL shorteners are not permitted."
               ]


@app.route("/2faqr/<secret>", methods=["GET"])
@auth_required
def mfa_qr(secret, v):
    x = pyotp.TOTP(secret)
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_L
    )
    qr.add_data(x.provisioning_uri(v.username, issuer_name=app.config["SITE_NAME"]))
    img = qr.make_image(fill_color="#"+app.config["SITE_COLOR"], back_color="white")

    mem = io.BytesIO()

    img.save(mem, format="PNG")
    mem.seek(0, 0)
    return send_file(mem, mimetype="image/png", as_attachment=False)


@app.route("/api/is_available/<name>", methods=["GET"])
@app.route("/api/v1/is_available/<name>", methods=["GET"])
@auth_desired
@api("read")
def api_is_available(name, v):

    name=name.lstrip().rstrip()

    if len(name)<3 or len(name)>25:
        return jsonify({name:False})
        
    x=get_user(name)

    if x:
        return jsonify({name: False})
    else:
        return jsonify({name: True})


@app.route("/uid/<uid>", methods=["GET"])
def user_uid(uid):
    return redirect(get_account(uid).permalink)

# Allow Id of user to be queryied, and then redirect the bot to the
# actual user api endpoint.
# So they get the data and then there will be no need to reinvent
# the wheel.
@app.route("/api/v1/uid/<uid>", methods=["GET"])
@auth_desired
@api("read")
def user_by_uid(uid, v=None):
    user=get_account(uid)
    
    return redirect(f'/api/v1/user/{user.username}/info')
        
@app.route("/u/<username>", methods=["GET"])
def redditor_moment_redirect(username):

    return redirect(f"/@{username}")


@app.route("/@<username>", methods=["GET"])
@app.route("/@<username>/spaces", methods=["GET"], defaults={"tab": "spaces"})
@app.route("/api/v1/user/<username>/listing", methods=["GET"])
@app.get("/api/v2/users/<username>/submissions")
@auth_desired
@api("read")
def u_username(username, v=None, tab=None):
    """
Get posts created by another user.

URL path parameters:
* `username` - The user whose posts are being fetched.

Optional query parameters:
* `sort` - One of `hot`, `new`, `old`, `top`, `activity`, or `disputed`. Default `new`.
* `t` - One of `day`, `week`, `month`, `year`, `all`. Default `all`.
* `page` - Page of results to return. Default `1`.
"""

    # username is unique so at most this returns one result. Otherwise 404

    # case insensitive search

    u = get_user(username, v=v)

    # a filtered word in the username hides the whole profile from this viewer
    if user_hidden(u, v):
        return hidden_notice(v)

    # a bot that has not been approved is its owner's and the admins' business only (helpers/bots.py)
    if not u.bot_shown_to(v):
        abort(404)

    # check for wrong cases

    #if username != u.username:
        #return redirect(request.path.replace(username, u.username))

    if u.reserved:
        return {'html': lambda: render_template("userpage_reserved.html",
                                                u=u,
                                                v=v),
                'api': lambda: {"error": f"That username is reserved for: {u.reserved}"}
                }

    if u.is_suspended and not (v and (v.admin_level >=3 or v.id==u.id)):
        return {'html': lambda: render_template("userpage_banned.html",
                                                u=u,
                                                v=v),
                'api': lambda: {"error": "That user is banned"}
                }

    if u.is_deleted and not (v and v.admin_level >= 3):
        return {'html': lambda: render_template("userpage_deleted.html",
                                                u=u,
                                                v=v),
                'api': lambda: {"error": "That user deactivated their account."}
                }

    if u.is_private and not (v and (v.admin_level >=3 or v.id==u.id)):
        return {'html': lambda: render_template("userpage_private.html",
                                                u=u,
                                                v=v),
                'api': lambda: {"error": "That userpage is private"}
                }

    if u.is_blocking and not (v and v.admin_level >= 3):
        return {'html': lambda: render_template("userpage_blocking.html",
                                                u=u,
                                                v=v),
                'api': lambda: {"error": f"You are blocking @{u.username}."}
                }

    if u.is_blocked and not (v and v.admin_level >= 3):
        return {'html': lambda: render_template("userpage_blocked.html",
                                                u=u,
                                                v=v),
                'api': lambda: {"error": "This person is blocking you."}
                }

    sort = request.args.get("sort", "new")
    t = request.args.get("t", "all")
    page = page_number()
    page = max(page, 1)

    # the Spaces tab lists the account's Spaces, never under Posts (helpers/spaces.py: a Space is not a post)
    spaces_tab = tab == "spaces"
    ids = u.userpagelisting(v=v, page=page, sort=sort, t=t, spaces=spaces_tab, filter_level=viewer_level(v))

    # we got 26 items just to see if a next page exists
    next_exists = (len(ids) == 26)
    ids = ids[0:25]

    listing = get_posts(ids, v=v)
    subscriber_badges.attach(u, listing, v)       # helpers/subscriber_badges.py

    reposted_ids = {r.submission_id for r in g.db.query(RepostRelationship).filter(
        RepostRelationship.user_id == u.id,
        RepostRelationship.submission_id.in_(ids)
    ).all()} if ids else set()

    return {'html': lambda: render_template("userpage.html",
                                            u=u,
                                            v=v,
                                            listing=listing,
                                            page=page,
                                            next_exists=next_exists,
                                            is_following=(v and u.has_follower(v)),
                                            reposted_ids=reposted_ids,
                                            profile_tab="spaces" if spaces_tab else "posts"),
            'api': lambda: jsonify({"data": [x.json for x in listing]})
            }


@app.route("/@<username>/comments", methods=["GET"])
@app.route("/api/v1/user/<username>/comments", methods=["GET"])
@app.get("/api/v2/users/<username>/comments")
@auth_desired
@api("read")
def u_username_comments(username, v=None):
    """
Get comments created by another user.

URL path parameters:
* `username` - The user whose comments are being fetched.

Optional query parameters:
* `sort` - One of `hot`, `new`, `old`, `top`, or `disputed`. Default `new`.
* `t` - One of `day`, `week`, `month`, `year`, `all`. Default `all`.
* `page` - Page of results to return. Default `1`.
"""

    # username is unique so at most this returns one result. Otherwise 404

    # case insensitive search

    user = get_user(username, v=v)

    # a filtered word in the username hides the whole profile from this viewer
    if user_hidden(user, v):
        return hidden_notice(v)

    # check for wrong cases

    if username != user.username:
        return redirect(f'{user.url}/comments')

    u = user

    if u.reserved:
        return {'html': lambda: render_template("userpage_reserved.html",
                                                u=u,
                                                v=v),
                'api': lambda: {"error": f"That username is reserved for: {u.reserved}"}
                }

    if u.is_suspended and not (v and (v.admin_level >=3 or v.id==u.id)):
        return {'html': lambda: render_template("userpage_banned.html",
                                                u=u,
                                                v=v),
                'api': lambda: {"error": "That user is banned"}
                }

    if u.is_deleted and not (v and v.admin_level >= 3):
        return {'html': lambda: render_template("userpage_deleted.html",
                                                u=u,
                                                v=v),
                'api': lambda: {"error": "That user deactivated their account."}
                }

    if u.is_private and not (v and (v.admin_level >=3 or v.id==u.id)):
        return {'html': lambda: render_template("userpage_private.html",
                                                u=u,
                                                v=v),
                'api': lambda: {"error": "That userpage is private"}
                }

    if u.is_blocking and not (v and v.admin_level >= 3):
        return {'html': lambda: render_template("userpage_blocking.html",
                                                u=u,
                                                v=v),
                'api': lambda: {"error": f"You are blocking @{u.username}."}
                }

    if u.is_blocked and not (v and v.admin_level >= 3):
        return {'html': lambda: render_template("userpage_blocked.html",
                                                u=u,
                                                v=v),
                'api': lambda: {"error": "This person is blocking you."}
                }

    page = page_number()

    ids = user.commentlisting(
        v=v, 
        filter_level=viewer_level(v),
        page=page,
        sort=request.args.get("sort","new"),
        t=request.args.get("t","all")
        )

    # we got 26 items just to see if a next page exists
    next_exists = (len(ids) == 26)
    ids = ids[0:25]

    listing = get_comments(ids, v=v)
    subscriber_badges.attach(user, listing, v)       # helpers/subscriber_badges.py

    is_following = (v and user.has_follower(v))

    comment_reposted_ids = {r.comment_id for r in g.db.query(CommentRepostRelationship).filter(
        CommentRepostRelationship.user_id == user.id,
        CommentRepostRelationship.comment_id.in_(ids)
    ).all()} if ids else set()

    return {"html": lambda: render_template("userpage_comments.html",
                                            u=user,
                                            v=v,
                                            listing=listing,
                                            page=page,
                                            next_exists=next_exists,
                                            is_following=is_following,
                                            standalone=True,
                                            comment_reposted_ids=comment_reposted_ids),
            "api": lambda: jsonify({"data": [c.json for c in listing]})
            }


@app.route("/@<username>/forwarded", methods=["GET"])
@app.route("/api/v1/user/<username>/forwarded", methods=["GET"])
@auth_desired
@api("read")
def u_username_forwarded(username, v=None):
    """
Get posts another user has forwarded to a guild, or comments they've
forwarded into a new post - as the actor, regardless of who authored the
original content.

URL path parameters:
* `username` - The user whose forwarding activity is being fetched.

Optional query parameters:
* `page` - Page of results to return. Default `1`.
"""

    user = get_user(username, v=v)

    # a filtered word in the username hides the whole profile from this viewer
    if user_hidden(user, v):
        return hidden_notice(v)

    if username != user.username:
        return redirect(f'{user.url}/forwarded')

    u = user

    if u.reserved:
        return {'html': lambda: render_template("userpage_reserved.html",
                                                u=u,
                                                v=v),
                'api': lambda: {"error": f"That username is reserved for: {u.reserved}"}
                }

    if u.is_suspended and not (v and (v.admin_level >=3 or v.id==u.id)):
        return {'html': lambda: render_template("userpage_banned.html",
                                                u=u,
                                                v=v),
                'api': lambda: {"error": "That user is banned"}
                }

    if u.is_deleted and not (v and v.admin_level >= 3):
        return {'html': lambda: render_template("userpage_deleted.html",
                                                u=u,
                                                v=v),
                'api': lambda: {"error": "That user deactivated their account."}
                }

    if u.is_private and not (v and (v.admin_level >=3 or v.id==u.id)):
        return {'html': lambda: render_template("userpage_private.html",
                                                u=u,
                                                v=v),
                'api': lambda: {"error": "That userpage is private"}
                }

    if u.is_blocking and not (v and v.admin_level >= 3):
        return {'html': lambda: render_template("userpage_blocking.html",
                                                u=u,
                                                v=v),
                'api': lambda: {"error": f"You are blocking @{u.username}."}
                }

    if u.is_blocked and not (v and v.admin_level >= 3):
        return {'html': lambda: render_template("userpage_blocked.html",
                                                u=u,
                                                v=v),
                'api': lambda: {"error": "This person is blocking you."}
                }

    page = page_number()

    ids = user.forwarded_idlist(v=v, page=page)

    next_exists = (len(ids) == 26)
    ids = ids[0:25]

    listing = get_posts(ids, v=v)
    subscriber_badges.attach(user, listing, v)       # helpers/subscriber_badges.py

    is_following = (v and user.has_follower(v))

    return {"html": lambda: render_template("userpage_forwarded.html",
                                            u=user,
                                            v=v,
                                            listing=listing,
                                            page=page,
                                            next_exists=next_exists,
                                            is_following=is_following),
            "api": lambda: jsonify({"data": [x.json for x in listing]})
            }


@app.route("/api/v1/user/<username>/info", methods=["GET"])
@app.get("/api/v2/users/<username>")
@auth_desired
@api("read")
def u_username_info(username, v=None):
    """
Get information about another user.

URL path parameters:
* `username` - A user's name.
"""

    user=get_user(username, v=v)

    # a filtered word in the username hides the whole profile from this viewer
    if user_hidden(user, v):
        return hidden_notice(v)

    refused = profile_json_refusal(user, v)
    if refused:
        return jsonify({"error": refused}), 403

    return jsonify(user.json)


def profile_json_refusal(u, v):
    """Why `v` may not read `u`'s profile as JSON, or None: the same gates as the profile page (userpage)."""
    if v and (v.id == u.id or v.admin_level >= 3):
        return None
    if u.reserved:
        return f"That username is reserved for: {u.reserved}"
    if u.is_private:
        return "That userpage is private"
    if u.is_blocking:
        return f"You are blocking @{u.username}."
    if u.is_blocked:
        return "This person is blocking you."
    return None


@app.route("/api/follow/<username>", methods=["POST"])
@app.post("/api/v2/users/<username>/follow")
@auth_required
@api("update")        # an app's token needs the update scope (bug hunt: no scope was checked)
@validate_formkey
def follow_user(username, v):
    """
Follow another user.

URL path parameters:
* `username` - A user's name.
"""

    target = get_user(username)

    if target.id==v.id:
        return jsonify({"error": "You can't follow yourself!"}), 400

    # the profile's own gates (userpage); the message never says which, so it cannot reveal a block
    if (target.is_deleted or target.is_suspended or target.is_nofollow or target.is_private
            or v.any_block_exists(target)):
        return jsonify({"error": "You can't follow this account."}), 403

    # check for existing follow
    if g.db.query(Follow).filter_by(user_id=v.id, target_id=target.id).first():
        abort(409)

    new_follow = Follow(user_id=v.id,
                        target_id=target.id)

    g.db.add(new_follow)
    g.db.flush()
    target.stored_subscriber_count=target.follower_count
    g.db.add(target)
    g.db.commit()

    cache.delete_memoized(User.idlist, v, kind="user")

    return "", 204


@app.route("/api/unfollow/<username>", methods=["POST"])
@app.delete("/api/v2/users/<username>/follow")
@auth_required
@api("update")        # an app's token needs the update scope (bug hunt: no scope was checked)
@validate_formkey
def unfollow_user(username, v):
    """
Unfollow another user.

URL path parameters:
* `username` - A user's name.
"""

    target = get_user(username)

    # check for existing follow
    follow = g.db.query(Follow).filter_by(
        user_id=v.id, target_id=target.id).first()

    if not follow:
        abort(409)

    g.db.delete(follow)

    cache.delete_memoized(User.idlist, v, kind="user")

    return "", 204


@app.route("/api/agree_tos", methods=["POST"])
@auth_required
@validate_formkey
def api_agree_tos(v):

    v.tos_agreed_utc = int(time.time())

    g.db.add(v)

    return redirect("/help/terms")


@app.get("/@<username>/pic/profile")
@limiter.exempt
def user_profile(username):
    x = get_user(username)
    return redirect(x.profile_url)

@app.get("/uid/<uid>/pic/profile")
@limiter.exempt
def user_profile_uid(uid):
    x=get_account(uid)
    return redirect(x.profile_url)


@app.post("/@<username>/toggle_bell")
@app.post("/api/v2/users/<username>/toggle_bell")
@auth_required
@api("update")
@validate_formkey
def toggle_user_bell(username, v):
    """
Toggle notifications for new posts by a user. You must be following the user.

URL path parameters:
* `username` - Another user's name
"""

    user=get_user(username, v=v, graceful=True)
    if not user:
        return jsonify({"error": f"User '@{username}' not found."}), 404

    follow=g.db.query(Follow).filter_by(user_id=v.id, target_id=user.id).first()
    if not follow:
        return jsonify({"error": f"You aren't following @{user.username}"}), 404

    follow.get_notifs = not follow.get_notifs
    g.db.add(follow)
    g.db.commit()

    # posts, forwards, reposts, Spaces going live and stories (helpers/bell.py)
    if follow.get_notifs:
        return jsonify({"message": f"You'll be told about everything @{user.username} does."})
    return jsonify({"message": f"Notifications for @{user.username} turned off."})



