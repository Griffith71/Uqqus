"""Fills a scratch database so a guild's "Similar guilds" box has something to show beyond the same-topic guilds
(templates/partials/similar_guilds_box.html, helpers/similar_guilds_store.py). The fixtures have 5 members in all, and the box
needs at least 5 shared members and 25 members in both guilds before it names a guild from its members, so nothing but the same
topic ever shows on them. Dev only. The guilds are the ones scripts/seed_ui_fixtures.py made, so run that first.

    PYTHONPATH=. python scripts/ui/seed_similar_guilds.py            make the accounts and their joins
    PYTHONPATH=. python scripts/ui/seed_similar_guilds.py --remove   take them out again

It makes 30 accounts, `ui_sim_00` to `ui_sim_29` (password ui-Password-1), which join:
  * +ui_photography: all 30        (a guild above the 25-member floor)
  * +ui_cooking:     28 of them    (above the floor and 28 members in common: shown on +ui_photography's page, and the reverse)
  * +ui_midnight:    10 of them    (10 in common, but it has too few members: never shown, which is the floor working)
It refuses any database but a scratch one (`ui_preview`, `ci_*` or `test*`), because it writes rows straight into the tables past
every rule. Run twice it adds nothing new. The guilds' stored member counts are not touched (the box counts live).
"""
import os
import re
import sys
import time

DB_NAME = re.compile(r"^(ui_preview|ci_\w+|test\w*)$")
PASSWORD = "ui-Password-1"
COUNT = 30
JOINS = (("ui_photography", 30), ("ui_cooking", 28), ("ui_midnight", 10))     # guild, how many of the accounts join it


def refuse_unless_scratch():
    name = os.environ.get("DATABASE_URL", "").rsplit("/", 1)[-1].split("?", 1)[0]
    if not DB_NAME.match(name):
        sys.exit(f"Refusing to seed database {name!r}: only ui_preview, ci_* or test* (it writes past every rule).")


def names():
    return [f"ui_sim_{i:02d}" for i in range(COUNT)]


def main():
    refuse_unless_scratch()
    from ruqqus.__main__ import app
    with app.test_request_context("/", environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        remove() if "--remove" in sys.argv else seed()


def seed():
    from ruqqus.__main__ import db_session
    from ruqqus.classes import Board, Subscription, User

    db = db_session()
    now = int(time.time())
    made = 0
    users = []
    for name in names():
        user = db.query(User).filter_by(username=name).first()
        if not user:
            user = User(username=name, original_username=name, password=PASSWORD, email=f"{name}@example.com",
                        created_utc=now - 90 * 86400, creation_ip="127.0.0.1", tos_agreed_utc=now - 90 * 86400)
            db.add(user)
            made += 1
        users.append(user)
    db.flush()
    joined = 0
    for guild, how_many in JOINS:
        board = db.query(Board).filter_by(name=guild).first()
        if not board:
            print(f"There is no +{guild}: run scripts/seed_ui_fixtures.py first.")
            continue
        for user in users[:how_many]:
            if not db.query(Subscription).filter_by(user_id=user.id, board_id=board.id).first():
                db.add(Subscription(user_id=user.id, board_id=board.id, is_active=True))
                joined += 1
    db.commit()
    print(f"Made {made} accounts and {joined} joins. Look at /+ui_photography and /+ui_cooking (the floors keep +ui_midnight out).")


def remove():
    from sqlalchemy import text
    from ruqqus.__main__ import db_session

    db = db_session()
    ids = [r[0] for r in db.execute(text("SELECT id FROM users WHERE username LIKE 'ui\\_sim\\_%'"))]
    for user_id in ids:
        db.execute(text("DELETE FROM subscriptions WHERE user_id = :u"), {"u": user_id})
        db.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})
    db.commit()
    print(f"Removed {len(ids)} accounts and their joins.")


if __name__ == "__main__":
    main()
