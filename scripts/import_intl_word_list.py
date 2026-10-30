"""Add the word filter's built-in words for other languages (ruqqus/helpers/wordfilter_intl.json, from LDNOOBW, CC BY
4.0) to a word list that already has entries, as /admin/word_filter's "Add the built-in words for other languages"
does. A word already on the list, on or off, is never changed, so it is safe to run again. An empty list needs
nothing: the built-in list, with these words, is what the filter uses then.

    PYTHONPATH=. python scripts/import_intl_word_list.py --once [--dry-run]

Re-scan afterwards (the admin page's button, or scripts/rescan_word_filter.py) to rate existing content with them.
"""
import sys

from sqlalchemy import text

from ruqqus.__main__ import db_session
from ruqqus.helpers.word_filter_store import add_builtin


def run(dry_run=False):
    db = db_session()
    try:
        if not db.execute(text("SELECT 1 FROM word_filter_entries LIMIT 1")).first():
            print("The list is empty: the built-in list, with these words, is already in use. Nothing to do.")
            return
        added = add_builtin(db, "intl", dry_run=dry_run)
        if dry_run:
            db.rollback()
            print(f"{len(added)} built-in word(s) for other languages would be added.", flush=True)
        else:
            db.commit()
            print(f"Added {len(added)} built-in word(s) for other languages. Re-scan to apply them.", flush=True)
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
