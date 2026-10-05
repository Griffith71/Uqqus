import time
import calendar
from flask import *
import imagehash
from PIL import Image
from os import remove
from sqlalchemy import func

from ruqqus.classes import *
from ruqqus.helpers.wrappers import *
from ruqqus.helpers.aws import delete_file
from ruqqus.helpers.base36 import *
from ruqqus.helpers.alerts import *
from ruqqus.helpers.sanitize import *
from ruqqus.helpers.markdown import *
from ruqqus.helpers.security import *
from urllib.parse import urlparse
from secrets import token_hex
import matplotlib.pyplot as plt

from ruqqus.__main__ import app, cache


@app.route("/api/ban_user/<user_id>", methods=["POST"])
@admin_level_required(3)
@validate_formkey
def ban_user(user_id, v):

    user = g.db.query(User).filter_by(id=user_id).first()

    # check for number of days for suspension
    days = int(request.form.get("days")) if request.form.get('days') else 0
    reason = request.form.get("reason", "")
    message = request.form.get("message", "")

    if not user:
        abort(400)

    if days > 0:
        if message:
            text = f"Your Ruqqus account has been suspended for {days} days for the following reason:\n\n> {message}"
        else:
            text = f"Your Ruqqus account has been suspended for {days} days due to a Terms of Service violation."
        user.ban(admin=v, reason=reason, days=days)

    else:
        if message:
            text = f"Your Ruqqus account has been permanently suspended for the following reason:\n\n> {message}"
        else:
            text = "Your Ruqqus account has been permanently suspended due to a Terms of Service violation."

        user.ban(admin=v, reason=reason)


    for x in user.alts:
        if not x.is_deleted:
            x.ban(admin=v, reason=reason)




    send_notification(user, text)

    return (redirect(user.url), user)


@app.route("/api/unban_user/<user_id>", methods=["POST"])
@admin_level_required(3)
@validate_formkey
def unban_user(user_id, v):

    user = g.db.query(User).filter_by(id=user_id).first()

    if not user:
        abort(400)

    user.unban()

    send_notification(user,
                      "Your Ruqqus account has been reinstated. Please carefully review and abide by the [terms of service](/help/terms) and [content policy](/help/rules) to ensure that you don't get suspended again.")


    return (redirect(user.url), user)


@app.route("/api/ban_post/<post_id>", methods=["POST"])
@admin_level_required(3)
@validate_formkey
def ban_post(post_id, v):

    post = g.db.query(Submission).filter_by(id=base36decode(post_id)).first()

    if not post:
        abort(400)

    post.is_banned = True
    post.is_approved = 0
    post.approved_utc = 0
    post.stickied = False
    post.is_pinned = False

    ban_reason=request.form.get("reason", "")
    with CustomRenderer() as renderer:
        ban_reason = renderer.render(mistletoe.Document(ban_reason))
    ban_reason = sanitize(ban_reason, linkgen=True)

    post.ban_reason = ban_reason

    g.db.add(post)

    cache.delete_memoized(Board.idlist, post.board)

    ma=ModAction(
        kind="ban_post",
        user_id=v.id,
        target_submission_id=post.id,
        board_id=post.board_id,
        note="admin action"
        )
    g.db.add(ma)
    return (redirect(post.permalink), post)


@app.route("/api/unban_post/<post_id>", methods=["POST"])
@admin_level_required(3)
@validate_formkey
def unban_post(post_id, v):

    post = g.db.query(Submission).filter_by(id=base36decode(post_id)).first()

    if not post:
        abort(400)

    if post.is_banned:
        ma=ModAction(
            kind="unban_post",
            user_id=v.id,
            target_submission_id=post.id,
            board_id=post.board_id,
            note="admin action"
        )
        g.db.add(ma)

    post.is_banned = False
    post.is_approved = v.id
    post.approved_utc = int(time.time())

    g.db.add(post)

    return (redirect(post.permalink), post)


@app.route("/api/purge_post/<post_id>", methods=["POST"])
@admin_level_required(3)
@validate_formkey
def purge_post(post_id, v):
    """
Permanently erase a post's title/body/url text.

Unlike ban (which keeps the content intact for admin review) or an
author's own delete (which anonymizes the author but keeps the content
visible), this is irreversible - the original text is not recoverable.
The row, its id, its votes, and its comment thread all stay intact so
nothing that references it breaks; only the text itself is gone.
"""

    post = g.db.query(Submission).filter_by(id=base36decode(post_id)).first()

    if not post:
        abort(400)

    post.purged_utc = int(time.time())
    post.is_pinned = False
    post.stickied = False
    post.title = ""
    post.body = ""
    post.body_html = ""
    post.url = ""

    g.db.add(post)

    cache.delete_memoized(Board.idlist, post.board)

    ma=ModAction(
        kind="purge_post",
        user_id=v.id,
        target_submission_id=post.id,
        board_id=post.board_id,
        note="admin action"
        )
    g.db.add(ma)
    return (redirect(post.permalink), post)


@app.route("/api/distinguish/<post_id>", methods=["POST"])
@admin_level_required(1)
@validate_formkey
def api_distinguish_post(post_id, v):

    post = g.db.query(Submission).filter_by(id=base36decode(post_id)).first()

    if not post:
        abort(404)

    if not post.author_id == v.id:
        abort(403)

    if post.distinguish_level:
        post.distinguish_level = 0
    else:
        post.distinguish_level = v.admin_level

    g.db.add(post)

    return (redirect(post.permalink), post)


@app.route("/api/sticky/<post_id>", methods=["POST"])
@admin_level_required(3)
def api_sticky_post(post_id, v):

    post = g.db.query(Submission).filter_by(id=base36decode(post_id)).first()
    if post:
        if post.stickied:
            post.stickied = False
            g.db.add(post)

            return redirect(post.permalink)

    already_stickied = g.db.query(Submission).filter_by(stickied=True).first()

    post.stickied = True

    if already_stickied:
        already_stickied.stickied = False
        g.db.add(already_stickied)

    g.db.add(post)

    return (redirect(post.permalink), post)


@app.route("/api/ban_comment/<c_id>", methods=["post"])
@admin_level_required(1)
def api_ban_comment(c_id, v):

    comment = g.db.query(Comment).filter_by(id=base36decode(c_id)).first()
    if not comment:
        abort(404)

    comment.is_banned = True
    comment.is_approved = 0
    comment.approved_utc = 0

    g.db.add(comment)
    ma=ModAction(
        kind="ban_comment",
        user_id=v.id,
        target_comment_id=comment.id,
        board_id=comment.post.board_id,
        note="admin action"
        )
    g.db.add(ma)
    return "", 204


@app.route("/api/unban_comment/<c_id>", methods=["post"])
@admin_level_required(1)
def api_unban_comment(c_id, v):

    comment = g.db.query(Comment).filter_by(id=base36decode(c_id)).first()
    if not comment:
        abort(404)
    g.db.add(comment)

    if comment.is_banned:
        ma=ModAction(
            kind="unban_comment",
            user_id=v.id,
            target_comment_id=comment.id,
            board_id=comment.post.board_id,
            note="admin action"
            )
        g.db.add(ma)

    comment.is_banned = False
    comment.is_approved = v.id
    comment.approved_utc = int(time.time())


    return "", 204


@app.route("/api/purge_comment/<c_id>", methods=["post"])
@admin_level_required(3)
def purge_comment(c_id, v):
    """
Permanently erase a comment's body text.

Unlike ban (which keeps the content intact for admin review) or an
author's own delete (which anonymizes the author but keeps the content
visible), this is irreversible - the original text is not recoverable.
The row, its id, its votes, and any comments to it all stay intact so
nothing that references it breaks; only the text itself is gone.
"""

    comment = g.db.query(Comment).filter_by(id=base36decode(c_id)).first()
    if not comment:
        abort(404)

    comment.purged_utc = int(time.time())
    comment.body = ""
    comment.body_html = ""

    g.db.add(comment)
    ma=ModAction(
        kind="purge_comment",
        user_id=v.id,
        target_comment_id=comment.id,
        board_id=comment.post.board_id,
        note="admin action"
        )
    g.db.add(ma)
    return "", 204


@app.route("/admin/obliterate_post/<pid>", methods=["POST"])
@admin_level_required(4)
@validate_formkey
def admin_obliterate_post(pid, v):
    """
Permanently and structurally destroy a post's content - title, body,
url, embed/meta fields - for itself, every Forward/repost copy, and
every comment underneath all of them.

Unlike purge_post, this cascades fully to comments and forward copies,
and it also scrubs any pre-existing content_edit_history snapshots for
all of those rows, so no earlier edited-out or hidden version of the
content can resurface there either. Only a reason and the periphery
metadata (who authored it, who obliterated it, when, why) survive, in
obliteration_records - a table with no content-bearing columns at all.
This cannot be undone, even by an admin.

Required form data:
* `reason_category` - "illegal_content" | "legal_takedown" | "other"
* `reason` - free-text detail
"""

    post = g.db.query(Submission).filter_by(id=base36decode(pid)).first()
    if not post:
        abort(404)

    reason = request.form.get("reason", "").strip()
    reason_category = request.form.get("reason_category", "").strip()
    if not reason or reason_category not in ("illegal_content", "legal_takedown", "other"):
        abort(400)

    author_id = post.author_id
    board_id = post.board_id
    now = int(time.time())

    # resolve the full target set: the post itself, plus every forward/
    # repost copy of it - unless the target IS itself a forward copy, in
    # which case scope stays to just that one row (mirrors delete_post_pid)
    if post.is_repost:
        targets = [post]
    else:
        targets = [post] + g.db.query(Submission).filter_by(repost_id=post.id).all()

    target_ids = [t.id for t in targets]

    comments = g.db.query(Comment).filter(
        Comment.parent_submission.in_(target_ids)
    ).all()
    comment_ids = [c.id for c in comments]

    # scrub pre-existing history snapshots FIRST, before touching live
    # rows, so a failure partway through never leaves an old snapshot as
    # the only surviving copy of destroyed text
    g.db.query(ContentEditHistory).filter(
        ContentEditHistory.target_submission_id.in_(target_ids)
    ).update({
        ContentEditHistory.previous_title: None,
        ContentEditHistory.previous_url: None,
        ContentEditHistory.previous_body: None,
        ContentEditHistory.previous_body_html: None
    }, synchronize_session=False)

    if comment_ids:
        g.db.query(ContentEditHistory).filter(
            ContentEditHistory.target_comment_id.in_(comment_ids)
        ).update({
            ContentEditHistory.previous_body: None,
            ContentEditHistory.previous_body_html: None
        }, synchronize_session=False)

    # blank live content + clean up hosted media, for every target post
    for t in targets:
        if t.domain == "i.ruqqus.com" and t.url:
            segments = t.url.split("/")
            if len(segments) > 5:
                tpid, rand = segments[4], segments[5]
                if tpid == t.base36id:
                    delete_file(f"post/{tpid}/{rand}")
                    t.is_image = False

        t.purged_utc = now
        t.is_pinned = False
        t.stickied = False
        t.title = ""
        t.body = ""
        t.body_html = ""
        t.url = ""
        t.embed_url = ""
        t.meta_title = ""
        t.meta_description = ""
        t.preview_image_url = ""
        g.db.add(t)
        cache.delete_memoized(Board.idlist, t.board)

    # blank every comment under every target post
    for c in comments:
        c.purged_utc = now
        c.body = ""
        c.body_html = ""
        g.db.add(c)

    g.db.add(ObliterationRecord(
        actor_id=v.id,
        author_id=author_id,
        target_submission_id=post.id,
        board_id=board_id,
        reason_category=reason_category,
        reason=reason
    ))

    g.db.add(ModAction(
        kind="obliterate_post",
        user_id=v.id,
        target_submission_id=post.id,
        board_id=board_id,
        note="admin action"
    ))

    g.db.commit()

    return redirect(post.permalink)


@app.route("/admin/obliterate_comment/<cid>", methods=["POST"])
@admin_level_required(4)
@validate_formkey
def admin_obliterate_comment(cid, v):
    """
Permanently and structurally destroy a single comment's body text. Does
NOT cascade to its own comments (same scoping purge_comment already
uses) - child comments are independently authored content.

Required form data:
* `reason_category` - "illegal_content" | "legal_takedown" | "other"
* `reason` - free-text detail
"""

    comment = g.db.query(Comment).filter_by(id=base36decode(cid)).first()
    if not comment:
        abort(404)

    reason = request.form.get("reason", "").strip()
    reason_category = request.form.get("reason_category", "").strip()
    if not reason or reason_category not in ("illegal_content", "legal_takedown", "other"):
        abort(400)

    author_id = comment.author_id
    board_id = comment.post.board_id
    now = int(time.time())

    g.db.query(ContentEditHistory).filter_by(target_comment_id=comment.id).update({
        ContentEditHistory.previous_body: None,
        ContentEditHistory.previous_body_html: None
    }, synchronize_session=False)

    comment.purged_utc = now
    comment.body = ""
    comment.body_html = ""
    g.db.add(comment)

    g.db.add(ObliterationRecord(
        actor_id=v.id,
        author_id=author_id,
        target_comment_id=comment.id,
        board_id=board_id,
        reason_category=reason_category,
        reason=reason
    ))

    g.db.add(ModAction(
        kind="obliterate_comment",
        user_id=v.id,
        target_comment_id=comment.id,
        board_id=board_id,
        note="admin action"
    ))

    g.db.commit()

    return "", 204


@app.route("/api/distinguish_comment/<c_id>", methods=["post"])
@admin_level_required(1)
def admin_distinguish_comment(c_id, v):

    comment = get_comment(c_id, v=v)

    if comment.author_id != v.id:
        abort(403)

    comment.distinguish_level = 0 if comment.distinguish_level else v.admin_level

    g.db.add(comment)
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



@app.route("/api/ban_guild/<bid>", methods=["POST"])
@admin_level_required(4)
@validate_formkey
def api_ban_guild(v, bid):

    board = get_board(bid, v=v)

    board.is_banned = True
    board.ban_reason = request.form.get("reason", "")

    g.db.add(board)

    return redirect(board.permalink)


@app.route("/api/unban_guild/<bid>", methods=["POST"])
@admin_level_required(4)
@validate_formkey
def api_unban_guild(v, bid):

    board = get_board(bid, v=v)

    board.is_banned = False
    board.ban_reason = ""

    g.db.add(board)

    return redirect(board.permalink)


@app.route("/api/mod_self/<bid>", methods=["POST"])
@admin_level_required(4)
@validate_formkey
def mod_self_to_guild(v, bid):

    board = get_board(bid)
    if not board.has_mod(v):
        mr = ModRelationship(user_id=v.id,
                             board_id=board.id,
                             accepted=True,
                             perm_full=True,
                             perm_access=True,
                             perm_config=True,
                             perm_appearance=True,
                             perm_content=True)
        g.db.add(mr)

        ma=ModAction(
            kind="add_mod",
            user_id=v.id,
            target_user_id=v.id,
            board_id=board.id,
            note="admin action"
        )
        g.db.add(ma)

    return redirect(f"/+{board.name}/mod/mods")


@app.route("/api/user_stat_data", methods=['GET'])
@admin_level_required(2)
@cache.memoize(timeout=60)
def user_stat_data(v):

    days = int(request.args.get("days", 30))

    now = time.gmtime()
    midnight_this_morning = time.struct_time((now.tm_year,
                                              now.tm_mon,
                                              now.tm_mday,
                                              0,
                                              0,
                                              0,
                                              now.tm_wday,
                                              now.tm_yday,
                                              0)
                                             )
    today_cutoff = calendar.timegm(midnight_this_morning)

    day = 3600 * 24

    day_cutoffs = [today_cutoff - day * i for i in range(days)]
    day_cutoffs.insert(0, calendar.timegm(now))

    daily_signups = [{"date": time.strftime("%d %b %Y", time.gmtime(day_cutoffs[i + 1])),
                      "day_start":day_cutoffs[i + 1],
                      "signups": g.db.query(User).filter(User.created_utc < day_cutoffs[i],
                                                         User.created_utc > day_cutoffs[i + 1],
                                                         User.is_banned == 0
                                                         ).count()
                      } for i in range(len(day_cutoffs) - 1)
                     ]

    user_stats = {'current_users': g.db.query(User).filter_by(is_banned=0, reserved=None).count(),
                  'banned_users': g.db.query(User).filter(User.is_banned != 0).count(),
                  'reserved_users': g.db.query(User).filter(User.reserved is not None).count(),
                  'email_verified_users': g.db.query(User).filter_by(is_banned=0, is_activated=True).count(),
                  'real_id_verified_users': g.db.query(User).filter(User.reserved is not None, User.real_id is not None).count()
                  }

    post_stats = [{"date": time.strftime("%d %b %Y", time.gmtime(day_cutoffs[i + 1])),
                   "day_start":day_cutoffs[i + 1],
                   "posts": g.db.query(Submission).filter(Submission.created_utc < day_cutoffs[i],
                                                          Submission.created_utc > day_cutoffs[i + 1],
                                                          Submission.is_banned == False
                                                          ).count()
                   } for i in range(len(day_cutoffs) - 1)
                  ]

    guild_stats = [{"date": time.strftime("%d %b %Y", time.gmtime(day_cutoffs[i + 1])),
                    "day_start": day_cutoffs[i + 1],
                    "members": g.db.query(Board).filter(Board.created_utc < day_cutoffs[i],
                                                        Board.created_utc > day_cutoffs[i + 1]
                                                        ).count()
                    } for i in range(len(day_cutoffs) - 1)
                   ]

    comment_stats = [{"date": time.strftime("%d %b %Y", time.gmtime(day_cutoffs[i + 1])),
                      "day_start": day_cutoffs[i + 1],
                      "comments": g.db.query(Comment).filter(Comment.created_utc < day_cutoffs[i],
                                                             Comment.created_utc > day_cutoffs[i + 1],
                                                             Comment.is_banned == False,
                                                             Comment.author_id != 1
                                                             ).count()
                      } for i in range(len(day_cutoffs) - 1)
                     ]

    vote_stats = [{"date": time.strftime("%d %b %Y", time.gmtime(day_cutoffs[i + 1])),
                   "day_start": day_cutoffs[i + 1],
                   "votes": g.db.query(Vote).join(Vote.user).filter(Vote.created_utc < day_cutoffs[i],
                                                                    Vote.created_utc > day_cutoffs[i + 1],
                                                                    User.is_banned == 0
                                                                    ).count()
                   } for i in range(len(day_cutoffs) - 1)
                  ]

    x = create_plot(sign_ups={'daily_signups': daily_signups},
                    guilds={'guild_stats': guild_stats},
                    posts={'post_stats': post_stats},
                    comments={'comment_stats': comment_stats},
                    votes={'vote_stats': vote_stats}
                    )

    final = {"user_stats": user_stats,
             "signup_data": daily_signups,
             "post_data": post_stats,
             "guild_data": guild_stats,
             "comment_data": comment_stats,
             "vote_data": vote_stats,
             "single_plot": f"https://i.ruqqus.com/{x[0]}",
             "multi_plot": f"https://i.ruqqus.com/{x[1]}"
             }

    return jsonify(final)


def create_plot(**kwargs):

    if not kwargs:
        return abort(400)

    # create multiple charts
    daily_signups = [d["signups"] for d in kwargs["sign_ups"]['daily_signups']]
    guild_stats = [d["members"] for d in kwargs["guilds"]['guild_stats']]
    post_stats = [d["posts"] for d in kwargs["posts"]['post_stats']]
    comment_stats = [d["comments"]
                     for d in kwargs["comments"]['comment_stats']]
    vote_stats = [d["votes"] for d in kwargs["votes"]['vote_stats']]
    daily_times = [d["day_start"] for d in kwargs["sign_ups"]['daily_signups']]

    multi_plots = multiple_plots(sign_ups=daily_signups,
                                 guilds=guild_stats,
                                 posts=post_stats,
                                 comments=comment_stats,
                                 votes=vote_stats,
                                 daily_times=daily_times)

    # create single chart
    plt.legend(loc='upper left', frameon=True)

    plt.xlabel("Time")
    plt.ylabel("Growth")

    plt.plot(daily_times, daily_signups, color='red', label="Users")
    plt.plot(daily_times, guild_stats, color='blue', label="Guilds")
    plt.plot(daily_times, post_stats, color='green', label="Posts")
    plt.plot(daily_times, comment_stats, color='gold', label="Comments")
    plt.plot(daily_times, vote_stats, color='silver', label="Votes")
    plt.grid()
    plt.legend()

    now = int(time.time())
    single_plot = "single_plot.png"
    plt.savefig(single_plot)

    aws.delete_file(single_plot)
    aws.upload_from_file(single_plot, single_plot)

    return [single_plot, multi_plots]


def multiple_plots(**kwargs):

    # create multiple charts
    signup_chart = plt.subplot2grid((10, 2), (0, 0), rowspan=4, colspan=2)
    guilds_chart = plt.subplot2grid((10, 2), (4, 0), rowspan=3, colspan=1)
    posts_chart = plt.subplot2grid((10, 2), (4, 1), rowspan=3, colspan=1)
    comments_chart = plt.subplot2grid((10, 2), (7, 0), rowspan=3, colspan=1)
    votes_chart = plt.subplot2grid((10, 2), (7, 1), rowspan=3, colspan=1)

    signup_chart.grid(), guilds_chart.grid(), posts_chart.grid(
    ), comments_chart.grid(), votes_chart.grid()

    signup_chart.plot(
        kwargs['daily_times'],
        kwargs['sign_ups'],
        color='red',
        label="Users")
    guilds_chart.plot(
        kwargs['daily_times'],
        kwargs['guilds'],
        color='blue',
        label="Guilds")
    posts_chart.plot(
        kwargs['daily_times'],
        kwargs['posts'],
        color='green',
        label="Posts")
    comments_chart.plot(
        kwargs['daily_times'],
        kwargs['comments'],
        color='gold',
        label="Comments")
    votes_chart.plot(
        kwargs['daily_times'],
        kwargs['votes'],
        color='silver',
        label="Votes")

    signup_chart.set_ylabel("Signups")
    guilds_chart.set_ylabel("Joins")
    posts_chart.set_ylabel("Posts")
    comments_chart.set_ylabel("Comments")
    votes_chart.set_ylabel("Votes")
    comments_chart.set_xlabel("Time (UTC)")
    votes_chart.set_xlabel("Time (UTC)")

    signup_chart.legend(loc='upper left', frameon=True)
    guilds_chart.legend(loc='upper left', frameon=True)
    posts_chart.legend(loc='upper left', frameon=True)
    comments_chart.legend(loc='upper left', frameon=True)
    votes_chart.legend(loc='upper left', frameon=True)

    now = int(time.time())
    name = "multiplot.png"

    plt.savefig(name)
    plt.clf()

    aws.delete_file(name)
    aws.upload_from_file(name, name)
    return name


@app.route("/admin/csam_nuke/<pid>", methods=["POST"])
@admin_level_required(4)
@validate_formkey
def admin_csam_nuke(pid, v):

    post = get_post(pid)

    post.is_banned = True
    post.ban_reason = "CSAM [1]"
    g.db.add(post)
    ma=ModAction(
        user_id=1,
        target_submission_id=post.id,
        board_id=post.board_id,
        kind="ban_post",
        note="CSAM detected"
        )

    user = post.author
    user.is_banned = v.id
    g.db.add(user)
    for alt in user.alts:
        alt.is_banned = v.id
        g.db.add(alt)

    if post.domain == "i.ruqqus.com":

        x = requests.get(url)
        # load image into PIL
        # take phash
        # add phash to db

        name = urlparse(post.url).path.lstrip('/')
        delete_file(name)  # this also dumps cloudflare


@app.route("/admin/dump_cache", methods=["POST"])
@admin_level_required(3)
@validate_formkey
def admin_dump_cache(v):

    cache.clear()

    return jsonify({"message": "Internal cache cleared."})



@app.route("/admin/ban_domain", methods=["POST"])
@admin_level_required(4)
@validate_formkey
def admin_ban_domain(v):

    domain=request.form.get("domain",'').lstrip().rstrip()

    if not domain:
        abort(400)

    reason=int(request.form.get("reason",0))
    if not reason:
        abort(400)

    d_query=domain.replace("_",r"\_")
    d=g.db.query(Domain).filter_by(domain=d_query).first()
    if d:
        d.can_submit=False
        d.can_comment=False
        d.reason=reason
    else:
        d=Domain(
            domain=domain,
            can_submit=False,
            can_comment=False,
            reason=reason,
            show_thumbnail=False,
            embed_function=None,
            embed_template=None
            )

    g.db.add(d)
    g.db.commit()
    return redirect(d.permalink)


@app.route("/admin/nuke_user", methods=["POST"])
@admin_level_required(4)
@validate_formkey
def admin_nuke_user(v):

    user=get_user(request.form.get("user"))

    note='admin action'
    if user.ban_reason:
        note+=f" | {user.ban_reason}"


    for post in g.db.query(Submission).filter_by(author_id=user.id).all():
        if post.is_banned:
            continue
            
        post.is_banned=True
        post.ban_reason=user.ban_reason
        g.db.add(post)

        ma=ModAction(
            kind="ban_post",
            user_id=v.id,
            target_submission_id=post.id,
            board_id=post.board_id,
            note=note
            )
        g.db.add(ma)

    for comment in g.db.query(Comment).filter_by(author_id=user.id).all():
        if comment.is_banned:
            continue

        comment.is_banned=True
        g.db.add(comment)

        ma=ModAction(
            kind="ban_comment",
            user_id=v.id,
            target_comment_id=comment.id,
            board_id=comment.post.board_id,
            note=note
            )
        g.db.add(ma)

    return redirect(user.permalink)

@app.route("/admin/demod_user", methods=["POST"])
@admin_level_required(4)
@validate_formkey
def admin_demod_user(v):

    user=get_user(request.form.get("user"))

    for mod in g.db.query(ModRelationship).filter_by(user_id=user.id, accepted=True):

        ma=ModAction(
            user_id=v.id,
            target_user_id=user.id,
            board_id=mod.board_id,
            kind="remove_mod",
            note="admin_action"
            )
        g.db.add(ma)

        g.db.delete(mod)

    g.db.commit()
    return redirect(user.permalink)

@app.route("/admin/signature", methods=["POST"])
@admin_level_required(5)
@validate_formkey
def admin_sig_generate(v):

    file=request.files["file"]
    return generate_hash(str(file.read()))

@app.route("/help/signature", methods=["POST"])
@auth_desired
def sig_validate(v):

    file=request.files["file"]

    sig=request.form.get("sig").lstrip().rstrip()

    valid=validate_hash(str(file.read()), sig)

    return render_template(
        "help/signature.html",
        v=v,
        success = valid,
        error = not valid
        )
