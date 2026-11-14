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
from ruqqus.helpers.visibility import post_hidden, comment_hidden, comment_thread_hidden, hidden_notice, history_open, history_text_shown
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
from ruqqus.helpers.alerts import notify
from ruqqus.helpers.i18n_runtime import N_, msg, tr
from ruqqus.helpers.aws import *
from ruqqus.helpers.word_filter_store import apply_comment_severity
from ruqqus.helpers.languages import detect_language
from ruqqus.helpers.post_fields import flag
from ruqqus.helpers import comment_permission as cperm
from ruqqus.helpers import age_declaration, anonymity
from ruqqus.helpers import action_log, log_notices
from ruqqus.helpers import anonymise, deletion, deletion_store
from ruqqus.classes import *
from flask import *
from ruqqus.helpers import sharing
from ruqqus.helpers import bell as bell_rules
from ruqqus.helpers import text_length
from ruqqus.__main__ import app, limiter, db_session, member_limit_key
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

                'api': lambda: {'error': tr('+%(guild)s is banned.', guild=board.name)}

                }

    # a comment on a deleted post is read in the Log's thread of that post (slice D3a), where the post is Anonymous and the comment is as it was
    if (post.deleted_utc or 0) > 0 and not request.path.startswith("/api/") and not (post.is_space or cperm.is_space(post)):
        return redirect(f"/log/post/{post.base36id}#comment-{comment.base36id}")

    moved = canonical_redirect(comment)
    if moved:
        return moved

    post._preloaded_comments = [comment]

    # context improver
    context = min(whole_arg("context"), 4)
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
@not_quarantined("comment")
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
        return jsonify({"error": tr("Bots can't comment.")}), 403

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
    if text_length.over_limit(body, COMMENT_MAX, stored_max=COMMENT_MAX):
        return jsonify({"error": text_length.limit_message("Comments", COMMENT_MAX)}), 400

    if not body and not (v.has_premium and request.files.get('file')):
        return jsonify({"error": tr("You need to actually write something!")}), 400
    
    body=preprocess(body)
    with CustomRenderer(post_id=parent_id, breaks=True) as renderer:
        body_md = renderer.render(mistletoe.Document(body))
    body_html = sanitize(body_md, linkgen=True)

    # Run safety filter
    bans = filter_comment_html(body_html)

    if bans:
        ban = bans[0]
        if ban.reason:
            reason = tr("Remove the %(domain)s link from your comment and try again. %(reason)s", domain=ban.domain,
                        reason=msg(ban.reason_text))
        else:
            reason = tr("Remove the %(domain)s link from your comment and try again.", domain=ban.domain)

        #auto ban for digitally malicious content; a limited QR sign-in is only refused (helpers/qr_login.py)
        banned = True
        if v.qr_limited:
            banned = False
        elif any([x.reason==7 for x in bans]):
            v.ban(reason=N_("Sexualizing minors"))
        elif any([x.reason==4 for x in bans]):
            v.ban(days=30, reason=N_("Digitally malicious content"))
        elif any([x.reason==9 for x in bans]):
            v.ban(days=7, reason=N_("Engaging in illegal activity"))
        else:
            banned = False

        # a 401 told an API client its sign-in was bad (bug hunt): 403 when the link bans the account, else 400, as
        # editing a comment answers
        return jsonify({"error": reason}), 403 if banned else 400

    # a child-locked account can't comment anonymously, and a parent can switch commenting off for a sub-account (helpers/age_declaration.py)
    locked_error = (age_declaration.content_refusal(v)
                    or age_declaration.post_refusal(v, False, flag(request.form, "anonymous")))
    if locked_error:
        return jsonify({"error": msg(locked_error)}), 403

    # check existing
    existing = g.db.query(Comment).join(CommentAux).filter(Comment.author_id == v.id,
                                                           Comment.deleted_utc == 0,
                                                           Comment.parent_comment_id == parent_comment_id,
                                                           Comment.parent_submission == parent_submission,
                                                           CommentAux.body == body
                                                           ).options(contains_eager(Comment.comment_aux)).first()
    if existing:
        return jsonify({"error": tr("You already made that comment: %(link)s", link=existing.permalink)}), 409

    # No commenting on deleted/removed things
    if parent.is_banned or parent.deleted_utc > 0:
        return jsonify(
            {"error": tr("You can't comment on things that have been deleted.")}), 403

    # an anonymous author is not "blocking" anyone: the refusal would say who they are
    if parent.is_blocking and not parent.is_anonymous and not v.admin_level>=3 and not parent.board.has_mod(v, "content"):
        return jsonify(
            {"error": tr("You can't comment on content from users that you're blocking.")}
            ), 403

    if parent.is_blocked and not parent.is_anonymous and not v.admin_level>=3 and not parent.board.has_mod(v, "content"):
        return jsonify(
            {"error": tr("You can't comment on content from users that are blocking you.")}
            ), 403

    # check for archive and ban state
    post = get_post(parent_id)
    if post.is_archived or not post.board.can_comment(v):

        return jsonify({"error": tr("You can't comment on this.")}), 403

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
            # this would suspend the member and every account linked to them: not on a limited QR sign-in's word (helpers/qr_login.py)
            if v.qr_limited:
                return qr_limited_answer(v)

            notify(v, N_("Your %(site)s account has been suspended for 1 day for the following reason:\n\n> Too much spam!"))

            v.ban(reason=N_("Spamming."),
                  days=1)

            for alt in v.alts:
                if not alt.is_suspended:
                    alt.ban(reason=N_("Spamming."), days=1)

            for comment in similar_comments:
                comment.is_banned = True
                comment.ban_reason = N_("Automatic spam removal. This happened because the post's creator submitted too much "
                                         "similar content too quickly.")
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
            return jsonify({"error": tr("Too much spam!")}), 403

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
            return jsonify({"error": tr("Remove the following link and try again: `%(link)s`. Reason: %(reason)s", link=check_url,
                                       reason=msg(badlink.reason_text))}), 403

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
                return jsonify({"error": tr("That wasn't an image!")}), 400
            
            name = f'comment/{c.base36id}/{secrets.token_urlsafe(8)}'
            upload_file(name, file)

            body = request.form.get("body") + f"\n\n![](https://{BUCKET}/{name})"
            body=preprocess(body)
            with CustomRenderer(post_id=parent_id, breaks=True) as renderer:
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
                    action_log.record_image_check_removal(db, comment_id=banned.id, legal=True,
                                                          board_id=banned.post.board_id if banned.post else None,
                                                          detail="a picture in this comment was taken down by the image check")
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
    c.language_code = detect_language(body)
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


    # drawn as the page draws it after a refresh: at its own depth, with the (empty) container its own comments will go
    # into, so the page can place it under its parent and the member can answer it at once (assets/js/all_js.js post_comment)
    return {"html": lambda: jsonify({"html": render_template("comments.html",
                                                             v=v,
                                                             comments=[c],
                                                             render_child_comments=True,
                                                             start_level=level,
                                                             is_allowed_to_comment=True
                                                             )}),
            "api": lambda: c.json
            }



@app.route("/edit_comment/<cid>", methods=["POST"])
@app.patch("/api/v2/comments/<cid>")
@limiter.limit(deletion.EDITS_PER_HOUR, key_func=member_limit_key)         # per account, as Wikipedia throttles edits rather than merging log lines
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
* `edit_summary` - A few words on what you changed, shown in the comment's history.
* `paid_partnership` - `true` if the comment is a paid partnership, empty to unmark it.
* `made_with_ai` - `true` if the comment was made with AI, empty to unmark it.
"""

    c = get_comment(cid, v=v)

    if not anonymity.is_author(c, v):
        abort(403)

    if c.is_banned or c.deleted_utc > 0:
        abort(403)

    if c.board.has_ban(v):
        abort(403)

    commenting_error = age_declaration.content_refusal(v)      # an edit puts new words in a thread as a comment does; deleting stays open
    if commenting_error:
        return jsonify({"error": msg(commenting_error)}), 403

    body = request.form.get("body", "")
    if text_length.over_limit(body.strip(), COMMENT_MAX, stored_max=COMMENT_MAX):
        return jsonify({"error": text_length.limit_message("Comments", COMMENT_MAX)}), 400
    body=preprocess(body)
    with CustomRenderer(post_id=c.post.base36id, breaks=True) as renderer:
        body_md = renderer.render(mistletoe.Document(body))
    body_html = sanitize(body_md, linkgen=True)

    # Run safety filter
    bans = filter_comment_html(body_html)

    if bans:
        
        ban = bans[0]

        #auto ban for digitally malicious content
        # (bug hunt L2: this answered 200, which the page took for a saved edit: the comment read "undefined")
        if any([x.reason==4 for x in bans]) and not v.qr_limited:
            v.ban(days=30, reason=N_("Digitally malicious content is not allowed."))
            return jsonify({"error": tr("Digitally malicious content is not allowed.")}), 403
        
        if ban.reason:
            reason = tr("Remove the %(domain)s link from your comment and try again. %(reason)s", domain=ban.domain,
                        reason=msg(ban.reason_text))
        else:
            reason = tr("Remove the %(domain)s link from your comment and try again.", domain=ban.domain)

        return jsonify({"error": reason}), 400

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
            return jsonify({"error": tr("Remove the following link and try again: `%(link)s`. Reason: %(reason)s", link=check_url,
                                       reason=msg(badlink.reason_text))}), 403

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
        if v.qr_limited:
            return qr_limited_answer(v)

        notify(v, N_("Your %(site)s account has been suspended for 1 day for the following reason:\n\n> Too much spam!"))

        v.ban(reason=N_("Spamming."),
              days=1)

        # (this call used to pass include_alts=True, which User.ban has never accepted: a TypeError on this path)
        for alt in v.alts:
            if not alt.is_suspended:
                alt.ban(reason=N_("Spamming."), days=1)

        for comment in similar_comments:
            comment.is_banned = True
            comment.ban_reason = N_("Automatic spam removal. This happened because the post's creator submitted too much "
                                     "similar content too quickly.")
            g.db.add(comment)
            g.db.add(ModAction(
                user_id=1,
                target_comment_id=comment.id,
                kind="ban_comment",
                board_id=comment.post.board_id,
                note="spam"
                ))

        g.db.commit()
        return jsonify({"error": tr("Too much spam!")}), 403

    now = int(time.time())

    # edits within the grace window of the comment's own creation (five minutes, `deletion.GRACE_SECONDS`) are treated as part of drafting the original,
    # not a tracked edit - no history row, no Log entry, no edited_utc bump
    if now - c.created_utc > deletion.GRACE_SECONDS:
        action_log.record_quietly(
            g.db, "edit_comment", v.id, role=action_log.AUTHOR, board_id=c.post.board_id, comment_id=c.id,
            reason=action_log.clean_reason(request.form.get("edit_summary"), 256),
            floor=deletion.entry_floor(circle=bool(c.post.audience) or bool(getattr(c.post.board, "is_circle", False))), now=now)
        g.db.add(ContentEditHistory(
            actor_id=v.id,
            target_comment_id=c.id,
            board_id=c.post.board_id,
            action="edit",
            summary=action_log.clean_reason(request.form.get("edit_summary"), 256) or None,
            previous_body=c.body,
            previous_body_html=c.body_html
        ))
        c.edited_utc = now

    # stamped only once the edit is accepted: a refused edit (a bad link, the spam check, which commits) must leave
    # the comment's rating and language as they were
    apply_comment_severity(c, body_html)
    c.language_code = detect_language(body, keep=c.language_code)
    c.body = body
    c.body_html = body_html
    c.paid_partnership = flag(request.form, "paid_partnership", c.paid_partnership)
    c.made_with_ai = flag(request.form, "made_with_ai", c.made_with_ai)

    g.db.add(c)

    attached_media = media_attach.sync(g.db, v.id, (body,), comment_id=c.id)

    g.db.commit()
    media_safety.scan_later(attached_media)

    path = request.form.get("current_page", "/")

    # the new text's language, and whether Translate still applies to it (all_js.js comment_edit)
    from ruqqus.routes.translation_pages import translate_offered
    return jsonify({"html": c.body_html, "language_code": c.language_code, "translate": translate_offered(c, v)})


@app.route("/delete/comment/<cid>", methods=["POST"])
@app.route("/api/v1/delete/comment/<cid>", methods=["POST"])
@app.delete("/api/v2/comments/<cid>")
@auth_required
@validate_formkey
@api("delete")
def delete_comment(cid, v):
    """
Delete your comment, or (on the site only) take your name off it.

What you delete leaves the site, and what it said stays in the public Log as Anonymous, like every contribution on Wikipedia. A
comment less than five minutes old is erased with no public trace instead, unless someone else has commented on it. A comment
on a post that was not public keeps its copy with that post's own audience only.

URL path parameters:
* `cid` - The base 36 id of the comment to delete

Form field (the site only; an API app always deletes):
* `how` - `delete` (the default) or `unlink`: keep the comment, remove your name from it, for good.
"""


    c = g.db.query(Comment).filter_by(id=base36decode(cid)).first()

    if not c:
        abort(404)

    if not anonymity.is_author(c, v):
        abort(403)

    # already deleted: a 404, as for posts (a second delete wrote a second public deletion-log entry)
    if c.deleted_utc > 0:
        abort(404)

    if deletion.parse_how(request.form.get("how")) == deletion.UNLINK:
        if request.path.startswith(("/api/v1", "/api/v2")):
            return jsonify({"error": msg(UNLINK_SITE_ONLY)}), 400
        return unlink_comment(c, v)

    now = int(time.time())
    author = c.author
    post = c.post
    board = post.board
    child = age_declaration.is_locked(author)
    what = deletion.plan(created_utc=c.created_utc, now=now, erased_today=deletion_store.erased_today(g.db, c.author_id, now),
                         others_replied=deletion_store.others_replied_to_comment(g.db, c.id, c.author_id), child=child)
    if what != deletion.ERASE and getattr(v, "qr_limited", False):
        return qr_limited_answer(v)          # it publishes, for good: only a full sign-in does that

    circle = bool(post.audience) or bool(getattr(board, "is_circle", False))
    published = deletion.text_published(child=child, removed=bool(c.is_banned), hidden_by_guild=bool(c.hidden_by_guild or c.author_hidden),
                                        board_banned=bool(board.is_banned))

    c.deleted_utc = now

    g.db.add(c)
    media_cdn.purge(media_attach.attached_paths(g.db, comment_id=c.id))

    g.db.add(ContentEditHistory(
        actor_id=v.id,
        target_comment_id=c.id,
        board_id=post.board_id,
        action="delete"
    ))
    if what == deletion.ERASE:
        # a private copy for its author and the admins only: it carries the guild's board id, but a guild's guildmasters have
        # no business reading what an author deleted (kind GRACE, the audit's H2)
        action_log.keep_snapshot(g.db, action_log.snapshot_of(c, post.board_id, comment=True, kind=action_log.GRACE))
    else:
        keep = None
        if what == deletion.LINE:
            keep = action_log.snapshot_of(c, post.board_id, comment=True, kind=action_log.GRACE)
        elif published:
            keep = action_log.snapshot_of(c, post.board_id, comment=True, kind=action_log.DELETE, public=True,
                                          sensitive=bool(post.is_sensitive or board.is_sensitive))
        entry = action_log.record_quietly(g.db, "author_delete_comment", v.id, role=action_log.AUTHOR, board_id=post.board_id, comment_id=c.id,
                                          floor=deletion.entry_floor(circle=circle), snapshot=keep, now=now)
        if entry is None and keep is not None:
            action_log.keep_snapshot(g.db, action_log.snapshot_of(c, post.board_id, comment=True, kind=action_log.GRACE))
        anonymise.anonymise_comment(g.db, c)

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


UNLINK_SITE_ONLY = N_("Removing your name is done on the site itself.")


def unlink_comment(c, v):
    """Remove the author's name from a comment and the posts it was forwarded as, for good ("Remove my name"). Refused for what needs the name (a
    comment on a Circle post, a tipped one, a bot's); the dialog doesn't offer it there, and this says why if a request comes anyway."""
    if getattr(v, "qr_limited", False):
        return qr_limited_answer(v)
    post = c.post
    refusal = deletion.unlink_refusal(
        circle=bool(post.audience) or bool(getattr(post.board, "is_circle", False)), tipped=deletion_store.comment_tipped(g.db, c.id),
        shared=False, space=bool(post.is_space), already=bool(c.is_anonymous), deleted=False, bot=bool(c.is_bot))
    if refusal:
        return jsonify({"error": msg(refusal)}), 409
    anonymise.anonymise_comment(g.db, c)
    return "", 204


@app.route("/comment/<cid>/history", methods=["GET"])
@auth_desired
def comment_history(cid, v):
    """
View the history of a comment - every edit (with the version edited away and the note
its editor gave), self-delete and guild hide/unhide, each showing who and when. The
text of a delete or a hide is not here: it is a private copy for the comment's author,
the guild's guildmasters and the admins (`copy_url`). If the comment was later
obliterated by an admin, only a metadata-only notice is shown - no content.
"""

    comment = get_comment(cid, v=v)

    if comment.post and comment.post.in_private_guild_for(v):
        abort(403)
    if comment_hidden(comment, v) or (comment.post and post_hidden(comment.post, v)):
        return hidden_notice(v)["html"]()
    if not history_open(comment, v):
        abort(404)

    entries = g.db.query(ContentEditHistory).filter_by(
        target_comment_id=comment.id
    ).order_by(ContentEditHistory.id.asc()).all()

    obliteration = g.db.query(ObliterationRecord).filter_by(
        target_comment_id=comment.id
    ).first()

    # only for a signed-in reader who may open one (see post_history: with no viewer snapshot_for answers the newest copy of all)
    copy_url = (f"/comment/{comment.base36id}/copy"
                if v is not None and action_log.snapshot_for(g.db, comment_id=comment.id, viewer=v) else None)

    return render_template(
        "content_history.html",
        v=v,
        target=comment,
        target_type="comment",
        entries=entries,
        obliteration=obliteration,
        copy_url=copy_url,
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

    return jsonify({"message": tr("Comment bookmarked.")})


@app.route("/unsave_comment/<cid>", methods=["POST"])
@auth_required
@validate_formkey
def unsave_comment(cid, v):

    comment = get_comment(cid)

    existing = g.db.query(CommentSaveRelationship).filter_by(
        user_id=v.id, comment_id=comment.id).first()

    if existing:
        g.db.delete(existing)

    return jsonify({"message": tr("Bookmark removed.")})


@app.route("/comment/<cid>/toggle_sensitive", methods=["POST"])
@auth_required
@validate_formkey
def toggle_comment_sensitive(cid, v):
    """
Toggle "Sensitive Content" status on a comment - a blur-until-clicked flag
for material that isn't a rule violation but is sexual or could be upsetting.

URL path parameters:
* `cid` - The base 36 comment id.
"""

    comment = get_comment(cid, v=v)

    mod = comment.board.has_mod(v)

    if not anonymity.is_author(comment, v) and not v.admin_level >= 3 and not mod:
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
@not_quarantined("repost")
@validate_formkey
def repost_comment(cid, v):
    """Repost a comment to your own profile, Twitter-retweet style - your
    own comments included, same as retweeting your own tweet to resurface
    it. This creates no independent copy - votes/thread/authorship all
    stay on the shared original comment; this just records that it should
    also appear (with an inline "Repost" tag next to its byline) on the
    reposter's Comments tab."""

    posting_error = age_declaration.content_refusal(v)      # a parent can switch posting off for a sub-account: that covers sharing too
    if posting_error:
        return {"error": msg(posting_error)}, 403
    comment = get_comment(cid)

    if comment.is_banned or comment.deleted_utc or comment.purged_utc:
        return {"error": tr("This comment can't be reposted.")}, 400

    if comment.post and comment.post.in_private_guild_for(v):
        abort(403)

    refusal = sharing.comment_refusal(comment, v)        # on a post that isn't public: only its author shares it
    if refusal:
        return {"error": refusal}, 403

    if circle_guard.under_audience(g.db, comment):
        return {"error": tr("A comment on a post for a Circle can't be reposted.")}, 400

    # the refusal below would tell a limited QR sign-in whose it is, and a repost of their own would put it on the profile
    if comment.is_anonymous and v.qr_limited:
        return qr_limited_answer(v)

    if comment.is_anonymous and comment.author_id == v.id:
        return {"error": tr("You can't repost your own anonymous comment: it would put it on your profile.")}, 400

    existing = g.db.query(CommentRepostRelationship).filter_by(
        user_id=v.id, comment_id=comment.id).first()
    if existing:
        return {"error": tr("You've already reposted this.")}, 409

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

    return jsonify({"message": tr("Reposted to your profile.")})


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

    return jsonify({"message": tr("Repost removed.")})


@app.route("/comment/<cid>/forward", methods=["POST"])
@auth_required
@throttle_check
@not_quarantined("forward")
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
    posting_error = age_declaration.content_refusal(v)      # a parent can switch posting off for a sub-account: that covers sharing too
    if posting_error:
        return {"error": msg(posting_error)}, 403
    from .posts import create_forward_post_from_comment

    comment = get_comment(cid)

    if comment.is_banned or comment.deleted_utc or comment.purged_utc:
        return {"error": tr("This comment can't be forwarded.")}, 400

    if comment.post and comment.post.in_private_guild_for(v):
        abort(403)

    refusal = sharing.comment_refusal(comment, v)        # on a post that isn't public: only its author shares it
    if refusal:
        return {"error": refusal}, 403

    if circle_guard.under_audience(g.db, comment):
        return {"error": tr("A comment on a post for a Circle can't be forwarded to a guild.")}, 400

    target = get_guild(request.form.get("board", ""), graceful=True)
    if not target or target.name.lower() == PROFILE_BOARD_NAME:
        return {"error": tr("That guild doesn't exist.")}, 400

    if target.is_banned:
        return {"error": tr("+%(guild)s has been banned.", guild=target.name)}, 403

    if target.has_ban(v):
        return {"error": tr("Exiled from +%(guild)s.", guild=target.name)}, 403

    if (target.restricted_forwarding or target.is_private) and not target.can_forward(v):
        return {"error": tr("+%(guild)s only accepts forwards from approved contributors.", guild=target.name)}, 403

    if target.disallowbots and (request.headers.get("X-User-Type", "").lower() == "bot" or comment.is_bot or v.is_bot):
        return {"error": tr("403 Not Authorized - +%(guild)s disallows bots from forwarding!", guild=target.name)}, 403

    already = g.db.query(CommentForwardRelationship).filter_by(
        comment_id=comment.id, board_id=target.id).first()
    if already:
        return {"error": tr("Already forwarded to +%(guild)s.", guild=target.name)}, 409

    # the guild's required flair applies to a forwarded comment as to a forwarded post (audit G-01)
    from ruqqus.helpers import guild_flair as flair_rules, guild_flair_store as flair_store
    raw_flair = request.form.get("flair", "")
    flair_id = flair_rules.parse_id(raw_flair) if raw_flair else None
    if raw_flair and flair_id is None:
        return {"error": tr("That isn't a flair.")}, 400
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
            return jsonify({'error': tr('Comment %(id)s has been removed', id=cid)}), 410
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
        return jsonify({"error": tr("A reason is required.")}), 400

    comment.hidden_by_guild = True
    comment.hidden_reason = reason
    g.db.add(comment)

    g.db.add(ContentEditHistory(
        actor_id=v.id,
        target_comment_id=comment.id,
        board_id=board.id,
        action="guild_hide",
        reason=reason
    ))

    ma=ModAction(
        kind="hide_comment_from_guild",
        user_id=v.id,
        board_id=board.id,
        target_comment_id=comment.id,
        note=html.escape(reason)[:256]          # the log draws notes as HTML (bug hunt C1)
    )
    g.db.add(ma)
    g.db.flush()
    # the rule cited, and the text as it was: a private copy for the guild's guildmasters, the author and the admins (not in the entry)
    entry = action_log.annotate(g.db, ma.id, rule=request.form.get("rule"),
                                snapshot=action_log.snapshot_of(comment, board.id, comment=True, kind=action_log.HIDE))
    g.db.commit()
    # the person the decision is about is told (best effort, after the commit): rule, reason, the Log, the way to appeal
    log_notices.tell_decision(g.db, entry, "hide_comment_from_guild", comment=comment, guild=board.name, reason=reason, actor_id=v.id,
                              rule_key=action_log.parse_rule(request.form.get("rule")))

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
        return jsonify({"error": tr("This comment is on a copy forwarded to a guild - ask that guild's guildmasters to remove it "
                                       "instead.")}), 403

    if not anonymity.is_author(comment.post, v):
        abort(403)

    reason = request.form.get("reason", "").strip()
    if not reason:
        return jsonify({"error": tr("A reason is required.")}), 400

    comment.author_hidden = True
    comment.author_hidden_reason = reason
    g.db.add(comment)

    g.db.add(ContentEditHistory(
        actor_id=v.id,
        target_comment_id=comment.id,
        action="author_hide",
        reason=reason
    ))
    # a post's author hid it: the comment's author, the hider and the admins may open the copy, not the guild's guildmasters
    snapshot = action_log.snapshot_of(comment, comment.post.board_id, comment=True, kind=action_log.AUTHOR_HIDE, hider_id=v.id)
    # the Log: a decision too (the paper trail of the audit: it had a history row and a private copy, no entry). It names the role, never
    # the account: the post may be anonymous. The copy is tied to the entry, so who may open it is still `snapshot_visible`'s rule
    entry = action_log.record_quietly(g.db, "author_hide_comment", v.id, role=action_log.AUTHOR, board_id=comment.post.board_id,
                                      comment_id=comment.id, reason=reason, snapshot=snapshot)
    if entry is None:
        action_log.keep_snapshot(g.db, snapshot)                      # the entry failed: the private copy is still kept
    g.db.commit()
    if entry:
        log_notices.tell_decision(g.db, entry, "author_hide_comment", comment=comment, reason=reason, actor_id=v.id)

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

    if not anonymity.is_author(comment.post, v):
        abort(403)

    comment.author_hidden = False
    g.db.add(comment)

    g.db.add(ContentEditHistory(
        actor_id=v.id,
        target_comment_id=comment.id,
        action="author_restore"
    ))
    action_log.record_quietly(g.db, "author_unhide_comment", v.id, role=action_log.AUTHOR, board_id=comment.post.board_id,
                              comment_id=comment.id)
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
