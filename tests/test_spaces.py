"""Spaces: the rules (ruqqus/helpers/spaces.py) and the SQL (helpers/space_store.py on SQLite).

The promises: never anonymous, never by a bot, never in a Circle guild; a block with the host or a co-host, being
removed, or not being allowed to see the post all read the same ("isn't available"); at most MAX_STAGE on stage and
MAX_LISTENERS listening; only the host and co-hosts run it, and only the host names co-hosts; a report is once per
person and nobody is ever told who reported; the replay is WebM or Ogg audio, recognised by its first bytes."""
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import spaces as rules
from ruqqus.helpers import space_store as store
from ruqqus.helpers.media import rules as media_rules

NOW = 2_000_000_000
HOST, BOB, CAT, DAN = 1, 2, 3, 4


# --- rules ----------------------------------------------------------------------------------------------------

def test_who_can_host_a_space():
    assert rules.make_refusal(False, False, False, 0) is None
    assert rules.make_refusal(True, False, False, 0) == "A Space can't be anonymous: people hear your voice."
    assert rules.make_refusal(False, True, False, 0) == "Bots can't host Spaces."
    assert rules.make_refusal(False, False, True, 0) == "A Space can't be hosted in a Circle guild."
    assert "at most" in rules.make_refusal(False, False, False, rules.SPACES_PER_DAY)


@pytest.mark.parametrize("value, expected", [
    ("", (NOW, None)), (None, (NOW, None)), ("0", (NOW, None)),
    (str(NOW + 3600), (NOW + 3600, None)),
    (str(NOW - 100), (NOW, None)),                       # a few seconds behind the member's clock: now
])
def test_when_it_starts(value, expected):
    assert rules.parse_start(value, NOW) == expected


def test_a_start_time_that_cant_be():
    assert rules.parse_start("soon", NOW) == (None, "That start time isn't a time.")
    assert rules.parse_start(str(NOW - 3600), NOW) == (None, "A Space can't start in the past.")
    assert rules.parse_start(str(NOW + (rules.SCHEDULE_DAYS + 1) * 86400), NOW)[1].startswith("A Space can be scheduled")


def test_joining_never_says_why_it_isnt_available():
    live = rules.LIVE
    assert rules.join_refusal(live, True, False, False, 0, False) is None
    for may_see, blocked, removed in ((False, False, False), (True, True, False), (True, False, True)):
        assert rules.join_refusal(live, may_see, blocked, removed, 0, False) == rules.UNAVAILABLE
    assert rules.join_refusal(rules.SCHEDULED, True, False, False, 0, False) == rules.NOT_STARTED
    assert rules.join_refusal(rules.ENDED, True, False, False, 0, False) == rules.OVER
    assert rules.join_refusal(live, True, False, False, rules.MAX_LISTENERS, False) == rules.FULL
    assert rules.join_refusal(live, True, False, False, rules.MAX_LISTENERS, True) is None   # a speaker coming back


def test_roles():
    roles = {BOB: ("speaker", "accepted"), CAT: ("cohost", "accepted"), DAN: ("speaker", "invited")}
    assert rules.role_of(HOST, HOST, roles) == rules.HOST
    assert rules.role_of(BOB, HOST, roles) == rules.SPEAKER
    assert rules.role_of(CAT, HOST, roles) == rules.COHOST
    assert rules.role_of(DAN, HOST, roles) == rules.LISTENER            # an invitation is not the stage yet
    assert rules.role_of(9, HOST, roles) == rules.LISTENER
    assert rules.on_stage(rules.COHOST) and not rules.on_stage(rules.LISTENER)
    assert rules.may_run(rules.HOST) and rules.may_run(rules.COHOST) and not rules.may_run(rules.SPEAKER)


def test_who_runs_the_stage():
    assert rules.invite_refusal(rules.HOST, rules.LISTENER, 1, False) is None
    assert rules.invite_refusal(rules.SPEAKER, rules.LISTENER, 1, False).startswith("Only the host")
    assert rules.invite_refusal(rules.HOST, rules.SPEAKER, 1, False) == "They're already on stage."
    assert rules.invite_refusal(rules.HOST, rules.LISTENER, 1, True) == "They've already been invited to speak."
    assert rules.invite_refusal(rules.COHOST, rules.LISTENER, rules.MAX_STAGE, False) == rules.STAGE_FULL
    assert rules.cohost_refusal(rules.HOST, rules.SPEAKER, 0) is None
    assert rules.cohost_refusal(rules.HOST, rules.COHOST, rules.MAX_COHOSTS) is None             # taking it back
    assert rules.cohost_refusal(rules.COHOST, rules.SPEAKER, 0) == "Only the host can choose co-hosts."
    assert rules.cohost_refusal(rules.HOST, rules.LISTENER, 0) == "Invite them to speak first."
    assert rules.cohost_refusal(rules.HOST, rules.SPEAKER, rules.MAX_COHOSTS).startswith("A Space can have at most")
    assert rules.remove_refusal(rules.HOST, rules.COHOST, HOST, CAT) is None
    assert rules.remove_refusal(rules.COHOST, rules.LISTENER, CAT, BOB) is None
    assert rules.remove_refusal(rules.COHOST, rules.COHOST, CAT, DAN) == "Only the host can remove a co-host."
    assert rules.remove_refusal(rules.COHOST, rules.HOST, CAT, HOST) == "Only the host can remove a co-host."
    assert rules.remove_refusal(rules.SPEAKER, rules.LISTENER, BOB, DAN).startswith("Only the host and co-hosts")
    assert rules.remove_refusal(rules.HOST, rules.HOST, HOST, HOST) == "Use Leave to leave the Space."


def test_labels_and_reasons():
    assert rules.state_label(rules.LIVE, NOW, NOW) == "Live"
    assert rules.state_label(rules.ENDED, NOW, NOW) == "Ended"
    assert rules.state_label(rules.SCHEDULED, NOW - 5, NOW) == "Starting soon"
    assert rules.state_label(rules.SCHEDULED, NOW + 60, NOW) == "Scheduled"
    assert rules.clean_reason("  too\n\nloud  ") == "too loud"
    assert len(rules.clean_reason("x" * 500)) == rules.REPORT_MAX


def test_a_replay_is_recognised_by_its_first_bytes():
    assert media_rules.sniff(bytes([0x1A, 0x45, 0xDF, 0xA3]) + b"\x00" * 8) == "webm"
    assert media_rules.TYPES["webm"] == (media_rules.AUDIO, "audio/webm")
    assert media_rules.sniff(b"OggS" + b"\x00" * 8) == "ogg"


# --- the SQL --------------------------------------------------------------------------------------------------

def _tables():
    from pathlib import Path
    sql = (Path(__file__).resolve().parent.parent / "scripts" / "migrations" / "2026-10-24_spaces.sql").read_text(encoding="utf-8")
    sql = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
    for s in (s.strip() for s in sql.split(";") if "CREATE" in s):
        yield (s.replace("public.", "").replace("IF NOT EXISTS ", "").replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")
               .replace(" WHERE status = 'live'", ""))


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for statement in _tables():
            conn.execute(text(statement))
    with Session(engine) as session:
        yield session


def _space(db, post=10, record=False):
    return store.create(db, post, HOST, NOW + 60, record, NOW)


def test_a_space_starts_and_ends_once(db):
    sid = _space(db)
    row = store.get(db, sid)
    assert (row[1], row[2], row[3], row[4], bool(row[7])) == (10, HOST, "scheduled", NOW + 60, False)
    assert store.start(db, sid, NOW + 70) and not store.start(db, sid, NOW + 80)
    store.set_room(db, sid, "!room")
    assert store.get(db, sid)[3] == "live" and store.get(db, sid)[11] == "!room"
    assert store.end(db, sid, NOW + 900) and not store.end(db, sid, NOW + 901)
    assert store.get(db, sid)[3] == "ended" and store.get(db, sid)[6] == NOW + 900
    assert store.room(sid) == f"space:{sid}"


def test_spaces_for_a_page_of_posts_and_the_daily_count(db):
    a = _space(db, post=10)
    b = _space(db, post=11)
    found = store.for_posts(db, [10, 11, 12])
    assert set(found) == {10, 11} and found[10][0] == a and found[11][0] == b
    assert store.for_posts(db, []) == {}
    assert store.made_since(db, HOST, NOW - 1) == 2 and store.made_since(db, HOST, NOW + 1) == 0


def test_the_stage(db):
    sid = _space(db)
    store.invite_speaker(db, sid, BOB, NOW)
    assert store.roles(db, sid) == {BOB: ("speaker", "invited")}
    assert store.accept_speaker(db, sid, BOB, NOW) and not store.accept_speaker(db, sid, BOB, NOW)
    store.set_role(db, sid, BOB, "cohost", NOW)
    assert store.roles(db, sid) == {BOB: ("cohost", "accepted")}
    store.drop_role(db, sid, BOB)
    assert store.roles(db, sid) == {}
    with pytest.raises(Exception):
        db.execute(text("INSERT INTO space_roles (space_id, user_id, role, status) VALUES (:s, 9, 'host', 'accepted')"), {"s": sid})


def test_hands_reminders_and_removal(db):
    sid = _space(db)
    store.raise_hand(db, sid, CAT, NOW + 2)
    store.raise_hand(db, sid, BOB, NOW + 1)
    store.raise_hand(db, sid, BOB, NOW + 5)              # twice is once
    assert store.hands(db, sid) == [BOB, CAT]
    store.lower_hand(db, sid, BOB)
    assert store.hands(db, sid) == [CAT]
    store.remind(db, sid, DAN, NOW)
    assert store.reminded(db, sid, DAN) and store.reminder_ids(db, sid) == [DAN]
    store.forget_reminder(db, sid, DAN)
    assert not store.reminded(db, sid, DAN)
    store.invite_speaker(db, sid, CAT, NOW)
    store.remove(db, sid, CAT, HOST, NOW)
    assert store.removed(db, sid, CAT) and store.hands(db, sid) == [] and store.roles(db, sid) == {}


def test_reports_are_once_per_person_and_never_name_who(db):
    sid = _space(db)
    assert store.report(db, sid, BOB, "too loud", NOW)
    assert not store.report(db, sid, BOB, "again", NOW)
    assert store.report(db, sid, CAT, "", NOW + 1)
    queue = store.open_reports(db)
    assert queue == [(sid, 2, ["too loud"])]               # counts and reasons, never who
    store.resolve_reports(db, sid, NOW + 2)
    assert store.open_reports(db) == []


def test_the_replay_and_the_peak(db):
    sid = _space(db, record=True)
    store.note_listeners(db, sid, 3)
    store.note_listeners(db, sid, 2)
    store.set_replay(db, sid, 48)
    row = store.get(db, sid)
    assert row[9] == 3 and row[8] == 48 and bool(row[7])
