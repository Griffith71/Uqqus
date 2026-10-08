"""Renews Circle subscriptions.

Runs forever as its own supervisord program ([program:ruqquscircles]); run it once by hand with

    PYTHONPATH=. python scripts/renew_circles.py --once

Every pass settles each subscriber whose paid days have run out (ruqqus/helpers/circle_store.py `renew_due`): renewed
from their coins at the price they signed up for, or ended. Access never depends on this job: a subscriber's access
stops at the date they paid to even if the job is late or down (helpers/circles.py `tier_of`), so the job only moves
the coins and tidies the rows. A member whose coins did not cover the renewal is told; one who cancelled, or whose
subscription ended for any other reason (a block among them), is not, so a notice never reveals why.
"""
import sys
import time
import traceback

from flask import g

from ruqqus.__main__ import app, db_session
from ruqqus.classes import Board, User
from ruqqus.helpers import circle_store
from ruqqus.helpers.alerts import send_notification

POLL_SECONDS = 3600


def log(message):
    print(f"[circles] {message}", flush=True)


def tick(now=None):
    """One pass. Returns {event: count} for renewed, lapsed and ended subscriptions."""
    db = db_session()
    try:
        events = circle_store.renew_due(db, now)
    except Exception:
        db.rollback()
        raise
    finally:
        db_session.remove()

    counts = {}
    for event, *_ in events:
        counts[event] = counts.get(event, 0) + 1

    lapsed = [(payer, owner, coins, board) for event, payer, owner, coins, board in events if event == "lapsed"]
    if lapsed:
        with app.test_request_context():
            g.db = db_session()
            try:
                for payer_id, owner_id, coins, board_id in lapsed:
                    payer = g.db.query(User).filter_by(id=payer_id).first()
                    owner = g.db.query(User).filter_by(id=owner_id).first()
                    if payer is None or owner is None:
                        continue
                    board = g.db.query(Board).filter_by(id=board_id).first() if board_id else None
                    if board is not None:
                        send_notification(
                            payer,
                            f"Your membership of [+{board.name}](/+{board.name}) ended: "
                            f"you didn't have the {coins} coins it renews for. You can join again from its page.")
                        continue
                    send_notification(
                        payer,
                        f"Your subscription to [@{owner.username}](/@{owner.username})'s Circle ended: "
                        f"you didn't have the {coins} coins it renews for. You can subscribe again from their profile.")
            finally:
                db_session.remove()
    return counts


def main():
    if "--once" in sys.argv:
        print(tick())
        return
    log(f"started, settling subscriptions every {POLL_SECONDS}s")
    while True:
        try:
            counts = tick()
            if counts:
                log(f"settled {counts}")
        except Exception:
            traceback.print_exc()
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
