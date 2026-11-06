"""Sign every chat device of every account out, through the durable queue (ruqqus/helpers/chat_signouts.py): a development
machine's chat reset, and the step after restoring an older pair of databases (RESTORE.md).

    PYTHONPATH=. python scripts/queue_chat_signouts.py --all --reason=legacy --once [--confirm] [--dry-run]

It queues an every-device sign-out for every chat identity (one commit per account, so it never holds more than one account's mint
lock), works the queue once (what the chat server can't take now is retried by scripts/recomputes.py, for ever), and empties
`chat_site_devices`, so every browser starts again as a new device. Safe to run twice.

It refuses a database that isn't a scratch one (named `ci_*`, `test*` or `ui_*`) unless `--confirm` says this is meant: on a live
site it signs every member's chat out.

While the interim of build steps 2 and 3 keeps the site's own browsers (chat_signouts.KEEP_SITE_BROWSERS), the sign-outs it queues reach
chat APPS only and the site's rows are kept: today's chat page deletes its store when its device is signed out. From build step 4 it
reaches every device.
"""
import argparse
import re
import sys

SCRATCH = ("ci_", "test", "ui_")
REASON = re.compile(r"^[a-z_]{1,24}$")


def scratch(database_name):
    """A database these tests and specs make and throw away (tests/pg/conftest.py names `ci_*` / `test*`; the UI specs `ui_*`)."""
    return bool(database_name) and database_name.startswith(SCRATCH)


def run(reason, confirm=False, dry_run=False, out=print, db=None, matrix=None):
    """`db` and `matrix` are the tests' (SQLite and a fake chat server); otherwise the app's session and helpers/matrix_client."""
    from sqlalchemy import text

    from ruqqus.helpers import chat_signouts

    own = db is None
    if own:
        from ruqqus.__main__ import db_session
        db = db_session()
    if matrix is None:
        import ruqqus.helpers.matrix_client as matrix
    try:
        name = db.get_bind().url.database
        if not scratch(name) and not confirm:
            out(f"Refusing: {name!r} isn't a scratch database, and this signs every member's chat out. Add --confirm if that is meant.")
            return 2
        if not chat_signouts.tables_ready(db):
            out("The migration 2026-12-20_chat_sign_outs.sql isn't applied: run scripts/migrate.py --apply first.")
            return 2
        accounts = db.execute(text("SELECT user_id, matrix_user_id FROM chat_identities ORDER BY user_id")).all()
        if dry_run:
            out(f"{len(accounts)} account(s) would have every chat device signed out.")
            return 0
        for user_id, mxid in accounts:
            chat_signouts.queue(db, user_id, reason, "all", mxid=mxid)
            # one commit per account: each queue holds its account's mint lock (a transaction-level advisory lock) until the commit, so
            # one transaction for every account would hold thousands at once (Postgres's shared lock table runs out, about 64 per
            # connection, and the whole run rolls back) and keep every account's chat from making a device until the end
            db.commit()
        if not chat_signouts.KEEP_SITE_BROWSERS:
            db.execute(text("DELETE FROM chat_site_devices"))
            db.commit()
        out(f"Queued a sign-out of every chat device for {len(accounts)} account(s)."
            + (" The interim keeps the site's own browsers: chat apps only." if chat_signouts.KEEP_SITE_BROWSERS else ""))
        done = {}
        while True:
            batch = chat_signouts.run_due(db, matrix, limit=200)
            for outcome, count in batch.items():
                done[outcome] = done.get(outcome, 0) + count
            if not batch or set(batch) == {"failed"}:
                break
        out(f"Worked now: {done or 'nothing'}. Anything left is retried by scripts/recomputes.py.")
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        if own:
            db_session.remove()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--all", action="store_true", help="every chat identity (the only scope there is)")
    parser.add_argument("--reason", required=True, help="written on each row, e.g. legacy or restore")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--confirm", action="store_true", help="run against a database that isn't a scratch one")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not args.all or not args.once:
        parser.error("say --all --once: it signs every chat device of every account out")
    if not REASON.match(args.reason):
        parser.error("--reason is lower-case letters and _, at most 24")
    return run(args.reason, confirm=args.confirm, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
