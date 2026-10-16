"""Counts what the call relay (coturn) sends, against its monthly allowance.

Runs forever as its own supervisord program ([program:ruqqusturn]); run it once by hand with

    PYTHONPATH=. python scripts/turn_usage.py --once

Every 5 minutes it reads coturn's own counters from its Prometheus page (`TURN_METRICS_URL`, an address inside the
server, never a public one) and adds what was sent since the last reading to this month (helpers/turn_usage_store.py).
The call and Space routes then read the month's total against `TURN_MONTHLY_GB` (helpers/turn_usage.py): audio only
from 80%, paused from 100%. If this job stops, calls pause within 30 minutes (on cost), so keep it running.
Without `TURN_METRICS_URL` (no relay configured) it does nothing.
"""
import os
import sys
import time
import traceback

# the app first: it patches the standard library for gevent, and ssl (which requests imports) must come after that
from ruqqus.__main__ import db_session
from ruqqus.helpers import turn_usage, turn_usage_store

import requests

POLL_SECONDS = 300


def log(message):
    print(f"[turn] {message}", flush=True)


def read_counter(url):
    resp = requests.get(url, timeout=5)
    resp.raise_for_status()
    counter = turn_usage.sent_bytes(resp.text)
    if counter is None:
        raise ValueError("the metrics page has no coturn traffic counters")
    return counter


def tick(now=None):
    url = os.environ.get("TURN_METRICS_URL", "").strip()
    if not url:
        return None
    now = int(now or time.time())
    counter = read_counter(url)
    db = db_session()
    try:
        spent = turn_usage_store.record(db, counter, now)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db_session.remove()
    allowance = turn_usage.allowance_bytes(os.environ.get("TURN_MONTHLY_GB"))
    return {"month": turn_usage.month_of(now), "sent_gb": round(spent / turn_usage.GB, 3),
            "state": turn_usage.state(spent, allowance, now, now)}


def main():
    if "--once" in sys.argv:
        print(tick())
        return
    if not os.environ.get("TURN_METRICS_URL", "").strip():
        log("TURN_METRICS_URL is not set: no relay to count")
    else:
        log(f"started, reading the relay every {POLL_SECONDS}s")
    last_state = None
    while True:
        try:
            result = tick()
            if result and result["state"] != last_state:
                log(f"{result}")
                last_state = result["state"]
        except Exception:
            traceback.print_exc()
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
