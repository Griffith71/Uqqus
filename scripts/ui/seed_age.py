"""Accounts for scripts/ui/age.spec.ts (password ui-Password-1): `ui_age_old` and `ui_age_child`, who haven't answered the age
question, `ui_age_rtl`, the same reading the site in Arabic, and `ui_age_admin` (level 3). Dev only. Run it before each full run:
a run answers the question, which a member can't take back, so this puts the answers back.

    PYTHONPATH=. python scripts/ui/seed_age.py            make them (and clear their answers)
    PYTHONPATH=. python scripts/ui/seed_age.py --remove   take them out again

The ids are high and fixed on purpose (see seed_chat_session.py). It refuses any database but a scratch one (`ui_preview`,
`ci_*` or `test*`). Run twice it adds nothing new.
"""
import os
import re
import sys
import time

DB_NAME = re.compile(r"^(ui_preview|ci_\w+|test\w*)$")
PASSWORD = "ui-Password-1"
ACCOUNTS = (("ui_age_old", 90720), ("ui_age_child", 90721), ("ui_age_rtl", 90722), ("ui_age_admin", 90723))
OLD_ENOUGH = 1500000000


def refuse_unless_scratch():
    name = os.environ.get("DATABASE_CONNECTION_POOL_URL", os.environ.get("DATABASE_URL", "")).rsplit("/", 1)[-1].split("?", 1)[0]
    if not DB_NAME.match(name):
        sys.exit(f"Refusing to seed database {name!r}: only ui_preview, ci_* or test*.")


def main():
    refuse_unless_scratch()
    from ruqqus.__main__ import app
    with app.test_request_context("/", environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        remove() if "--remove" in sys.argv else seed()


def seed():
    from ruqqus.__main__ import db_session
    from ruqqus.classes import User

    db = db_session()
    now = int(time.time())
    for name, user_id in ACCOUNTS:
        if not db.query(User).filter_by(id=user_id).first():
            db.add(User(id=user_id, username=name, original_username=name, password=PASSWORD, email=f"{name}@example.com",
                        creation_ip="127.0.0.1", tos_agreed_utc=now - 90 * 86400))
            db.flush()
        # in one statement: the database's own rule won't let a child account off the Child filter otherwise
        db.query(User).filter_by(id=user_id).update(
            {"created_utc": OLD_ENOUGH, "age_declared": None, "age_declared_utc": 0, "filter_level": 1, "all_shows_sensitive": False,
             "languages": "ar" if name == "ui_age_rtl" else "", "admin_level": 3 if name == "ui_age_admin" else 0},
            synchronize_session=False)
    db.query(User).filter_by(id=ACCOUNTS[-1][1]).update({"age_declared": 1, "age_declared_utc": now}, synchronize_session=False)
    db.commit()
    print("Seeded " + ", ".join(name for name, _ in ACCOUNTS) + ".")


def remove():
    from sqlalchemy import text
    from ruqqus.__main__ import db_session

    db = db_session()
    for _, user_id in ACCOUNTS:
        db.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})
    db.commit()
    print("Removed.")


if __name__ == "__main__":
    main()
