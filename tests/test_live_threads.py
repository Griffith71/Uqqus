"""Live threads and livestreams: the rules (ruqqus/helpers/live_threads.py, helpers/livestreams.py) and the SQL
(helpers/live_thread_store.py on SQLite).

The promises: only the author and co-authors run a live thread; it ends after IDLE_HOURS without an update or
MAX_DAYS in all; updates are counted per day and a deleted one is never listed; a livestream is only the member's own
YouTube or Twitch, and a YouTube link that is not a stream shows nothing."""
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import live_threads as rules
from ruqqus.helpers import live_thread_store as store
from ruqqus.helpers import livestreams

NOW = 2_000_000_000
HOUR = 3600


# --- rules ----------------------------------------------------------------------------------------------------

def test_how_long_it_is_live():
    assert rules.is_live(NOW, 0, NOW, NOW + HOUR)
    assert not rules.is_live(0, 0, 0, NOW)                                        # never went live
    assert not rules.is_live(NOW, NOW + 5, NOW, NOW + 10)                         # ended
    assert not rules.is_live(NOW, 0, NOW, NOW + rules.IDLE_HOURS * HOUR + 1)      # a day without an update
    assert rules.is_live(NOW, 0, NOW + 20 * HOUR, NOW + 30 * HOUR)               # an update keeps it going
    assert not rules.is_live(NOW, 0, NOW + 7 * 24 * HOUR, NOW + 7 * 24 * HOUR + 1)  # a week at most


def test_who_runs_it():
    assert rules.may_run(1, 1, [])
    assert rules.may_run(2, 1, [2, 3])                                            # a co-author
    assert not rules.may_run(4, 1, [2, 3]) and not rules.may_run(None, 1, [])
    assert rules.start_refusal(True, False, False, False) is None
    assert rules.start_refusal(False, False, False, False).startswith("Only the post's author")
    assert rules.start_refusal(True, True, False, False) == "This post can't go live."
    assert rules.start_refusal(True, False, False, True) == "This post is live already."


def test_updates():
    assert rules.clean_update("  Polls closed  ") == "Polls closed"
    for bad in ("", "   ", "x" * (rules.UPDATE_MAX + 1)):
        with pytest.raises(rules.Refused):
            rules.clean_update(bad)
    assert rules.update_refusal(True, True, 0) is None
    assert rules.update_refusal(False, True, 0).startswith("Only")
    assert rules.update_refusal(True, False, 0) == "This post isn't live."
    assert rules.update_refusal(True, True, rules.UPDATES_PER_DAY).startswith("A post can have at most")


@pytest.mark.parametrize("url, ref", [
    ("https://www.twitch.tv/SomeChannel", ("twitch", "somechannel")),
    ("https://twitch.tv/some_channel/", ("twitch", "some_channel")),
    ("https://www.twitch.tv/directory", None),
    ("https://www.twitch.tv/somechannel/videos", None),
    ("https://www.twitch.tv/ab", None),
    ("https://evil.example/twitch.tv/x", None),
    ("ftp://twitch.tv/somechannel", None),
    ("https://www.youtube.com/live/abcdefghijk", ("youtube", "abcdefghijk")),
    ("https://youtu.be/abcdefghijk", ("youtube", "abcdefghijk")),
    ("https://example.com/", None),
    ("", None),
    (None, None),
])
def test_which_links_are_streams(url, ref):
    assert livestreams.stream_ref(url) == ref


def test_what_the_card_says():
    twitch = ("twitch", "somechannel")
    youtube = ("youtube", "abcdefghijk")
    assert livestreams.card(twitch, None)["text"] == "Stream on Twitch"
    assert livestreams.card(twitch, "live")["text"] == "Live now on Twitch"
    assert livestreams.card(twitch, "offline")["text"] == "Offline on Twitch"
    assert livestreams.card(twitch, None)["url"] == "https://www.twitch.tv/somechannel"
    assert livestreams.card(youtube, None) is None                   # most YouTube links are not streams
    assert livestreams.card(youtube, "offline") is None
    assert livestreams.card(youtube, "upcoming")["text"] == "Starting soon on YouTube"
    assert livestreams.card(youtube, "live")["url"] == "https://www.youtube.com/watch?v=abcdefghijk"
    assert livestreams.card(None, "live") is None


# --- the SQL --------------------------------------------------------------------------------------------------

def _tables():
    from pathlib import Path
    sql = (Path(__file__).resolve().parent.parent / "scripts" / "migrations" / "2026-10-26_live_threads.sql").read_text(encoding="utf-8")
    sql = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
    for s in (s.strip() for s in sql.split(";") if "CREATE" in s):
        yield s.replace("public.", "").replace("IF NOT EXISTS ", "").replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for statement in _tables():
            conn.execute(text(statement))
    with Session(engine) as session:
        yield session


def test_going_live_ending_and_going_live_again(db):
    assert store.thread(db, 10) is None
    store.start(db, 10, 1, NOW)
    assert tuple(store.thread(db, 10)) == (NOW, 0, NOW, 1)
    store.end(db, 10, NOW + 50)
    store.end(db, 10, NOW + 60)                                          # once
    assert store.thread(db, 10)[1] == NOW + 50
    store.start(db, 10, 2, NOW + 100)
    assert tuple(store.thread(db, 10)) == (NOW + 100, 0, NOW + 100, 2)
    assert store.threads_for(db, [10, 11]) == {10: (NOW + 100, 0, NOW + 100)}
    assert store.threads_for(db, []) == {}


def test_updates_newest_first_newer_after_an_id_never_deleted(db):
    store.start(db, 10, 1, NOW)
    a = store.add_update(db, 10, 1, "one", "<p>one</p>", 0, NOW + 1)
    b = store.add_update(db, 10, 2, "two", "<p>two</p>", 1, NOW + 2)
    c = store.add_update(db, 10, 1, "three", "<p>three</p>", 0, NOW + 3)
    assert [u[0] for u in store.updates(db, 10)] == [c, b, a]
    assert [u[0] for u in store.updates(db, 10, after_id=a)] == [b, c]
    assert store.thread(db, 10)[2] == NOW + 3                            # the last update keeps it live
    store.delete_update(db, b, NOW + 4)
    assert [u[0] for u in store.updates(db, 10)] == [c, a]
    assert store.update(db, b)[8] == NOW + 4
    assert store.updates_since(db, 10, NOW + 2) == 2
