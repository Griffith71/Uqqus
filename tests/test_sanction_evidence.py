"""Documentation for a sanction (helpers/sanction_evidence.py; the Log, 2026-11-14): the content an exile cites, kept as it was.

The promises: only a named post or comment of the exiled account, made in this guild and not deleted, can be cited (an anonymous item only for an alias
exile, by the caller's say-so); at most five are kept, once each, as private `evidence` copies tied to the entry, written as the exiled account's
and kept a year; the public Log is shown only the ids of named, public content; and a copy is opened by the person, the deciding guild's
guildmasters and the admins, and nobody else."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import action_log as log, sanction_evidence as evidence

import log_tables

ROOT = Path(__file__).resolve().parent.parent
NOW = 1_793_000_000
BOARD = 3


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    sql = (ROOT / "scripts" / "migrations" / "2026-12-09_action_log.sql").read_text(encoding="utf-8")
    with engine.begin() as conn:
        for statement in log_tables.migration_statements(sql):
            conn.execute(text(statement))
        log_tables.apply(conn)
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, admin_level INTEGER DEFAULT 0)"))
        conn.execute(text("INSERT INTO users (id, admin_level) VALUES (10, 0), (20, 3), (30, 0), (31, 0)"))
        conn.execute(text("CREATE TABLE boards (id INTEGER PRIMARY KEY, is_private BOOLEAN DEFAULT 0, is_circle BOOLEAN DEFAULT 0)"))
        conn.execute(text("CREATE TABLE submissions (id INTEGER PRIMARY KEY, audience INTEGER DEFAULT 0, author_id INTEGER, board_id INTEGER, is_anonymous BOOLEAN DEFAULT 0, "
                          "deleted_utc INTEGER DEFAULT 0, post_public BOOLEAN DEFAULT 1)"))
        conn.execute(text("CREATE TABLE comments (id INTEGER PRIMARY KEY, author_id INTEGER, parent_submission INTEGER, is_anonymous BOOLEAN DEFAULT 0, "
                          "deleted_utc INTEGER DEFAULT 0)"))
        conn.execute(text("INSERT INTO boards VALUES (3, 0, 0), (4, 1, 0), (5, 0, 1)"))
        conn.execute(text("INSERT INTO submissions (id, author_id, board_id, is_anonymous, deleted_utc, post_public) VALUES "
                          "(1, 30, 3, 0, 0, 1), (2, 30, 3, 1, 0, 1), (3, 30, 3, 0, 5, 1), (4, 30, 4, 0, 0, 0), (5, 30, 5, 0, 0, 0), (6, 30, 3, 0, 0, 0)"))
        conn.execute(text("INSERT INTO comments (id, author_id, parent_submission, is_anonymous) VALUES (10, 30, 1, 0), (11, 30, 1, 1), (12, 30, 6, 0)"))
    with Session(engine) as session:
        yield session


def post(**kw):
    base = dict(id=1, author_id=30, board_id=BOARD, original_board_id=BOARD, is_anonymous=False, deleted_utc=0, title="A title", url=None,
                body="the words", body_html="<p>the words</p>", word_severity=0, is_sensitive=False)
    base.update(kw)
    return SimpleNamespace(**base)


def comment(**kw):
    return post(parent_submission=1, **{"id": 10, **kw})


# --- what can be cited ------------------------------------------------------------------------------------------------------------------

def test_a_named_live_item_of_the_exiled_account_made_in_this_guild_can_be_cited():
    assert evidence.usable(post(), BOARD, 30, 30)
    assert evidence.usable(comment(), BOARD, 30, 30)


@pytest.mark.parametrize("change", [
    {"is_anonymous": True},                     # it would pair the account with an anonymous item
    {"deleted_utc": 5},                         # gone
    {"original_board_id": 4},                   # another guild's
])
def test_an_anonymous_deleted_or_other_guilds_item_cannot_be_cited(change):
    assert not evidence.usable(post(**change), BOARD, 30, 30)


def test_it_must_be_the_exiled_accounts_and_a_forwarded_copy_is_its_forwarders():
    assert not evidence.usable(post(), BOARD, 30, 31), "someone else's"
    assert evidence.usable(post(author_id=99), BOARD, 31, 31), "a guild's copy keeps the original author's id: the owner (its forwarder) is the one exiled"
    assert not evidence.usable(None, BOARD, 30, 30) and not evidence.usable(SimpleNamespace(id=1), BOARD, 30, 30)


def test_an_alias_exile_may_cite_the_anonymous_item_itself_when_the_caller_says_so():
    anonymous = post(is_anonymous=True)
    assert not evidence.usable(anonymous, BOARD, 30, 30) and evidence.usable(anonymous, BOARD, 30, 30, allow_anonymous=True)


# --- what is kept -----------------------------------------------------------------------------------------------------------------------

def entry(db):
    return log.record(db, "exile_user", 10, board_id=BOARD, target_user_id=30, reason="Harassment.", now=NOW)


def test_each_cited_item_is_kept_as_a_private_evidence_copy_of_the_exiled_account_for_a_year(db):
    e = entry(db)
    assert evidence.keep(db, e, BOARD, 30, [post(author_id=99), comment()], now=NOW) == 2
    rows = db.execute(text("SELECT kind, author_id, board_id, target_submission_id, target_comment_id, expires_utc, public, body, entry_id "
                           "FROM log_snapshots ORDER BY id")).mappings().all()
    assert [r["kind"] for r in rows] == ["evidence", "evidence"] and all(r["entry_id"] == e for r in rows)
    assert all(r["author_id"] == 30 for r in rows), "the person it is about, not a guild copy's original author"
    assert (rows[0]["target_submission_id"], rows[0]["target_comment_id"]) == (1, None) and (rows[1]["target_submission_id"], rows[1]["target_comment_id"]) == (None, 10)
    assert all(r["expires_utc"] == NOW + 365 * 86400 and r["public"] == 0 and r["body"] == "the words" for r in rows)
    assert log.EVIDENCE_DAYS == 365 and log.SNAPSHOT_DAYS == 90, "a hide's copy keeps its 90 days"


def test_at_most_five_are_kept_and_each_only_once(db):
    e = entry(db)
    items = [post(id=1), post(id=1)] + [post(id=n) for n in range(2, 12)]
    assert evidence.keep(db, e, BOARD, 30, items, now=NOW) == evidence.MAX_CITED == 5
    ids = [r[0] for r in db.execute(text("SELECT target_submission_id FROM log_snapshots ORDER BY id"))]
    assert ids == [1, 2, 3, 4, 5], "the duplicate of the first was not kept twice"


def test_a_post_and_a_comment_with_the_same_number_are_two_items(db):
    e = entry(db)
    assert evidence.keep(db, e, BOARD, 30, [post(id=10), comment(id=10)], now=NOW) == 2


def test_cited_lists_what_an_entry_relied_on_in_order_and_nothing_for_another_entry(db):
    first, second = entry(db), entry(db)
    evidence.keep(db, first, BOARD, 30, [post(id=1), comment(id=10)], now=NOW)
    evidence.keep(db, second, BOARD, 30, [post(id=6)], now=NOW)
    assert evidence.cited(db, [first, second, 999]) == {first: [("post", 1), ("comment", 10)], second: [("post", 6)]}
    assert evidence.cited(db, []) == {} and evidence.cited(db, [0, None]) == {}
    db.execute(text("INSERT INTO log_snapshots (entry_id, author_id, board_id, target_submission_id, kind, created_utc, expires_utc) "
                    "VALUES (:e, 30, 3, 1, 'hide', 0, 0)"), {"e": first})
    assert evidence.cited(db, [first])[first] == [("post", 1), ("comment", 10)], "a hide's copy is not evidence"


# --- what the public Log may show -------------------------------------------------------------------------------------------------------

def test_the_public_log_is_shown_only_the_ids_of_named_public_content(db):
    e = entry(db)
    cited = [post(id=1), post(id=2), post(id=3), post(id=4), post(id=5), post(id=6), comment(id=10), comment(id=11), comment(id=12)]
    for item in cited:
        evidence.keep(db, e, BOARD, 30, [item], now=NOW)
    shown = evidence.public_cited(db, [e])[e]
    assert shown == [("post", 1), ("comment", 10)], "anonymous, deleted, private-guild, circle and never-public items, and a comment under one, are left out"


# --- who opens a copy -------------------------------------------------------------------------------------------------------------------

def copy(**kw):
    return {"scrubbed_utc": 0, "legal_hold": False, "kind": "evidence", "author_id": 30, "hider_id": None, **kw}


def test_the_person_the_deciding_guilds_guildmasters_and_admins_open_an_evidence_copy_and_nobody_else():
    assert log.snapshot_visible(copy(), viewer_id=30, level=0)
    assert log.snapshot_visible(copy(), viewer_id=10, level=0, guild_mod=True)
    assert log.snapshot_visible(copy(), viewer_id=20, level=3)
    assert not log.snapshot_visible(copy(), viewer_id=31, level=0), "another member"
    assert not log.snapshot_visible(copy(), viewer_id=None, level=0), "a visitor"
    assert not log.snapshot_visible(copy(), viewer_id=10, level=0, guild_mod=False), "a guildmaster of another guild"
    assert not log.snapshot_visible(copy(scrubbed_utc=NOW), viewer_id=30, level=3), "an emptied one has no text for anyone"
    # a guild's hide is still the only other kind its guildmasters open: a copy an author erased is theirs and the admins' alone
    assert log.snapshot_visible(copy(kind="hide"), viewer_id=10, level=0, guild_mod=True)
    assert not log.snapshot_visible(copy(kind="grace"), viewer_id=10, level=0, guild_mod=True)


# --- after the review of 2026-11-14 ------------------------------------------------------------------------------------------------------

def test_an_item_an_admin_removed_or_a_posts_author_hid_cannot_be_cited():
    """A guildmaster is shown only a placeholder for these, so they may not cite them and keep a copy the guild's other guildmasters could open."""
    assert not evidence.usable(post(is_banned=True), BOARD, 30, 30)
    assert not evidence.usable(comment(author_hidden=True), BOARD, 30, 30)
    assert evidence.usable(post(is_banned=False), BOARD, 30, 30) and evidence.usable(comment(author_hidden=False), BOARD, 30, 30)


def kept(db, item_id, action="exile_user", comment_item=False, owner=30):
    entry = log.record(db, action, 10, board_id=BOARD, target_user_id=owner if action == "exile_user" else None, now=NOW,
                       **({"comment_id": item_id} if comment_item else {"post_id": item_id}))
    snapshot = log.snapshot_of(SimpleNamespace(id=item_id, author_id=owner, board_id=BOARD, original_board_id=BOARD, title="T", url=None, body="b", body_html="<p>b</p>",
                                               word_severity=0, is_sensitive=False, is_anonymous=False, deleted_utc=0, parent_submission=1) if comment_item else post(id=item_id),
                               BOARD, comment=comment_item, kind=log.EVIDENCE)
    snapshot["author_id"] = owner
    log.keep_snapshot(db, snapshot, NOW, entry)
    return entry


def test_a_guildmaster_loses_the_copy_of_an_item_made_anonymous_since_but_the_person_and_the_admins_keep_it(db):
    entry = kept(db, 1)
    assert evidence.openable(db, [entry], None, 0, [BOARD]) == {entry: [("post", 1)]}, "the guildmasters, while it is named"
    db.execute(text("UPDATE submissions SET is_anonymous = 1 WHERE id = 1"))
    assert evidence.openable(db, [entry], None, 0, [BOARD]) == {}, "a Remove my name since: the line would pair the account with the anonymous post"
    assert evidence.openable(db, [entry], 30, 0, []) == {entry: [("post", 1)]}, "the person"
    assert evidence.openable(db, [entry], 20, 3, []) == {entry: [("post", 1)]}, "an admin"
    assert evidence.openable(db, [entry], None, 0, [4]) == {}, "and another guild's guildmasters never"


def test_an_exile_by_alias_keeps_its_anonymous_item_for_the_guildmasters_who_cited_it(db):
    db.execute(text("UPDATE submissions SET is_anonymous = 1 WHERE id = 1"))
    entry = kept(db, 1, action="exile_alias")
    assert evidence.openable(db, [entry], None, 0, [BOARD]) == {entry: [("post", 1)]}


def test_the_copy_pages_follow_the_same_rule_for_the_viewer(db):
    entry = kept(db, 1)
    guildmaster = SimpleNamespace(id=10, admin_level=0, qr_limited=False, moderates=[SimpleNamespace(board_id=BOARD, accepted=True, invite_rescinded=False)])
    person = SimpleNamespace(id=30, admin_level=0, qr_limited=False, moderates=[])
    assert log.snapshot_for(db, post_id=1, viewer=guildmaster) is not None
    db.execute(text("UPDATE submissions SET is_anonymous = 1 WHERE id = 1"))
    assert log.snapshot_for(db, post_id=1, viewer=guildmaster) is None, "the guildmaster may not open a copy that says an anonymous post was the exiled account's"
    assert log.snapshot_for(db, post_id=1, viewer=person) is not None
    assert entry


def test_a_limited_sign_in_is_a_stranger_to_the_copies_of_its_own_member():
    snapshot = {"scrubbed_utc": 0, "legal_hold": False, "kind": "evidence", "author_id": 5, "board_id": BOARD, "hider_id": None}
    viewer = SimpleNamespace(id=5, admin_level=0, qr_limited=False, moderates=[])
    assert log.may_open_copy(snapshot, viewer) is True
    viewer.qr_limited = True
    assert log.may_open_copy(snapshot, viewer) is False, "a relayed code can't open what the member's anonymous items were kept as"
