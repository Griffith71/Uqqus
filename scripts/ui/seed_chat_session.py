"""Accounts for scripts/ui/chat_session.spec.ts (password ui-Password-1): `ui_chat_a` and `ui_chat_b`, who follow each
other, so a chat between them lands in both Inboxes and can be called, `ui_chat_c`, whose password the spec changes and puts
back (this puts it back too, should a run stop half way), `ui_chat_d`, whose password the spec resets by email (the chat plan's
spec 8: this puts the password back; the spec keeps the recovery code its chat showed when it was first set up, in
`scripts/ui/.auth/ui_chat_d.code`, and unlocks with it whatever password an earlier run left the key locked with. Its id is 90708:
90707 was spent on a Start over this chat server refuses half way, see CLAUDE.md "Chat encryption"), and `ui_chat_gone`, whom the spec deletes (run this again to make it afresh: it never opens chat, so its id can be
used again), and `ui_chat_e`, whom the spec makes start chat over after a reset by email (its chat identity is replaced every run;
the Start over the site makes for it, routes/chat_key_reset_pages.py), and `ui_chat_f`, whose chat the spec rescues from the browser's
kept store after a reset by email (rescue.js), and `ui_chat_h`, whom chat_history.spec.ts follows from browser to browser (it changes its
password through Settings and back, and starts its chat over if a run stopped between the two). `ui_chat_b` also follows `ui_chat_c`, `ui_chat_d`, `ui_chat_e`, `ui_chat_f` and `ui_chat_h` both ways, so a group `ui_chat_b` starts with either has them in it
at once (the harvest specs need a room no older device of theirs ever had a key for). Each run also resets what a run uses up: every password, the sign-in
limits, and the accounts' chat devices: each account's devices on the chat server are signed out through the durable queue
(`chat_signouts`, reason `legacy`, which a new device's unlock box says nothing about), then the rows on the site's side go
(`chat_site_devices`, `chat_device_signouts`), so a run starts with no device the site or Synapse knows (a browser context of a run
is new anyway; deleting the rows alone left every earlier run's devices on Synapse with no row). The accounts' `ended_sign_ins`
rows (one per cookie a run ended: the spec signs each context in with a cookie of its own) go too, so they don't pile up. The v2
index fixtures the spec needs (`uqqus_chat_devices` entries, a wrong `ed`, a stale store) are written in the page by the spec
itself (chat_session.spec.ts), not here. Dev only.

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
ACCOUNTS = {"ui_chat_a": 90703, "ui_chat_b": 90704, "ui_chat_gone": 90705, "ui_chat_c": 90706, "ui_chat_d": 90708, "ui_chat_e": 90709, "ui_chat_f": 90711, "ui_chat_h": 90712}


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
        held = db.query(User).filter_by(id=user_id).first()
        if held and held.username != name:              # another seed's account: never chat as it (90710 is ui_profile_edit's)
            raise SystemExit(f"id {user_id} is {held.username}'s, not {name}'s: give {name} an id no seed uses")
        if not held:
            db.add(User(age_declared=1, id=user_id, username=name, original_username=name, password=PASSWORD, email=f"{name}@example.com",
                        created_utc=now - 90 * 86400, creation_ip="127.0.0.1", tos_agreed_utc=now - 90 * 86400))
            db.flush()
    # a seed starts clean: a Space made by each run counts against the 5 a day, and the spam guard may have suspended them
    db.query(User).filter(User.id.in_(list(ACCOUNTS.values()))).update({"is_banned": 0, "unban_utc": 0, "ban_reason": None}, synchronize_session=False)
    for user in db.query(User).filter(User.id.in_(list(ACCOUNTS.values()))).all():
        user.passhash = user.hash_password(PASSWORD)
    ids = {"ids": list(ACCOUNTS.values())}
    db.commit()
    sign_out_chat(db, ids)
    db.execute(text("DELETE FROM chat_device_signouts WHERE user_id = ANY(:ids)"), ids)
    db.execute(text("DELETE FROM chat_site_devices WHERE user_id = ANY(:ids)"), ids)
    db.execute(text("DELETE FROM ended_sign_ins WHERE user_id = ANY(:ids)"), ids)
    db.execute(text("UPDATE spaces SET created_utc = created_utc - 172800 WHERE host_id = ANY(:ids)"), {"ids": list(ACCOUNTS.values())})
    # and a group started by each run counts against the 10 a day, its people against the day's adds
    db.execute(text("UPDATE chat_groups SET created_utc = created_utc - 172800 WHERE created_by_id = ANY(:ids)"), ids)
    db.execute(text("UPDATE chat_group_adds SET created_utc = created_utc - 172800 WHERE adder_id = ANY(:ids)"), ids)
    # and a channel made by each run counts against the 3 an account may have open
    db.execute(text("UPDATE broadcasts SET deleted_utc = :now WHERE owner_id = ANY(:ids) AND deleted_utc = 0"), {**ids, "now": now})
    a, b = ACCOUNTS["ui_chat_a"], ACCOUNTS["ui_chat_b"]
    pairs = [(a, b), (b, a)] + [pair for name in ("ui_chat_c", "ui_chat_d", "ui_chat_e", "ui_chat_f", "ui_chat_h")
                                for pair in ((b, ACCOUNTS[name]), (ACCOUNTS[name], b))]
    for follower, target in pairs:
        if not db.query(Follow).filter_by(user_id=follower, target_id=target).first():
            db.add(Follow(user_id=follower, target_id=target, created_utc=now))
    db.commit()
    forget_limits()
    print("Seeded " + ", ".join(ACCOUNTS) + ".")


def sign_out_chat(db, ids):
    """Every chat device of the accounts that have chat, signed out on Synapse through the queue the site itself uses (one commit per
    account, as scripts/queue_chat_signouts.py). A device the chat server can't drop now is left; the next run tries again."""
    from sqlalchemy import text
    import ruqqus.helpers.matrix_client as matrix
    from ruqqus.helpers import chat_signouts

    for user_id, mxid in db.execute(text("SELECT user_id, matrix_user_id FROM chat_identities WHERE user_id = ANY(:ids)"), ids).all():
        row = chat_signouts.queue(db, user_id, "legacy", "all", mxid=mxid)
        db.commit()
        if row:
            try:
                chat_signouts.run_one(db, matrix, row)
            except Exception as e:
                db.rollback()
                print(f"chat devices of {user_id} not signed out now: {type(e).__name__}")


def forget_limits():
    from ruqqus.__main__ import r
    for pattern in ("*LIMITER*login*", "*LIMITER*chat*", "throttle:login*", "throttle:signup*"):
        for key in r.scan_iter(pattern):
            r.delete(key)


def remove():
    from sqlalchemy import text
    from ruqqus.__main__ import db_session

    db = db_session()
    ids = {"ids": list(ACCOUNTS.values())}
    db.execute(text("DELETE FROM follows WHERE user_id = ANY(:ids) OR target_id = ANY(:ids)"), ids)
    db.execute(text("DELETE FROM chat_device_signouts WHERE user_id = ANY(:ids)"), ids)
    db.execute(text("DELETE FROM chat_site_devices WHERE user_id = ANY(:ids)"), ids)
    db.execute(text("DELETE FROM ended_sign_ins WHERE user_id = ANY(:ids)"), ids)
    db.execute(text("DELETE FROM chat_identities WHERE user_id = ANY(:ids)"), ids)
    db.execute(text("DELETE FROM users WHERE id = ANY(:ids)"), ids)
    db.commit()
    print("Removed.")


if __name__ == "__main__":
    main()
