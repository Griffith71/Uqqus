"""The reviewed events that ship with the site (helpers/happening_seed.json): the next elections and the big sporting and
political events of the next few years, each with the organiser's own page as its only link. An admin loads (or reloads)
them from /admin/happening; nothing loads them on its own.

Every entry goes through the same `site_events.clean_event` an admin's form does, so the seed can never hold what the table
refuses (tests/test_happening_seed.py runs the file through it). They are written as source "curated": reloading refreshes
the source's own columns (title, times, status) and never undoes an admin's hide, edit, priority or link
(site_event_store.upsert_source).
"""
import json
from pathlib import Path

from ruqqus.helpers import site_event_store as store
from ruqqus.helpers import site_events as ev

SEED_FILE = Path(__file__).with_name("happening_seed.json")
SOURCE = "curated"


def entries():
    return json.loads(SEED_FILE.read_text(encoding="utf-8"))["events"]


def sync(db, now, rate, version=""):
    """Insert or refresh every entry. Returns how many were inserted, updated and left as an admin edited them."""
    counts = {"inserted": 0, "updated": 0, "kept": 0}
    for entry in entries():
        fields = ev.clean_event(entry["form"], now)
        counts[store.upsert_source(db, SOURCE, entry["key"], fields, now, rate, version)] += 1
    return counts
