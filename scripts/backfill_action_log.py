"""Copy the guild mod log (`modactions`) into the Log (`log_entries`, ruqqus/helpers/action_log.py) for the rows made
before the Log existed. New rows are copied as they are made (classes/mod_logs.py), so this is only for the old ones.

    PYTHONPATH=. python scripts/backfill_action_log.py --once [--dry-run]

Safe to run twice: a row whose id is already in `log_entries.legacy_modaction_id` is skipped. The audience of an entry is
worked out the way a new one's is (a private or Circle guild's entries are the guild's, not the public's), and the legacy
note, which was stored HTML-escaped, becomes plain text. Run it after migration 2026-12-09_action_log.sql.
"""
import sys

from sqlalchemy import text

from ruqqus.__main__ import db_session
from ruqqus.helpers import action_log

BATCH = 2000


def run(dry_run=False):
    db = db_session()
    copied, last = 0, 0
    try:
        while True:
            rows = db.execute(text(
                "SELECT m.id, m.user_id, m.board_id, m.kind, m.target_user_id, m.target_submission_id, m.target_comment_id, "
                "m._note, m.created_utc FROM modactions m WHERE m.id > :last AND NOT EXISTS "
                "(SELECT 1 FROM log_entries e WHERE e.legacy_modaction_id = m.id) ORDER BY m.id LIMIT :n"),
                {"last": last, "n": BATCH}).all()
            if not rows:
                break
            for r in rows:
                if action_log.mirror_modaction(db, *r):
                    copied += 1
            last = rows[-1][0]
            if dry_run:
                db.rollback()
            else:
                db.commit()
            print(f"  up to mod action {last}: {copied} copied so far", flush=True)
        print(f"{'Would copy' if dry_run else 'Copied'} {copied} mod action(s) into the Log.", flush=True)
    except Exception:
        db.rollback()
        raise
    finally:
        db_session.remove()


if __name__ == "__main__":
    if "--once" not in sys.argv:
        print(__doc__)
        sys.exit(1)
    run(dry_run="--dry-run" in sys.argv)
