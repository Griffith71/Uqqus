"""Spaces: the rules (ruqqus/helpers/spaces.py) and the SQL (helpers/space_store.py on SQLite).

The promises: never anonymous, never by a bot, never in a Circle guild; a block with the host or a co-host, being
removed, or not being allowed to see the post all read the same ("isn't available"); at most MAX_STAGE on stage and
MAX_LISTENERS listening; only the host and co-hosts run it, and only the host names co-hosts; a report is once per
person and nobody is ever told who reported; the replay is WebM or Ogg audio, recognised by its first bytes. The board
(Phase 2): plain text, only from people in a live Space, slowed and capped, quotes only from the same Space, reports once
per person and never naming who; a Space that was not recorded leaves no message behind but a reported one, which goes
once its reports are closed. Video: only while the host allowed it, the Space is small and the relay's month allows."""
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

MIGRATIONS = ("2026-10-24_spaces.sql", "2026-11-11_space_board.sql")


def _tables():
    from pathlib import Path
    for name in MIGRATIONS:
        sql = (Path(__file__).resolve().parent.parent / "scripts" / "migrations" / name).read_text(encoding="utf-8")
        sql = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
        for s in (s.strip() for s in sql.split(";") if "CREATE" in s or "ALTER" in s):
            if "ADD COLUMN IF NOT EXISTS matrix_room_id" in s:
                continue                                  # already in the CREATE above it
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


def test_a_space_remembers_whether_it_allows_video_and_its_slow_mode(db):
    sid = store.create(db, 12, HOST, NOW + 60, False, NOW, video=True)
    row = store.get(db, sid)
    assert bool(row[12]) is True and row[13] == 0
    store.set_slow(db, sid, 30)
    assert store.get(db, sid)[13] == 30
    assert bool(store.get(db, _space(db, post=13))[12]) is False          # off unless the host allowed it


# --- video ----------------------------------------------------------------------------------------------------

def test_video_only_while_allowed_small_and_the_relay_allows():
    assert rules.video_refusal(False, 2, True) == rules.VIDEO_OFF
    assert rules.video_refusal(True, 2, False) == rules.VIDEO_RELAY
    assert rules.video_refusal(True, rules.VIDEO_PEOPLE_MAX, True) is None
    assert rules.video_refusal(True, rules.VIDEO_PEOPLE_MAX + 1, True) == rules.VIDEO_TOO_BIG


def test_a_camera_needs_the_stage_and_a_free_place():
    assert rules.camera_refusal(True, 3, True, True, 0) is None
    assert rules.camera_refusal(True, 3, True, False, 0).startswith("Only people on stage")
    assert rules.camera_refusal(True, 3, True, True, rules.VIDEO_STAGE_MAX) == rules.VIDEO_FULL
    assert rules.camera_refusal(True, 3, True, True, rules.VIDEO_STAGE_MAX - 1) is None
    assert rules.camera_refusal(True, rules.VIDEO_PEOPLE_MAX + 1, True, True, 0) == rules.VIDEO_TOO_BIG
    assert rules.VIDEO_STAGE_MAX <= rules.MAX_STAGE


# --- the board ------------------------------------------------------------------------------------------------

def test_a_message_is_plain_text_and_bounded():
    assert rules.clean_message("  hello\r\n\n\n\nthere\x07  ") == "hello\n\nthere"
    for empty in ("", "   ", None, "\x00\x01"):
        with pytest.raises(rules.Refused):
            rules.clean_message(empty)
    assert len(rules.clean_message("x" * rules.BOARD_MAX)) == rules.BOARD_MAX
    with pytest.raises(rules.Refused):
        rules.clean_message("x" * (rules.BOARD_MAX + 1))


def test_who_writes_on_the_board():
    live = rules.LIVE
    assert rules.message_refusal(live, True, False, 0, 0) is None
    assert rules.message_refusal(rules.SCHEDULED, True, False, 0, 0) == rules.BOARD_CLOSED
    assert rules.message_refusal(rules.ENDED, True, False, 0, 0) == rules.BOARD_CLOSED
    assert rules.message_refusal(live, False, False, 0, 0) == rules.BOARD_JOIN
    assert rules.message_refusal(live, True, True, 0, 0) == "Your account is suspended."
    assert "fast" in rules.message_refusal(live, True, False, rules.BOARD_PER_MINUTE, 0)
    assert "a day" in rules.message_refusal(live, True, False, 0, rules.BOARD_PER_DAY)


def test_slow_mode_and_who_moderates():
    assert rules.clean_slow("30") == 30 and rules.clean_slow("0") == 0
    for bad in ("7", "-10", "", None, "١٠"):
        with pytest.raises(rules.Refused):
            rules.clean_slow(bad)
    assert rules.slow_label(0) == "Off" and rules.slow_label(30) == "30 s" and rules.slow_label(300) == "5 min"
    assert rules.may_moderate(rules.HOST, False) and rules.may_moderate(rules.COHOST, False)
    assert not rules.may_moderate(rules.SPEAKER, False) and rules.may_moderate(rules.LISTENER, True)
    assert rules.may_delete_message(BOB, BOB) and not rules.may_delete_message(CAT, BOB) and not rules.may_delete_message(None, BOB)


def test_the_board_lists_quotes_and_counts(db):
    sid = _space(db)
    other = _space(db, post=11)
    a = store.add_message(db, sid, BOB, "first", 0, None, NOW)
    b = store.add_message(db, sid, CAT, "second", 1, a, NOW + 1)
    store.add_message(db, other, BOB, "elsewhere", 0, None, NOW + 2)
    assert [m[0] for m in store.messages(db, sid)] == [a, b]               # oldest first
    assert [m[0] for m in store.messages(db, sid, after_id=a)] == [b]
    assert store.message(db, b)[5] == a and store.message(db, b)[4] == 1
    assert store.quotable(db, sid, a) and not store.quotable(db, other, a)      # a quote stays in its own Space
    assert store.messages_since(db, BOB, NOW) == 2 and store.messages_since(db, BOB, NOW + 1) == 1
    assert set(store.messages_by(db, {a, b})) == {a, b} and store.messages_by(db, set()) == {}


def test_deleting_and_removing_take_a_message_off_the_board(db):
    sid = _space(db)
    a = store.add_message(db, sid, BOB, "mine", 0, None, NOW)
    b = store.add_message(db, sid, CAT, "rude", 2, None, NOW)
    assert store.delete_message(db, a, NOW + 5) and not store.delete_message(db, a, NOW + 6)
    assert store.message(db, a)[3] == "" and not store.quotable(db, sid, a)    # its text goes at once
    assert store.remove_message(db, b, HOST, NOW + 7) and not store.remove_message(db, b, HOST, NOW + 8)
    assert store.message(db, b)[3] == "" and store.messages(db, sid) == []


def test_a_reported_message_keeps_its_text_until_the_reports_are_closed(db):
    sid = _space(db, record=True)
    m = store.add_message(db, sid, CAT, "rude", 2, None, NOW)
    assert store.report_message(db, m, BOB, "rude", NOW)
    assert not store.report_message(db, m, BOB, "again", NOW)              # once per person
    assert store.report_message(db, m, DAN, "", NOW + 1)
    queue = store.open_message_reports(db)
    assert [(row[0], n, reasons) for row, n, reasons in queue] == [(m, 2, ["rude"])]     # never who
    store.remove_message(db, m, HOST, NOW + 2)
    assert store.message(db, m)[3] == "rude"                                 # kept for the admins
    store.resolve_message_reports(db, m, NOW + 3)
    assert store.message(db, m)[3] == "" and store.open_message_reports(db) == []


def test_a_space_that_was_not_recorded_leaves_no_message_behind(db):
    sid = _space(db, record=False)
    kept = store.add_message(db, sid, BOB, "reported", 0, None, NOW)
    store.add_message(db, sid, CAT, "ordinary", 0, None, NOW)
    store.report_message(db, kept, DAN, "", NOW)
    store.end(db, sid, NOW + 10)
    store.clear_board(db, sid)
    left = db.execute(text("SELECT id FROM space_messages WHERE space_id = :s"), {"s": sid}).fetchall()
    assert [row[0] for row in left] == [kept]                                # waits for the admins
    store.resolve_message_reports(db, kept, NOW + 20)
    assert store.message(db, kept) is None


def test_a_recorded_space_keeps_its_board_after_a_report_is_closed(db):
    sid = _space(db, record=True)
    m = store.add_message(db, sid, BOB, "fine", 0, None, NOW)
    store.report_message(db, m, DAN, "", NOW)
    store.end(db, sid, NOW + 10)
    store.resolve_message_reports(db, m, NOW + 20)
    assert store.message(db, m)[3] == "fine"
