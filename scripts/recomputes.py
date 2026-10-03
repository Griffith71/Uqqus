from ruqqus.__main__ import db_session
from ruqqus.classes import *
from ruqqus.helpers.regions import compute_region_state

import time
import gevent
import daemon

db = db_session()

# This script's own loop has no sleep between cycles, so the region/VPN
# recompute below (which scans each active user's full login history) is
# gated on its own wall-clock interval instead of running every tight-loop
# cycle - the multi-month settling windows it decides on don't need to be
# re-evaluated more often than this anyway.
REGION_RECOMPUTE_INTERVAL = 60 * 60 * 24  # 24h
last_region_recompute_utc = 0

def print_(x):

    try:
        print(x)
    except OSError:
        pass


def recompute():

    global last_region_recompute_utc

    cycle=0

    while True:

        cycle +=1
        print_(f"cycle {cycle}")

        #purge deleted content older than 90 days

        now = int(time.time())
        cutoff_purge = now - (60 * 60 * 24 * 90)
        print_("beginning post purge")

        purge_posts = db.query(
            Submission
            ).filter(
            Submission.deleted_utc>0,
            Submission.deleted_utc < cutoff_purge, 
            Submission.purged_utc==0
            ).all()

        x=0
        for p in purge_posts:
            x += 1
            p.submission_aux.body = ""
            p.submission_aux.body_html = ""
            p.submission_aux.url = ""
            p.submission_aux.embed_url = ""
            p.submission_aux.meta_text=""
            p.submission_aux.meta_description=""
            p.creation_ip = ""
            p.creation_region=""
            p.purged_utc=int(time.time())
            p.is_pinned = False
            p.is_stickied = False
            p.domain_ref=None
            db.add(p)
            db.add(p.submission_aux)

            if not x % 100:
                print_(f"purged {x} posts")
                db.commit()

        db.commit()
        print_(f"Done with post purge. Purged {x} posts")

        x = 0
        print_("beginning comment purge")
        purge_comments = db.query(
            Comment
            ).filter(
            Comment.deleted_utc>0,
            Comment.deleted_utc < cutoff_purge, 
            Comment.purged_utc==0,
            Comment.author_id != 1
            ).all()

        for c in purge_comments:
            x+=1
            c.comment_aux.body = ""
            c.comment_aux.body_html = ""
            c.creation_ip = ""
            c.creation_region=""
            c.purged_utc=int(time.time())
            c.is_pinned = False
            db.add(c)
            db.add(c.comment_aux)

            if not x % 100:
                print_(f"purged {x} comments")
                db.commit()

        db.commit()
        print_(f"Done with comment purge. Purged {x} comments")

        
        if not cycle-2 % 10:
            print_("beginning guild trend recompute")
            boards = db.query(Board).options(
                lazyload('*')
            ).filter_by(is_banned=False).filter(
                    or_(
                        Board.id.in_(
                            db.query(Board.id).order_by(Board.rank_trending.desc()).limit(1000)
                        ),
                        Board.id.in_(
                               db.query(Board.id).order_by(Board.stored_subscriber_count.desc()).limit(1000)
                        )
                    )
            )
            #if cycle % 10:
            #    print_("top 1000 boards only")
            #    boards = boards.limit(1000)
            #else:
            #    print_("all boards")
            board_count=boards.count()
            print_(f"{board_count} boards to re-rank")
            i = 0
            for board in boards.all():
                i += 1
                board.rank_trending = board.trending_rank
                board.stored_subscriber_count = board.subscriber_count
                db.add(board)

                if not i % 100:
                    print(f"re-ranked {i} boards")
                    db.commit()

            print_(f"Re-ranked {i} boards")
            db.commit()


        cutoff = now - (60 * 60 * 24 * 180)

        print_("Beginning post recompute")
        page = 1
        post_count = 0
        posts_exist=True
        while posts_exist:
            posts = db.query(Submission
                ).options(
                    lazyload('*')
                ).filter(
                    Submission.is_banned==False,
                    Submission.deleted_utc==0,
                    Submission.created_utc > cutoff
                ).join(
                    Submission.board
                ).filter(
                    Board.is_banned==False
                ).order_by(
                    Submission.id.asc()
                ).offset(
                    100 * (page - 1)
                ).limit(100).all()


            posts_exist=False
            for post in posts:
                posts_exist=True
                post_count += 1

                post.upvotes = post.ups
                post.downvotes = post.downs
                db.add(post)
                db.flush()

                post.score_hot = post.rank_hot
                post.score_disputed = post.rank_fiery
                # post.score_top=post.score
                post.score_activity = post.rank_activity
                post.score_best = post.rank_best

                db.add(post)

            db.commit()

            page += 1
            print_(f"re-scored {post_count} posts")

        print_(f"Done with posts. Rescored {post_count} posts")

        db.commit()

        print_("Deleting old mod actions")

        actions=db.query(ModAction).filter(ModAction.created_utc<int(time.time())-60*60*24*180)
        count=actions.count()
        actions.delete()
        db.commit()

        print_(f"deleted {count} old mod actions")

        if now - last_region_recompute_utc >= REGION_RECOMPUTE_INTERVAL:
            print_("beginning region/VPN-suspicion recompute")

            region_cutoff = now - REGION_RECOMPUTE_INTERVAL
            user_ids = [
                row[0] for row in db.query(LoginEvent.user_id).filter(
                    LoginEvent.created_utc >= region_cutoff
                ).distinct().all()
            ]
            print_(f"{len(user_ids)} users with recent logins to recompute")

            updated = 0
            for user_id in user_ids:
                user = db.query(User).filter_by(id=user_id).first()
                if not user:
                    continue

                state = compute_region_state(user, db=db)
                changed = False

                if state["suspicious"] != user.region_suspicion_flag:
                    user.region_suspicion_flag = state["suspicious"]
                    changed = True

                if state["migrate_to"] and state["migrate_to"] != user.display_region:
                    user.display_region = state["migrate_to"]
                    user.region_settled_utc = now
                    changed = True

                if changed:
                    db.add(user)
                    updated += 1

                    if not updated % 100:
                        db.commit()

            db.commit()
            last_region_recompute_utc = now
            print_(f"Updated region/suspicion state for {updated} users")



#with daemon.DaemonContext():
#    recompute()

#gevent.spawn()

recompute()
