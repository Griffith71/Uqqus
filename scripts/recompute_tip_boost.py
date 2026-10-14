"""Work out every tipped post's tip totals again (helpers/tips_store.recompute_post) and its stored Hot and Best
scores with them. Run once after the 2026-10-13 tips migration, and again whenever the tip rules change:

    PYTHONPATH=. python scripts/recompute_tip_boost.py --once

Tips from before that migration count as 1 coin each (their amounts were never kept).
"""
import sys

from sqlalchemy import text

from ruqqus.__main__ import db_session
from ruqqus.helpers import tips_store

_TIPPED = text("SELECT DISTINCT s.id, s.author_id FROM submissions s JOIN award_relationships a ON a.submission_id = s.id")
_RESCORE = text("UPDATE submissions SET score_hot = rank_hot(submissions), score_best = rank_best(submissions) "
                "WHERE id = :p")


def run():
    db = db_session()
    try:
        posts = db.execute(_TIPPED).fetchall()
        for post_id, author_id in posts:
            coins, boost = tips_store.recompute_post(db, post_id, author_id)
            db.execute(_RESCORE, {"p": post_id})
            print(f"post {post_id}: {coins} coins tipped, boost {boost:g}", flush=True)
        db.commit()
        print(f"re-worked {len(posts)} tipped posts", flush=True)
    except Exception:
        db.rollback()
        raise
    finally:
        db_session.remove()


if __name__ == "__main__":
    if "--once" not in sys.argv:
        print(__doc__)
        sys.exit(1)
    run()
