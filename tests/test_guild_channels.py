"""Guild channels (helpers/guild_channels.py, helpers/guild_channel_store.py). The promises: who reads and writes
follows the channel's ladder and never a bot, an exile or a guild the person may not see, and a refusal to read never
says why; announcements are written by guildmasters with the content permission only and writing is never looser than
reading; names, topics and rules are plain text; slow mode, the burst limit and the daily count hold; a signed message
can be sent once, by its sender, as rated, in its room, and nothing else a member's own client could send survives;
one guild never reaches another's channel, message or report; unread is a date, not a counter per member; the sync
step names exactly the people who should no longer be in a room."""
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import guild_channel_store as store
from ruqqus.helpers import guild_channels as rules

from tests.fake_redis import FakeRedis

ROOT = Path(__file__).resolve().parent.parent
NOW = 1_800_000_000
SECRET = "test-secret"
ROOM = "!room:test"


# --- the rules -------------------------------------------------------------------------------------------------------

def test_names_topics_and_rules_are_plain_text():
    assert rules.clean_name("  #General   chat ") == "General chat"
    assert rules.clean_name("café ☕") == "café ☕"
    for bad in ("", "   ", "#", "x" * (rules.NAME_MAX + 1), "a<b", "a>b", "a@b", "bell\x07", "zero​width"):
        with pytest.raises(rules.Refused):
            rules.clean_name(bad)
    assert rules.clean_topic("  Where   we talk ") == "Where we talk" and rules.clean_topic(None) == ""
    with pytest.raises(rules.Refused):
        rules.clean_topic("x" * (rules.TOPIC_MAX + 1))
    with pytest.raises(rules.Refused):
        rules.clean_topic("hidden‮right to left")
    assert rules.clean_rules("1.  Be   kind\r\n\r\n\r\n\r\n2. No spam\n") == "1. Be kind\n\n2. No spam"
    with pytest.raises(rules.Refused):
        rules.clean_rules("x" * (rules.RULES_MAX + 1))
    assert rules.clean_slow("") == 0 and rules.clean_slow("30") == 30
    for bad in ("7", "-5", "abc", 99999):
        with pytest.raises(rules.Refused):
            rules.clean_slow(bad)


def test_settings_follow_the_kind():
    assert rules.clean_settings("text", "members", "members", "10") == ("text", "members", "members", 10)
    assert rules.clean_settings("text", "approved", "mods", 0) == ("text", "approved", "mods", 0)
    # an announcement channel is written by guildmasters and needs no slow mode, whatever was asked
    assert rules.clean_settings("announcement", "members", "members", "300") == ("announcement", "members", "mods", 0)
    with pytest.raises(rules.Refused):
        rules.clean_settings("text", "mods", "members", 0)          # writing is never looser than reading
    with pytest.raises(rules.Refused):
        rules.clean_settings("text", "approved", "members", 0)
    # a voice channel is spoken in, never typed in; a stage channel only shows a Space, so nobody writes in it
    assert rules.clean_settings("voice", "members", "approved", "300") == ("voice", "members", "approved", 0)
    assert rules.clean_settings("stage", "approved", "members", 60) == ("stage", "approved", "mods", 0)
    with pytest.raises(rules.Refused):
        rules.clean_settings("voice", "mods", "members", 0)
    for kind, read, write in (("video", "members", "members"), ("text", "everyone", "members"), ("text", "members", "all")):
        with pytest.raises(rules.Refused):
            rules.clean_settings(kind, read, write, 0)


def test_the_limits():
    assert rules.make_refusal(rules.MAX_CHANNELS - 1, False) is None
    assert "at most" in rules.make_refusal(rules.MAX_CHANNELS, False)
    assert "already" in rules.make_refusal(0, True) and "already" in rules.rename_refusal(True)
    assert rules.rename_refusal(False) is None
    assert rules.full_refusal(rules.MAX_MEMBERS - 1) is None and rules.full_refusal(rules.MAX_MEMBERS)
    assert rules.clean_message("  hi ") == "hi"
    for bad in ("", "  \n ", "x" * (rules.MESSAGE_MAX + 1)):
        with pytest.raises(rules.Refused):
            rules.clean_message(bad)
    assert rules.message_refusal(rules.PER_MINUTE, 0) is None and rules.message_refusal(rules.PER_MINUTE + 1, 0)
    assert rules.message_refusal(1, rules.PER_DAY - 1) is None and rules.message_refusal(1, rules.PER_DAY)
    assert "5 second" in rules.slow_message(5) and "1 second " in rules.slow_message(1) and "3 minutes" in rules.slow_message(170)
    assert rules.slow_exempt(True) and not rules.slow_exempt(False)


def _read(level=rules.MEMBERS, **over):
    args = dict(can_view=True, hidden=False, bot=False, exiled=False, joined=False, contributor=False, mod=False, admin=False)
    args.update(over)
    return rules.read_refusal(level, **args)


def test_who_reads_a_channel():
    assert _read(joined=True) is None and _read(contributor=True) is None and _read(mod=True) is None
    assert _read() == rules.JOIN_FIRST                                       # the one refusal that says what to do
    assert _read(rules.APPROVED, joined=True) == rules.UNAVAILABLE           # the others say nothing
    assert _read(rules.APPROVED, contributor=True) is None and _read(rules.APPROVED, mod=True) is None
    assert _read(rules.MODS, contributor=True) == rules.UNAVAILABLE and _read(rules.MODS, mod=True) is None
    for level in rules.LEVELS:                                               # never, whatever else is true
        for bad in ({"bot": True}, {"exiled": True}, {"can_view": False}, {"hidden": True}):
            assert _read(level, joined=True, contributor=True, mod=True, admin=True, **bad) == rules.UNAVAILABLE
    assert _read(rules.MODS, admin=True) is None                             # a site admin who may see the guild


def test_a_private_guild_is_for_its_own_people():
    assert rules.guild_visible(False, False, False) and not rules.guild_visible(True, False, False)
    assert rules.guild_visible(True, True, False) and rules.guild_visible(True, False, True)
    assert rules.guild_visible(True, False, False, admin=True)


def _write(level=rules.MEMBERS, read=None, **over):
    args = dict(suspended=False, muted=False, joined=True, contributor=False, mod=False, content_mod=False)
    args.update(over)
    return rules.write_refusal(level, read, **args)


def test_who_writes_in_a_channel():
    assert _write() is None
    assert _write(joined=False) == rules.JOIN_FIRST and _write(joined=False, contributor=True) is None
    assert _write(rules.APPROVED) and _write(rules.APPROVED, contributor=True) is None and _write(rules.APPROVED, mod=True) is None
    assert _write(rules.MODS, mod=True) and _write(rules.MODS, content_mod=True) is None   # the content permission, not any
    assert _write(suspended=True, content_mod=True) == "Your account is suspended."
    assert "muted" in _write(muted=True, content_mod=True)
    assert _write(read=rules.UNAVAILABLE, content_mod=True) == rules.UNAVAILABLE           # not readable: not writable


def test_signatures_are_for_one_message_by_one_sender():
    nonce = "a" * 32
    mark = rules.site_mark(SECRET, ROOM, "@u1:test", 1, nonce, "hello")

    def content(**over):
        out = {"msgtype": "m.text", "body": "hello", rules.SEVERITY_KEY: 1, rules.NONCE_KEY: nonce, rules.SITE_KEY: mark}
        out.update(over)
        return out

    assert rules.mark_ok(SECRET, ROOM, "@u1:test", content())
    assert not rules.mark_ok(SECRET, ROOM, "@u2:test", content())                 # copied by someone else
    assert not rules.mark_ok(SECRET, "!other:test", "@u1:test", content())        # moved to another room
    assert not rules.mark_ok(SECRET, ROOM, "@u1:test", content(body="hello!"))    # text changed
    assert not rules.mark_ok(SECRET, ROOM, "@u1:test", content(**{rules.SEVERITY_KEY: 0}))   # rating lowered
    assert not rules.mark_ok(SECRET, ROOM, "@u1:test", content(**{rules.NONCE_KEY: "b" * 32}))
    assert not rules.mark_ok("other-secret", ROOM, "@u1:test", content())
    assert not rules.mark_ok("", ROOM, "@u1:test", content())
    for bad in ({rules.SEVERITY_KEY: True}, {rules.SEVERITY_KEY: "1"}, {rules.NONCE_KEY: "short"}, {rules.SITE_KEY: None}, {"body": None}):
        assert not rules.mark_ok(SECRET, ROOM, "@u1:test", content(**bad))
    assert not rules.mark_ok(SECRET, ROOM, "@u1:test", "not a dict")


def test_reactions_and_quotes():
    assert rules.reaction_ok("👍") and not rules.reaction_ok("lol") and not rules.reaction_ok("")
    assert rules.quoted_event_id("$abcdefghij1234567890") == "$abcdefghij1234567890"
    assert rules.quoted_event_id("$short") is None and rules.quoted_event_id("abcdefghij1234567890") is None
    assert rules.quoted_event_id('$abc"><script>alert(1)</script>') is None and rules.quoted_event_id(None) is None


# --- the SQL ---------------------------------------------------------------------------------------------------------

MIGRATIONS = ("2026-11-04_guild_channels.sql", "2026-11-05_guild_channels_polish.sql", "2026-11-06_guild_channels_unread.sql",
              "2026-11-07_guild_voice.sql", "2026-11-08_guild_roles.sql", "2026-11-09_guild_channels_extras.sql")


def _tables():
    """The migrations' own statements, as SQLite takes them (every part of the feature, in order). SQLite can't change a
    CHECK afterwards, so the kinds the last migration allows are in the table from the start."""
    for name in MIGRATIONS:
        sql = (ROOT / "scripts" / "migrations" / name).read_text(encoding="utf-8")
        sql = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
        for s in (s.strip() for s in sql.split(";")):
            if s.startswith("ALTER") and "CONSTRAINT" in s:
                continue
            if s.startswith(("CREATE", "ALTER")):
                yield (s.replace("public.", "").replace("IF NOT EXISTS ", "")
                       .replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")
                       .replace("kind IN ('text', 'announcement', 'voice')", "kind IN ('text', 'announcement', 'voice', 'stage')"))


STUBS = (
    "CREATE TABLE boards (id integer PRIMARY KEY, name text, is_private boolean DEFAULT 0, is_banned boolean DEFAULT 0)",
    "CREATE TABLE users (id integer PRIMARY KEY, is_bot boolean DEFAULT 0, is_deleted boolean DEFAULT 0, "
    "is_banned integer DEFAULT 0, admin_level integer DEFAULT 0)",
    "CREATE TABLE subscriptions (id integer PRIMARY KEY AUTOINCREMENT, user_id integer, board_id integer, is_active boolean DEFAULT 1, "
    "created_utc integer DEFAULT 0)",
    "CREATE TABLE contributors (id integer PRIMARY KEY AUTOINCREMENT, user_id integer, board_id integer, is_active boolean DEFAULT 1, "
    "created_utc integer DEFAULT 0)",
    "CREATE TABLE mods (id integer PRIMARY KEY AUTOINCREMENT, user_id integer, board_id integer, accepted boolean DEFAULT 0, "
    "invite_rescinded boolean DEFAULT 0, perm_content boolean DEFAULT 0, perm_full boolean DEFAULT 0)",
    "CREATE TABLE bans (id integer PRIMARY KEY AUTOINCREMENT, user_id integer, board_id integer, is_active boolean DEFAULT 0)",
    "CREATE TABLE chatbans (id integer PRIMARY KEY AUTOINCREMENT, user_id integer, board_id integer, created_utc integer, banning_mod_id integer)",
    "CREATE TABLE submissions (id integer PRIMARY KEY, author_id integer, deleted_utc integer DEFAULT 0, is_banned boolean, "
    "post_public boolean DEFAULT 1, repost_id integer DEFAULT 0)",
    "CREATE TABLE submissions_aux (id integer, title text, url text)",
    "CREATE TABLE chat_identities (user_id integer, matrix_user_id text, provisioned_utc integer DEFAULT 0)",
)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for statement in STUBS:
            conn.execute(text(statement))
        conn.execute(text("INSERT INTO boards (id, name, is_private) VALUES (1, 'pets', 0), (2, 'secret', 1), (3, 'gone', 0)"))
        conn.execute(text("UPDATE boards SET is_banned = 1 WHERE id = 3"))
        for uid in range(1, 12):
            conn.execute(text("INSERT INTO users (id) VALUES (:i)"), {"i": uid})
        for statement in _tables():
            conn.execute(text(statement))
    with Session(engine) as session:
        yield session


def make(db, board=1, name="General", kind="text", read="members", write="members", slow=0, room=None):
    return store.create(db, board, kind, name, "", "", 0, read, write, slow, room or f"!{board}{name}:test", NOW)


def test_channels_are_made_ordered_and_listed(db):
    a, b = make(db, name="General"), make(db, name="Memes")
    other = make(db, board=2, name="General")
    assert [row.name for row in store.of_board(db, 1)] == ["General", "Memes"]
    assert store.count(db, 1) == 2 and store.count(db, 2) == 1
    assert store.get(db, a).matrix_room_id == "!1General:test" and store.by_room(db, "!1Memes:test").id == b
    store.move(db, 1, [b, a, other])                                   # another guild's channel in the list is ignored
    assert [row.name for row in store.of_board(db, 1)] == ["Memes", "General"]
    assert store.get(db, other).position == 1
    assert store.name_taken(db, 1, "general") and store.name_taken(db, 1, "GENERAL")        # ignoring case
    assert store.name_taken(db, 1, "ÉCOLE") is False and not store.name_taken(db, 2, "Memes")
    assert not store.name_taken(db, 1, "General", except_id=a)         # renaming a channel to its own name
    make(db, name="École")
    assert store.name_taken(db, 1, "école") and store.name_taken(db, 1, "ÉCOLE")           # Unicode case, not only ASCII


def test_one_guild_never_reaches_anothers_channel(db):
    mine, theirs = make(db, board=1), make(db, board=2)
    assert store.get_in_board(db, 1, theirs) is None and store.get_in_board(db, 2, theirs) is not None
    store.update(db, 1, theirs, "Hijacked", "", "", 0, "mods", "mods", 0)
    assert store.get(db, theirs).name == "General" and store.get(db, theirs).read_level == "members"
    store.close(db, 1, theirs, NOW)
    assert store.get(db, theirs) is not None
    store.close(db, 1, mine, NOW)
    assert store.get(db, mine) is None and store.count(db, 1) == 0     # a closed channel is gone from every read
    store.close_board(db, 2, NOW)
    assert store.count(db, 2) == 0


def test_the_viewers_standing_comes_with_the_channels(db):
    cid = make(db)
    db.execute(text("INSERT INTO subscriptions (user_id, board_id) VALUES (1, 1)"))
    db.execute(text("INSERT INTO contributors (user_id, board_id) VALUES (2, 1)"))
    db.execute(text("INSERT INTO mods (user_id, board_id, accepted, perm_content) VALUES (3, 1, 1, 1)"))
    db.execute(text("INSERT INTO mods (user_id, board_id, accepted, perm_content) VALUES (4, 1, 1, 0)"))
    db.execute(text("INSERT INTO mods (user_id, board_id, accepted, perm_full) VALUES (5, 1, 1, 1)"))
    db.execute(text("INSERT INTO mods (user_id, board_id, accepted, invite_rescinded, perm_content) VALUES (6, 1, 1, 1, 1)"))
    db.execute(text("INSERT INTO mods (user_id, board_id, accepted, perm_content) VALUES (7, 1, 0, 1)"))     # not accepted
    db.execute(text("INSERT INTO bans (user_id, board_id, is_active) VALUES (8, 1, 1)"))
    db.execute(text("INSERT INTO bans (user_id, board_id, is_active) VALUES (9, 1, 0)"))                     # lifted
    db.execute(text("INSERT INTO chatbans (user_id, board_id) VALUES (10, 1)"))

    def stand(uid):
        row = store.one_for(db, cid, uid)
        return tuple(bool(getattr(row, k)) for k in ("joined", "contributor", "mod", "content_mod", "exiled", "muted"))

    assert stand(1) == (True, False, False, False, False, False)
    assert stand(2) == (False, True, False, False, False, False)
    assert stand(3) == (False, False, True, True, False, False)
    assert stand(4) == (False, False, True, False, False, False)          # a guildmaster without the content permission
    assert stand(5) == (False, False, True, True, False, False)           # full counts as content
    assert stand(6)[2:4] == (False, False) and stand(7)[2:4] == (False, False)
    assert stand(8)[4] and not stand(9)[4] and stand(10)[5]
    assert store.one_for(db, cid, 1).board_name == "pets" and not store.one_for(db, cid, 1).is_private
    assert [r.id for r in store.of_board_for(db, 1, 1)] == [cid]


def test_the_guilds_a_person_is_in(db):
    pets, secret, gone = make(db, board=1), make(db, board=2), make(db, board=3)
    db.execute(text("INSERT INTO subscriptions (user_id, board_id) VALUES (1, 1), (1, 3)"))     # 3 is banned
    db.execute(text("INSERT INTO contributors (user_id, board_id) VALUES (1, 2)"))
    db.execute(text("INSERT INTO subscriptions (user_id, board_id, is_active) VALUES (2, 1, 0)"))
    db.execute(text("INSERT INTO mods (user_id, board_id, accepted) VALUES (3, 2, 1)"))
    assert [r.id for r in store.joined_guilds_for(db, 1)] == [secret, pets] or \
        {r.id for r in store.joined_guilds_for(db, 1)} == {pets, secret}
    assert gone not in {r.id for r in store.joined_guilds_for(db, 1)}                 # a banned guild has no channels
    assert store.joined_guilds_for(db, 2) == []                                       # left the guild
    assert [r.id for r in store.joined_guilds_for(db, 3)] == [secret]                 # a guildmaster is in it
    assert [r.id for r in store.joined_guilds_for(db, 4)] == []
    assert next(r for r in store.joined_guilds_for(db, 1) if r.id == secret).is_private


def test_unread_is_a_date_not_a_counter(db):
    cid = make(db)
    row = lambda uid: store.one_for(db, cid, uid)           # noqa: E731
    assert not store.unread(row(1))                         # a new channel with nothing in it
    assert store.touch(db, "!1General:test", NOW + 10) and not store.touch(db, "!nowhere:test", NOW)
    assert store.unread(row(1))                             # never opened, and someone has written
    store.join(db, cid, 1, NOW + 20)
    assert not store.unread(row(1)) and store.in_room(db, cid, 1) and not store.in_room(db, cid, 2)
    store.touch(db, "!1General:test", NOW + 30)
    assert store.unread(row(1)) and store.unread(row(2))
    store.mark_read(db, cid, 1, NOW + 40)
    assert not store.unread(row(1))
    store.join(db, cid, 1, NOW + 99)                        # joining again changes nothing
    assert store.one_for(db, cid, 1).in_room_since == NOW + 20
    assert store.size(db, cid) == 1
    store.leave(db, cid, 1)
    assert store.size(db, cid) == 0


def test_leaving_a_guild_leaves_its_rooms(db):
    a, b, other = make(db, name="A"), make(db, name="B"), make(db, board=2, name="A")
    for cid in (a, b, other):
        store.join(db, cid, 1, NOW)
    assert sorted(store.leave_board(db, 1, 1)) == sorted([a, b])
    assert store.in_room(db, other, 1) and not store.in_room(db, a, 1)


def _in_room(db, cid, *uids):
    for uid in uids:
        store.join(db, cid, uid, NOW)


def test_who_should_leave_a_room(db):
    cid = make(db, read="members")
    channel = store.get(db, cid)
    # 1 joined, 2 left the guild, 3 exiled, 4 approved only, 5 deleted, 6 a bot, 7 idle, 8 is a site admin who never joined
    db.execute(text("INSERT INTO subscriptions (user_id, board_id) VALUES (1, 1), (3, 1), (5, 1), (6, 1), (7, 1)"))
    db.execute(text("INSERT INTO subscriptions (user_id, board_id, is_active) VALUES (2, 1, 0)"))
    db.execute(text("INSERT INTO contributors (user_id, board_id) VALUES (4, 1)"))
    db.execute(text("INSERT INTO bans (user_id, board_id, is_active) VALUES (3, 1, 1)"))
    db.execute(text("UPDATE users SET is_deleted = 1 WHERE id = 5"))
    db.execute(text("UPDATE users SET is_bot = 1 WHERE id = 6"))
    db.execute(text("UPDATE users SET admin_level = 4 WHERE id = 8"))
    _in_room(db, cid, 1, 2, 3, 4, 5, 6, 8)
    store.join(db, cid, 7, NOW - 40 * 86400)
    out = set(store.who_should_leave(db, channel, (False, False), NOW + 60))
    assert out == {2, 3, 5, 6, 7}

    # the channel is tightened: only approved members and guildmasters stay
    db.execute(text("UPDATE guild_channels SET read_level = 'approved' WHERE id = :c"), {"c": cid})
    assert set(store.who_should_leave(db, store.get(db, cid), (False, False), NOW + 60)) == {1, 2, 3, 5, 6, 7}

    # the guild went private: a plain subscriber is out, an approved member stays
    db.execute(text("UPDATE guild_channels SET read_level = 'members' WHERE id = :c"), {"c": cid})
    assert set(store.who_should_leave(db, store.get(db, cid), (True, False), NOW + 60)) == {1, 2, 3, 5, 6, 7}
    # the guild was banned: everyone
    assert set(store.who_should_leave(db, store.get(db, cid), (False, True), NOW + 60)) == {1, 2, 3, 4, 5, 6, 7, 8}
    assert store.who_should_leave(db, store.get(db, make(db, name="Empty")), (False, False), NOW) == []


def test_messages_are_kept_for_reports_and_blanked(db):
    cid, other = make(db), make(db, board=2)
    store.record_post(db, cid, 1, "$e1", "hello", 0, NOW)
    store.record_post(db, cid, 1, "$e1", "hello again", 0, NOW + 1)           # delivered twice: one row
    store.record_post(db, cid, 2, "$e2", "rude", 2, NOW + 5)
    store.record_post(db, other, 3, "$e1", "elsewhere", 0, NOW)               # the same event id in another room
    assert store.posts_since(db, cid, 1, NOW) == 1 and store.posts_since(db, cid, 1, NOW + 1) == 0
    assert store.post_by_event(db, cid, "$e1")[2] == "hello" and store.post_by_event(db, other, "$e1")[2] == "elsewhere"
    assert store.authors_among(db, cid, [1, 2, 3, 4]) == {1, 2}              # a name lookup reaches nobody else
    assert store.authors_among(db, cid, []) == set()
    store.remove_post(db, cid, "$e1", NOW + 9)
    gone = store.post_by_event(db, cid, "$e1")
    assert gone[2] == "" and gone[3] == NOW + 9                              # the text goes with the message
    store.remove_post(db, cid, "$e1", NOW + 99)
    assert store.post_by_event(db, cid, "$e1")[3] == NOW + 9                 # taking it back twice changes nothing
    assert store.post_by_event(db, other, "$e1")[3] == 0

    pid = store.post_by_event(db, cid, "$e2")[0]
    store.report(db, cid, pid, 5, "rude", NOW)
    store.blank_old_text(db, NOW + 1000)                                     # an open report keeps the text
    assert store.post_by_event(db, cid, "$e2")[2] == "rude"
    store.resolve_reports(db, pid, NOW + 2000)
    store.blank_old_text(db, NOW + 1000)
    assert store.post_by_event(db, cid, "$e2")[2] == ""


def test_reports_once_per_person_and_never_across_guilds(db):
    cid, other = make(db, board=1), make(db, board=2)
    store.record_post(db, cid, 1, "$a", "bad", 0, NOW)
    store.record_post(db, other, 1, "$b", "bad too", 0, NOW)
    pid, other_pid = store.post_by_event(db, cid, "$a")[0], store.post_by_event(db, other, "$b")[0]
    assert store.report(db, cid, pid, 5, "spam", NOW) and not store.report(db, cid, pid, 5, "again", NOW)
    assert store.report(db, cid, pid, 6, "", NOW) and store.report(db, other, other_pid, 5, "x", NOW)
    reports = store.open_reports(db, 1)
    assert len(reports) == 1 and reports[0][0] == cid and reports[0][1] == "General" and reports[0][5] == 2
    assert reports[0][6] == ["spam"] and all(isinstance(x, (int, str, list)) for x in reports[0])   # no reporter anywhere
    assert store.post_in_board(db, 1, pid) is not None and store.post_in_board(db, 1, other_pid) is None
    store.resolve_reports(db, pid, NOW + 1)
    assert store.open_reports(db, 1) == [] and len(store.open_reports(db, 2)) == 1


def test_the_guild_wide_chat_mute(db):
    assert store.mute(db, 1, 5, 3, NOW) and not store.mute(db, 1, 5, 3, NOW + 1)
    assert store.is_muted(db, 1, 5) and not store.is_muted(db, 2, 5) and store.muted_ids(db, 1) == [5]
    assert store.unmute(db, 1, 5) and not store.unmute(db, 1, 5) and store.muted_ids(db, 1) == []


def test_writers_of_an_announcement_room_are_the_guildmasters_inside_it(db):
    cid = make(db, kind="announcement", write="mods")
    db.execute(text("INSERT INTO mods (user_id, board_id, accepted, perm_content) VALUES (1, 1, 1, 1), (2, 1, 1, 0), (3, 1, 1, 1)"))
    db.execute(text("INSERT INTO mods (user_id, board_id, accepted, perm_content) VALUES (4, 2, 1, 1)"))
    _in_room(db, cid, 1, 2, 4)
    assert store.writer_ids(db, cid, 1) == [1]            # not 2 (no content permission), 3 (not in the room), 4 (another guild)


def test_rescan_re_rates_names_topics_and_rules(db):
    cid = make(db)
    store.rescan_severities(db, lambda text_: 2 if "bad" in text_ else 0)
    assert store.get(db, cid).word_severity == 0
    store.update(db, 1, cid, "General", "a bad topic", "", 0, "members", "members", 0)
    store.rescan_severities(db, lambda text_: 2 if "bad" in text_ else 0)
    assert store.get(db, cid).word_severity == 2


# --- Redis: slow mode, the burst limit and a signature's one use ---------------------------------------------------------

def test_slow_mode():
    r = FakeRedis()
    assert store.slow_wait(r, 1, 7, 0) == 0                    # off
    assert store.slow_wait(r, 1, 7, 30) == 0                   # the first message goes, and slow mode starts
    assert store.slow_wait(r, 1, 7, 30) > 0                    # the next must wait
    assert store.slow_wait(r, 1, 8, 30) == 0 and store.slow_wait(r, 2, 7, 30) == 0       # someone else, another channel
    store.slow_release(r, 1, 7)                                # the message failed to send: no wait
    assert store.slow_wait(r, 1, 7, 30) == 0


def test_the_burst_count_is_per_person_channel_and_minute():
    r = FakeRedis()
    assert [store.burst_count(r, 1, 7, NOW) for _ in range(3)] == [1, 2, 3]
    assert store.burst_count(r, 1, 8, NOW) == 1 and store.burst_count(r, 2, 7, NOW) == 1
    assert store.burst_count(r, 1, 7, NOW + 60) == 1


def test_a_nonce_is_good_for_one_message():
    r = FakeRedis()
    nonce = store.new_nonce(r)
    assert len(nonce) == 32 and store.new_nonce(r) != nonce
    assert store.claim_nonce(r, nonce, "$first")
    assert store.claim_nonce(r, nonce, "$first")                # the webhook delivering the same event again
    assert not store.claim_nonce(r, nonce, "$copy")             # the same signature on another event
    assert store.claim_nonce(r, nonce, "$first")                # ... and the copy did not take the first one's place
    assert not store.claim_nonce(r, "f" * 32, "$made-up")       # a nonce the site never made
    assert not any("f" * 32 in key for key in r.data)           # ... leaves nothing behind


# --- the sync step ------------------------------------------------------------------------------------------------------------

def test_the_sync_step_empties_rooms_it_should_and_keeps_the_rest(db, monkeypatch):
    from ruqqus.helpers import guild_channel_sync as sync

    talk = make(db, name="Talk")
    news = make(db, name="News", kind="announcement", write="mods")
    old = make(db, name="Old")
    db.execute(text("INSERT INTO subscriptions (user_id, board_id) VALUES (1, 1), (4, 1)"))
    db.execute(text("INSERT INTO mods (user_id, board_id, accepted, perm_content) VALUES (1, 1, 1, 1)"))
    db.execute(text("INSERT INTO bans (user_id, board_id, is_active) VALUES (4, 1, 1)"))               # exiled
    _in_room(db, talk, 1, 2, 4)
    _in_room(db, news, 1)
    _in_room(db, old, 1, 3)
    store.close(db, 1, old, NOW)                                                                         # a closed channel: everyone out
    db.execute(text("INSERT INTO guild_channel_posts (channel_id, author_id, event_id, body, created_utc) "
                    "VALUES (:c, 1, '$ancient', 'old text', :t)"), {"c": talk, "t": NOW - 100 * 86400})

    kicked, writers = [], []
    # a moment when the announcement room's hourly turn for its writers has come
    now = (NOW - NOW % 3600) + 300 * (news % sync.WRITER_CYCLES)
    count = sync.run(db, now, lambda room, mxid, why: kicked.append((room, mxid)),
                     lambda room, mxids: writers.append((room, mxids)), lambda uid: f"@u{uid}:test")
    assert sorted(kicked) == sorted([("!1Talk:test", "@u2:test"), ("!1Talk:test", "@u4:test"),
                                     ("!1Old:test", "@u1:test"), ("!1Old:test", "@u3:test")])
    assert count == 4
    assert store.readers(db, talk) == [1] and store.readers(db, old) == [] and store.readers(db, news) == [1]
    assert writers == [("!1News:test", ["@u1:test"])]                                                    # the guildmaster in the room
    assert store.post_by_event(db, talk, "$ancient")[2] == ""                                            # old text is not kept

    # Matrix is down: nobody is forgotten, the next cycle tries again
    _in_room(db, talk, 2)

    def down(room, mxid, why):
        raise RuntimeError("Matrix is down")

    assert sync.run(db, now, down, lambda *a: None, lambda uid: f"@u{uid}:test") == 0
    assert 2 in store.readers(db, talk)
    # a cycle takes out only so many
    monkeypatch.setattr(sync, "MAX_KICKS", 0)
    assert sync.run(db, now, lambda *a: kicked.append(a), lambda *a: None, lambda uid: f"@u{uid}:test") == 0
    assert 2 in store.readers(db, talk)
    monkeypatch.undo()
    assert sync.run(db, now, lambda *a: None, lambda *a: None, lambda uid: f"@u{uid}:test") == 1 and 2 not in store.readers(db, talk)


def test_columns_the_real_schema_leaves_null_do_not_hide_anything(db):
    """boards.is_banned and mods.invite_rescinded / accepted are nullable in schema.sql: `NOT NULL` is NULL, so a
    careless negation would drop the guildmaster or the whole guild."""
    cid = make(db)
    db.execute(text("UPDATE boards SET is_banned = NULL WHERE id = 1"))
    db.execute(text("INSERT INTO mods (user_id, board_id, accepted, invite_rescinded, perm_content, perm_full) "
                    "VALUES (11, 1, 1, NULL, NULL, 1)"))
    row = store.one_for(db, cid, 11)
    assert bool(row.mod) and bool(row.content_mod)
    assert [r.id for r in store.joined_guilds_for(db, 11)] == [cid]
    assert store.writer_ids(db, cid, 1) == [] and (store.join(db, cid, 11, NOW) or store.writer_ids(db, cid, 1) == [11])
    assert 11 in store.standing_sets(db, 1, [11])["mod"]


# --- part 2: categories, rules to accept, mentions, mute, threads, pins, search -----------------------------------------------

def test_part_two_rules():
    assert rules.clean_category("  #Off  topic ") == "Off topic"
    for bad in ("", "#", "x" * (rules.CATEGORY_MAX + 1), "a<b", "zero​width"):
        with pytest.raises(rules.Refused):
            rules.clean_category(bad)
    assert rules.category_refusal(rules.MAX_CATEGORIES - 1, False) is None
    assert "at most" in rules.category_refusal(rules.MAX_CATEGORIES, False) and "already" in rules.category_refusal(0, True)

    # accepting the rules is bound to their exact text
    key = rules.rules_key("Be kind")
    assert len(key) == 32 and key == rules.rules_key("Be kind") and key != rules.rules_key("Be kind.")
    assert rules.rules_pending(True, "Be kind", None) and rules.rules_pending(True, "Be kind", "old")
    assert not rules.rules_pending(True, "Be kind", key) and not rules.rules_pending(False, "Be kind", None)
    assert not rules.rules_pending(True, "", None)                       # nothing to accept
    assert rules.gate_refusal(True, "") and rules.gate_refusal(True, "x") is None and rules.gate_refusal(False, "") is None
    # ... and asked after every other reason, and never of a guildmaster
    assert _write(rules_pending=True) == rules.RULES_PENDING
    assert _write(rules_pending=True, mod=True, content_mod=True) is None
    assert _write(rules.MODS, rules_pending=True) == "Only the guild's guildmasters write in this channel."
    assert _write(joined=False, rules_pending=True) == rules.JOIN_FIRST
    assert _write(suspended=True, rules_pending=True) == "Your account is suspended."


def test_mentions_mute_threads_and_search_terms():
    assert rules.parse_mentions("hi @alice and @Bob_2, @alice again; mail a@bob.com; @@x @ab @éclair") == ["alice", "Bob_2"]
    assert rules.parse_mentions("@a_b @c_d @e_f @g_h @i_j @k_l @m_n") == ["a_b", "c_d", "e_f", "g_h", "i_j"]      # five at most
    assert rules.parse_mentions("(@alice) and @alice.") == ["alice"] and rules.parse_mentions(None) == []
    text_ = rules.mention_text("al*ice", "Gen[eral]", "pets", "/+pets/chat?c=3")
    assert "al" in text_ and "*" not in text_ and "[eral]" not in text_ and "(/+pets/chat?c=3)" in text_

    assert rules.mute_until(0, NOW) == 0 and rules.mute_until("8", NOW) == NOW + 8 * 3600
    assert rules.mute_until(-1, NOW) == rules.FOREVER
    for bad in (2, "x", None, 99):
        with pytest.raises(rules.Refused):
            rules.mute_until(bad, NOW)
    assert rules.is_muted(NOW + 5, NOW) and not rules.is_muted(NOW, NOW) and not rules.is_muted(0, NOW) and rules.is_muted(rules.FOREVER, NOW)

    root = (1, 7, "text", 0, "")
    assert rules.thread_refusal(root) is None
    assert rules.thread_refusal(None) and rules.thread_refusal((1, 7, "", NOW, "")) and rules.thread_refusal((1, 7, "x", 0, "$other"))

    assert rules.search_terms("  Hello   WORLD x a  ") == ["hello", "world"]
    assert len(rules.search_terms("one two three four five six seven")) == rules.SEARCH_TERMS and rules.search_terms(None) == []
    assert rules.like_pattern("50%_off\\x") == "%50\\%\\_off\\\\x%"


def test_categories_are_the_guilds_own_and_group_the_list(db):
    cats = store.make_category(db, 1, "Games", 0), store.make_category(db, 1, "Art", 0)
    other = store.make_category(db, 2, "Games", 0)
    assert [c.name for c in store.categories_of(db, 1)] == ["Games", "Art"] and store.category_count(db, 1) == 2
    assert store.category_taken(db, 1, "games") and store.category_taken(db, 1, "GAMES") and not store.category_taken(db, 2, "Art")
    assert not store.category_taken(db, 1, "Games", except_id=cats[0])
    assert store.category(db, 1, other) is None and store.category(db, 2, other) is not None       # never another guild's
    store.rename_category(db, 1, other, "Hijacked", 0)
    assert store.category(db, 2, other).name == "Games"

    plain = make(db, name="Plain")
    art = store.create(db, 1, "text", "Sketches", "", "", 0, "members", "members", 0, "!1Sketches:test", NOW, cats[1], False)
    game = store.create(db, 1, "text", "Lobby", "", "", 0, "members", "members", 0, "!1Lobby:test", NOW, cats[0], True)
    rows = store.of_board_for(db, 1, 1)
    assert [r.name for r in rows] == ["Plain", "Lobby", "Sketches"]                    # no category first, then by category order
    assert rows[1].category_name == "Games" and rows[1].rules_gate and rows[0].category_name is None and not rows[0].rules_gate
    store.move_categories(db, 1, [cats[1], cats[0], other])                            # another guild's id is ignored
    assert [r.name for r in store.of_board_for(db, 1, 1)] == ["Plain", "Sketches", "Lobby"]
    assert store.category(db, 2, other).position != 3
    store.delete_category(db, 1, cats[0])
    after = {r.name: r for r in store.of_board_for(db, 1, 1)}
    assert after["Lobby"].category_id is None and after["Sketches"].category_name == "Art"      # its channels stay
    store.update(db, 1, plain, "Plain", "", "", 0, "members", "members", 0, cats[1], False)
    assert store.get(db, plain).category_id == cats[1]
    store.rescan_category_severities(db, lambda t: 2 if "Art" in t else 0)
    assert store.category(db, 1, cats[1]).word_severity == 2


def test_threads_pins_and_the_text_pins_keep(db):
    cid = make(db)
    first = store.record_post(db, cid, 1, "$root", "the question", 0, NOW)
    store.record_post(db, cid, 2, "$answer", "an answer", 0, NOW + 5, "$root")
    assert store.record_post(db, cid, 2, "$answer", "again", 0, NOW + 6, "$root") == store.post_by_event(db, cid, "$answer")[0]
    assert store.post_by_event(db, cid, "$answer")[4] == "$root" and store.post_by_event(db, cid, "$root")[4] == ""
    assert rules.thread_refusal(store.post_by_event(db, cid, "$root")) is None
    assert rules.thread_refusal(store.post_by_event(db, cid, "$answer"))                 # a thread of a thread: no

    assert store.pin_count(db, cid) == 0 and not store.is_pinned(db, cid, first)
    store.pin(db, cid, first, 9, NOW + 10)
    store.pin(db, cid, first, 9, NOW + 11)                                               # twice is one pin
    assert store.pin_count(db, cid) == 1 and store.is_pinned(db, cid, first)
    pinned = store.pins_of(db, cid)
    assert len(pinned) == 1 and pinned[0][0] == "$root" and pinned[0][2] == "the question"
    store.blank_old_text(db, NOW + 10_000_000)                                          # old text goes, a pinned message keeps its own
    assert store.post_by_event(db, cid, "$root")[2] == "the question" and store.post_by_event(db, cid, "$answer")[2] == ""
    store.remove_post(db, cid, "$root", NOW + 20)                                       # a removed message is not pinned any more
    assert store.pin_count(db, cid) == 0 and store.pins_of(db, cid) == [] and not store.unpin(db, cid, first)
    other = make(db, name="Other")
    store.pin(db, other, first, 9, NOW)
    assert store.pins_of(db, cid) == []                                                  # a pin is for one channel


def test_a_members_own_mute_the_rules_they_accepted_and_their_mentions(db):
    cid = make(db)
    db.execute(text("UPDATE guild_channels SET rules = 'Be kind', rules_gate = 1 WHERE id = :c"), {"c": cid})
    store.touch(db, "!1General:test", NOW + 10)
    row = lambda: store.one_for(db, cid, 1)                                              # noqa: E731
    assert row().muted_until == 0 and row().acked_key is None and row().mentions == 0 and store.unread(row(), NOW + 20)
    store.set_mute(db, cid, 1, NOW + 3600)
    assert row().muted_until == NOW + 3600 and not store.unread(row(), NOW + 20) and store.unread(row(), NOW + 7200)
    assert store.unread(row()) and store.muted_until(db, cid, 1) == NOW + 3600           # unread without a clock ignores mutes
    store.set_mute(db, cid, 1, 0)
    assert store.unread(row(), NOW + 20) and store.muted_until(db, cid, 2) == 0

    key = rules.rules_key("Be kind")
    assert rules.rules_pending(row().rules_gate, row().rules, row().acked_key)
    store.accept_rules(db, cid, 1, key, NOW)
    assert not rules.rules_pending(row().rules_gate, row().rules, row().acked_key)
    assert rules.rules_pending(1, "Be kind, always", row().acked_key)                    # the rules changed: asked again
    store.accept_rules(db, cid, 1, rules.rules_key("Be kind, always"), NOW + 1)
    assert store.one_for(db, cid, 2).acked_key is None                                   # one person's answer is theirs

    post = store.record_post(db, cid, 2, "$m", "hi @u1", 0, NOW)
    store.record_mentions(db, cid, post, [1], NOW)
    assert row().mentions == 1 and store.one_for(db, cid, 2).mentions == 0
    assert store.unread_total(db, 1, NOW + 20) == 1                                      # never opened: only the mention counts
    store.join(db, cid, 1, NOW)
    store.touch(db, "!1General:test", NOW + 15)                                          # news after they opened it
    assert store.unread_total(db, 1, NOW + 20) == 2                                      # the channel has news and one mention
    store.set_mute(db, cid, 1, NOW + 3600)
    assert store.unread_total(db, 1, NOW + 20) == 1                                      # a muted channel has none; the mention stays
    store.clear_mentions(db, cid, 1, NOW + 30)
    assert row().mentions == 0 and store.unread_total(db, 1, NOW + 20) == 0
    store.record_mentions(db, cid, post, [1], NOW)
    store.remove_post(db, cid, "$m", NOW + 40)                                           # a removed message mentions nobody
    assert row().mentions == 0


def test_search_looks_in_one_channel_at_the_words_still_kept(db):
    a, b = make(db, name="A"), make(db, name="B")
    store.record_post(db, a, 1, "$1", "The quick brown fox", 0, NOW)
    store.record_post(db, a, 1, "$2", "a lazy dog and the fox", 0, NOW + 5)
    store.record_post(db, a, 2, "$3", "100% sure_thing", 1, NOW + 9)
    store.record_post(db, a, 2, "$4", "old fox", 0, NOW - 200 * 86400)
    store.record_post(db, b, 1, "$5", "fox in another channel", 0, NOW + 6)
    store.record_post(db, a, 1, "$6", "removed fox", 0, NOW + 7)
    store.remove_post(db, a, "$6", NOW + 8)
    since = NOW - 90 * 86400
    found = lambda q: [row[0] for row in store.search(db, a, rules.search_terms(q), since)]       # noqa: E731
    assert found("FOX") == ["$2", "$1"]                                 # newest first, any case, this channel only
    assert found("fox lazy") == ["$2"] and found("quick fox dog") == [] # every word
    assert found("100% sure_") == ["$3"] and found("%%") == [] and found("_ox") == []             # wildcards are text
    assert found("") == [] and store.search(db, a, ["fox"], since, limit=1)[0][0] == "$2"
    assert store.search(db, a, ["fox"], NOW - 300 * 86400)[-1][0] == "$4"
    store.blank_old_text(db, NOW - 100 * 86400)
    assert "$4" not in found("fox")                                      # blanked text is no longer searched


def test_a_mention_notice_goes_once_an_hour_per_writer_person_and_channel():
    r = FakeRedis()
    assert store.notice_due(r, 1, 7, 8) and not store.notice_due(r, 1, 7, 8)
    assert store.notice_due(r, 1, 7, 9) and store.notice_due(r, 1, 6, 8) and store.notice_due(r, 2, 7, 8)

    class Down:
        def set(self, *a, **k):
            raise RuntimeError("Redis is down")

    assert store.notice_due(Down(), 1, 7, 8) is False                   # no spam when it can't be counted


def test_a_message_in_the_same_second_as_a_read_is_still_news(db):
    """Unread counts messages, not seconds: reading and the next message can share a second."""
    cid = make(db)
    room = "!1General:test"
    store.join(db, cid, 1, NOW)
    store.join(db, cid, 2, NOW)
    row = lambda uid: store.one_for(db, cid, uid)                       # noqa: E731
    store.touch(db, room, NOW, 2)                                       # 2 writes it: they have read it, 1 has not
    assert store.unread(row(1)) and not store.unread(row(2))
    store.mark_read(db, cid, 1, NOW)
    assert not store.unread(row(1))
    store.touch(db, room, NOW, 2)                                       # the same second again
    assert store.unread(row(1)) and not store.unread(row(2))
    store.mark_read(db, cid, 1, NOW)
    store.touch(db, room, NOW)                                          # nobody's own (a removed message's reaction etc.)
    assert store.unread(row(1)) and store.unread(row(2))
    assert store.one_for(db, cid, 3).activity_seq == 3 and store.unread(store.one_for(db, cid, 3))   # never opened
    assert store.unread_total(db, 1, NOW) == 1
    store.touch(db, "!nowhere:test", NOW, 1)
    assert store.touch(db, "!nowhere:test", NOW, 1) is False


def test_a_voice_room_holds_only_the_people_in_its_call(db):
    cid = store.create(db, 1, "voice", "Lounge", "", "", 0, "members", "members", 0, "!voice:test", NOW)
    for uid in (1, 2, 3):
        store.join(db, cid, uid, NOW - 3600)
    store.join(db, cid, 4, NOW)                                    # just came in: not in the call yet
    assert sorted(store.voice_stale(db, cid, {1}, NOW - 600)) == [2, 3]
    assert store.voice_stale(db, cid, {1, 2, 3}, NOW - 600) == []


def test_the_sync_step_empties_a_voice_room_and_skips_a_stage(db):
    from ruqqus.helpers import guild_channel_sync as sync

    voice = store.create(db, 1, "voice", "Lounge", "", "", 0, "members", "members", 0, "!voice:test", NOW)
    stage = store.create(db, 1, "stage", "Stage", "", "", 0, "members", "mods", 0, "stage:abc", NOW, None, False, 77)
    assert store.get(db, stage).space_post_id == 77 and store.get(db, voice).space_post_id is None
    db.execute(text("INSERT INTO subscriptions (user_id, board_id) VALUES (1, 1), (2, 1), (3, 1)"))
    for uid in (1, 2, 3):
        store.join(db, voice, uid, NOW - 3600)
    kicked = []
    left = sync.run(db, NOW, lambda room, mxid, why: kicked.append((room, mxid)), lambda *a: None, lambda uid: f"@u{uid}:test",
                    roster_of=lambda room: {1})
    assert sorted(kicked) == [("!voice:test", "@u2:test"), ("!voice:test", "@u3:test")] and left == 2
    assert store.readers(db, voice) == [1]
    # no roster given (Redis down): nobody is let go for it
    store.join(db, voice, 2, NOW - 3600)
    assert sync.run(db, NOW, lambda *a: None, lambda *a: None, lambda uid: f"@u{uid}:test") == 0


def test_a_voice_room_keeps_its_keys_while_anyone_is_in_it():
    from ruqqus.helpers import call_store

    class Spy(FakeRedis):
        def __init__(self):
            super().__init__()
            self.kept = []

        def expire(self, key, seconds):
            self.kept.append(key)
            return super().expire(key, seconds)

    r = Spy()
    call_store.refresh(r, "!voice:test")                      # nobody in it: nothing to keep
    assert r.kept == []
    call_id, started = call_store.join(r, "!voice:test", 1, "DEV", "audio", False, NOW)
    r.kept.clear()
    call_store.refresh(r, "!voice:test")
    assert sorted(r.kept) == sorted(["call:meta:!voice:test", "call:roster:!voice:test", f"call:id:{call_id}"])


# --- part 4: roles ---------------------------------------------------------------------------------------------------------------

def test_role_rules():
    from ruqqus.helpers import guild_roles as roles

    assert roles.clean_name("  Mod   helper ") == "Mod helper" and roles.clean_colour("") == "gray" and roles.clean_colour("Teal") == "teal"
    for bad in ("", "x" * (roles.NAME_MAX + 1), "a@b", "bell\x07"):
        with pytest.raises(roles.Refused):
            roles.clean_name(bad)
    with pytest.raises(roles.Refused):
        roles.clean_colour("hotpink")                                     # the palette, never free CSS
    assert roles.clean_perms(lambda key: key == "perm_pin") == {"slow_exempt": False, "remove_messages": False, "pin": True}
    assert roles.make_refusal(roles.MAX_ROLES - 1, False) is None and "at most" in roles.make_refusal(roles.MAX_ROLES, False)
    assert "already" in roles.make_refusal(0, True) and "already" in roles.rename_refusal(True)
    assert [roles.parse_overwrite(v) for v in (None, "", "0", "1", 2)] == [0, 0, 0, 1, 2]
    for bad in ("3", "-1", "allow"):
        with pytest.raises(roles.Refused):
            roles.parse_overwrite(bad)
    assert roles.resolve(True, True) is True and roles.resolve(False, True) is False and roles.resolve(False, False) is None
    ok = dict(in_guild=True, exiled=False, bot=False, gone=False, count=0, already=False)
    assert roles.member_refusal(**ok) is None
    for bad in ({"in_guild": False}, {"exiled": True}, {"bot": True}, {"gone": True}, {"already": True}, {"count": roles.MAX_MEMBERS}):
        assert roles.member_refusal(**{**ok, **bad})


def test_what_a_role_says_over_the_ladder():
    # reading: a role allows over the ladder, denies under it; never against the absolute refusals or a guildmaster
    assert _read(rules.MODS, joined=True, role_read=True) is None
    assert _read(rules.MEMBERS, joined=True, role_read=False) == rules.UNAVAILABLE
    assert _read(rules.MEMBERS, joined=True, role_read=None) is None and _read(rules.MODS, joined=True, role_read=None) == rules.UNAVAILABLE
    for bad in ({"bot": True}, {"exiled": True}, {"can_view": False}, {"hidden": True}):
        assert _read(rules.MEMBERS, role_read=True, **bad) == rules.UNAVAILABLE      # no role brings someone past these
    assert _read(rules.MODS, mod=True, role_read=False) is None                     # a role never takes a guildmaster's access
    # writing: allow lets them (over the level), deny stops them (not a guildmaster), the rest as ever
    assert _write(rules.MODS, role_write=True) is None
    assert _write(role_write=False) == "You can't write in this channel." and _write(role_write=False, mod=True) is None
    assert _write(rules.MODS, role_write=True, suspended=True) == "Your account is suspended."
    assert _write(role_write=True, muted=True) and _write(role_write=True, rules_pending=True) == rules.RULES_PENDING
    assert _write(rules.MODS, read=rules.UNAVAILABLE, role_write=True) == rules.UNAVAILABLE      # not readable: not writable


def _roles(db):
    from ruqqus.helpers import guild_role_store as role_store
    return role_store


def test_roles_are_a_guilds_own(db):
    rs = _roles(db)
    helper = rs.make_role(db, 1, "Helper", 0, "green", {"remove_messages": True, "pin": True})
    other = rs.make_role(db, 2, "Helper", 0, "red", {})
    assert [r.name for r in rs.roles_of(db, 1)] == ["Helper"] and rs.role_count(db, 1) == 1
    assert rs.name_taken(db, 1, "helper") and rs.name_taken(db, 1, "HELPER") and not rs.name_taken(db, 1, "Helper", except_id=helper)
    got = rs.role(db, 1, helper)
    assert got.colour == "green" and got.perm_remove_messages and got.perm_pin and not got.perm_slow_exempt
    assert rs.role(db, 1, other) is None and rs.role(db, 2, other) is not None and rs.role(db, 1, None) is None
    rs.update_role(db, 1, other, "Hijacked", 0, "blue", {"pin": True})                # another guild's role: nothing happens
    assert rs.role(db, 2, other).name == "Helper"
    rs.update_role(db, 1, helper, "Mod helper", 2, "teal", {"slow_exempt": True})
    changed = rs.role(db, 1, helper)
    assert (changed.name, changed.word_severity, changed.colour) == ("Mod helper", 2, "teal")
    assert changed.perm_slow_exempt and not changed.perm_pin
    second = rs.make_role(db, 1, "Regular", 0, "gray", {})
    rs.move_roles(db, 1, [second, helper, other])                                      # another guild's id is ignored
    assert [r.name for r in rs.roles_of(db, 1)] == ["Regular", "Mod helper"] and rs.role(db, 2, other).position == 1
    rs.rescan_severities(db, lambda t: 2 if "Regular" in t else 0)
    assert rs.role(db, 1, second).word_severity == 2


def test_who_has_a_role_and_which_shows(db):
    rs = _roles(db)
    first = rs.make_role(db, 1, "Veteran", 0, "gold" if False else "orange", {})
    second = rs.make_role(db, 1, "Helper", 0, "green", {})
    other = rs.make_role(db, 2, "Elsewhere", 0, "red", {})
    for uid in (1, 2):
        rs.add_member(db, first, uid, 9, NOW)
    rs.add_member(db, first, 1, 9, NOW + 5)                                           # twice is once
    rs.add_member(db, second, 2, 9, NOW + 1)
    rs.add_member(db, other, 1, 9, NOW)
    assert rs.members_of(db, first) == [1, 2] and rs.member_count(db, first) == 2 and rs.has_role(db, second, 2) and not rs.has_role(db, second, 1)
    assert rs.top_roles(db, 1, [1, 2, 3]) == {1: ("Veteran", "orange", 0), 2: ("Veteran", "orange", 0)}   # the first by the guildmasters' order, this guild's only
    rs.move_roles(db, 1, [second, first])
    assert rs.top_roles(db, 1, [2])[2][0] == "Helper" and rs.top_roles(db, 2, [1]) == {1: ("Elsewhere", "red", 0)}
    assert rs.remove_member(db, second, 2) and not rs.remove_member(db, second, 2)
    rs.drop_roles(db, 1, 1)                                                            # left the guild: its roles go, another guild's stay
    assert not rs.has_role(db, first, 1) and rs.has_role(db, other, 1) and rs.has_role(db, first, 2)
    rs.delete_role(db, 1, first)
    assert rs.role(db, 1, first) is None and rs.member_count(db, first) == 0
    rs.delete_role(db, 1, other)                                                       # not this guild's: nothing
    assert rs.role(db, 2, other) is not None


def test_a_roles_words_about_a_channel(db):
    rs = _roles(db)
    staff = make(db, name="Staff", read="mods", write="mods")
    lobby = make(db, name="Lobby")
    muted_role = rs.make_role(db, 1, "Quiet", 0, "gray", {"slow_exempt": True, "remove_messages": True})
    insider = rs.make_role(db, 1, "Insider", 0, "blue", {"pin": True})
    elsewhere = rs.make_role(db, 2, "Foreign", 0, "red", {})
    for uid, role in ((1, insider), (2, muted_role), (3, insider), (3, muted_role), (4, elsewhere)):
        rs.add_member(db, role, uid, 9, NOW)
    rs.set_overwrites(db, 1, staff, {insider: (1, 0), muted_role: (0, 0), elsewhere: (1, 1)})        # a foreign role is ignored
    rs.set_overwrites(db, 1, lobby, {muted_role: (0, 2), insider: (0, 1)})
    assert rs.overwrites_of(db, staff) == {insider: (1, 0)} and rs.overwrites_of(db, lobby) == {muted_role: (0, 2), insider: (0, 1)}

    row = lambda cid, uid: store.one_for(db, cid, uid)                                  # noqa: E731
    assert bool(row(staff, 1).role_allow_read) and not row(staff, 1).role_deny_read
    assert bool(row(lobby, 2).role_deny_write) and not row(lobby, 2).role_allow_write
    assert bool(row(lobby, 1).role_allow_write) and bool(row(lobby, 1).role_allow_read)    # allowed to write: allowed to read
    both = row(lobby, 3)                                                                # one role allows, another denies: allow wins
    assert bool(both.role_allow_write) and bool(both.role_deny_write)
    assert not row(staff, 2).role_allow_read and not row(staff, 4).role_allow_read and not row(lobby, 4).role_deny_write
    assert [bool(row(lobby, u).role_slow_exempt) for u in (1, 2, 3, 4)] == [False, True, True, False]
    assert [bool(row(lobby, u).role_remove) for u in (1, 2, 3, 4)] == [False, True, True, False]
    assert [bool(row(lobby, u).role_pin) for u in (1, 2, 3, 4)] == [True, False, True, False]
    assert not row(staff, 4).role_slow_exempt                                           # a role of another guild gives nothing here

    assert rs.overwrites_for_users(db, lobby, [1, 2, 3, 4]) == {1: (True, False, True, False), 2: (False, False, False, True), 3: (True, False, True, True)}
    rs.set_overwrites(db, 1, lobby, {})
    assert rs.overwrites_of(db, lobby) == {} and not row(lobby, 1).role_allow_write
    rs.set_overwrites(db, 1, staff, {insider: (1, 0)})
    rs.delete_role(db, 1, insider)                                                      # the role takes its overwrites with it
    assert rs.overwrites_of(db, staff) == {}


def test_the_sync_step_reads_roles_too_and_an_announcement_is_written_by_a_role(db):
    rs = _roles(db)
    staff = make(db, name="Staff", read="mods", write="mods")
    news = make(db, name="News", kind="announcement", write="mods")
    helper = rs.make_role(db, 1, "Helper", 0, "green", {})
    banned = rs.make_role(db, 1, "Shadow", 0, "gray", {})
    db.execute(text("INSERT INTO subscriptions (user_id, board_id) VALUES (1, 1), (2, 1), (3, 1)"))
    rs.add_member(db, helper, 1, 9, NOW)
    rs.add_member(db, banned, 3, 9, NOW)
    rs.set_overwrites(db, 1, staff, {helper: (1, 0), banned: (2, 0)})
    rs.set_overwrites(db, 1, news, {helper: (0, 1)})
    for uid in (1, 2, 3):
        store.join(db, staff, uid, NOW)
        store.join(db, news, uid, NOW)
    channel = store.get(db, staff)
    # the guildmasters' room: a subscriber who is a Helper stays; one without a role, and one a role denies, go
    assert sorted(store.who_should_leave(db, channel, (False, False), NOW + 60)) == [2, 3]
    # an announcement room's writers: the guildmasters with the content permission and the people in a role that may write
    db.execute(text("INSERT INTO mods (user_id, board_id, accepted, perm_content) VALUES (2, 1, 1, 1)"))
    assert sorted(store.writer_ids(db, news, 1)) == [1, 2]
    store.leave(db, news, 1)
    assert store.writer_ids(db, news, 1) == [2]                                         # only while they are in the room


# --- part 5: auto-moderation, bot feeds, encrypted channels ------------------------------------------------------------------------

def test_the_guilds_own_rules_for_its_channels():
    from types import SimpleNamespace as NS
    from ruqqus.helpers import guild_automod as a

    assert a.clean_days("", "x") == 0 and a.clean_days("30", "x") == 30 and a.clean_days(None, "x") == 0
    for bad in ("-1", "366", "ten", "1.5"):
        with pytest.raises(a.Refused):
            a.clean_days(bad, "The account age")
    assert a.clean_words("Spam\r\n  BUY   now \nspam\n\n") == "spam\nbuy now"
    for bad in ("x" * (a.WORD_MAX + 1), "bell\x07", "\n".join(f"w{i}" for i in range(a.MAX_WORDS + 1))):
        with pytest.raises(a.Refused):
            a.clean_words(bad)
    assert a.has_link("see www.example.com") and a.has_link("HTTPS://x.test/a") and not a.has_link("a link-ish thing")
    assert a.has_blocked_word("I sell SPAM!", "spam") and not a.has_blocked_word("spammer", "spam")
    assert a.has_blocked_word("buy   NOW please", "buy now") and not a.has_blocked_word("anything", "")
    assert a.has_blocked_word("(spam)", "spam\nother") and not a.has_blocked_word("sp am", "spam")

    s = NS(min_account_days=3, min_member_days=2, link_member_days=7, blocked_words="buy now")
    ask = lambda **kw: a.refusal(s, **{**dict(account_days=30, member_days=30, body="hello", exempt=False), **kw})    # noqa: E731
    assert ask() is None
    assert "account is too new" in ask(account_days=1) and "2 days" in ask(member_days=1) and "1 day" not in ask(member_days=1)
    assert ask(member_days=3, body="see http://x.test") and "7 days" in ask(member_days=3, body="see http://x.test")
    assert ask(member_days=9, body="see http://x.test") is None                         # in long enough for links
    assert "doesn't allow" in ask(body="BUY NOW!!") and "buy" not in ask(body="BUY NOW!!")          # never says which word
    assert ask(account_days=0, member_days=0, body="buy now http://x", exempt=True) is None          # guildmasters
    assert a.refusal(None, account_days=0, member_days=0, body="buy now", exempt=False) is None      # no settings
    assert a.refusal(NS(min_account_days=0, min_member_days=0, link_member_days=0, blocked_words=""), account_days=0,
                     member_days=0, body="www.x.com", exempt=False) is None


def test_encrypted_channels_are_only_where_the_site_cannot_be_asked_to_read_them():
    ok = dict(kind="text", encrypted=True, guild_private=True, read_level="members", write_level="members", gate=False)
    assert rules.encrypted_refusal(**ok) is None and rules.encrypted_refusal(**{**ok, "encrypted": False, "guild_private": False}) is None
    for bad in ({"kind": "announcement"}, {"kind": "voice"}, {"guild_private": False}, {"write_level": "mods"}, {"gate": True}):
        assert rules.encrypted_refusal(**{**ok, **bad}), bad
    assert rules.full_refusal(rules.MAX_ENCRYPTED_MEMBERS - 1, True) is None and rules.full_refusal(rules.MAX_ENCRYPTED_MEMBERS, True)
    assert rules.full_refusal(rules.MAX_ENCRYPTED_MEMBERS, False) is None                     # a plain channel is bigger


def test_the_guilds_settings_and_when_someone_came_in(db):
    assert store.automod_of(db, 1) is None
    store.save_automod(db, 1, 3, 2, 7, "spam")
    store.save_automod(db, 1, 4, 0, 0, "spam\nscam")                                         # saved again: the same row
    got = store.automod_of(db, 1)
    assert (got.min_account_days, got.min_member_days, got.link_member_days, got.blocked_words) == (4, 0, 0, "spam\nscam")
    assert store.automod_of(db, 2) is None                                                    # a guild's own
    assert store.member_since(db, 1, 1) is None
    db.execute(text("INSERT INTO subscriptions (user_id, board_id, created_utc) VALUES (1, 1, 500)"))
    assert store.member_since(db, 1, 1) == 500
    db.execute(text("INSERT INTO contributors (user_id, board_id, created_utc) VALUES (1, 1, 300)"))
    assert store.member_since(db, 1, 1) == 300                                                # whichever came first
    db.execute(text("INSERT INTO subscriptions (user_id, board_id, created_utc, is_active) VALUES (2, 1, 100, 0)"))
    assert store.member_since(db, 1, 2) is None and store.member_since(db, 2, 1) is None      # left, or another guild


def test_an_encrypted_room_is_for_people_who_may_write_in_it(db):
    cid = store.create(db, 1, "text", "Vault", "", "", 0, "members", "members", 0, "!vault:test", NOW, None, False, None, True)
    assert store.get(db, cid).encrypted and not store.get(db, make(db, name="Plain")).encrypted
    db.execute(text("UPDATE boards SET is_private = 1 WHERE id = 1"))
    db.execute(text("INSERT INTO contributors (user_id, board_id) VALUES (1, 1), (2, 1), (3, 1)"))
    db.execute(text("INSERT INTO chatbans (user_id, board_id) VALUES (2, 1)"))
    db.execute(text("INSERT INTO mods (user_id, board_id, accepted, invite_rescinded) VALUES (3, 1, 1, 0)"))
    db.execute(text("INSERT INTO chatbans (user_id, board_id) VALUES (3, 1)"))
    for uid in (1, 2, 3):
        store.join(db, cid, uid, NOW)
    assert store.members_among(db, cid, [1, 2, 9]) == {1, 2}                                  # the people in its room, nobody else
    assert store.encrypted_rooms_of(db, 1, 2) == [(cid, "!vault:test")] and store.encrypted_rooms_of(db, 1, 9) == []
    channel = store.get(db, cid)
    # a muted person is not in an encrypted room (the site can't stop them writing in it); a guildmaster is never put out
    assert store.who_should_leave(db, channel, (True, False), NOW + 60) == [2]
    plain = store.create(db, 1, "text", "Open", "", "", 0, "members", "members", 0, "!open:test", NOW)
    store.join(db, plain, 2, NOW)
    assert store.who_should_leave(db, store.get(db, plain), (True, False), NOW + 60) == []     # in a plain one they read, they just can't write


def test_bots_feed_an_announcement_channel(db):
    from ruqqus.helpers import guild_channel_feeds as feeds

    news = make(db, name="News", kind="announcement", write="mods")
    other = make(db, name="Talk")
    for sid, author, title, url, public in ((10, 7, "Old story", "http://a.test/old", 1), (11, 7, "First", "http://a.test/1", 1),
                                           (12, 7, "Second", "", 1), (13, 7, "Private", "http://a.test/p", 0),
                                           (14, 8, "Another bot", "http://b.test/", 1)):
        db.execute(text("INSERT INTO submissions (id, author_id, post_public) VALUES (:i, :a, :p)"), {"i": sid, "a": author, "p": public})
        db.execute(text("INSERT INTO submissions_aux (id, title, url) VALUES (:i, :t, :u)"), {"i": sid, "t": title, "u": url})
    db.execute(text("INSERT INTO submissions (id, author_id, post_public, repost_id) VALUES (15, 7, 1, 10)"))      # a forwarded copy
    db.execute(text("INSERT INTO submissions_aux (id, title, url) VALUES (15, 'copy', '')"))
    db.execute(text("INSERT INTO submissions (id, author_id, post_public, deleted_utc) VALUES (16, 7, 1, 99)"))
    db.execute(text("INSERT INTO submissions_aux (id, title, url) VALUES (16, 'deleted', '')"))
    assert store.latest_bot_post(db, 7) == 16 and store.latest_bot_post(db, 99) == 0
    assert store.add_feed(db, news, 7, 1, NOW, 10) and not store.add_feed(db, news, 7, 1, NOW, 10)         # a new feed starts after history
    assert [(f[0], f[3]) for f in store.feeds_of(db, news)] == [(7, 10)]
    assert [row[0] for row in store.bot_posts_after(db, 7, 10, 5)] == [11, 12]                    # public, live, original, its own
    assert [f[:4] for f in store.all_feeds(db)] == [(news, "!1News:test", 1, 7)]
    store.add_feed(db, other, 8, 1, NOW, 0)
    assert len(store.all_feeds(db)) == 1                                                            # only an announcement channel's

    log, sent_to, power = [], [], []
    def send(room, mxid, content):
        sent_to.append((room, mxid, content["body"]))
        return f"$event{len(sent_to)}"
    kw = dict(provision=lambda bot: log.append(("provision", bot)), put_in_room=lambda room, mxid: log.append(("put", room, mxid)),
              set_writers=lambda room, mxids: power.append((room, mxids)), send=send, mxid_for=lambda uid: f"@u{uid}:test",
              secret=SECRET, rate=lambda text_: 1 if "Second" in text_ else 0, link_of=lambda pid, url: url or f"https://site.test/post/{pid}",
              log=log.append)
    r = FakeRedis()
    assert feeds.run(db, r, NOW, **kw) == 2
    assert sent_to == [("!1News:test", "@u7:test", "First\nhttp://a.test/1"), ("!1News:test", "@u7:test", "Second\nhttps://site.test/post/12")]
    assert ("provision", 7) in log and ("put", "!1News:test", "@u7:test") in log and power and "@u7:test" in power[-1][1]
    first = store.post_by_event(db, news, "$event1")
    assert first[1] == 7 and first[2] == "First\nhttp://a.test/1" and store.post_by_event(db, news, "$event2")[5] == 1     # the rating is kept
    assert [f[3] for f in store.feeds_of(db, news)] == [12]
    # what was sent is signed as the bot, for this room: the webhook accepts it
    assert store.in_room(db, news, 7)
    assert feeds.run(db, r, NOW + 60, **kw) == 0 and len(sent_to) == 2                             # nothing new: nothing sent
    db.execute(text("INSERT INTO submissions (id, author_id, post_public) VALUES (20, 7, 1)"))
    db.execute(text("INSERT INTO submissions_aux (id, title, url) VALUES (20, 'Third', 'http://a.test/3')"))
    def down(room, mxid, content):
        raise RuntimeError("Matrix is down")
    assert feeds.run(db, r, NOW + 120, **{**kw, "send": down}) == 0
    assert [f[3] for f in store.feeds_of(db, news)] == [12]                                         # the cursor stays: tried again
    assert feeds.run(db, r, NOW + 180, **kw) == 1 and [f[3] for f in store.feeds_of(db, news)] == [20]
    # a bot that can't get a chat account is skipped, not forgotten
    db.execute(text("INSERT INTO submissions (id, author_id, post_public) VALUES (21, 7, 1)"))
    db.execute(text("INSERT INTO submissions_aux (id, title, url) VALUES (21, 'Fourth', '')"))
    store.leave(db, news, 7)
    def refuse(bot):
        raise RuntimeError("orphan account")
    assert feeds.run(db, r, NOW + 240, **{**kw, "provision": refuse}) == 0 and [f[3] for f in store.feeds_of(db, news)] == [20]
    # a bot's day is limited
    for i in range(rules.FEED_PER_DAY):
        store.record_post(db, news, 7, f"$bulk{i}", "x", 0, NOW + 250)
    store.join(db, news, 7, NOW)
    assert feeds.run(db, r, NOW + 260, **kw) == 0
    assert feeds.message_for("  A   spaced   title ", "http://x.test") == "A spaced title\nhttp://x.test" and len(feeds.message_for("x" * 5000, "")) == rules.MESSAGE_MAX
    assert feeds.post_link("site.test", True, 12, "") == "https://site.test/post/c" and feeds.post_link("localhost", 0, 12, "http://e.test") == "http://e.test"
    feeds.provision_bot(db, 7, NOW, lambda bot_id, adopt_existing: f"@ruqqus_{bot_id}:test")
    feeds.provision_bot(db, 7, NOW, lambda *a, **k: 1 / 0)                                          # already known: nothing is asked of Matrix
    assert db.execute(text("SELECT matrix_user_id FROM chat_identities WHERE user_id = 7")).scalar() == "@ruqqus_7:test"
