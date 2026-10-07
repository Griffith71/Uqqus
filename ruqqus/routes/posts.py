from urllib.parse import urlparse, ParseResult, urlunparse, urlencode
from sqlalchemy import func
from sqlalchemy.orm import aliased
from bs4 import BeautifulSoup
import requests
import re
import bleach
import time
import gevent
import mistletoe

from ruqqus.helpers.wrappers import *
from ruqqus.helpers.visibility import post_hidden, hidden_notice
from ruqqus.helpers.base36 import *
from ruqqus.helpers.sanitize import *
from ruqqus.helpers.filters import *
from ruqqus.helpers.embed import *
from ruqqus.helpers.markdown import *
from ruqqus.helpers.get import *
from ruqqus.helpers.thumbs import *
from ruqqus.helpers.session_helpers import *
from ruqqus.helpers.aws import *
from ruqqus.helpers.alerts import send_notification
from ruqqus.helpers.text import split_title_body
from ruqqus.helpers.languages import detect_language
from ruqqus.helpers.post_fields import clean_title, check_body, normalize_url, flag, PostFieldError
from ruqqus.helpers import comment_permission as cperm
from ruqqus.helpers import post_drafts
from ruqqus.helpers.media import attach as media_attach, cdn as media_cdn, safety as media_safety
from ruqqus.helpers.word_filter_store import post_severity, apply_post_severity
from ruqqus.classes import *
from ruqqus.helpers import coauthors       # after the star-import: ruqqus.classes has a module of this name too
from .front import frontlist
from ruqqus.__main__ import app, limiter, cache, db_session
from flask import session as flask_session


BAN_REASONS = ['',  #placeholder
               "URL shorteners are not permitted.",
               "",  # placeholder, formerly "no porn"
               "Copyright infringement is not permitted.",
               "Digitally malicious content is not permitted.",
               "Spam",
               "No doxxing",
               "Sexualizing minors",
               'User safety - This site is a Ruqqus clone which is still displaying "Ruqqus" as its site name.',
               "Engaging in or planning unlawful activity"
               ]

BUCKET = app.config.get("S3_BUCKET", "i.ruqqus.com")


@app.route("/post_short/", methods=["GET"])
@app.route("/post_short/<base36id>", methods=["GET"])
@app.route("/post_short/<base36id>/", methods=["GET"])
def incoming_post_shortlink(base36id=None):

    if not base36id:
        return redirect('/')

    if base36id == "robots.txt":
        return redirect('/robots.txt')

    try:
        x=base36decode(base36id)
    except:
        abort(400)

    post = get_post(base36id)
    return redirect(post.permalink)

@app.get("/+<boardname>/post/<pid>")
def post_redirect(boardname, pid):
    return redirect(get_post(pid).permalink)

@app.get("/api/v2/submissions/<pid>")
@auth_desired
@api("read")
def post_base36id_no_comments(pid,v=None):
    """
Get a single submission (without comments).

URL path parameters:
* `pid` - The base 36 post id
"""
    
    post = get_post(
        pid,
        v=v
        )
    if post_hidden(post, v):
        return hidden_notice(v)
    board = post.board

    if board.is_banned and not (v and v.admin_level > 3):
        return render_template("board_banned.html",
                               v=v,
                               b=board,
                               p=True)

    return {
        "html":lambda:post.rendered_page(v=v),
        "api":lambda:jsonify(post.json)
        }


@app.route("/+<boardname>/post/<pid>/", methods=["GET"])
@app.route("/+<boardname>/post/<pid>/<anything>", methods=["GET"])
@app.route("/api/v1/post/<pid>", methods=["GET"])
@app.get("/api/v2/submissions/<pid>/comments")
@auth_desired
@api("read")
def post_base36id_with_comments(pid, boardname=None, anything=None, v=None):
    """
Get the comment tree for a submission.

URL path parameters:
* `pid` - The base 36 post id

Optional query parameters:
* `sort` - Comment sort order. One of `hot`, `new`, `top`, `disputed`, `old`. Default `hot`.
"""
    
    post = get_post_with_comments(
        pid, v=v, sort_type=request.args.get(
            "sort", "top"))

    if post_hidden(post, v):
        return hidden_notice(v)

    board = post.board
    #if the guild name is incorrect, fix the link and redirect

    if boardname and not boardname == board.name:
        return redirect(post.permalink)

    if board.is_banned and not (v and v.admin_level > 3):
        return render_template("board_banned.html",
                               v=v,
                               b=board,
                               p=True)

    post.tree_comments(v=v)

    if v:
        record_view(v, post)

    return {
        "html":lambda:post.rendered_page(v=v),
        "api":lambda:jsonify({"data":[x.json for x in post.nested_comments]})
        }

#profile-primary posts render here directly (no guild in the URL to redirect
#into); anything else redirects to add the guild name into the url
@app.route("/post/<base36id>", methods=["GET"])
@app.route("/post/<base36id>/", methods=["GET"])
@app.route("/post/<base36id>/<anything>", methods=["GET"])
@auth_desired
@api("read")
def post_base36id_noboard(base36id, anything=None, v=None):

    post=get_post_with_comments(base36id, v=v, sort_type=request.args.get("sort","top"))

    if not post.is_profile_post:
        return redirect(post.permalink)

    if post_hidden(post, v):
        return hidden_notice(v)

    post.tree_comments(v=v)

    if v:
        record_view(v, post)

    return {
        "html":lambda:post.rendered_page(v=v),
        "api":lambda:jsonify({"data":[x.json for x in post.nested_comments]})
        }



@app.route("/submit", methods=["GET"])
@is_not_banned
@no_negative_balance("html")
def submit_get(v):

    board = request.args.get("guild")
    b = get_guild(board, graceful=True) if board else None

    # ?draft=<id> reopens a saved draft or scheduled post of your own
    draft = None
    raw_draft = request.args.get("draft", "")
    if raw_draft.isdigit():
        draft = g.db.query(PostDraft).filter(
            PostDraft.id == int(raw_draft),
            PostDraft.user_id == v.id,
            PostDraft.status.in_(post_drafts.EDITABLE)).first()

    if draft:
        return render_template("submit.html",
                               v=v,
                               b=b,
                               draft=draft,
                               title=draft.title,
                               url=draft.url,
                               body=draft.body,
                               forward_guild_names=draft.forward_guild_list
                               )

    return render_template("submit.html",
                           v=v,
                           b=b,
                           forward_guild_names=[]
                           )


def _discard_draft(v, raw_id):
    """A post made from a saved draft uses it up: delete it (unless the
    scheduler is publishing it at this very moment)."""
    if not (raw_id or "").strip().isdigit():
        return
    draft = g.db.query(PostDraft).filter_by(id=int(raw_id), user_id=v.id).first()
    if draft and draft.status in post_drafts.EDITABLE:
        g.db.delete(draft)


def _own_image_key(post):
    """Storage key of the image this post uploaded to our bucket, or None
    (no link, or a link to something hosted elsewhere)."""
    bucket_root = f"https://{BUCKET}/"
    if post.url and post.url.startswith(f"{bucket_root}post/{post.base36id}/"):
        return post.url[len(bucket_root):]
    return None


def _edit_error(message, code=400):
    return jsonify({"error": message}), code


@app.route("/edit_post/<pid>", methods=["POST"])
@app.patch("/api/v2/submissions/<pid>")
@is_not_banned
@no_negative_balance("html")
@api("update")
@validate_formkey
def edit_post(pid, v):
    """
Edit your post. Every field is optional - anything you leave out stays as
it is. A post that has been forwarded to guilds is one post: editing it
updates every forwarded copy, and editing from a forwarded copy edits the
original.

URL path parameters:
* `pid` - The base 36 id of the post to edit

Optional form data:
* `title` - The new title. 280 character limit.
* `body` - The new raw text body. 25000 character limit.
* `url` - The new link. Send it empty to remove the link.
* `sensitive` - `true` to mark the post sensitive, empty to unmark it.
* `comment_permission` - Who can comment: `0` everyone, `1` accounts you follow,
  `2` Premium accounts. Only applies to the post on your profile (forwarded
  copies follow their guild's rules).
* `paid_partnership` - `true` if the post is a paid partnership, empty to unmark it.
* `made_with_ai` - `true` if the post was made with AI, empty to unmark it.
* `remove_image` - `true` to remove the post's uploaded image.

Optional file data:
* `file` - An image to replace the post's link/image with.
"""

    p = get_post(pid)

    if not p.author_id == v.id:
        abort(403)

    if p.is_banned:
        abort(403)

    if p.board.has_ban(v):
        abort(403)

    # A forwarded copy has no content of its own: edit the original, which
    # carries the change to every copy (including the one being looked at).
    primary = p.reposts if (p.is_forward_copy and p.reposts) else p
    if primary.author_id != v.id or primary.is_banned:
        abort(403)

    form = request.form
    old_url = primary.url or ""
    old_body = primary.body or ""

    try:
        title = clean_title(form["title"]) if "title" in form else primary.title
        url = normalize_url(form["url"]) if "url" in form else old_url
        body = preprocess(check_body(form["body"])) if "body" in form else old_body
    except PostFieldError as e:
        return _edit_error(str(e))

    sensitive = any(form.getlist("sensitive")) if "sensitive" in form else bool(primary.is_sensitive)

    paid_partnership = flag(form, "paid_partnership", primary.paid_partnership)
    made_with_ai = flag(form, "made_with_ai", primary.made_with_ai)

    comment_permission = cperm.mode_of(primary)
    if "comment_permission" in form:
        comment_permission = cperm.parse(form["comment_permission"])
        if comment_permission is None:
            return _edit_error("Choose who can comment.")

    # image: replace it with an upload, or remove it
    old_image_key = _own_image_key(primary)
    upload = request.files.get("file")
    if not (upload and upload.filename):
        upload = None

    if upload:
        if not v.can_submit_image:
            abort(403)
        if (request.content_length or 0) > 16 * 1024 * 1024 and not v.has_premium:
            abort(413)
        if not (upload.content_type or "").startswith("image/"):
            return _edit_error("Image files only.")
    elif (form.get("remove_image") and url == old_url
          and (old_image_key or media_attach.is_media_url(old_url, app.config["SERVER_NAME"]))):
        url = ""

    body_changed = body != old_body
    url_changed = bool(upload) or url != old_url

    # a link that would name the author of a post that does not (helpers/media/attach.py)
    link_refusal = media_attach.link_refusal_for(g.db, primary, url) if url_changed else None
    if link_refusal:
        return _edit_error(link_refusal)

    if body_changed:
        with CustomRenderer() as renderer:
            body_md = renderer.render(mistletoe.Document(body))
        body_html = sanitize(body_md, linkgen=True)

        # Run safety filter
        bans = filter_comment_html(body_html)
        if bans:
            ban = bans[0]
            reason = f"Remove the {ban.domain} link from your post and try again."
            if ban.reason:
                reason += f" {ban.reason_text}"

            #auto ban for digitally malicious content
            if any([x.reason==4 for x in bans]):
                v.ban(days=30, reason="Digitally malicious content is not allowed.")
                abort(403)

            return _edit_error(reason, 403)
    else:
        body_html = primary.body_html

    # the same link checks a new post gets
    domain_obj = None
    embed = None
    if url_changed and url and not upload:
        domain_obj = get_domain(urlparse(url).netloc)
        if domain_obj and not domain_obj.can_submit:
            reason = BAN_REASONS[domain_obj.reason] if 0 < (domain_obj.reason or 0) < len(BAN_REASONS) else ""
            return _edit_error(reason or "Links to that site aren't allowed.", 403)
        if domain_obj and domain_obj.embed_function:
            try:
                embed = eval(domain_obj.embed_function)(url)
            except BaseException:
                embed = None

    # check spam
    links = []
    if body_changed:
        soup = BeautifulSoup(body_html, features="html.parser")
        links = [x['href'] for x in soup.find_all('a') if x.get('href')]
    if url_changed and url and not upload:
        links = [url] + links

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
            if badlink.autoban:
                text = "Your Ruqqus account has been suspended for 1 day for the following reason:\n\n> Too much spam!"
                send_notification(v, text)
                v.ban(days=1, reason="spam")

                return redirect('/notifications')
            else:

                return _edit_error(f"The link `{badlink.link}` is not allowed. Reason: {badlink.reason}", 403)

    if primary.is_profile_post and comment_permission != cperm.mode_of(primary):
        primary.comment_permission = comment_permission
        g.db.add(primary)

    unchanged = (title == primary.title and not body_changed and not url_changed
                 and sensitive == bool(primary.is_sensitive)
                 and paid_partnership == bool(primary.paid_partnership)
                 and made_with_ai == bool(primary.made_with_ai))

    if not unchanged:

        if upload:
            image_name = f'post/{primary.base36id}/{secrets.token_urlsafe(8)}'
            upload_file(image_name, upload)
            url = f'https://{BUCKET}/{image_name}'

        word_severity, word_filter_version = post_severity(title, body_html)

        language_code = detect_language(title, body)

        now = int(time.time())

        # edits within 90s of the post's own creation are treated as part of
        # drafting the original, not a tracked edit - no history row, no
        # edited_utc bump
        tracked = now - primary.created_utc > 90

        # the original plus every live forwarded copy; a copy in a guild
        # that has since exiled the author is left as it was
        rows = [primary] + [f for f in primary.forwards
                            if not f.is_banned and not f.is_deleted and not f.board.has_ban(v)]

        for row in rows:
            if tracked:
                g.db.add(ContentEditHistory(
                    actor_id=v.id,
                    target_submission_id=row.id,
                    board_id=row.board_id,
                    action="edit",
                    previous_title=row.title,
                    previous_url=(row.url or "")[:500] or None,
                    previous_body=row.body,
                    previous_body_html=row.body_html
                ))
                row.edited_utc = now

            row.title = title
            row.body = body
            row.body_html = body_html

            if url_changed:
                aux = row.submission_aux
                aux.url = url
                # copies read the original's embed/preview (see
                # Submission.embed_url), so only the original carries one
                aux.embed_url = (embed or None) if row is primary else None
                aux.embed_type = None
                aux.meta_title = None
                aux.meta_description = None
                aux.preview_image_url = None
                g.db.add(aux)
                row.domain_ref = 1 if upload else (domain_obj.id if domain_obj else None)
                row.is_image = bool(upload) and row is primary
                row.has_thumb = False

            row.word_severity = word_severity
            row.word_filter_version = word_filter_version
            row.is_offensive = word_severity >= 2
            row.language_code = language_code
            # same rule as forwarding: a sensitive guild keeps its copy marked
            row.is_sensitive = sensitive or (row is not primary and row.board.is_sensitive)
            row.paid_partnership = paid_partnership
            row.made_with_ai = made_with_ai
            g.db.add(row)

        # files from the author's linked storage: attach the ones the post shows now,
        # let go of the ones it no longer does (helpers/media)
        attached_media = media_attach.sync(g.db, v.id, (url, body), submission_id=primary.id)

        # the image scan and the thumbnail/embed worker read from their own
        # sessions, so the edit has to be committed before they start
        g.db.commit()
        media_safety.scan_later(attached_media)

        if url_changed:
            if old_image_key:
                # the edit is already saved: a storage problem must not turn it
                # into an error for the author (worst case the old file lingers)
                try:
                    delete_file(old_image_key)
                except Exception as e:
                    app.logger.warning(f"edit_post: could not delete replaced image {old_image_key}: {e}")

            if upload:
                row_ids = [row.id for row in rows]

                def del_function():
                    db = db_session()
                    delete_file(image_name)
                    for row_id in row_ids:
                        banned = db.query(Submission).filter_by(id=row_id).first()
                        if banned:
                            banned.is_banned = True
                            db.add(banned)
                    db.add(ModAction(
                        kind="ban_post",
                        user_id=1,
                        note="banned image",
                        target_submission_id=row_ids[0]
                    ))
                    db.commit()
                    db.close()

                gevent.spawn(check_csam_url, url, v, del_function)

            if url:
                gevent.spawn(thumbnail_thread, primary.base36id)

        cache.delete_memoized(frontlist)

    # the edit form saves in the background and then loads this address
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"redirect": p.permalink})

    return redirect(p.permalink)


@app.route("/post/<pid>/history", methods=["GET"])
@auth_desired
def post_history(pid, v):
    """
View the edit/removal history of a post - every edit, self-delete, and
guild hide/unhide, each showing who, when, and (except for restores) what
the content looked like immediately before that action. If the post was
later obliterated by an admin, only a metadata-only notice is shown -
no content.
"""

    post = get_post(pid)

    entries = g.db.query(ContentEditHistory).filter_by(
        target_submission_id=post.id
    ).order_by(ContentEditHistory.id.asc()).all()

    obliteration = g.db.query(ObliterationRecord).filter_by(
        target_submission_id=post.id
    ).first()

    return render_template(
        "content_history.html",
        v=v,
        target=post,
        target_type="post",
        entries=entries,
        obliteration=obliteration
    )


@app.route("/submit/title", methods=['GET'])
@limiter.limit("6/minute")
@is_not_banned
@no_negative_balance("html")
#@tos_agreed
#@validate_formkey
def get_post_title(v):

    url = request.args.get("url", None)
    if not url:
        return abort(400)

    #mimic chrome browser agent
    headers = {"User-Agent": f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/89.0.4389.72 Safari/537.36"}
    try:
        x = requests.get(url, headers=headers)
    except BaseException:
        return jsonify({"error": "Could not reach page"}), 400


    if not x.status_code == 200:
        return jsonify({"error": f"Page returned {x.status_code}"}), x.status_code


    try:
        soup = BeautifulSoup(x.content, 'html.parser')

        data = {"url": url,
                "title": soup.find('title').string
                }

        return jsonify(data)
    except BaseException:
        return jsonify({"error": f"Could not find a title"}), 400


def _build_standalone_submission(author_id, target, title, body, body_html,
                                  url=None, embed_url=None, domain_ref=None,
                                  is_offensive=False, is_sensitive=False,
                                  paid_partnership=False, made_with_ai=False, is_anonymous=False,
                                  app_id=None, creation_region=None, is_bot=False,
                                  auto_upvote=True, repost_id=0, language_code=None):
    """Create + flush one independent new Submission (own votes, own
    comments) in `target`, with its own SubmissionAux row and an optional
    author auto-upvote. Shared by post-Forward (create_forward_post) and
    comment-Forward (create_forward_post_from_comment)."""

    new_post = Submission(
        author_id=author_id,
        domain_ref=domain_ref,
        board_id=target.id,
        original_board_id=target.id,
        post_public=not target.is_private,
        repost_id=repost_id,
        is_offensive=is_offensive,
        is_sensitive=(is_sensitive or target.is_sensitive),
        paid_partnership=paid_partnership,
        made_with_ai=made_with_ai,
        is_anonymous=is_anonymous,
        app_id=app_id,
        creation_region=creation_region,
        is_bot=is_bot,
        language_code=language_code
    )
    g.db.add(new_post)
    g.db.flush()

    new_post_aux = SubmissionAux(id=new_post.id,
                                  url=url,
                                  body=body,
                                  body_html=body_html,
                                  embed_url=embed_url,
                                  title=title
                                  )
    g.db.add(new_post_aux)
    apply_post_severity(new_post, title, body_html)

    if auto_upvote:
        g.db.add(Vote(user_id=author_id, vote_type=1, submission_id=new_post.id))

    return new_post


def create_forward_post(primary, target, forwarded_by):
    """Create one independent per-guild copy (own votes, own comments) of
    `primary`'s content in `target`, linked back via repost_id, and record
    the ForwardRelationship. Used both when forwarding at submit time and
    when forwarding an already-existing primary post afterward."""

    forward_post = _build_standalone_submission(
        author_id=primary.author_id,
        target=target,
        title=primary.title,
        body=primary.body,
        body_html=primary.body_html,
        url=primary.url,
        embed_url=primary.embed_url,
        domain_ref=primary.domain_ref,
        is_offensive=primary.is_offensive,
        is_sensitive=primary.is_sensitive,
        paid_partnership=primary.paid_partnership,
        made_with_ai=primary.made_with_ai,
        is_anonymous=primary.is_anonymous,
        app_id=primary.app_id,
        creation_region=primary.creation_region,
        is_bot=primary.is_bot,
        repost_id=primary.id,
        language_code=primary.language_code
    )

    g.db.add(ForwardRelationship(
        primary_submission_id=primary.id,
        board_id=target.id,
        forward_submission_id=forward_post.id,
        forwarded_by_id=forwarded_by.id
    ))

    return forward_post


def create_forward_post_from_comment(comment, target, comment_forwarded_by):
    """Forward a comment's text into a brand-new, independent post in
    `target` (its own votes/comment thread) - the comment-forward
    equivalent of create_forward_post(). The new post's author_id stays
    the comment's original author (matching post-Forward's delete-rights
    convention); CommentForwardRelationship tracks provenance and who
    triggered the promotion when different from the author.

    The title is not user-editable - it's the exact first 280 characters
    of the comment's own text (the same split used for the comment's own
    preview/overflow display, see Comment.is_long), so the promoted post
    looks structurally identical to how the comment already presented:
    title = what was visible, body = what was collapsed. No duplication."""

    title_raw, body_raw = split_title_body(comment.body or "", max_title=280)

    title = title_raw.replace("\n", "").replace("\r", "").replace("\t", "")
    title = bleach.clean(title, tags=[])
    if not title:
        title = "Untitled"

    if body_raw:
        body = preprocess(body_raw)
        with CustomRenderer() as renderer:
            body_md = renderer.render(mistletoe.Document(body))
        body_html = sanitize(body_md, linkgen=True)
    else:
        body, body_html = "", ""

    new_post = _build_standalone_submission(
        author_id=comment.author_id,
        target=target,
        title=title,
        body=body,
        body_html=body_html,
        is_offensive=comment.is_offensive,
        is_sensitive=comment.is_sensitive,
        paid_partnership=comment.paid_partnership,
        made_with_ai=comment.made_with_ai,
        is_anonymous=comment.is_anonymous,
        app_id=comment.app_id,
        creation_region=comment.creation_region,
        is_bot=comment.is_bot,
        language_code=detect_language(title, body)
    )

    g.db.add(CommentForwardRelationship(
        comment_id=comment.id,
        board_id=target.id,
        forwarded_submission_id=new_post.id,
        forwarded_by_id=comment_forwarded_by.id
    ))

    return new_post


@app.route("/submit", methods=['POST'])
@app.route("/api/v1/submit", methods=["POST"])
@app.route("/api/vue/submit", methods=["POST"])
@app.post("/api/v2/submissions")
@is_not_banned
@throttle_check
@no_negative_balance('html')
@tos_agreed
@validate_formkey
@api("create")
def submit_post(v):
    """
Create a post

A post always lives on your profile first. Optionally forward it to other
guilds for community-scoped discussion - each forward is its own
independent post (own votes, own comments) linked back to this one.
Forwarding to several guilds in one request costs proportionally more of
your posting-rate budget (see the boil/gear throttle), rather than being
capped at a fixed guild count.

Required form data:
* `title` - The post title. 280 character limit. A title alone, with no
  body/url/image, is a complete, valid post.

Optional form data:
* `body` - The text body of the post. Uses markdown. 25000 character limit.
* `url` - A link to attach to the post.
* `forward_guilds` - Guild name(s) to forward this post to (repeat the
  field for multiple guilds, e.g. forward_guilds=foo&forward_guilds=bar).
* `draft_id` - The saved draft this post was made from; it is deleted once the post exists.
* `paid_partnership` - `true` to mark the post as a paid partnership.
* `made_with_ai` - `true` to mark the post as made with AI.
* `anonymous` - `true` to post anonymously: other users are not told who wrote it
  (the author and site admins are). It cannot be changed afterwards.
* `comment_permission` - Who can comment on the post on your profile: `0`
  everyone (default), `1` accounts you follow, `2` Premium accounts.
  Forwarded copies follow their guild's rules instead.
* `content` - Legacy combined-field form, retained for API clients: the
  first 280 characters become the title, anything past that becomes the
  body. Only used when `title`/`body` are absent.

Optional file data:
* `file` - An image to upload as the post target. Requires premium or 500 Rep.
* `body_image` - An image to embed inline in the body. Requires the same
  access level as inline comment images.
"""

    content = request.form.get("content")

    if content is not None:
        title, body = split_title_body(content, max_title=280)
        text_for_redisplay = content
    else:
        title = request.form.get("title", "")
        body = request.form.get("body", "")
        text_for_redisplay = title + ("\n\n" + body if body else "")

    title = title.lstrip().rstrip()
    title = title.replace("\n", "")
    title = title.replace("\r", "")
    title = title.replace("\t", "")

    # sanitize title
    title = bleach.clean(title)

    url = request.form.get("url", "")

    forward_guild_names = [
        x.strip().lstrip('+') for x in request.form.getlist("forward_guilds") if x.strip()
    ]
    # de-dupe, case-insensitive, preserving order
    seen_names = set()
    forward_guild_names = [
        x for x in forward_guild_names
        if not (x.lower() in seen_names or seen_names.add(x.lower()))
    ]

    raw_permission = request.form.get("comment_permission", "")
    comment_permission = cperm.EVERYONE if raw_permission.strip() == "" else cperm.parse(raw_permission)
    if comment_permission is None:
        return {"html": lambda: (render_template("submit.html",
                                                 v=v,
                                                 error="Choose who can comment.",
                                                 title=title,
                                                 url=url,
                                                 body=body,
                                                 text=text_for_redisplay,
                                                 b=None, forward_guild_names=forward_guild_names
                                                 ), 400),
                "api": lambda: ({"error": "Choose who can comment."}, 400)
                }

    if not title:
        return {"html": lambda: (render_template("submit.html",
                                                 v=v,
                                                 error="Please enter a better title.",
                                                 title=title,
                                                 url=url,
                                                 body=body,
                                                 text=text_for_redisplay,
                                                 b=None, forward_guild_names=forward_guild_names
                                                 ), 400),
                "api": lambda: ({"error": "Please enter a better title"}, 400)
                }

    # if len(title)<10:
    #     return render_template("submit.html",
    #                            v=v,
    #                            error="Please enter a better title.",
    #                            title=title,
    #                            url=url,
    #                            text=text_for_redisplay,
    #                            b=None, forward_guild_names=forward_guild_names
    #                            )


    elif len(title) > 280:
        return {"html": lambda: (render_template("submit.html",
                                                 v=v,
                                                 error="280 character limit for titles.",
                                                 title=title[0:280],
                                                 url=url,
                                                 body=body,
                                                 text=text_for_redisplay,
                                                 b=None, forward_guild_names=forward_guild_names
                                                 ), 400),
                "api": lambda: ({"error": "280 character limit for titles"}, 400)
                }

    parsed_url = urlparse(url)

    # sanitize title
    title = bleach.clean(title, tags=[])

    # Force https for submitted urls

    if request.form.get("url"):
        new_url = ParseResult(scheme="https",
                              netloc=parsed_url.netloc,
                              path=parsed_url.path,
                              params=parsed_url.params,
                              query=parsed_url.query,
                              fragment=parsed_url.fragment)
        url = urlunparse(new_url)
    else:
        url = ""

    # check for duplicate (exact resubmission of the same primary post)
    dup = g.db.query(Submission).join(Submission.submission_aux).filter(

        Submission.author_id == v.id,
        Submission.deleted_utc == 0,
        Submission.repost_id == None,
        SubmissionAux.title == title,
        SubmissionAux.url == url,
        SubmissionAux.body == body
    ).first()

    if dup:
        return redirect(dup.permalink)


    # check for domain specific rules

    parsed_url = urlparse(url)

    domain = parsed_url.netloc

    # check ban status
    domain_obj = get_domain(domain)
    #print('domain_obj', domain_obj)
    if domain_obj:
        if not domain_obj.can_submit:
          
            if domain_obj.reason==4:
                v.ban(days=30, reason="Digitally malicious content")
            elif domain_obj.reason==9:
                v.ban(days=7, reason="Engaging in illegal activity")
            elif domain_obj.reason==7:
                v.ban(reason="Sexualizing minors")

            # return {"html": lambda: (render_template("submit.html",
            #                                          v=v,
            #                                          error=BAN_REASONS[domain_obj.reason],
            #                                          title=title,
            #                                          url=url,
            #                                          body=request.form.get(
            #                                              "body", ""),
            #                                          b=None, forward_guild_names=forward_guild_names
            #                                          ), 400),
            #         "api": lambda: ({"error": BAN_REASONS[domain_obj.reason]}, 400)
            #         }

        # check for embeds
        if domain_obj.embed_function:
            #print('domainobj embed function', domain_obj.embed_function)
            try:
                embed = eval(domain_obj.embed_function)(url)
            except BaseException as e:
                #print('exception', e)
                embed = ""
        else:
            embed = ""
    else:

        embed = ""

    # A post always lives on the author's profile first.
    board = get_guild(PROFILE_BOARD_NAME)

    # Validate every guild the author wants to forward to. All-or-nothing:
    # if any target guild is invalid, reject the whole submission up front
    # rather than partially forwarding.
    forward_boards = []
    for forward_name in forward_guild_names:

        target = get_guild(forward_name, graceful=True)

        if not target or target.name.lower() == PROFILE_BOARD_NAME:
            return {"html": lambda fn=forward_name: (render_template("submit.html",
                                                     v=v,
                                                     error=f"+{fn} doesn't exist.",
                                                     title=title,
                                                     url=url, body=body, text=text_for_redisplay,
                                                     b=None, forward_guild_names=forward_guild_names
                                                     ), 400),
                    "api": lambda fn=forward_name: ({"error": f"+{fn} doesn't exist."}, 400)
                    }

        if target.is_banned:
            return {"html": lambda: (render_template("submit.html",
                                                     v=v,
                                                     error=f"+{target.name} has been banned.",
                                                     title=title,
                                                     url=url, body=body, text=text_for_redisplay,
                                                     b=None, forward_guild_names=forward_guild_names
                                                     ), 403),
                    "api": lambda: ({"error": f"403 Forbidden - +{target.name} has been banned."}, 403)
                    }

        if target.has_ban(v):
            return {"html": lambda: (render_template("submit.html",
                                                     v=v,
                                                     error=f"You are exiled from +{target.name}.",
                                                     title=title,
                                                     url=url, body=body, text=text_for_redisplay,
                                                     b=None, forward_guild_names=forward_guild_names
                                                     ), 403),
                    "api": lambda: ({"error": f"403 Not Authorized - You are exiled from +{target.name}"}, 403)
                    }

        if (target.restricted_forwarding or target.is_private) and not (
                target.can_forward(v)):
            return {"html": lambda: (render_template("submit.html",
                                                     v=v,
                                                     error=f"You can't forward to +{target.name}: it only accepts forwards from approved contributors.",
                                                     title=title,
                                                     url=url,
                                                     body=body,
                                                     text=text_for_redisplay,
                                                     b=None, forward_guild_names=forward_guild_names
                                                     ), 403),
                    "api": lambda: ({"error": f"403 Not Authorized - +{target.name} only accepts forwards from approved contributors"}, 403)
                    }

        if target.disallowbots and request.headers.get("X-User-Type")=="Bot":
            return {"api": lambda: ({"error": f"403 Not Authorized - +{target.name} disallows bots from forwarding and commenting!"}, 403)}

        forward_boards.append(target)

    # similarity check
    now = int(time.time())
    cutoff = now - 60 * 60 * 24

    # A single Forward action can create multiple rows sharing the exact
    # same title/url across different guilds - those aren't independently
    # authored content, so they shouldn't multiply against this user's
    # spam-similarity count (which would otherwise make heavy Forward use
    # look identical to actual repeated spam-posting).
    forward_copies = g.db.query(ForwardRelationship.forward_submission_id).subquery()

    similar_posts = g.db.query(Submission).options(
        lazyload('*')
        ).join(
            Submission.submission_aux
        ).filter(
            #or_(
            #    and_(
                    Submission.author_id == v.id,
                    Submission.id.notin_(forward_copies),
                    SubmissionAux.title.op('<->')(title) < app.config["SPAM_SIMILARITY_THRESHOLD"],
                    Submission.created_utc > cutoff
            #    ),
            #    and_(
            #        SubmissionAux.title.op('<->')(title) < app.config["SPAM_SIMILARITY_THRESHOLD"]/2,
            #        Submission.created_utc > cutoff
            #    )
            #)
    ).all()

    if url:
        similar_urls = g.db.query(Submission).options(
            lazyload('*')
        ).join(
            Submission.submission_aux
        ).filter(
            #or_(
            #    and_(
                    Submission.author_id == v.id,
                    Submission.id.notin_(forward_copies),
                    SubmissionAux.url.op('<->')(url) < app.config["SPAM_URL_SIMILARITY_THRESHOLD"],
                    Submission.created_utc > cutoff
            #    ),
            #    and_(
            #        SubmissionAux.url.op('<->')(url) < app.config["SPAM_URL_SIMILARITY_THRESHOLD"]/2,
            #        Submission.created_utc > cutoff
            #    )
            #)
        ).all()
    else:
        similar_urls = []

    threshold = app.config["SPAM_SIMILAR_COUNT_THRESHOLD"]
    if v.age >= (60 * 60 * 24 * 7):
        threshold *= 3
    elif v.age >= (60 * 60 * 24):
        threshold *= 2

    if max(len(similar_urls), len(similar_posts)) >= threshold:

        text = "Your Ruqqus account has been suspended for 1 day for the following reason:\n\n> Too much spam!"
        send_notification(v, text)

        v.ban(reason="Spamming.",
              days=1)

        for alt in v.alts:
            if not alt.is_suspended:
                alt.ban(reason="Spamming.", days=1)

        for post in similar_posts + similar_urls:
            post.is_banned = True
            post.is_pinned = False
            post.ban_reason = "Automatic spam removal. This happened because the post's creator submitted too much similar content too quickly."
            g.db.add(post)
            ma=ModAction(
                    user_id=1,
                    target_submission_id=post.id,
                    kind="ban_post",
                    board_id=post.board_id,
                    note="spam"
                    )
            g.db.add(ma)
        g.db.commit()
        return redirect("/notifications")

    # catch too-long body
    if len(str(body)) > 25000:

        return {"html": lambda: (render_template("submit.html",
                                                 v=v,
                                                 error="25000 character limit for text body.",
                                                 title=title,
                                                 url=url,
                                                 body=body,
                                                 text=text_for_redisplay,
                                                 b=None, forward_guild_names=forward_guild_names
                                                 ), 400),
                "api": lambda: ({"error": "25000 character limit for text body."}, 400)
                }

    if len(url) > 2048:

        return {"html": lambda: (render_template("submit.html",
                                                 v=v,
                                                 error="2048 character limit for URLs.",
                                                 title=title,
                                                 url=url,
                                                 body=body,
                                                 text=text_for_redisplay,
                                                 b=None, forward_guild_names=forward_guild_names
                                                 ), 400),
                "api": lambda: ({"error": "2048 character limit for URLs."}, 400)
                }

    # render text

    body=preprocess(body)

    with CustomRenderer() as renderer:
        body_md = renderer.render(mistletoe.Document(body))
    body_html = sanitize(body_md, linkgen=True)

    # Run safety filter
    bans = filter_comment_html(body_html)
    if bans:
        ban = bans[0]
        reason = f"Remove the {ban.domain} link from your post and try again."
        if ban.reason:
            reason += f" {ban.reason_text}"
            
        #auto ban for digitally malicious content
        if any([x.reason==4 for x in bans]):
            v.ban(days=30, reason="Digitally malicious content is not allowed.")
            abort(403)
            
        return {"html": lambda: (render_template("submit.html",
                                                 v=v,
                                                 error=reason,
                                                 title=title,
                                                 url=url,
                                                 body=body,
                                                 text=text_for_redisplay,
                                                 b=None, forward_guild_names=forward_guild_names
                                                 ), 403),
                "api": lambda: ({"error": reason}, 403)
                }

    # check spam
    soup = BeautifulSoup(body_html, features="html.parser")
    links = [x['href'] for x in soup.find_all('a') if x.get('href')]

    if url:
        links = [url] + links

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
            if badlink.autoban:
                text = "Your Ruqqus account has been suspended for 1 day for the following reason:\n\n> Too much spam!"
                send_notification(v, text)
                v.ban(days=1, reason="spam")

                return redirect('/notifications')
            else:

                return {"html": lambda: (render_template("submit.html",
                                                         v=v,
                                                         error=f"The link `{badlink.link}` is not allowed. Reason: {badlink.reason}.",
                                                         title=title,
                                                         url=url,
                                                         body=body,
                                                         text=text_for_redisplay,
                                                         b=None, forward_guild_names=forward_guild_names
                                                         ), 400),
                        "api": lambda: ({"error": f"The link `{badlink.link}` is not allowed. Reason: {badlink.reason}"}, 400)
                        }

    # check for embeddable video
    domain = parsed_url.netloc

    # unrelated to Forward - just a "you already posted this exact link"
    # spam guard, scoped to this author's own content site-wide
    if url:
        existing_dup_url = g.db.query(Submission).join(Submission.submission_aux).filter(
            SubmissionAux.url.ilike(url),
            Submission.author_id == v.id,
            Submission.deleted_utc == 0,
            Submission.is_banned == False
        ).order_by(
            Submission.id.asc()
        ).first()
    else:
        existing_dup_url = None

    if existing_dup_url and request.values.get("no_repost"):
        return {'html':lambda:redirect(existing_dup_url.permalink),
		'api': lambda:({"error":"This content has already been posted", "repost":existing_dup_url.json}, 409)
	       }

    if request.files.get('file') and not v.can_submit_image:
        abort(403)

    link_refusal = media_attach.link_refusal(g.db, v.id, flag(request.form, "anonymous"), url)
    if link_refusal:
        return {"html": lambda: (render_template("submit.html", v=v, error=link_refusal,
                                                 title=title, url=url, body=body, text=text_for_redisplay,
                                                 b=None, forward_guild_names=forward_guild_names), 400),
                "api": lambda: ({"error": link_refusal}, 400)
                }

    # co-authors named in the composer (helpers/coauthors.py): a name that cannot be invited stops the post
    # here, before anything is made, so it never vanishes quietly afterwards
    try:
        coauthor_names = coauthors.parse_names(request.form.get("coauthors"))
        coauthor_error = (coauthors.check_names(g.db, v, coauthor_names, anonymous=flag(request.form, "anonymous"))
                          if coauthor_names else None)
    except coauthors.CoauthorError as error:
        coauthor_names, coauthor_error = [], error.message
    if coauthor_error:
        return {"html": lambda: (render_template("submit.html", v=v, error=coauthor_error,
                                                 title=title, url=url, body=body, text=text_for_redisplay,
                                                 b=None, forward_guild_names=forward_guild_names), 400),
                "api": lambda: ({"error": coauthor_error}, 400)
                }

    new_post = Submission(
        author_id=v.id,
        domain_ref=domain_obj.id if domain_obj else None,
        board_id=board.id,
        original_board_id=board.id,
        is_sensitive=bool(request.form.get("sensitive", "")),
        comment_permission=comment_permission,
        paid_partnership=flag(request.form, "paid_partnership"),
        made_with_ai=flag(request.form, "made_with_ai"),
        is_anonymous=flag(request.form, "anonymous"),
        post_public=not board.is_private,
        repost_id=None,
        app_id=v.client.application.id if v.client else None,
        creation_region=request.headers.get("cf-ipcountry"),
        is_bot = request.headers.get("X-User-Type","").lower()=="bot",
        language_code=detect_language(title, body)
    )

    g.db.add(new_post)
    g.db.flush()

    new_post_aux = SubmissionAux(id=new_post.id,
                                 url=url,
                                 body=body,
                                 body_html=body_html,
                                 embed_url=embed,
                                 title=title
                                 )
    g.db.add(new_post_aux)
    apply_post_severity(new_post, title, body_html)
    g.db.flush()

    vote = Vote(user_id=v.id,
                vote_type=1,
                submission_id=new_post.id
                )
    g.db.add(vote)
    g.db.flush()

    g.db.refresh(new_post)

    # the post's main picture, already uploaded to the author's linked storage
    # (helpers/media): the `media` field names it. Nothing is stored here, so no
    # thumbnail is made either: the picture itself is the thumbnail.
    main_media = None
    if request.form.get("media") and not request.files.get('file'):
        main_media = media_attach.own_asset(g.db, v.id, request.form.get("media"), kinds=("image",))
        if main_media is None:
            g.db.rollback()
            return {"html": lambda: (render_template("submit.html", v=v, error="That upload can no longer be used. Add it again.",
                                                     title=title, url=url, body=body, text=text_for_redisplay,
                                                     b=None, forward_guild_names=forward_guild_names), 400),
                    "api": lambda: ({"error": "That upload can no longer be used. Add it again."}, 400)
                    }
        new_post.url = media_cdn.absolute(main_media.path)
        new_post.is_image = True
        new_post.domain_ref = None
        g.db.add(new_post)
        g.db.add(new_post.submission_aux)
        g.db.commit()

    # check for uploaded image
    if request.files.get('file'):

        #check file size
        if request.content_length > 16 * 1024 * 1024 and not v.has_premium:
            g.db.rollback()
            abort(413)

        file = request.files['file']
        if not file.content_type.startswith('image/'):
            return {"html": lambda: (render_template("submit.html",
                                                         v=v,
                                                         error=f"Image files only.",
                                                         title=title,
                                                         url=url,
                                                         body=body,
                                                         text=text_for_redisplay,
                                                         b=None, forward_guild_names=forward_guild_names
                                                         ), 400),
                        "api": lambda: ({"error": f"Image files only"}, 400)
                        }

        name = f'post/{new_post.base36id}/{secrets.token_urlsafe(8)}'
        upload_file(name, file)

        # thumb_name=f'posts/{new_post.base36id}/thumb.png'
        #upload_file(name, file, resize=(375,227))

        # update post data
        new_post.url = f'https://{BUCKET}/{name}'
        new_post.is_image = True
        new_post.domain_ref = 1  # id of i.ruqqus.com domain
        g.db.add(new_post)
        g.db.add(new_post.submission_aux)
        g.db.commit()

        #csam detection
        def del_function():
            db=db_session()
            delete_file(name)
            new_post.is_banned=True
            db.add(new_post)
            db.commit()
            ma=ModAction(
                kind="ban_post",
                user_id=1,
                note="banned image",
                target_submission_id=new_post.id
                )
            db.add(ma)
            db.commit()
            db.close()

            
        csam_thread=gevent.spawn(
            check_csam_url,
            f"https://{BUCKET}/{name}",
            v,
            del_function
          )
        csam_thread.start()

    # Inline image dropped into the body via the body toolbar's Image
    # button - mirrors comments.py's own comment-image-upload pattern.
    if v.can_upload_comment_image and request.files.get('body_image'):
        bfile = request.files['body_image']
        if bfile.content_type.startswith('image/'):
            bname = f'post/{new_post.base36id}/{secrets.token_urlsafe(8)}'
            upload_file(bname, bfile)

            new_post_aux.body = (new_post_aux.body or "") + f"\n\n![](https://{BUCKET}/{bname})"
            with CustomRenderer() as renderer:
                body_md = renderer.render(mistletoe.Document(preprocess(new_post_aux.body)))
            new_post_aux.body_html = sanitize(body_md, linkgen=True)
            g.db.add(new_post_aux)
            apply_post_severity(new_post, title, new_post_aux.body_html)
            g.db.commit()

            #csam detection
            def del_body_image_function():
                db = db_session()
                delete_file(bname)
                new_post.is_banned = True
                db.add(new_post)
                db.commit()
                db.close()

            body_csam_thread = gevent.spawn(
                check_csam_url,
                f"https://{BUCKET}/{bname}",
                v,
                del_body_image_function
              )
            body_csam_thread.start()

    # Forward to each validated guild: each is its own independent post
    # (own votes, own comments) with the same content, linked back to the
    # primary post via repost_id.
    forward_posts = [
        create_forward_post(new_post, target, v)
        for target in forward_boards
    ]

    g.db.commit()

    # spin off thumbnail generation and csam detection as  new threads
    if (new_post.url or request.files.get('file')) and main_media is None and (v.is_activated or request.headers.get('cf-ipcountry')!="T1"):
        new_thread = gevent.spawn(
            thumbnail_thread,
            new_post.base36id
        )

    # expire the relevant caches: front page new, board new
    cache.delete_memoized(frontlist)
    g.db.commit()
    cache.delete_memoized(Board.idlist, board, sort="new")
    for target in forward_boards:
        cache.delete_memoized(Board.idlist, target, sort="new")
    
    
    # queue up notifications for username mentions
    notify_users = set()
	
    soup = BeautifulSoup(body_html, features="html.parser")
    for mention in soup.find_all("a", href=re.compile(r"^/@(\w+)") , limit=3):
        username = mention["href"].split("@")[1]
        user = g.db.query(User).filter_by(username=username).first()
        if user and not v.any_block_exists(user) and user.id != v.id: 
            notify_users.add(user.id)
    
    # print(f"Content Event: @{new_post.author.username} post
    # {new_post.base36id}")

    #Bell notifs


    board_uids = g.db.query(
        Subscription.user_id
        ).options(lazyload('*')).filter(
        Subscription.board_id==new_post.board_id, 
        Subscription.is_active==True,
        Subscription.get_notifs==True,
        Subscription.user_id != v.id,
        Subscription.user_id.notin_(
            g.db.query(UserBlock.user_id).filter_by(target_id=v.id).subquery()
            )
        )

    follow_uids=g.db.query(
        Follow.user_id
        ).options(lazyload('*')).filter(
        Follow.target_id==v.id,
        Follow.get_notifs==True,
        Follow.user_id!=v.id,
        Follow.user_id.notin_(
            g.db.query(UserBlock.user_id).filter_by(target_id=v.id).subquery()
            ),
        Follow.user_id.notin_(
            g.db.query(UserBlock.target_id).filter_by(user_id=v.id).subquery()
            )
        ).join(Follow.target).filter(
        User.is_private==False,
        User.is_nofollow==False,
        )

    if not new_post.is_public:

        contribs=g.db.query(ContributorRelationship).filter_by(board_id=new_post.board_id, is_active=True).subquery()
        mods=g.db.query(ModRelationship).filter_by(board_id=new_post.board_id, accepted=True).subquery()

        board_uids=board_uids.join(
            contribs,
            contribs.c.user_id==Subscription.user_id,
            isouter=True
            ).join(
            mods,
            mods.c.user_id==Subscription.user_id,
            isouter=True
            ).filter(
                or_(
                    mods.c.id != None,
                    contribs.c.id !=None
                )
            )

        follow_uids=follow_uids.join(
            contribs,
            contribs.c.user_id==Follow.user_id,
            isouter=True
            ).join(
            mods,
            mods.c.user_id==Follow.user_id,
            isouter=True
            ).filter(
                or_(
                    mods.c.id != None,
                    contribs.c.id !=None
                )
            )

    # an anonymous post does not notify the author's followers: only they would get it
    follower_ids = [] if new_post.is_anonymous else [x[0] for x in follow_uids.all()]
    uids=list(set([x[0] for x in board_uids.all()] + follower_ids).union(notify_users))
    if not new_post.is_anonymous:
        muters = {x[0] for x in g.db.query(UserMute.user_id).filter_by(target_id=v.id).all()}
        uids = [uid for uid in uids if uid not in muters]

    for uid in uids:
        new_notif=Notification(
            user_id=uid,
            submission_id=new_post.id
            )
        g.db.add(new_notif)
    g.db.commit()


    # Charge one action's worth of heat for the post itself, plus one
    # more per guild actually forwarded to - so bulk-forwarding several
    # guilds in a single request costs proportionally more of the same
    # posting-rate budget instead of registering as a single free action.
    g.throttle_weight = 1 + len(forward_boards)

    # files from the author's linked storage that this post shows (helpers/media)
    attached_media = media_attach.sync(g.db, v.id, (new_post.url, new_post_aux.body), submission_id=new_post.id)
    g.db.commit()
    media_safety.scan_later(attached_media)

    if coauthor_names:
        coauthors.invite_names(g.db, v, new_post, coauthor_names)

    _discard_draft(v, request.form.get("draft_id"))

    return {"html": lambda: redirect(new_post.permalink),
            "api": lambda: jsonify(new_post.json)
            }


@app.get("/composer")
@auth_required
def composer_panel(v):
    """The composer on its own, for the Create post side panel (assets/js/side_panels.js)."""
    return render_template("composer_panel.html", v=v)


@app.get("/inpage/post_card/<pid>")
@auth_required
def post_card_fragment(pid, v):
    """
One of your own posts as the card a feed shows, so the inline composer can add
it to the top of the feed without reloading the page. With `guild`, the card of
the copy forwarded to that guild (what a guild's feed lists) when there is one.
"""
    post = get_post(pid, v=v)
    if post.author_id != v.id:
        abort(403)

    shown = post
    board = get_guild(request.args.get("guild", ""), graceful=True)
    if board:
        shown = g.db.query(Submission).filter_by(repost_id=post.id, board_id=board.id).first() or post

    return render_template("submission_listing.html", v=v, listing=get_posts([shown.id], v=v))


@app.route("/post/<pid>/forward", methods=["POST"])
@auth_required
@throttle_check
@validate_formkey
def forward_post(pid, v):
    """
Forward a post into one more guild. Usable by anyone logged in, whether
or not they authored the post - the resulting guild copy stays authored
by (and deletable by) the original creator, never the person who
forwarded it. The target guild's own posting rules (bans,
restricted-posting/private-guild contributor lists) are checked against
the person doing the forwarding, same as if they were submitting there
themselves.

`pid` may be either a primary (profile) post or one of its existing
guild-forward copies - forwarding always acts on the underlying primary
post either way, since a forward-of-a-forward isn't allowed (this keeps
the graph a strict one-hop star, matching every "p.reposts.permalink"
single-hop dereference elsewhere).

URL path parameters:
* `pid` - The base 36 id of the post to forward (primary or forward-copy)

Required form data:
* `board` - Name of the guild to forward into
"""

    post = get_post(pid, v=v)
    primary = post.reposts if post.is_repost else post

    target = get_guild(request.form.get("board", ""), graceful=True)
    if not target or target.name.lower() == PROFILE_BOARD_NAME:
        return {"error": "That guild doesn't exist."}, 400

    if target.is_banned:
        return {"error": f"+{target.name} has been banned."}, 403

    if target.has_ban(v):
        return {"error": f"Exiled from +{target.name}."}, 403

    if (target.restricted_forwarding or target.is_private) and not target.can_forward(v):
        return {"error": f"+{target.name} only accepts forwards from approved contributors."}, 403

    if target.disallowbots and (request.headers.get("X-User-Type", "").lower() == "bot" or primary.is_bot):
        return {"error": f"403 Not Authorized - +{target.name} disallows bots from forwarding and commenting!"}, 403

    already = g.db.query(ForwardRelationship).filter_by(
        primary_submission_id=primary.id, board_id=target.id).first()
    if already:
        return {"error": f"Already forwarded to +{target.name}."}, 409

    new_forward = create_forward_post(primary, target, v)
    g.db.commit()

    cache.delete_memoized(frontlist)
    cache.delete_memoized(Board.idlist, target, sort="new")

    return jsonify(new_forward.json)


@app.route("/delete_post/<pid>", methods=["POST"])
@app.route("/api/v1/delete_post/<pid>", methods=["POST"])
@app.delete("/api/v2/submissions/<pid>")
@auth_required
@api("delete")
@validate_formkey
def delete_post_pid(pid, v):
    """
Delete your post.

URL path parameters:
* `pid` - The base 36 id of the post being deleted
"""

    post = get_post(pid)
    if not post.author_id == v.id:
        abort(403)

    if post.is_deleted:
        abort(404)

    now = int(time.time())

    post.deleted_utc = now
    post.is_pinned = False
    post.stickied = False

    g.db.add(post)
    media_cdn.purge(media_attach.attached_paths(g.db, submission_id=post.id))

    g.db.add(ContentEditHistory(
        actor_id=v.id,
        target_submission_id=post.id,
        board_id=post.board_id,
        action="delete",
        previous_title=post.title,
        previous_body=post.body,
        previous_body_html=post.body_html
    ))

    # clear cache
    cache.delete_memoized(User.userpagelisting, v, sort="new")
    cache.delete_memoized(Board.idlist, post.board)

    if post.age >= 3600 * 6:
        cache.delete_memoized(Board.idlist, post.board, sort="new")
        cache.delete_memoized(frontlist, sort="new")

    # delete i.ruqqus.com - only the primary's copy, forwards share the same url
    if post.domain == "i.ruqqus.com":

        segments = post.url.split("/")
        pid = segments[4]
        rand = segments[5]
        if pid == post.base36id:
            key = f"post/{pid}/{rand}"
            delete_file(key)
            post.is_image = False
            g.db.add(post)

    # deleting a primary post cascades to every guild it was forwarded to -
    # deleting one specific forward copy (post.is_repost==True) stays scoped
    # to just that row and doesn't touch the primary or its other forwards
    if not post.is_repost:
        forwards = g.db.query(Submission).filter_by(repost_id=post.id).all()
        for forward in forwards:
            if forward.is_deleted:
                continue
            forward.deleted_utc = now
            forward.is_pinned = False
            forward.stickied = False
            g.db.add(forward)
            cache.delete_memoized(Board.idlist, forward.board)
            if forward.age >= 3600 * 6:
                cache.delete_memoized(Board.idlist, forward.board, sort="new")

    return "", 204


@app.route("/embed/post/<pid>", methods=["GET"])
def embed_post_pid(pid):

    post = get_post(pid)

    if post.is_banned or post.board.is_banned:
        abort(410)

    if post_hidden(post, None):
        abort(403)

    return render_template("embeds/submission.html", p=post)


@app.route("/api/toggle_post_sensitive/<pid>", methods=["POST"])
@app.route("/api/v1/toggle_post_sensitive/<pid>", methods=["POST"])
@app.patch("/api/v2/submissions/<pid>/toggle_sensitive")
@is_not_banned
@api("update")
@validate_formkey
def toggle_post_sensitive(pid, v):
    """
Toggle "Sensitive Content" status on a post - an author-discretionary
blur-until-clicked flag for material that isn't a rule violation but
could be upsetting.

URL path parameters:
* `pid` - The base 36 post id.
"""

    post = get_post(pid)

    mod = post.board.has_mod(v)

    if not post.author_id == v.id and not v.admin_level >= 3 and not mod:
        abort(403)

    if post.board.is_sensitive and post.is_sensitive:
        abort(403)

    post.is_sensitive = not post.is_sensitive
    g.db.add(post)

    if post.author_id != v.id:
        ma = ModAction(
            kind="set_sensitive" if post.is_sensitive else "unset_sensitive",
            user_id=v.id,
            target_submission_id=post.id,
            board_id=post.board.id,
            note=None if mod else "admin action"
            )
        g.db.add(ma)

    return "", 204


@app.route("/retry_thumb/<pid>", methods=["POST"])
@app.put("/api/v2/submissions/<pid>/thumb")
@is_not_banned
@api("identity")
@validate_formkey
def retry_thumbnail(pid, v):
    """
Retry thumbnail scraping on your post.

URL path parameters:
* `pid` - The base 36 post id.
"""

    post = get_post(pid, v=v)

    if post.author_id != v.id and v.admin_level < 3:
        return jsonify({"error": "That isn't your post."}), 403

    if post.is_archived:
        return jsonify({"error": "Post is archived"}), 409

    try:
        success, msg = thumbnail_thread(post.base36id, debug=True)
    except Exception as e:
        return jsonify({"error":str(e)}), 500

    if not success:
        return jsonify({"error":msg}), 500


    return jsonify({"message": "Success"})


@app.route("/save_post/<base36id>", methods=["POST"])
#@app.post("/api/v2/submissions/<base36id>/save")
@auth_required
@validate_formkey
def save_post(base36id, v):

    post=get_post(base36id)

    existing=g.db.query(SaveRelationship).filter_by(user_id=v.id, submission_id=post.id).first()

    if not existing:
        new_save=SaveRelationship(
            user_id=v.id,
            submission_id=post.id,
            created_utc=int(time.time()))

        g.db.add(new_save)

        try:
            g.db.flush()
        except:
            abort(422)

    return jsonify({"message": "Post bookmarked."})


@app.route("/unsave_post/<base36id>", methods=["POST"])
#@app.delete("/api/v2/submissions/<base36id>/save")
@auth_required
@validate_formkey
def unsave_post(base36id, v):

    post=get_post(base36id)

    save=g.db.query(SaveRelationship).filter_by(user_id=v.id, submission_id=post.id).first()

    if save:
        g.db.delete(save)

    return jsonify({"message": "Bookmark removed."})


@app.route("/post/<base36id>/repost", methods=["POST"])
@auth_required
@throttle_check
@validate_formkey
def repost_post(base36id, v):
    """Repost a post to your own profile, Twitter-retweet style - your
    own posts included, same as retweeting your own tweet to resurface
    it. This creates no independent copy - votes/comments/authorship
    all stay on the shared primary post; this just records that it should
    also appear (with an inline "Repost" tag next to its byline) on the
    reposter's profile.

    `base36id` may be either a primary (profile) post or one of its
    existing guild-forward copies - reposting always acts on the
    underlying primary post either way, matching Forward's convention, so
    the option isn't effectively hidden on every forward-copy (which is
    most of what shows up in guild feeds)."""

    post = get_post(base36id, v=v)
    primary = post.reposts if post.is_repost else post

    if primary.is_anonymous and primary.author_id == v.id:
        return {"error": "You can't repost your own anonymous post: it would put it on your profile."}, 400

    existing = g.db.query(RepostRelationship).filter_by(
        user_id=v.id, submission_id=primary.id).first()
    if existing:
        return {"error": "You've already reposted this."}, 409

    g.db.add(RepostRelationship(
        user_id=v.id,
        submission_id=primary.id,
        created_utc=int(time.time())
    ))
    g.db.commit()

    cache.delete_memoized(User.userpagelisting, v)

    return jsonify({"message": "Reposted to your profile."})


@app.route("/post/<base36id>/unrepost", methods=["POST"])
@auth_required
@validate_formkey
def unrepost_post(base36id, v):

    post = get_post(base36id, v=v)
    primary = post.reposts if post.is_repost else post

    existing = g.db.query(RepostRelationship).filter_by(
        user_id=v.id, submission_id=primary.id).first()

    if existing:
        g.db.delete(existing)
        g.db.commit()
        cache.delete_memoized(User.userpagelisting, v)

    return jsonify({"message": "Repost removed."})
