"""Accounts for scripts/ui/qr_settings.spec.ts (password ui-Password-1): `ui_qr_settings`, and `ui_qr_rtl`, the same reading the site
in Arabic. Dev only. Run it before each run: it puts their QR sign-in setting back on, and forgets their sign-ins and notices.

    PYTHONPATH=. python scripts/ui/seed_qr_settings.py            make them (and put the setting back)
    PYTHONPATH=. python scripts/ui/seed_qr_settings.py --remove   take them out again

The ids are high and fixed on purpose (see seed_chat_session.py). It refuses any database but a scratch one (`ui_preview`,
`ci_*` or `test*`). Run twice it adds nothing new.
"""
import os
import re
import sys
import time

DB_NAME = re.compile(r"^(ui_preview|ci_\w+|test\w*)$")
PASSWORD = "ui-Password-1"
ACCOUNTS = (("ui_qr_settings", 90810), ("ui_qr_rtl", 90811))
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


def forget(db, user_id):
    from sqlalchemy import text
    db.execute(text("DELETE FROM login_events WHERE user_id = :u"), {"u": user_id})
    db.execute(text("DELETE FROM notifications WHERE user_id = :u"), {"u": user_id})


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
        db.query(User).filter_by(id=user_id).update(
            {"created_utc": OLD_ENOUGH, "age_declared": 1, "age_declared_utc": now, "qr_login_off": False,
             "languages": "ar" if name == "ui_qr_rtl" else ""}, synchronize_session=False)
        forget(db, user_id)
    db.commit()
    print("Seeded " + ", ".join(name for name, _ in ACCOUNTS) + ".")


def remove():
    from sqlalchemy import text
    from ruqqus.__main__ import db_session

    db = db_session()
    for _, user_id in ACCOUNTS:
        forget(db, user_id)
        db.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})
    db.commit()
    print("Removed.")


if __name__ == "__main__":
    main()
