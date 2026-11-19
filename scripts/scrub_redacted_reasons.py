"""Clear, for the entries a level-4 admin redacted BEFORE a redaction cleared the copies of the reason (the second review of the Log, C48), the words that
the decision's own pages still print: the guild's mod-log note, a removed or hidden item's reason, a banned account's or guild's, the exile list's and a
hide's history rows (ruqqus/helpers/reason_copies.py has the rules: only that decision's own copy, never one a later decision wrote, never what the
decision did).

    PYTHONPATH=. python scripts/scrub_redacted_reasons.py --once [--dry-run]

Safe to run twice: a copy already cleared is not found again. It writes no entry (each redaction already has its own); `--dry-run` rolls every batch back
and says how many copies it would have cleared. Run it once after deploying the change.
"""
import sys

from ruqqus.__main__ import db_session
from ruqqus.helpers import reason_copies

BATCH = 200


def run(dry_run=False):
    db = db_session()
    seen = cleared = last = 0
    try:
        while True:
            read, last, done = reason_copies.scrub_redacted(db, after=last, limit=BATCH)
            if not read:
                break
            seen += read
            cleared += done
            if dry_run:
                db.rollback()
            else:
                db.commit()
            print(f"  up to entry {last}: {seen} redacted entries read, {cleared} copies cleared so far", flush=True)
        print(f"{'Would clear' if dry_run else 'Cleared'} {cleared} copies of the reasons of {seen} redacted entries.", flush=True)
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
