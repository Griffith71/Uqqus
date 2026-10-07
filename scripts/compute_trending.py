"""Works out the trending topics.

Runs forever as its own supervisord program ([program:ruqqustrending]); run it once by
hand with

    PYTHONPATH=. python scripts/compute_trending.py --once

Every pass reads the last day's posts and the week before, finds what an unusual number of
different accounts are posting about (ruqqus/helpers/trending.py) and replaces the stored
lists in one transaction (ruqqus/helpers/trending_store.py), so a reader never sees half a
list. Two of these running at once only do the work twice.
"""
import sys
import time
import traceback

from ruqqus.__main__ import app, db_session
from ruqqus.helpers import trending_store

POLL_SECONDS = 300


def log(message):
    print(f"[trending] {message}", flush=True)


def tick(now=None):
    """One pass. Returns {scope: {filter level: number of topics}}."""
    own_host = (app.config.get("SERVER_NAME") or "").split(":")[0].lower()
    db = db_session()
    try:
        result = trending_store.compute(db, now=now, own_hosts=(own_host,) if own_host else (), log=log)
    except Exception:
        db.rollback()
        raise
    finally:
        db_session.remove()
    return {scope: {level: len(topics) for level, topics in levels.items()} for scope, levels in result.items()}


def main():
    if "--once" in sys.argv:
        print(tick())
        return
    log(f"started, working out the lists every {POLL_SECONDS}s")
    while True:
        try:
            tick()
        except Exception:
            traceback.print_exc()
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
