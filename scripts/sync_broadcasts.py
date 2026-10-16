"""Keeps broadcast channels for circles to the people in them.

Runs forever as its own supervisord program ([program:ruqqusbroadcasts]); run it once by hand with

    PYTHONPATH=. python scripts/sync_broadcasts.py --once

Every hour, each member of a channel for circles (helpers/broadcasts.py) who is no longer in any of its circles, or
who is now in a block with its owner either way, is taken out: out of its Matrix room (the bot's kick) and its
member list. Nobody is told why (that would say which circle they left, or who blocked whom). A public channel is
left alone: anyone may be in it, except across a block, which a join already refuses.
"""
import sys
import time
import traceback

from flask import g

from ruqqus.__main__ import app, db_session
from ruqqus.classes import User
import ruqqus.helpers.matrix_client as matrix
from ruqqus.helpers import broadcast_store, broadcasts, circle_guard

from sqlalchemy import text

POLL_SECONDS = 3600


def log(message):
    print(f"[broadcasts] {message}", flush=True)


def tick():
    removed = 0
    with app.test_request_context():
        g.db = db_session()
        try:
            channels = g.db.execute(text("""SELECT id FROM broadcasts WHERE deleted_utc = 0 AND audience = :aud"""),
                                    {"aud": broadcasts.CIRCLES}).fetchall()
            for (bid,) in channels:
                channel = broadcast_store.get(g.db, bid)
                owner = g.db.query(User).filter_by(id=channel[1]).first()
                circle_ids = broadcast_store.circle_ids(g.db, bid)
                for uid in broadcast_store.member_ids(g.db, bid):
                    if uid == channel[1]:
                        continue
                    member = g.db.query(User).filter_by(id=uid).first()
                    keep = (member is not None and owner is not None and not member.any_block_exists(owner)
                            and circle_guard.may_see(g.db, broadcasts.CIRCLES, channel[1], member, circle_ids=circle_ids))
                    if keep:
                        continue
                    try:
                        matrix.kick_as_bot(channel[5], matrix.mxid_for(uid))
                    except matrix.MatrixError:
                        continue                 # tried again next hour
                    broadcast_store.remove_member(g.db, bid, uid)
                    removed += 1
                g.db.commit()
        except Exception:
            g.db.rollback()
            raise
        finally:
            db_session.remove()
    return {"removed": removed}


def main():
    if "--once" in sys.argv:
        print(tick())
        return
    log(f"started, checking channels for circles every {POLL_SECONDS}s")
    while True:
        try:
            result = tick()
            if result["removed"]:
                log(f"{result}")
        except Exception:
            traceback.print_exc()
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
