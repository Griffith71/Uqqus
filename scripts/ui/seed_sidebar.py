"""Fills a scratch database with a member whose left sidebar has long lists, for scripts/ui/sidebar.spec.ts and for looking at the
sidebar's five-row cap (templates/sidebar-left.html). Dev only. The fixtures' viewer has five guilds and no curations, so nothing
past the cap ever shows on them.

    PYTHONPATH=. python scripts/ui/seed_sidebar.py            make the account and its lists
    PYTHONPATH=. python scripts/ui/seed_sidebar.py --remove   take them out again

It makes `ui_sidebar` (password ui-Password-1), who has joined 40 guilds (the names mix capitals and lengths, so the name order is
not the order they were made in), leads 7 more of them, owns two curations (one with a very long name, one in Arabic), follows 8 of
`ui_sidebar_owner`'s and is based in a region. It refuses any database but a scratch one (`ui_preview`, `ci_*` or `test*`), because it
writes rows straight into the tables past every rule. Run twice it adds nothing new.
"""
import os
import re
import sys
import time

DB_NAME = re.compile(r"^(ui_preview|ci_\w+|test\w*)$")
PASSWORD = "ui-Password-1"
MEMBER, OWNER = "ui_sidebar", "ui_sidebar_owner"
GUILDS = 40
LED = 7
CURATION_NAMES = ["Alpha picks", "bravo reads", "Charlie", "delta daily", "Echo", "Foxtrot weekly", "golf", "Hotel notes"]
LONG_NAME = "A curation whose name is much too long to fit on one line of the sidebar"
ARABIC_NAME = "مختارات الأخبار اليومية"


def refuse_unless_scratch():
    name = os.environ.get("DATABASE_URL", "").rsplit("/", 1)[-1].split("?", 1)[0]
    if not DB_NAME.match(name):
        sys.exit(f"Refusing to seed database {name!r}: only ui_preview, ci_* or test* (it writes past every rule).")


def guild_names():
    return [f"ui_sb_{('Zz', 'aa', 'Mm', 'bb')[i % 4]}{i:02d}{'_a_rather_long_guild_name' if i % 9 == 0 else ''}" for i in range(GUILDS + LED)]


def main():
    refuse_unless_scratch()
    from ruqqus.__main__ import app
    with app.test_request_context("/", environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        remove() if "--remove" in sys.argv else seed()


def user(db, name, now):
    from ruqqus.classes import User
    row = db.query(User).filter_by(username=name).first()
    if not row:
        row = User(age_declared=1, username=name, original_username=name, password=PASSWORD, email=f"{name}@example.com", created_utc=now - 90 * 86400,
                   creation_ip="127.0.0.1", tos_agreed_utc=now - 90 * 86400)
        db.add(row)
        db.flush()
    return row


def seed():
    from ruqqus.__main__ import db_session
    from ruqqus.classes import Board, Curation, CurationFollow, ModRelationship, Subscription
    from ruqqus.routes.front import REGION_CENTROIDS

    db = db_session()
    now = int(time.time())
    member, owner = user(db, MEMBER, now), user(db, OWNER, now)
    member.display_region = sorted(REGION_CENTROIDS)[0]              # a home region: it must not turn the Regional ring on
    names = guild_names()
    for name in names:
        if not db.query(Board).filter_by(name=name).first():
            db.add(Board(name=name, description="a guild for the sidebar", description_html="<p>a guild for the sidebar</p>",
                         creator_id=owner.id, subcat_id=0))
    db.flush()
    for i, name in enumerate(names):
        board = db.query(Board).filter_by(name=name).first()
        if i < GUILDS and not db.query(Subscription).filter_by(user_id=member.id, board_id=board.id).first():
            db.add(Subscription(user_id=member.id, board_id=board.id, is_active=True, created_utc=now - i))
        if i >= GUILDS and not db.query(ModRelationship).filter_by(user_id=member.id, board_id=board.id).first():
            db.add(ModRelationship(user_id=member.id, board_id=board.id, accepted=True, invite_rescinded=False, perm_full=True))
    for i, name in enumerate((LONG_NAME, ARABIC_NAME)):
        if not db.query(Curation).filter_by(owner_id=member.id, name=name).first():
            db.add(Curation(owner_id=member.id, name=name, slug=f"uisbmine{i}", is_private=bool(i), created_utc=now - i))
    for i, name in enumerate(CURATION_NAMES):
        if not db.query(Curation).filter_by(owner_id=owner.id, name=name).first():
            db.add(Curation(owner_id=owner.id, name=name, slug=f"uisbtheirs{i}", is_private=False, created_utc=now - i))
    db.flush()
    for curation in db.query(Curation).filter_by(owner_id=owner.id).all():
        if not db.query(CurationFollow).filter_by(user_id=member.id, curation_id=curation.id).first():
            db.add(CurationFollow(user_id=member.id, curation_id=curation.id, created_utc=now))
    db.commit()
    print(f"Made {MEMBER} (password {PASSWORD}): {GUILDS} guilds joined, {LED} led, 2 curations owned, {len(CURATION_NAMES)} followed.")


def remove():
    from sqlalchemy import text
    from ruqqus.__main__ import db_session

    db = db_session()
    ids = [r[0] for r in db.execute(text("SELECT id FROM users WHERE username IN (:a, :b)"), {"a": MEMBER, "b": OWNER})]
    if ids:
        db.execute(text("DELETE FROM curation_follows WHERE user_id = ANY(:ids) OR curation_id IN (SELECT id FROM curations WHERE owner_id = ANY(:ids))"), {"ids": ids})
        db.execute(text("DELETE FROM curations WHERE owner_id = ANY(:ids)"), {"ids": ids})
        db.execute(text("DELETE FROM subscriptions WHERE user_id = ANY(:ids)"), {"ids": ids})
        db.execute(text("DELETE FROM mods WHERE user_id = ANY(:ids)"), {"ids": ids})
    db.execute(text("DELETE FROM boards WHERE name LIKE 'ui\\_sb\\_%'"))
    db.execute(text("DELETE FROM users WHERE username IN (:a, :b)"), {"a": MEMBER, "b": OWNER})
    db.commit()
    print("Removed the sidebar fixtures.")


if __name__ == "__main__":
    main()
