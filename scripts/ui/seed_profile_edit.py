"""An account for scripts/ui/profile_edit.spec.ts (password ui-Password-1) that uploads a profile picture and a banner.
No Rep or age is asked for them any more (classes/user.py can_upload_avatar), so `ui_viewer` could too; this one stays as the
account the upload cases change and put back. Dev only.

    PYTHONPATH=. python scripts/ui/seed_profile_edit.py            make it (and put its profile back as it was)
    PYTHONPATH=. python scripts/ui/seed_profile_edit.py --remove   take it out again

The id is high and fixed on purpose (the dev Synapse keeps the accounts of deleted users, see seed_chat_session.py). It
refuses any database but a scratch one (`ui_preview`, `ci_*` or `test*`). Run twice it adds nothing new.
"""
import os
import re
import sys
import time

DB_NAME = re.compile(r"^(ui_preview|ci_\w+|test\w*)$")
PASSWORD = "ui-Password-1"
NAME, USER_ID = "ui_profile_edit", 90710
OLD_ENOUGH = 1500000000          # before 1592974538, when accounts got avatars and banners without the Rep


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
    account = db.query(User).filter_by(id=USER_ID).first()
    if not account:
        account = User(age_declared=1, id=USER_ID, username=NAME, original_username=NAME, password=PASSWORD, email=f"{NAME}@example.com",
                       created_utc=OLD_ENOUGH, creation_ip="127.0.0.1", tos_agreed_utc=now - 90 * 86400)
        db.add(account)
        db.flush()
    # a seed starts clean: no text, no pictures, not suspended. The model stamps "now" on a new account whatever it is given,
    # so the age that gives the right to pictures is written here
    db.query(User).filter_by(id=USER_ID).update(
        {"created_utc": OLD_ENOUGH, "is_banned": 0, "unban_utc": 0, "ban_reason": None, "display_name": "", "bio": "", "bio_html": "",
         "profile_job": "", "profile_location": "", "profile_website": "", "has_profile": False, "has_banner": False},
        synchronize_session=False)
    db.commit()
    print(f"Seeded {NAME}.")


def remove():
    from sqlalchemy import text
    from ruqqus.__main__ import db_session

    db = db_session()
    db.execute(text("DELETE FROM chat_identities WHERE user_id = :id"), {"id": USER_ID})
    db.execute(text("DELETE FROM users WHERE id = :id"), {"id": USER_ID})
    db.commit()
    print("Removed.")


if __name__ == "__main__":
    main()
