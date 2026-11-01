"""Tell members the Terms' Age section changed (helpers/age_declaration.py), once, by a notification with the link to the
Terms. A one-off an admin runs by hand after the change is live, never from a migration or at start-up:

    PYTHONPATH=. python scripts/notify_terms_age.py --once [--dry-run]

Goes to every live account that isn't a bot and agreed to the old Terms. Safe to run again: it leaves out anyone who
already has the notice. One system message with a notification row per person, in one commit (alerts.notify_many). The
Terms are English only, so the notice stays English until the catalogues hold it.
"""
import sys

from flask import g

from ruqqus.__main__ import app, db_session
from ruqqus.helpers import age_declaration
from ruqqus.helpers.alerts import notify_many


def run(dry_run=False):
    """How many were told (or would be)."""
    with app.test_request_context():
        g.db = db_session()
        try:
            ids = age_declaration.terms_notice_ids(g.db)
            if dry_run or not ids:
                print(f"{len(ids)} account(s) {'would be' if dry_run else 'were'} told.", flush=True)
                return len(ids)
            told = notify_many(ids, age_declaration.TERMS_NOTICE)
            print(f"{told} account(s) were told.", flush=True)
            return told
        except Exception:
            g.db.rollback()
            raise
        finally:
            db_session.remove()


if __name__ == "__main__":
    if "--once" not in sys.argv:
        print(__doc__)
        sys.exit(2)
    run(dry_run="--dry-run" in sys.argv)
