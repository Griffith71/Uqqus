from ruqqus.__main__ import db_session
from ruqqus.classes import *
from ruqqus.helpers.regions import compute_region_state
from ruqqus.helpers import picture_refresh
from ruqqus.helpers.embed import known_provider_embed

import time
import gevent

db = db_session()

# This script's own loop has no sleep between cycles, so the region/VPN
# recompute below (which scans each active user's full login history) is
# gated on its own wall-clock interval instead of running every tight-loop
# cycle - the multi-month settling windows it decides on don't need to be
# re-evaluated more often than this anyway.
REGION_RECOMPUTE_INTERVAL = 60 * 60 * 24  # 24h
CYCLE_PAUSE_SECONDS = 300
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

        
        if (cycle - 2) % 10 == 0:     # was `cycle-2 % 10`, which only ever ran on cycle 2
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

        # Mod actions older than 180 days used to be deleted here. Guild moderation logs are public records
        # (the owner's rule of 2026-10-09), so nothing is deleted until the owner decides how long they are kept.

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

        # quote card pictures whose address expires (Instagram, TikTok) are read again a day before they do
        try:
            print_(f"read again {picture_refresh.refresh(db, now, known_provider_embed)} expiring pictures")
        except Exception as e:
            db.rollback()
            print_(f"picture refresh failed: {e}")

        # Spaces whose time is up end even when nobody opens them (helpers/space_ending.py); one not recorded
        # disappears with its board, updates and comments
        try:
            from ruqqus.helpers import space_ending
            from ruqqus.__main__ import r as space_redis
            print_(f"ended {space_ending.end_due(db, space_redis, now)} Spaces whose time was up")
            db.commit()
        except Exception as e:
            db.rollback()
            print_(f"ending Spaces failed: {e}")

        # shared drafts: open proposals counted again (authors go inactive, proposals reach their end)
        try:
            from ruqqus.routes.collab import settle_open_proposals
            print_(f"decided {settle_open_proposals(db_session)} shared-draft proposals")
        except Exception as e:
            db.rollback()                 # the next step starts on a clean session (bug hunt)
            print_(f"shared-draft proposals failed: {e}")

        # guild events starting within the hour: everyone who answered Going or Interested is told, once
        try:
            from ruqqus.routes.guild_events import send_due_reminders
            print_(f"sent {send_due_reminders(db_session)} guild event reminders")
        except Exception as e:
            db.rollback()                 # the next step starts on a clean session (bug hunt)
            print_(f"guild event reminders failed: {e}")

        # guild channel rooms: out of them everyone who should not be there, announcement writers, old message text
        try:
            from ruqqus.helpers import guild_channel_sync, call_store
            from ruqqus.__main__ import r as redis_client
            import ruqqus.helpers.matrix_client as matrix
            roster_of = (lambda room: {e["user_id"] for e in call_store.roster(redis_client, room, now)}) if redis_client is not None else None
            print_(f"guild channels: {guild_channel_sync.run(db, now, matrix.kick_as_bot, matrix.set_writers, matrix.mxid_for, print_, roster_of)} taken out of rooms")
            db.commit()
        except Exception as e:
            db.rollback()
            print_(f"guild channels sync failed: {e}")

        # bot accounts' new posts into the announcement channels they feed (helpers/guild_channel_feeds.py)
        try:
            from ruqqus.helpers import guild_channel_feeds
            from ruqqus.helpers.word_filter_store import get_filter
            from ruqqus.__main__ import app as flask_app, r as feed_redis
            import ruqqus.helpers.matrix_client as feed_matrix
            import secrets as feed_secrets
            if feed_redis is not None:
                feed_sent = guild_channel_feeds.run(
                    db, feed_redis, now,
                    provision=lambda bot_id: guild_channel_feeds.provision_bot(db, bot_id, now, feed_matrix.provision_user),
                    put_in_room=feed_matrix.put_in_room, set_writers=feed_matrix.set_writers,
                    send=lambda room, mxid, content: feed_matrix.send_as(room, mxid, content, feed_secrets.token_hex(12)),
                    mxid_for=feed_matrix.mxid_for, secret=flask_app.config["SECRET_KEY"],
                    rate=lambda text_: get_filter(db).severity(text_),
                    link_of=lambda post_id, url: guild_channel_feeds.post_link(
                        flask_app.config["SERVER_NAME"], flask_app.config.get("FORCE_HTTPS"), post_id, url), log=print_)
                db.commit()
                print_(f"guild channel feeds: {feed_sent} items sent")
        except Exception as e:
            db.rollback()
            print_(f"guild channel feeds failed: {e}")

        # the counts of requests for pages, from Redis into site_traffic (helpers/traffic_store.py says what a failure does)
        try:
            from ruqqus.helpers import traffic_store
            from ruqqus.__main__ import r as traffic_redis
            if traffic_redis is not None:
                print_(f"traffic: {traffic_store.flush(db, traffic_redis, int(time.time()))} requests counted in")
        except Exception as e:
            db.rollback()
            print_(f"traffic flush failed: {e}")

        # shared-draft edits, deleted live updates and old view counts are not kept forever (helpers/retention.py)
        try:
            from ruqqus.helpers import retention
            print_(f"retention: {retention.prune(db, now)}")
        except Exception as e:
            db.rollback()
            print_(f"retention failed: {e}")

        # the site's database and Synapse's still agree, and new user ids start past every id that had a chat account
        # (helpers/chat_health.py; /admin/chat shows the last report)
        try:
            from ruqqus.helpers import chat_health
            from ruqqus.__main__ import r as chat_redis
            report = chat_health.run(db, chat_redis, fix=True)
            print_("chat health: " + ("ok" if report["ok"] else "; ".join(report["problems"])))
        except Exception as e:
            db.rollback()
            print_(f"chat health failed: {e}")

        # the chat sign-outs still waiting for Synapse (helpers/chat_signouts.py): each is run once its request has committed, and
        # retried for ever; this is the backstop that lands the ones Synapse was down for
        try:
            from ruqqus.helpers import chat_signouts
            import ruqqus.helpers.matrix_client as signout_matrix
            if chat_signouts.tables_ready(db):
                print_(f"chat sign-outs: {chat_signouts.run_due(db, signout_matrix)}")
            else:
                print_("chat sign-outs: migration 2026-12-20_chat_sign_outs.sql not applied")
        except Exception as e:
            db.rollback()
            print_(f"chat sign-outs failed: {e}")

        # the site's chat devices nobody has used for 90 days are signed out (never an account's most recently used chat device), at most
        # 200 a cycle: each is queued (its row revoked at once) and Synapse deletes it when the queue runs, at the next cycle
        try:
            from ruqqus.helpers import chat_signouts
            pruned = chat_signouts.prune_stale(db, int(time.time()))
            print_("stale chat devices: " + ("migration 2026-12-20_chat_sign_outs.sql not applied" if pruned is None else f"{pruned} queued"))
        except Exception as e:
            db.rollback()
            print_(f"stale chat devices failed: {e}")

        # the Log's paper trail (helpers/action_log.py): a timed suspension that ran out writes its own entry, and a guild mod-log row whose copy
        # failed when it was made is copied now, with a count of what could not be (shown on /admin and /admin/log)
        try:
            from ruqqus.helpers import action_log
            lapsed = action_log.lapse_due(db, int(time.time()))
            copied, not_copied = action_log.reconcile(db, int(time.time()))
            db.commit()
            print_(f"the Log: {lapsed} suspensions lapsed, {copied} mod-log rows copied, {not_copied} could not be")
        except Exception as e:
            db.rollback()
            print_(f"the Log's upkeep failed: {e}")

        # what deleted accounts wrote becomes anonymous (helpers/account_anonymise.py): the first batch ran with the deletion, this finishes the rest of
        # an account that wrote a lot, and any whose first batch failed; each account in its own transaction
        try:
            from ruqqus.helpers import account_anonymise
            if account_anonymise.tables_ready(db):
                finished, changed, failed = account_anonymise.run_due(db, int(time.time()))
                print_(f"deleted accounts' content: {finished} finished, {changed} items made anonymous, {failed} to try again")
            else:
                print_("deleted accounts' content: migration 2027-01-03_account_anonymise.sql not applied")
        except Exception as e:
            db.rollback()
            print_(f"deleted accounts' content failed: {e}")

        # disappearing messages that are due (helpers/chat_disappear.py): the bot deletes each (a redaction), and their chat files
        # stop being served
        try:
            import secrets as _secrets
            from ruqqus.helpers import chat_disappear
            import ruqqus.helpers.matrix_client as disappear_matrix
            done, failed = chat_disappear.run_due(db, int(time.time()), lambda room, event: disappear_matrix.redact_as_bot(
                room, event, _secrets.token_hex(12), "Disappearing message"))
            print_(f"disappearing messages: {done} deleted, {failed} to try again")
        except Exception as e:
            db.rollback()
            print_(f"disappearing messages failed: {e}")

        # a cycle rescans every live post of the last 180 days: once every few minutes is plenty
        time.sleep(CYCLE_PAUSE_SECONDS)




#gevent.spawn()

recompute()
