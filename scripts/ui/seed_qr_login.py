"""Accounts for scripts/ui/qr_login.spec.ts (password ui-Password-1): `ui_qr_member`, who approves codes and is signed in through them,
and `ui_qr_second`, who hosts a Space. Dev only. Run it before each run: it forgets their sign-ins, and clears what a run of the spec
uses up, the rate limits and heat of the QR routes.

    PYTHONPATH=. python scripts/ui/seed_qr_login.py            make it
    PYTHONPATH=. python scripts/ui/seed_qr_login.py --remove   take what a run made out of the feeds (the accounts stay)

The accounts stay on purpose: a post needs its author, and the dev Synapse keeps `@ruqqus_<id>`, which chat refuses to adopt for a new row.

The id is high and fixed on purpose (see seed_chat_session.py). It refuses any database but a scratch one (`ui_preview`, `ci_*` or
`test*`). Run twice it adds nothing new.
"""
import os
import re
import sys
import time

DB_NAME = re.compile(r"^(ui_preview|ci_\w+|test\w*)$")
PASSWORD = "ui-Password-1"
ACCOUNTS = (("ui_qr_member", 90840), ("ui_qr_second", 90841))
OLD_ENOUGH = 1500000000


def refuse_unless_scratch():
    name = os.environ.get("DATABASE_URL", "").rsplit("/", 1)[-1].split("?", 1)[0]
    if not DB_NAME.match(name):
        sys.exit(f"Refusing to seed database {name!r}: only ui_preview, ci_* or test*.")


def main():
    refuse_unless_scratch()
    from ruqqus.__main__ import app
    with app.test_request_context("/", environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        remove() if "--remove" in sys.argv else seed()


def forget_limits():
    from ruqqus.__main__ import r
    for pattern in ("*LIMITER*login*", "throttle:qr*", "throttle:login*", "qr:*"):
        for key in r.scan_iter(pattern):
            r.delete(key)


def seed():
    from sqlalchemy import text
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
            {"created_utc": OLD_ENOUGH, "age_declared": 1, "age_declared_utc": now, "qr_login_off": False}, synchronize_session=False)
        db.execute(text("DELETE FROM login_events WHERE user_id = :u"), {"u": user_id})
        db.execute(text("DELETE FROM notifications WHERE user_id = :u"), {"u": user_id})
        db.execute(text("UPDATE spaces SET created_utc = created_utc - 172800 WHERE host_id = :u"), {"u": user_id})
    db.commit()
    forget_limits()
    print("Seeded " + ", ".join(name for name, _ in ACCOUNTS) + ".")


def remove():
    from sqlalchemy import text
    from ruqqus.__main__ import db_session

    db = db_session()
    ids = {"ids": [user_id for _, user_id in ACCOUNTS], "now": int(time.time())}
    db.execute(text("UPDATE submissions SET deleted_utc = :now WHERE author_id = ANY(:ids) AND deleted_utc = 0"), ids)
    db.execute(text("DELETE FROM login_events WHERE user_id = ANY(:ids)"), ids)
    db.execute(text("DELETE FROM notifications WHERE user_id = ANY(:ids)"), ids)
    db.commit()
    forget_limits()
    print("Removed what a run made; the accounts stay.")


if __name__ == "__main__":
    main()
