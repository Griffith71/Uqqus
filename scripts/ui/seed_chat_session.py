"""Accounts for scripts/ui/chat_session.spec.ts (password ui-Password-1): `ui_chat_a` and `ui_chat_b`, who follow each
other, so a chat between them lands in both Inboxes and can be called, and `ui_chat_gone`, whom the spec deletes (run this
again to make it afresh: it never opens chat, so its id can be used again). Dev only.

    PYTHONPATH=. python scripts/ui/seed_chat_session.py            make them
    PYTHONPATH=. python scripts/ui/seed_chat_session.py --remove   take the rows out again (Synapse keeps its accounts)

The ids are high and fixed on purpose: the dev Synapse keeps the `@ruqqus_<id>` accounts of deleted users and chat refuses
to adopt them, so an id that was ever used by another account can't chat. It refuses any database but a scratch one
(`ui_preview`, `ci_*` or `test*`). Run twice it adds nothing new.
"""
import os
import re
import sys
import time

DB_NAME = re.compile(r"^(ui_preview|ci_\w+|test\w*)$")
PASSWORD = "ui-Password-1"
ACCOUNTS = {"ui_chat_a": 90703, "ui_chat_b": 90704, "ui_chat_gone": 90705}


def refuse_unless_scratch():
    name = os.environ.get("DATABASE_URL", "").rsplit("/", 1)[-1].split("?", 1)[0]
    if not DB_NAME.match(name):
        sys.exit(f"Refusing to seed database {name!r}: only ui_preview, ci_* or test*.")


def main():
    refuse_unless_scratch()
    from ruqqus.__main__ import app
    with app.test_request_context("/", environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        remove() if "--remove" in sys.argv else seed()


def seed():
    from sqlalchemy import text
    from ruqqus.__main__ import db_session
    from ruqqus.classes import Follow, User

    db = db_session()
    now = int(time.time())
    gone = db.query(User).filter_by(id=ACCOUNTS["ui_chat_gone"]).first()
    if gone and gone.is_deleted:
        db.delete(gone)
        db.flush()
    for name, user_id in ACCOUNTS.items():
        if not db.query(User).filter_by(id=user_id).first():
            db.add(User(age_declared=1, id=user_id, username=name, original_username=name, password=PASSWORD, email=f"{name}@example.com",
                        created_utc=now - 90 * 86400, creation_ip="127.0.0.1", tos_agreed_utc=now - 90 * 86400))
            db.flush()
    # a seed starts clean: a Space made by each run counts against the 5 a day, and the spam guard may have suspended them
    db.query(User).filter(User.id.in_(list(ACCOUNTS.values()))).update({"is_banned": 0, "unban_utc": 0, "ban_reason": None}, synchronize_session=False)
    db.execute(text("UPDATE spaces SET created_utc = created_utc - 172800 WHERE host_id = ANY(:ids)"), {"ids": list(ACCOUNTS.values())})
    a, b = ACCOUNTS["ui_chat_a"], ACCOUNTS["ui_chat_b"]
    for follower, target in ((a, b), (b, a)):
        if not db.query(Follow).filter_by(user_id=follower, target_id=target).first():
            db.add(Follow(user_id=follower, target_id=target, created_utc=now))
    db.commit()
    print("Seeded " + ", ".join(ACCOUNTS) + ".")


def remove():
    from sqlalchemy import text
    from ruqqus.__main__ import db_session

    db = db_session()
    ids = {"ids": list(ACCOUNTS.values())}
    db.execute(text("DELETE FROM follows WHERE user_id = ANY(:ids) OR target_id = ANY(:ids)"), ids)
    db.execute(text("DELETE FROM chat_identities WHERE user_id = ANY(:ids)"), ids)
    db.execute(text("DELETE FROM users WHERE id = ANY(:ids)"), ids)
    db.commit()
    print("Removed.")


if __name__ == "__main__":
    main()
