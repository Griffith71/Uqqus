"""Bot accounts, the SQL (ruqqus/helpers/bot_store.py) on SQLite, with the tables made from schema.sql (the shape the
migrations add up to) so the two can't drift apart."""
import re
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import bot_store as bs
from ruqqus.helpers import bots

NOW = 1_800_000_000
SCHEMA = (Path(__file__).resolve().parent.parent / "schema.sql").read_text(encoding="utf-8")


def bot_tables():
    """The CREATE TABLE and CREATE INDEX statements of the three bot tables, from schema.sql, for SQLite."""
    out = []
    for table in ("bots", "bot_sources", "bot_seen"):
        out.append(re.search(rf"CREATE TABLE public\.{table} \(.*?\n\);", SCHEMA, re.S).group(0))
        out += re.findall(rf"CREATE INDEX \w+ ON public\.{table} [^;]*;", SCHEMA)
    return [re.sub(r"--[^\n]*", "", x).replace("public.", "").replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")
            for x in out]


def make():
    db = Session(create_engine("sqlite://"))
    db.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, is_bot BOOLEAN DEFAULT 0, bot_owner_id INTEGER)"))
    for statement in bot_tables():
        db.execute(text(statement.rstrip(";")))
    for uid, name in ((1, "owner"), (2, "other"), (10, "bbc_tech"), (11, "spammy"), (12, "quiet")):
        db.execute(text("INSERT INTO users (id, username) VALUES (:i, :n)"), {"i": uid, "n": name})
    return db


def app_for(db, uid, name, owner=1, urls=("https://feeds.example.org/a.xml",)):
    bs.add_bot(db, uid, owner, name, "What it posts, for the admins.", ["news"], NOW)
    return [bs.add_source(db, uid, url, NOW) for url in urls]


def test_an_application_waits_with_its_sources():
    db = make()
    ids = app_for(db, 10, "bbc_tech", urls=("https://feeds.example.org/a.xml", "https://feeds.example.org/b.xml"))
    row = bs.bot_of(db, 10)
    assert row.status == bots.PENDING and row.owner_id == 1 and bs.guilds_of(row) == ["news"]
    assert [s.status for s in bs.sources_of(db, 10)] == [bots.PENDING, bots.PENDING] and all(ids)
    assert bs.bot_named(db, "BBC_TECH").user_id == 10
    assert bs.active_count(db, 1) == 1
    assert [r.username for r in bs.pending_bots(db)] == ["bbc_tech"]
    assert bs.waiting_count(db) == 1                           # its own sources wait with it, not on their own


def test_approving_a_bot_approves_the_sources_it_applied_with_and_a_decision_is_made_once():
    db = make()
    app_for(db, 10, "bbc_tech")
    assert bs.review_bot(db, 10, bots.APPROVED, 99, "fine", NOW)
    assert not bs.review_bot(db, 10, bots.REJECTED, 99, "", NOW)
    assert bs.bot_of(db, 10).status == bots.APPROVED and bs.bot_of(db, 10).reviewed_by_id == 99
    assert [s.status for s in bs.sources_of(db, 10)] == [bots.APPROVED]
    # a feed added later waits on its own
    later = bs.add_source(db, 10, "https://feeds.example.org/later.xml", NOW)
    assert [s.id for s in bs.pending_sources(db)] == [later] and bs.waiting_count(db) == 1
    assert bs.review_source(db, later, bots.APPROVED, 99, NOW) and not bs.review_source(db, later, bots.REJECTED, 99, NOW)


def test_rejecting_rejects_its_sources_and_frees_the_owners_place():
    db = make()
    app_for(db, 10, "bbc_tech")
    assert bs.review_bot(db, 10, bots.REJECTED, 99, "not a feed we can allow", NOW)
    assert bs.bot_of(db, 10).review_note == "not a feed we can allow"
    assert [s.status for s in bs.sources_of(db, 10)] == [bots.REJECTED]
    assert bs.active_count(db, 1) == 0 and bs.bots_of(db, 1)[0].status == bots.REJECTED


def test_a_source_once_per_bot_and_removed_only_by_its_bot():
    db = make()
    first, = app_for(db, 10, "bbc_tech")
    app_for(db, 11, "spammy", owner=2)
    assert bs.add_source(db, 10, "https://feeds.example.org/a.xml", NOW) is None
    assert bs.source_count(db, 10) == 1
    assert not bs.remove_source(db, 11, first)                 # another bot's source
    bs.mark_seen(db, first, "k" * 64, None, NOW)
    assert bs.remove_source(db, 10, first) and bs.source_count(db, 10) == 0
    assert db.execute(text("SELECT COUNT(*) FROM bot_seen")).scalar() == 0


def test_a_bot_an_admin_paused_is_resumed_by_an_admin_only():
    db = make()
    app_for(db, 10, "bbc_tech")
    bs.review_bot(db, 10, bots.APPROVED, 99, "", NOW)
    assert bs.set_running(db, 10, False, "Paused by its owner.")
    assert bs.set_running(db, 10, True)                        # the owner's own pause
    assert bs.set_running(db, 10, False, "Spam.", by_admin=True)
    assert not bs.set_running(db, 10, True)                    # the owner can't undo an admin
    assert bs.bot_of(db, 10).paused_by_admin
    assert bs.set_running(db, 10, True, by_admin=True) and bs.bot_of(db, 10).status == bots.APPROVED
    assert not bs.bot_of(db, 10).paused_by_admin


def test_only_due_approved_sources_of_approved_bots_are_claimed_and_held():
    db = make()
    a, = app_for(db, 10, "bbc_tech")
    b, = app_for(db, 11, "spammy", owner=2)
    bs.review_bot(db, 10, bots.APPROVED, 99, "", NOW)          # 11 stays pending
    assert bs.claim_due(db, NOW) == [a]
    assert bs.claim_due(db, NOW) == []                         # held for the lease
    assert bs.claim_due(db, NOW + 301) == [a]
    bs.set_running(db, 10, False, "x")
    assert bs.claim_due(db, NOW + 10000) == []                 # a paused bot reads nothing
    assert b not in bs.claim_due(db, NOW + 10000)


def test_seen_items_and_what_a_read_records():
    db = make()
    a, = app_for(db, 10, "bbc_tech")
    keys = [f"{i:064d}" for i in range(450)]                    # more than one IN (...) chunk
    for key in keys[:300]:
        bs.mark_seen(db, a, key, None, NOW)
    bs.mark_seen(db, a, keys[0], 5, NOW)                       # once only
    assert bs.seen_keys(db, a, keys) == set(keys[:300])
    bs.record_check(db, a, NOW, NOW + 1800, error="x", title="BBC News", etag="W/1", read_once=True, posted=2)
    bs.record_check(db, a, NOW + 1, NOW + 1801, posted=1)
    s = bs.source(db, a)
    assert (s.title, s.etag, s.read_once, s.posted_count, s.last_error) == ("BBC News", "W/1", True, 3, "")
    db.execute(text("UPDATE bot_seen SET seen_utc = :t WHERE item_key = :k"), {"t": NOW - bots.SEEN_DAYS * 86400 - 1, "k": keys[1]})
    assert bs.prune_seen(db, NOW) == 1


def test_the_daily_count_starts_again_each_day():
    db = make()
    app_for(db, 10, "bbc_tech")
    day = bots.utc_day(NOW)
    for _ in range(3):
        bs.count_post(db, 10, day)
    assert bs.posts_today(db, 10, day) == 3 and bs.posts_today(db, 10, day + 1) == 0
    bs.count_post(db, 10, day + 1)
    assert bs.posts_today(db, 10, day + 1) == 1


def test_the_running_list_and_forgetting_a_bot():
    db = make()
    app_for(db, 10, "bbc_tech")
    app_for(db, 12, "quiet")
    for uid in (10, 12):
        bs.review_bot(db, uid, bots.APPROVED, 99, "", NOW)
    bs.set_running(db, 12, False, "x")
    assert [r.username for r in bs.running_bots(db)] == ["quiet", "bbc_tech"]      # paused first
    bs.forget_bot(db, 10)
    assert bs.bot_of(db, 10) is None and bs.sources_of(db, 10) == []


# --- site bots: nobody's once approved, their proposer's until then ---------------------------------------------

def site_bot(db, uid=10, name="bbc_tech", proposer=1):
    bs.add_bot(db, uid, None, name, "Headlines.", [], NOW, kind=bots.SITE, proposed_by=proposer)
    return bs.add_source(db, uid, "https://feeds.example.org/a.xml", NOW)


def test_a_site_bot_has_no_owner_and_its_proposer_counts_it_only_while_it_waits():
    db = make()
    site_bot(db)
    row = bs.bot_of(db, 10)
    assert row.kind == bots.SITE and row.owner_id is None and row.proposed_by_id == 1
    assert bs.active_count(db, 1) == 1 and [r.username for r in bs.bots_of(db, 1)] == ["bbc_tech"]
    assert bs.pending_bots(db)[0].proposer_name == "owner" and bs.pending_bots(db)[0].owner_name is None
    bs.review_bot(db, 10, bots.APPROVED, 99, "", NOW)
    assert bs.active_count(db, 1) == 0                              # the site's now, not theirs
    assert bs.running_bots(db)[0].owner_name is None and bs.running_bots(db)[0].kind == bots.SITE
    assert bs.bots_of(db, 2) == []


def test_a_member_bot_is_proposed_by_its_owner():
    db = make()
    app_for(db, 10, "bbc_tech")
    row = bs.bot_of(db, 10)
    assert row.kind == bots.MEMBER and row.owner_id == 1 and row.proposed_by_id == 1
    assert bs.pending_bots(db)[0].owner_name == "owner"


def test_an_admin_changes_the_kind_and_the_name_only_while_it_waits():
    db = make()
    site_bot(db)
    bs.set_kind(db, 10, bots.MEMBER, 1)
    assert bs.bot_of(db, 10).kind == bots.MEMBER and bs.bot_of(db, 10).owner_id == 1
    bs.set_kind(db, 10, bots.SITE, 1)
    assert bs.bot_of(db, 10).kind == bots.SITE and bs.bot_of(db, 10).owner_id is None   # a site bot never has one
    bs.rename(db, 10, "bbc_headlines")
    assert bs.bot_named(db, "bbc_headlines").user_id == 10
    bs.review_bot(db, 10, bots.APPROVED, 99, "", NOW)
    bs.set_kind(db, 10, bots.MEMBER, 1)
    assert bs.bot_of(db, 10).kind == bots.SITE                       # decided at approval


def test_a_feed_an_admin_adds_is_approved_at_once():
    db = make()
    site_bot(db)
    bs.review_bot(db, 10, bots.APPROVED, 99, "", NOW)
    by_admin = bs.add_source(db, 10, "https://feeds.example.org/b.xml", NOW, approved_by=99)
    by_member = bs.add_source(db, 10, "https://feeds.example.org/c.xml", NOW)
    statuses = {s.id: s.status for s in bs.sources_of(db, 10)}
    assert statuses[by_admin] == bots.APPROVED and statuses[by_member] == bots.PENDING
    assert [s.id for s in bs.pending_sources(db)] == [by_member] and bs.pending_sources(db)[0].kind == bots.SITE


def test_the_table_takes_only_the_two_kinds():
    import pytest
    from sqlalchemy.exc import IntegrityError

    db = make()
    with pytest.raises(IntegrityError):
        bs.add_bot(db, 10, 1, "bbc_tech", "x", [], NOW, kind="robot")
