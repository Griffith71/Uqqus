"""Re-rate posts, comments, usernames and guild names with the current word
filter list. Run after applying scripts/migrations/2026-10-05_word_filter.sql
and whenever the list changes (the admin screen has a button for the same).

    PYTHONPATH=. python scripts/rescan_word_filter.py
"""
from ruqqus.__main__ import db_session
from ruqqus.helpers.word_filter_store import rescan

db = db_session()
changed = rescan(db, log=print)
print(f"done: {changed} rating(s) changed")
db.close()
