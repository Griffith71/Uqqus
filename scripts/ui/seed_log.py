"""Entries for scripts/ui/log.spec.ts: a few decisions in the scratch site's guilds, written the way the site writes them, with a mark in
`staff_note` ("ui-log-demo") so they can be taken out again. Dev only.

    PYTHONPATH=. python scripts/ui/seed_log.py            add them (nothing is added twice)
    PYTHONPATH=. python scripts/ui/seed_log.py --remove   take them out again, and the private copies they kept

It refuses any database but a scratch one (`ui_preview`, `ci_*` or `test*`). It needs the guilds and accounts scripts/seed_ui_fixtures.py makes.
"""
import os
import re
import sys

DB_NAME = re.compile(r"^(ui_preview|ci_\w+|test\w*)$")
MARK = "ui-log-demo"


def refuse_unless_scratch():
    name = os.environ.get("DATABASE_CONNECTION_POOL_URL", os.environ.get("DATABASE_URL", "")).rsplit("/", 1)[-1].split("?", 1)[0]
    if not DB_NAME.match(name):
        sys.exit(f"Refusing to seed database {name!r}: only ui_preview, ci_* or test*.")


def main():
    refuse_unless_scratch()
    from sqlalchemy import text
    from ruqqus.__main__ import app, db_session
    from ruqqus.helpers import action_log

    with app.test_request_context("/", environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        db = db_session()
        if "--remove" in sys.argv:
            db.execute(text("DELETE FROM log_snapshots WHERE entry_id IN (SELECT id FROM log_entries WHERE staff_note = :m)"), {"m": MARK})
            db.execute(text("DELETE FROM log_entries WHERE staff_note = :m"), {"m": MARK})
            db.commit()
            print("removed")
            return
        if db.execute(text("SELECT 1 FROM log_entries WHERE staff_note = :m LIMIT 1"), {"m": MARK}).first():
            print("already there")
            return
        ids = {r[0]: r[1] for r in db.execute(text("SELECT username, id FROM users WHERE username LIKE 'ui_%'"))}
        boards = {r[0]: r[1] for r in db.execute(text("SELECT name, id FROM boards WHERE name LIKE 'ui_%'"))}
        post = db.execute(text("SELECT id FROM submissions WHERE board_id = :b ORDER BY id DESC LIMIT 1"), {"b": 2}).scalar() or 1
        alice, bob, carol, newcomer = (ids[n] for n in ("ui_alice", "ui_bob", "ui_carol", "ui_newcomer"))
        photo, cooking = boards["ui_photography"], boards["ui_cooking"]
        item = type("Item", (), {"id": post, "author_id": carol, "board_id": photo, "title": "A title nobody may read in the log",
                                 "url": None, "body": "words", "body_html": "<p>The words of the hidden post.</p>"})()
        action_log.record(db, "hide_post_from_guild", alice, board_id=photo, post_id=post, rule="good_faith", reason="Advertising a shop",
                          staff_note=MARK, snapshot=action_log.snapshot_of(item, photo))
        action_log.record(db, "exile_user", bob, board_id=cooking, target_user_id=carol, rule="guild_rule",
                          reason="Repeated insults in the comments", staff_note=MARK)
        action_log.record(db, "ban_post", 1, board_id=photo, post_id=post, rule="illegal", reason="Copyright infringement",
                          staff_note=MARK, role=action_log.ADMIN)
        action_log.record(db, "ban_user", 1, target_user_id=newcomer, rule="evasion", reason="Posting the same link in many guilds",
                          staff_note=MARK, role=action_log.ADMIN)
        action_log.record(db, "update_settings", alice, board_id=photo, reason="", staff_note=MARK)
        # a private guild's decision: it must never reach /log
        private = db.execute(text("SELECT id FROM boards WHERE is_private LIMIT 1")).scalar()
        if private:
            action_log.record(db, "hide_post_from_guild", alice, board_id=private, post_id=post, reason="PRIVATE-GUILD-REASON", staff_note=MARK)
        db.commit()
        print("added")


if __name__ == "__main__":
    main()
