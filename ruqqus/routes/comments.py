import html
import re
from urllib.parse import urlparse
from sqlalchemy import func, literal
from bs4 import BeautifulSoup
from datetime import datetime
import gevent
from os import environ

from ruqqus.helpers.wrappers import *
from ruqqus.helpers.media import attach as media_attach, cdn as media_cdn, safety as media_safety
from ruqqus.helpers.visibility import post_hidden, comment_hidden, comment_thread_hidden, hidden_notice, history_text_shown
from ruqqus.helpers.base36 import *
from ruqqus.helpers.sanitize import *
from ruqqus.helpers.filters import *
from ruqqus.helpers.embed import *
from ruqqus.helpers.markdown import *
from ruqqus.helpers.get import *
from ruqqus.helpers import circle_guard
from ruqqus.helpers import post_follows
from ruqqus.helpers.session_helpers import *
from ruqqus.helpers.alerts import *
from ruqqus.helpers.aws import *
from ruqqus.helpers.word_filter_store import apply_comment_severity
from ruqqus.helpers.post_fields import flag
from ruqqus.helpers import comment_permission as cperm
from ruqqus.helpers import anonymity
from ruqqus.classes import *
from flask import *
from ruqqus.helpers import sharing
from ruqqus.helpers import bell as bell_rules
from ruqqus.__main__ import app, limiter, db_session
from .front import frontlist
from .posts import canonical_redirect


BUCKET=app.config["S3_BUCKET"]


# the longest comment (the comment boxes say so with maxlength, templates/comments.html and submission.html)
COMMENT_MAX = 10000


def _followers_who_may_read(post, ids):
    """Of `ids`, the ones that may still read `post`: a Circle post goes only to its audience and a private
    guild's post only to people who can view the guild (what the post page itself asks)."""
    ids = set(ids)
    if post.audience:
        eligible = circle_guard.eligible_ids(g.db, post.author_id, post.audience, board_id=post.board_id,
                                             circle_ids=circle_guard.post_circles(g.db, [post.id]).get(post.id, ()))
        ids &= eligible | {post.author_id}
    if ids and not post.is_public and not post.is_pinned:
        ids = {u.id for u in g.db.query(User).filter(User.id.in_(ids)).all() if not post.in_private_guild_for(u)}
    return ids


# a comment's page under every address it has had: /@author/comment/<id>, the anonymous /comment/<id>, the older
# /post/<pid>/<slug>/<cid> forms and the short links; anything but its own address is redirected to it after the checks
@app.route("/@<username>/comment/<cid>", methods=["GET"])
@app.route("/comment/<cid>", methods=["GET"])
@app.route("/post_short/<pid>/<cid>", methods=["GET"])
@app.route("/post_short/<pid>/<cid>/", methods=["GET"])
@app.route("/api/v1/comment/<cid>", methods=["GET"])
@app.route("/+<guildname>/post/<pid>/<anything>/<cid>", methods=["GET"])
@app.route("/post/<pid>/<anything>/<cid>", methods=["GET"])
@app.route("/api/v1/post/<pid>/comment/<cid>", methods=["GET"])
@app.get("/api/vue/comment/<cid>")
@app.get("/api/v2/comments/<cid>")
@auth_desired
@api("read")
def post_pid_comment_cid(cid, pid=None, guildname=None, anything=None, username=None, v=None):
    """
Fetch a single comment and up to 5 total layers above and below.

URL path parameters:
* `cid` - The base 36 comment id

Optional query parameters:
* `context` - Integer between `0` and `4` inclusive. Number of generations of parent comments to fetch. Default `0`
"""

    comment = get_comment(cid, v=v)

    # every check below runs on the comment's own post, never on the `pid` in the URL
    post = get_post(base36encode(comment.parent_submission), v=v)
    board = post.board

    if post.in_private_guild_for(v):
        abort(403)

    if post_hidden(post, v) or comment_thread_hidden(comment, v):
        return hidden_notice(v)

    if board.is_banned and not (v and v.admin_level > 3):
        return {'html': lambda: render_template("board_banned.html",
                                                v=v,
                                                b=board),

                'api': lambda: {'error': f'+{board.name} is banned.'}

                }

    moved = canonical_redirect(comment)
    if moved:
        return moved

    post._preloaded_comments = [comment]

    # context improver
    context = min(int(request.args.get("context", 0)), 4)
    comment_info = comment
    c = comment
    while context > 0 and not c.is_top_level:

        parent = get_comment(c.parent_comment_id, v=v)

        post._preloaded_comments += [parent]

        c = parent
        context -= 1
    top_comment = c

    sort_type = request.args.get("sort", "hot")
    # children comments

    current_ids = [comment.id]

    exile = select(ModAction).filter_by(
        kind="exile_user"
    ).distinct(ModAction.target_comment_id).subquery()

    for i in range(6 - context):
        if v:

            votes = select(CommentVote).filter(
                CommentVote.user_id == v.id
            ).subquery()

            blocking = v.blocking.subquery()
            blocked = v.blocked.subquery()


            comms = g.db.query(
                Comment,
                votes.c.vote_type,
                blocking.c.id,
                blocked.c.id,
                aliased(ModAction, alias=exile)
            ).select_from(Comment).options(
                lazyload('*'),
                joinedload(Comment.comment_aux),
                joinedload(Comment.author).joinedload(User.title),
                joinedload(Comment.post).lazyload('*'),
                joinedload(Comment.post).joinedload(Submission.submission_aux),
                joinedload(Comment.post).joinedload(Submission.board),
                Load(CommentVote).lazyload('*'),
                Load(UserBlock).lazyload('*'),
                Load(ModAction).lazyload('*')
            ).filter(
                Comment.parent_comment_id.in_(current_ids)
            ).join(
                votes,
                votes.c.comment_id == Comment.id,
                isouter=True
            ).join(
                blocking,
                blocking.c.target_id == Comment.author_id,
                isouter=True
            ).join(
                blocked,
                blocked.c.user_id == Comment.author_id,
                isouter=True
            ).join(
                exile,
                exile.c.target_comment_id==Comment.id,
                isouter=True
            )

            if sort_type == "hot":
                comments = comms.order_by(Comment.score_hot.asc()).all()
            elif sort_type == "top":
                comments = comms.order_by(Comment.score_top.asc()).all()
            elif sort_type == "new":
                comments = comms.order_by(Comment.created_utc.desc()).all()
            elif sort_type == "old":
                comments = comms.order_by(Comment.created_utc.asc()).all()
            elif sort_type == "disputed":
                comments = comms.order_by(Comment.score_disputed.asc()).all()
            elif sort_type == "random":
                c = comms.all()
                comments = random.sample(c, k=len(c))
            else:
                abort(422)

            output = []
            for c in comms:
                comment = c[0]
                comment._voted = c[1] or 0
                comment._is_blocking = c[2] or 0
                comment._is_blocked = c[3] or 0
                comment._is_guildmaster=top_comment._is_guildmaster
                comment._is_exiled_for=c[4] or 0
                output.append(comment)
        else:

            comms = g.db.query(
                Comment,
                aliased(ModAction, alias=exile)
            ).options(
                joinedload(Comment.author).joinedload(User.title)
            ).filter(
                Comment.parent_comment_id.in_(current_ids)
            ).join(
                exile,
                exile.c.target_comment_id==Comment.id,
                isouter=True
            )

            if sort_type == "hot":
                comments = comms.order_by(Comment.score_hot.asc()).all()
            elif sort_type == "top":
                comments = comms.order_by(Comment.score_top.asc()).all()
            elif sort_type == "new":
                comments = comms.order_by(Comment.created_utc.desc()).all()
            elif sort_type == "old":
                comments = comms.order_by(Comment.created_utc.asc()).all()
            elif sort_type == "disputed":
                comments = comms.order_by(Comment.score_disputed.asc()).all()
            elif sort_type == "random":
                c = comms.all()
                comments = random.sample(c, k=len(c))
            else:
                abort(422)

            output = []
            for c in comms:
                comment=c[0]
                comment._is_exiled_for=c[1] or 0
                output.append(comment)

        post._preloaded_comments += output

        current_ids = [x.id for x in output]


    post.tree_comments(v=v)

    post.nested_comments=[top_comment]

    return {'html': lambda: post.rendered_page(v=v, comment=top_comment, comment_info=comment_info),
            'api': lambda: top_comment.json
            }

@app.route("/api/comment", methods=["POST"])
@app.route("/api/v1/comment", methods=["POST"])
@app.post("/api/v2/comments")
@is_not_banned
@throttle_check
@no_negative_balance('toast')
@tos_agreed
@validate_formkey
@api("create")
def api_comment(v):
    """
Create a comment

Required form data:
* `parent_fullname` - The fullname of the post or comment that is being commented on
* `body` - Raw comment text

Optional form data:
* `paid_partnership` - `true` to mark the comment as a paid partnership.
* `made_with_ai` - `true` to mark the comment as made with AI.
* `anonymous` - `true` to comment anonymously: other users are not told who wrote it
  (the author and site admins are). It cannot be changed afterwards.

Optional file data:
* `file` - An image to upload and append to the comment body. Requires premium.
"""
    # bots may post, forward and help moderate, but never comment (or vote, routes/votes.py)
    if request.headers.get("X-User-Type", "").lower() == "bot" or v.is_bot:
        return jsonify({"error": "Bots can't comment."}), 403

    parent_fullname = request.form.get("parent_fullname")

    # get parent item info
    parent_id = parent_fullname.split("_")[1]
    if parent_fullname.startswith("t2"):
        parent_post = get_post(parent_id, v=v)
        parent = parent_post
        parent_comment_id = None
        level = 1
        parent_submission = base36decode(parent_id)
    elif parent_fullname.startswith("t3"):
        parent = get_comment(parent_id, v=v)
        parent_comment_id = parent.id
        level = parent.level + 1
        parent_id = parent.parent_submission
        parent_submission = parent_id
        parent_post = get_post(parent_id, v=v)
    else:
        abort(400)

    #process and sanitize
    body = request.form.get("body", "").strip()
    if len(body) > COMMENT_MAX:
        return jsonify({"error": f"Comments can be {COMMENT_MAX:,} characters at most."}), 400

    if not body and not (v.has_premium and request.files.get('file')):
        return jsonify({"error":"You need to actually write something!"}), 400
    
    body=preprocess(body)
    with CustomRenderer(post_id=parent_id) as renderer:
        body_md = renderer.render(mistletoe.Document(body))
    body_html = sanitize(body_md, linkgen=True)

    # Run safety filter
    bans = filter_comment_html(body_html)

    if bans:
        ban = bans[0]
        reason = f"Remove the {ban.domain} link from your comment and try again."
        if ban.reason:
            reason += f" {ban.reason_text}"
            
        #auto ban for digitally malicious content
        if any([x.reason==7 for x in bans]):
            v.ban(reason="Sexualizing minors")
        elif any([x.reason==4 for x in bans]):
            v.ban(days=30, reason="Digitally malicious content")
        elif any([x.reason==9 for x in bans]):
            v.ban(days=7, reason="Engaging in illegal activity")
        
        return jsonify({"error": reason}), 401

    # check existing
    existing = g.db.query(Comment).join(CommentAux).filter(Comment.author_id == v.id,
                                                           Comment.deleted_utc == 0,
                                                           Comment.parent_comment_id == parent_comment_id,
                                                           Comment.parent_submission == parent_submission,
                                                           CommentAux.body == body
                                                           ).options(contains_eager(Comment.comment_aux)).first()
    if existing:
        return jsonify({"error": f"You already made that comment: {existing.permalink}"}), 409

    # No commenting on deleted/removed things
    if parent.is_banned or parent.deleted_utc > 0:
        return jsonify(
            {"error": "You can't comment on things that have been deleted."}), 403

    # an anonymous author is not "blocking" anyone: the refusal would say who they are
    if parent.is_blocking and not parent.is_anonymous and not v.admin_level>=3 and not parent.board.has_mod(v, "content"):
        return jsonify(
            {"error": "You can't comment on content from users that you're blocking."}
            ), 403

    if parent.is_blocked and not parent.is_anonymous and not v.admin_level>=3 and not parent.board.has_mod(v, "content"):
        return jsonify(
            {"error": "You can't comment on content from users that are blocking you."}
            ), 403

    # check for archive and ban state
    post = get_post(parent_id)
    if post.is_archived or not post.board.can_comment(v):

        return jsonify({"error": "You can't comment on this."}), 403

    # the author may limit who can comment on the post on their profile
    limited = cperm.restriction(post, v)
    if limited:
        return jsonify({"error": limited}), 403

    # get bot status
    is_bot = request.headers.get("X-User-Type","").lower()=="bot"
    # check spam - this should hopefully be faster
    if not is_bot:
        now = int(time.time())
        cutoff = now - 60 * 60 * 24

        similar_comments = g.db.query(Comment
                                      ).options(
            lazyload('*')
        ).join(Comment.comment_aux
               ).filter(
            Comment.author_id == v.id,
            CommentAux.body.op(
                '<->')(body) < app.config["COMMENT_SPAM_SIMILAR_THRESHOLD"],
            Comment.created_utc > cutoff
        ).options(contains_eager(Comment.comment_aux)).all()

        threshold = app.config["COMMENT_SPAM_COUNT_THRESHOLD"]
        if v.age >= (60 * 60 * 24 * 7):
            threshold *= 3
        elif v.age >= (60 * 60 * 24):
            threshold *= 2

        if len(similar_comments) > threshold:
            text = f"Your {app.config['SITE_NAME']} account has been suspended for 1 day for the following reason:\n\n> Too much spam!"
            send_notification(v, text)

            v.ban(reason="Spamming.",
                  days=1)

            for alt in v.alts:
                if not alt.is_suspended:
                    alt.ban(reason="Spamming.", days=1)

            for comment in similar_comments:
                comment.is_banned = True
                comment.ban_reason = "Automatic spam removal. This happened because the post's creator submitted too much similar content too quickly."
                g.db.add(comment)
                ma=ModAction(
                    user_id=1,
                    target_comment_id=comment.id,
                    kind="ban_comment",
                    board_id=comment.post.board_id,
                    note="spam"
                    )
                g.db.add(ma)

            g.db.commit()
            return jsonify({"error": "Too much spam!"}), 403

    # check badlinks
    soup = BeautifulSoup(body_html, features="html.parser")
    links = [x['href'] for x in soup.find_all('a') if x.get('href')]

    for link in links:
        parse_link = urlparse(link)
        check_url = ParseResult(scheme="https",
                                netloc=parse_link.netloc,
                                path=parse_link.path,
                                params=parse_link.params,
                                query=parse_link.query,
                                fragment='')
        check_url = urlunparse(check_url)

        badlink = g.db.query(BadLink).filter(
            literal(check_url).contains(
                BadLink.link)).first()

        if badlink:
            return jsonify({"error": f"Remove the following link and try again: `{check_url}`. Reason: {badlink.reason_text}"}), 403

    # create comment
    c = Comment(author_id=v.id,
                parent_submission=parent_submission,
                #parent_fullname=parent.fullname,
                parent_comment_id=parent_comment_id,
                level=level,
                is_sensitive=(bool(request.form.get("sensitive", "")) or post.is_sensitive),
                paid_partnership=flag(request.form, "paid_partnership"),
                made_with_ai=flag(request.form, "made_with_ai"),
                # commenting on your own anonymous post is anonymous, or your name
                # next to the "OP" mark would undo it
                is_anonymous=flag(request.form, "anonymous") or anonymity.must_be_anonymous(parent_post, v),
                original_board_id=parent_post.board_id,
                is_bot=is_bot,
                app_id=v.client.application.id if v.client else None,
                creation_region=request.headers.get("cf-ipcountry")
                )

    g.db.add(c)
    g.db.flush()


    if v.has_premium:
        if request.files.get("file"):
            file=request.files["file"]
            if not file.content_type.startswith('image/'):
                return jsonify({"error": "That wasn't an image!"}), 400
            
            name = f'comment/{c.base36id}/{secrets.token_urlsafe(8)}'
            upload_file(name, file)

            body = request.form.get("body") + f"\n\n![](https://{BUCKET}/{name})"
            body=preprocess(body)
            with CustomRenderer(post_id=parent_id) as renderer:
                body_md = renderer.render(mistletoe.Document(body))
            body_html = sanitize(body_md, linkgen=True)
            
            #csam detection. The scan runs after this request is over, so what it does on a
            #match uses a session of its own and finds the comment again by id.
            scanned_comment_id = c.id

            def del_function():
                db = db_session()
                delete_file(name)
                banned = db.get(Comment, scanned_comment_id)
                if banned is not None:
                    banned.is_banned = True
                    db.add(banned)
                    db.commit()
                db.close()

            # the arguments go to the function itself: passed as args=(...), gevent handed it one
            # unknown keyword, the greenlet died at once and no comment image was ever scanned
            gevent.spawn(check_csam_url, f"https://{BUCKET}/{name}", v, del_function)



    c_aux = CommentAux(
        id=c.id,
        body_html=body_html,
        body=body
    )

    g.db.add(c_aux)
    apply_comment_severity(c, body_html)
    g.db.flush()

    notify_users = set()

    if level == 1:
        # a comment directly on a post: the person responsible for it (on a forward copy the one who
        # forwarded it, not the original author) and the people who follow it (helpers/post_follows.py)
        notify_users.update(post_follows.recipients(
            g.db, parent_post, v.id, anonymous=bool(c.is_anonymous),
            visible=lambda ids: _followers_who_may_read(parent_post, ids)))
    elif parent.author.id != v.id:
        # queue up notification for the parent comment's author
        notify_users.add(parent.author.id)

    # queue up notifications for username mentions
    soup = BeautifulSoup(body_html, features="html.parser")
    mentions = soup.find_all("a", href=re.compile(r"^/@(\w+)/?$") , limit=3)
    # only people who may see the post are told (as submit_post does with circle_guard.eligible_ids):
    # otherwise the comment is dropped from their inbox and leaves an unread count behind
    eligible = circle_guard.eligible_ids(g.db, parent_post.author_id, parent_post.audience, board_id=parent_post.board_id,
                                         circle_ids=circle_guard.post_circles(g.db, [parent_post.id]).get(parent_post.id, ())
                                         ) if parent_post.audience else None
    for mention in mentions:
        username = mention["href"].split("@")[1]

        user = g.db.query(User).filter_by(username=username).first()

        if user:
            if v.any_block_exists(user):
                continue
            if parent_post.in_private_guild_for(user):
                continue
            if eligible is not None and user.id not in eligible and user.id != parent_post.author_id:
                continue
            if user.id != v.id:
                notify_users.add(user.id)

    for x in notify_users:
        n = Notification(comment_id=c.id,
                         user_id=x)
        g.db.add(n)


    # create auto upvote
    vote = CommentVote(user_id=v.id,
                       comment_id=c.id,
                       vote_type=1
                       )

    g.db.add(vote)

    c.post.score_activity = c.post.rank_activity
    g.db.add(c.post)

    # files from the author's linked storage that this comment shows (helpers/media)
    attached_media = media_attach.sync(g.db, v.id, (body,), comment_id=c.id)

    g.db.commit()
    media_safety.scan_later(attached_media)

    # pages reading this post live (a Space, a live thread) are told a comment came: only its id
    from ruqqus.routes.live_comments import announce_comment
    announce_comment(c)

    c=get_comment(c.id, v=v)


    # print(f"Content Event: @{v.username} comment {c.base36id}")


    return {"html": lambda: jsonify({"html": render_template("comments.html",
                                                             v=v,
                                                             comments=[c],
                                                             render_child_comments=False,
                                                             is_allowed_to_comment=True
                                                             )}),
            "api": lambda: c.json
            }



@app.route("/edit_comment/<cid>", methods=["POST"])
@app.patch("/api/v2/comments/<cid>")
@is_not_banned
@validate_formkey
@api("update")
def edit_comment(cid, v):
    """
Edit your comment.

URL path parameters:
* `cid` - The base 36 id of the comment to edit

Required form data:
* `body` - The new raw comment text

Optional form data (left out: unchanged):
* `paid_partnership` - `true` if the comment is a paid partnership, empty to unmark it.
* `made_with_ai` - `true` if the comment was made with AI, empty to unmark it.
"""

    c = get_comment(cid, v=v)

    if not c.author_id == v.id:
        abort(403)

    if c.is_banned or c.deleted_utc > 0:
        abort(403)

    if c.board.has_ban(v):
        abort(403)

    body = request.form.get("body", "")
    if len(body.strip()) > COMMENT_MAX:
        return jsonify({"error": f"Comments can be {COMMENT_MAX:,} characters at most."}), 400
    body=preprocess(body)
    with CustomRenderer(post_id=c.post.base36id) as renderer:
        body_md = renderer.render(mistletoe.Document(body))
    body_html = sanitize(body_md, linkgen=True)

    # Run safety filter
    bans = filter_comment_html(body_html)

    if bans:
        
        ban = bans[0]
        reason = f"Remove the {ban.domain} link from your comment and try again."

        #auto ban for digitally malicious content
        if any([x.reason==4 for x in bans]):
            v.ban(days=30, reason="Digitally malicious content is not allowed.")
            return jsonify({"error":"Digitally malicious content is not allowed."})
        
        if ban.reason:
            reason += f" {ban.reason_text}"    
          
        return jsonify({"error": reason}), 401
    
        return {'html': lambda: render_template("comment_failed.html",
                                                action=f"/edit_comment/{c.base36id}",
                                                badlinks=[
                                                    x.domain for x in bans],
                                                body=body,
                                                v=v
                                                ),
                'api': lambda: ({'error': f'A blacklisted domain was used.'}, 400)
                }

    apply_comment_severity(c, body_html)

    # check badlinks
    soup = BeautifulSoup(body_html, features="html.parser")
    links = [x['href'] for x in soup.find_all('a') if x.get('href')]

    for link in links:
        parse_link = urlparse(link)
        check_url = ParseResult(scheme="https",
                                netloc=parse_link.netloc,
                                path=parse_link.path,
                                params=parse_link.params,
                                query=parse_link.query,
                                fragment='')
        check_url = urlunparse(check_url)

        badlink = g.db.query(BadLink).filter(
            literal(check_url).contains(
                BadLink.link)).first()

        if badlink:
            return jsonify({"error": f"Remove the following link and try again: `{check_url}`. Reason: {badlink.reason_text}"}), 403

    # check spam - this should hopefully be faster
    now = int(time.time())
    cutoff = now - 60 * 60 * 24

    similar_comments = g.db.query(Comment
                                  ).options(
        lazyload('*')
    ).join(Comment.comment_aux
           ).filter(
        Comment.author_id == v.id,
        CommentAux.body.op(
            '<->')(body) < app.config["SPAM_SIMILARITY_THRESHOLD"],
        Comment.created_utc > cutoff
    ).options(contains_eager(Comment.comment_aux)).all()

    threshold = app.config["SPAM_SIMILAR_COUNT_THRESHOLD"]
    if v.age >= (60 * 60 * 24 * 30):
        threshold *= 4
    elif v.age >= (60 * 60 * 24 * 7):
        threshold *= 3
    elif v.age >= (60 * 60 * 24):
        threshold *= 2

    if len(similar_comments) > threshold:
        text = f"Your {app.config['SITE_NAME']} account has been suspended for 1 day for the following reason:\n\n> Too much spam!"
        send_notification(v, text)

        v.ban(reason="Spamming.",
              include_alts=True,
              days=1)

        for comment in similar_comments:
            comment.is_banned = True
            comment.ban_reason = "Automatic spam removal. This happened because the post's creator submitted too much similar content too quickly."
            g.db.add(comment)

        g.db.commit()
        return jsonify({"error": "Too much spam!"}), 403

    now = int(time.time())

    # edits within 90s of the comment's own creation are treated as part
    # of drafting the original, not a tracked edit - no history row, no
    # edited_utc bump
    if now - c.created_utc > 90:
        g.db.add(ContentEditHistory(
            actor_id=v.id,
            target_comment_id=c.id,
            board_id=c.post.board_id,
            action="edit",
            previous_body=c.body,
            previous_body_html=c.body_html
        ))
        c.edited_utc = now

    c.body = body
    c.body_html = body_html
    c.paid_partnership = flag(request.form, "paid_partnership", c.paid_partnership)
    c.made_with_ai = flag(request.form, "made_with_ai", c.made_with_ai)

    g.db.add(c)

    attached_media = media_attach.sync(g.db, v.id, (body,), comment_id=c.id)

    g.db.commit()
    media_safety.scan_later(attached_media)

    path = request.form.get("current_page", "/")

    return jsonify({"html": c.body_html})


@app.route("/delete/comment/<cid>", methods=["POST"])
@app.route("/api/v1/delete/comment/<cid>", methods=["POST"])
@app.delete("/api/v2/comments/<cid>")
@auth_required
@validate_formkey
@api("delete")
def delete_comment(cid, v):
    """
Delete your comment.

URL path parameters:
* `cid` - The base 36 id of the comment to delete
"""


    c = g.db.query(Comment).filter_by(id=base36decode(cid)).first()

    if not c:
        abort(404)

    if not c.author_id == v.id:
        abort(403)

    # already deleted: a 404, as for posts (a second delete wrote a second public deletion-log entry)
    if c.deleted_utc > 0:
        abort(404)

    now = int(time.time())
    c.deleted_utc = now

    g.db.add(c)
    media_cdn.purge(media_attach.attached_paths(g.db, comment_id=c.id))

    g.db.add(ContentEditHistory(
        actor_id=v.id,
        target_comment_id=c.id,
        board_id=c.post.board_id,
        action="delete",
        previous_body=c.body,
        previous_body_html=c.body_html
    ))

    for forward_copy in c.forwarded_posts:
        if forward_copy.is_deleted:
            continue
        forward_copy.deleted_utc = now
        forward_copy.is_pinned = False
        forward_copy.stickied = False
        g.db.add(forward_copy)
        cache.delete_memoized(Board.idlist, forward_copy.board)
        if forward_copy.age >= 3600 * 6:
            cache.delete_memoized(Board.idlist, forward_copy.board, sort="new")

    cache.delete_memoized(User.commentlisting, v)

    return {"html": lambda: ("", 204),
            "api": lambda: ("", 204)}


@app.route("/comment/<cid>/history", methods=["GET"])
@auth_desired
def comment_history(cid, v):
    """
View the edit/removal history of a comment - every edit, self-delete,
and guild hide/unhide, each showing who, when, and (except for restores)
what the content looked like immediately before that action. If the
comment was later obliterated by an admin, only a metadata-only notice
is shown - no content.
"""

    comment = get_comment(cid, v=v)

    if comment.post and comment.post.in_private_guild_for(v):
        abort(403)
    if comment_hidden(comment, v) or (comment.post and post_hidden(comment.post, v)):
        return hidden_notice(v)["html"]()

    entries = g.db.query(ContentEditHistory).filter_by(
        target_comment_id=comment.id
    ).order_by(ContentEditHistory.id.asc()).all()

    obliteration = g.db.query(ObliterationRecord).filter_by(
        target_comment_id=comment.id
    ).first()

    return render_template(
        "content_history.html",
        v=v,
        target=comment,
        target_type="comment",
        entries=entries,
        obliteration=obliteration,
        show_text=history_text_shown(comment, v)
    )


@app.route("/save_comment/<cid>", methods=["POST"])
@auth_required
@validate_formkey
def save_comment(cid, v):

    comment = get_comment(cid, v=v)

    if comment.post and comment.post.in_private_guild_for(v):
        abort(403)

    existing = g.db.query(CommentSaveRelationship).filter_by(
        user_id=v.id, comment_id=comment.id).first()

    if not existing:
        g.db.add(CommentSaveRelationship(user_id=v.id, comment_id=comment.id, created_utc=int(time.time())))
        try:
            g.db.flush()
        except:
            abort(422)

    return jsonify({"message": "Comment bookmarked."})


@app.route("/unsave_comment/<cid>", methods=["POST"])
@auth_required
@validate_formkey
def unsave_comment(cid, v):

    comment = get_comment(cid)

    existing = g.db.query(CommentSaveRelationship).filter_by(
        user_id=v.id, comment_id=comment.id).first()

    if existing:
        g.db.delete(existing)

    return jsonify({"message": "Bookmark removed."})


@app.route("/comment/<cid>/toggle_sensitive", methods=["POST"])
@auth_required
@validate_formkey
def toggle_comment_sensitive(cid, v):
    """
Toggle "Sensitive Content" status on a comment - an author-discretionary
blur-until-clicked flag for material that isn't a rule violation but
could be upsetting.

URL path parameters:
* `cid` - The base 36 comment id.
"""

    comment = get_comment(cid, v=v)

    mod = comment.board.has_mod(v)

    if not comment.author_id == v.id and not v.admin_level >= 3 and not mod:
        abort(403)

    if comment.board.is_sensitive and comment.is_sensitive:
        abort(403)

    comment.is_sensitive = not comment.is_sensitive
    g.db.add(comment)

    if comment.author_id != v.id:
        ma = ModAction(
            kind="set_sensitive_comment" if comment.is_sensitive else "unset_sensitive_comment",
            user_id=v.id,
            target_comment_id=comment.id,
            board_id=comment.board.id,
            note=None if mod else "admin action"
            )
        g.db.add(ma)

    return "", 204


@app.route("/comment/<cid>/repost", methods=["POST"])
@auth_required
@throttle_check
@validate_formkey
def repost_comment(cid, v):
    """Repost a comment to your own profile, Twitter-retweet style - your
    own comments included, same as retweeting your own tweet to resurface
    it. This creates no independent copy - votes/thread/authorship all
    stay on the shared original comment; this just records that it should
    also appear (with an inline "Repost" tag next to its byline) on the
    reposter's Comments tab."""

    comment = get_comment(cid)

    if comment.is_banned or comment.deleted_utc or comment.purged_utc:
        return {"error": "This comment can't be reposted."}, 400

    if comment.post and comment.post.in_private_guild_for(v):
        abort(403)

    refusal = sharing.comment_refusal(comment, v)        # on a post that isn't public: only its author shares it
    if refusal:
        return {"error": refusal}, 403

    if circle_guard.under_audience(g.db, comment):
        return {"error": "A comment on a post for a Circle can't be reposted."}, 400

    if comment.is_anonymous and comment.author_id == v.id:
        return {"error": "You can't repost your own anonymous comment: it would put it on your profile."}, 400

    existing = g.db.query(CommentRepostRelationship).filter_by(
        user_id=v.id, comment_id=comment.id).first()
    if existing:
        return {"error": "You've already reposted this."}, 409

    g.db.add(CommentRepostRelationship(
        user_id=v.id,
        comment_id=comment.id,
        created_utc=int(time.time())
    ))
    g.db.commit()

    cache.delete_memoized(User.commentlisting, v)

    # the reposter's bell (helpers/bell.py): a comment anyone may open (not under a post only a private guild may read)
    if not (comment.post and comment.post.in_private_guild_for(None)):
        bell_rules.ring_account(v, bell_rules.COMMENT_REPOST, comment.id, f"/comment/{comment.base36id}")

    return jsonify({"message": "Reposted to your profile."})


@app.route("/comment/<cid>/unrepost", methods=["POST"])
@auth_required
@validate_formkey
def unrepost_comment(cid, v):

    comment = get_comment(cid)

    existing = g.db.query(CommentRepostRelationship).filter_by(
        user_id=v.id, comment_id=comment.id).first()

    if existing:
        g.db.delete(existing)
        g.db.commit()
        cache.delete_memoized(User.commentlisting, v)

    return jsonify({"message": "Repost removed."})


@app.route("/comment/<cid>/forward", methods=["POST"])
@auth_required
@throttle_check
@validate_formkey
def forward_comment(cid, v):
    """Forward a comment's text into a brand-new, independent post in a
    guild (its own votes/comment thread), quote-tweet style - usable by
    anyone logged in, whether or not they authored the comment. The new
    post's author stays the comment's original author (they keep delete
    rights); the target guild's own posting rules are checked against the
    person doing the forwarding, same as forwarding a post.

    URL path parameters:
    * `cid` - The base 36 id of the comment to forward

    Required form data:
    * `board` - Name of the guild to forward into

    The new post's title is generated automatically from the comment's own
    text - there is no user-editable title for a forwarded comment's post.
    """
    from .posts import create_forward_post_from_comment

    comment = get_comment(cid)

    if comment.is_banned or comment.deleted_utc or comment.purged_utc:
        return {"error": "This comment can't be forwarded."}, 400

    if comment.post and comment.post.in_private_guild_for(v):
        abort(403)

    refusal = sharing.comment_refusal(comment, v)        # on a post that isn't public: only its author shares it
    if refusal:
        return {"error": refusal}, 403

    if circle_guard.under_audience(g.db, comment):
        return {"error": "A comment on a post for a Circle can't be forwarded to a guild."}, 400

    target = get_guild(request.form.get("board", ""), graceful=True)
    if not target or target.name.lower() == PROFILE_BOARD_NAME:
        return {"error": "That guild doesn't exist."}, 400

    if target.is_banned:
        return {"error": f"+{target.name} has been banned."}, 403

    if target.has_ban(v):
        return {"error": f"Exiled from +{target.name}."}, 403

    if (target.restricted_forwarding or target.is_private) and not target.can_forward(v):
        return {"error": f"+{target.name} only accepts forwards from approved contributors."}, 403

    if target.disallowbots and (request.headers.get("X-User-Type", "").lower() == "bot" or comment.is_bot or v.is_bot):
        return {"error": f"403 Not Authorized - +{target.name} disallows bots from forwarding!"}, 403

    already = g.db.query(CommentForwardRelationship).filter_by(
        comment_id=comment.id, board_id=target.id).first()
    if already:
        return {"error": f"Already forwarded to +{target.name}."}, 409

    # the guild's required flair applies to a forwarded comment as to a forwarded post (audit G-01)
    from ruqqus.helpers import guild_flair as flair_rules, guild_flair_store as flair_store
    raw_flair = request.form.get("flair", "")
    flair_id = flair_rules.parse_id(raw_flair) if raw_flair else None
    if raw_flair and flair_id is None:
        return {"error": "That isn't a flair."}, 400
    flair_refusal = flair_rules.forward_refusal(target.require_flair, flair_id, flair_store.ids_of(g.db, target.id),
                                                target.name)
    if flair_refusal:
        return {"error": flair_refusal}, 409

    new_post = create_forward_post_from_comment(comment, target, v)
    if flair_id:
        flair_store.set_post_flair(g.db, new_post.id, flair_id)
    g.db.commit()

    cache.delete_memoized(frontlist)
    cache.delete_memoized(Board.idlist, target, sort="new")

    # the forwarder's bell (helpers/bell.py): only into an open guild, and never for their own anonymous comment
    if not (target.is_private or target.is_circle) and not (comment.is_anonymous and comment.author_id == v.id):
        bell_rules.ring_account(v, bell_rules.COMMENT_FORWARD, new_post.id, f"/post/{new_post.base36id}", guild_name=target.name)

    return jsonify(new_post.json)


@app.route("/embed/comment/<cid>", methods=["GET"])
@app.route("/embed/post/<pid>/comment/<cid>", methods=["GET"])
@app.route("/api/v1/embed/comment/<cid>", methods=["GET"])
@app.route("/api/v1/embed/post/<pid>/comment/<cid>", methods=["GET"])
def embed_comment_cid(cid, pid=None):

    comment = get_comment(cid)

    if not comment.parent:
        abort(403)

    # an embed is shown to anyone, so it is checked as a visitor
    if comment.post and comment.post.in_private_guild_for(None):
        abort(403)

    if comment.is_banned or comment.deleted_utc > 0:
        if request.path.startswith("/api/"):
            return jsonify({'error': f'Comment {cid} has been removed'}), 410
        return render_template("embeds/comment_removed.html", c=comment, v=None), 410

    if comment.board.is_banned:
        abort(410)

    if post_hidden(comment.post, None) or comment_thread_hidden(comment, None):
        abort(403)

    return render_template("embeds/comment.html", c=comment, v=None)

@app.route("/mod/comment_pin/<guildname>/<cid>", methods=["POST"])
@app.route("/api/v1/comment_pin/<guildname>/<cid>", methods=["POST"])
@app.patch("/api/v2/guilds/<guildname>/comments/<cid>/pin")
@auth_required
@is_guildmaster("content")
@api("guildmaster")
@validate_formkey
def mod_toggle_comment_pin(guildname, cid, board, v):
    """
Toggle pin status on a top-level comment.

URL path parameters:
* `guildname` - The guild in which you are a guildmaster
* `cid` - The base 36 comment id
"""

    comment = get_comment(cid, v=v)

    if comment.post.board_id != board.id:
        abort(400)
        
    #remove previous pin (if exists)
    if not comment.is_pinned:
        previous_sticky = g.db.query(Comment).filter(
            and_(
                Comment.parent_submission == comment.post.id, 
                Comment.is_pinned == True
                )
            ).first()
        if previous_sticky:
            previous_sticky.is_pinned = False
            g.db.add(previous_sticky)

    comment.is_pinned = not comment.is_pinned

    g.db.add(comment)
    ma=ModAction(
        kind="pin_comment" if comment.is_pinned else "unpin_comment",
        user_id=v.id,
        board_id=board.id,
        target_comment_id=comment.id
    )
    g.db.add(ma)

    html=render_template(
                "comments.html",
                v=v,
                comments=[comment],
                render_child_comments=False,
                is_allowed_to_comment=True
                )

    html=str(BeautifulSoup(html, features="html.parser").find(id=f"comment-{comment.base36id}-only"))

    return jsonify({"html":html})


@app.route("/mod/hide_comment/<guildname>/<cid>", methods=["POST"])
@auth_required
@is_guildmaster("content")
@api("guildmaster")
@validate_formkey
def mod_hide_comment(guildname, cid, board, v):
    """
Hide a comment from your guild for breaking its rules. The comment itself is
untouched - it stays visible wherever else its parent post is forwarded;
only your guild's view of it is hidden here, replaced with a notice
showing your reason. Reversible via /mod/unhide_comment.

URL path parameters:
* `guildname` - The guild in which you are a guildmaster
* `cid` - The base 36 comment id

Required form data:
* `reason` - Why this comment breaks +guildname's rules
"""

    comment = get_comment(cid, v=v)

    if comment.post.board_id != board.id:
        abort(400)

    reason = request.form.get("reason", "").strip()
    if not reason:
        return jsonify({"error": "A reason is required."}), 400

    comment.hidden_by_guild = True
    comment.hidden_reason = reason
    g.db.add(comment)

    g.db.add(ContentEditHistory(
        actor_id=v.id,
        target_comment_id=comment.id,
        board_id=board.id,
        action="guild_hide",
        reason=reason,
        previous_body=comment.body,
        previous_body_html=comment.body_html
    ))

    ma=ModAction(
        kind="hide_comment_from_guild",
        user_id=v.id,
        board_id=board.id,
        target_comment_id=comment.id,
        note=html.escape(reason)[:256]          # the log draws notes as HTML (bug hunt C1)
    )
    g.db.add(ma)
    g.db.commit()

    rendered=render_template(
                "comments.html",
                v=v,
                comments=[comment],
                render_child_comments=False,
                is_allowed_to_comment=True
                )

    rendered=str(BeautifulSoup(rendered, features="html.parser").find(id=f"comment-{comment.base36id}-only"))

    return jsonify({"html":rendered})


@app.route("/mod/unhide_comment/<guildname>/<cid>", methods=["POST"])
@auth_required
@is_guildmaster("content")
@api("guildmaster")
@validate_formkey
def mod_unhide_comment(guildname, cid, board, v):
    """
Un-hide a comment that was previously hidden from your guild.

URL path parameters:
* `guildname` - The guild in which you are a guildmaster
* `cid` - The base 36 comment id
"""

    comment = get_comment(cid, v=v)

    if comment.post.board_id != board.id:
        abort(400)

    comment.hidden_by_guild = False
    g.db.add(comment)

    g.db.add(ContentEditHistory(
        actor_id=v.id,
        target_comment_id=comment.id,
        board_id=board.id,
        action="guild_unhide"
    ))

    ma=ModAction(
        kind="unhide_comment_from_guild",
        user_id=v.id,
        board_id=board.id,
        target_comment_id=comment.id
    )
    g.db.add(ma)
    g.db.commit()

    html=render_template(
                "comments.html",
                v=v,
                comments=[comment],
                render_child_comments=False,
                is_allowed_to_comment=True
                )

    html=str(BeautifulSoup(html, features="html.parser").find(id=f"comment-{comment.base36id}-only"))

    return jsonify({"html":html})


@app.route("/author/hide_comment/<cid>", methods=["POST"])
@is_not_banned
@validate_formkey
def author_hide_comment(cid, v):
    """
Remove a comment from your own post's comment thread for breaking your
own rules for it. Only works on your post's own profile copy - comments
on a copy forwarded to a guild are pruned by that guild's guildmasters
instead (see /mod/hide_comment). The comment itself is untouched - just
replaced with a notice showing your reason here. Reversible via
/author/unhide_comment.

URL path parameters:
* `cid` - The base 36 comment id

Required form data:
* `reason` - Why you're removing this comment
"""

    comment = get_comment(cid, v=v)

    if not comment.post.is_profile_post:
        return jsonify({"error": "This comment is on a copy forwarded to a guild - ask that guild's guildmasters to remove it instead."}), 403

    if comment.post.author_id != v.id:
        abort(403)

    reason = request.form.get("reason", "").strip()
    if not reason:
        return jsonify({"error": "A reason is required."}), 400

    comment.author_hidden = True
    comment.author_hidden_reason = reason
    g.db.add(comment)

    g.db.add(ContentEditHistory(
        actor_id=v.id,
        target_comment_id=comment.id,
        action="author_hide",
        reason=reason,
        previous_body=comment.body,
        previous_body_html=comment.body_html
    ))
    g.db.commit()

    html=render_template(
                "comments.html",
                v=v,
                comments=[comment],
                render_child_comments=False,
                is_allowed_to_comment=True
                )

    html=str(BeautifulSoup(html, features="html.parser").find(id=f"comment-{comment.base36id}-only"))

    return jsonify({"html":html})


@app.route("/author/unhide_comment/<cid>", methods=["POST"])
@is_not_banned
@validate_formkey
def author_unhide_comment(cid, v):
    """
Undo a previous /author/hide_comment.

URL path parameters:
* `cid` - The base 36 comment id
"""

    comment = get_comment(cid, v=v)

    if not comment.post.is_profile_post:
        abort(403)

    if comment.post.author_id != v.id:
        abort(403)

    comment.author_hidden = False
    g.db.add(comment)

    g.db.add(ContentEditHistory(
        actor_id=v.id,
        target_comment_id=comment.id,
        action="author_restore"
    ))
    g.db.commit()

    html=render_template(
                "comments.html",
                v=v,
                comments=[comment],
                render_child_comments=False,
                is_allowed_to_comment=True
                )

    html=str(BeautifulSoup(html, features="html.parser").find(id=f"comment-{comment.base36id}-only"))

    return jsonify({"html":html})
