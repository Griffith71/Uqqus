"""Shared drafts and collaborative posts: who typed what (helpers/collab_authorship.py), the authors' rules and votes
(helpers/collab_rules.py) and the SQL (helpers/collab_store.py on SQLite, tables from the migration). The promises:
every character is credited to whoever sent it, and the credit survives other people's edits and forks; a decision
passes only by the authors' own rules, inactive authors aren't counted, changing the rules needs every counted author;
a block is never named; one draft never reaches another's ops, authors or proposals; ops are stamped with the
caller."""
import random
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import collab_authorship as authorship
from ruqqus.helpers import collab_ot as ot
from ruqqus.helpers import collab_rules as rules
from ruqqus.helpers import collab_store as store

ROOT = Path(__file__).resolve().parent.parent
NOW = 1_800_000_000
DAY = 86400


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


# --- who typed what --------------------------------------------------------------------------------------------------

def test_typists_are_credited_and_deletes_take_credit_away():
    runs = authorship.for_text("hello", 1)
    runs = authorship.apply(runs, ot.diff("hello", "hello world"), 2)         # Bob adds " world"
    assert runs == [[1, 5], [2, 6]]
    runs = authorship.apply(runs, ot.diff("hello world", "help world"), 3)     # Cara replaces "lo" with "p"
    assert runs == [[1, 3], [3, 1], [2, 6]]
    assert authorship.pieces("help world", runs) == [(1, "hel"), (3, "p"), (2, " world")]
    assert authorship.shares(runs) == [(2, 6, 60), (1, 3, 30), (3, 1, 10)]
    assert authorship.apply(runs, [10, "😀"], 1)[-1] == [1, 2]                  # an emoji is two units
    with pytest.raises(ot.OpError):
        authorship.apply(runs, [3], 1)


def test_credit_follows_the_text_through_random_edits():
    rng = random.Random(3)
    text_, runs = "", []
    for step in range(300):
        user = rng.randint(1, 4)
        chars = list(text_)
        i = rng.randint(0, len(chars))
        j = rng.randint(i, min(len(chars), i + 3))
        new = "".join(chars[:i]) + "".join(rng.choice("ab😀") for _ in range(rng.randint(0, 3))) + "".join(chars[j:])
        op = ot.diff(text_, new)
        runs = authorship.apply(runs, op, user)
        text_ = ot.apply(text_, op)
        assert authorship.length(runs) == ot.units(text_)
        assert "".join(p for _, p in authorship.pieces(text_, runs)) == text_
    assert sum(p for _, _, p in authorship.shares(runs)) == 100


def test_shares_add_up_and_clean_drops_junk():
    assert sum(p for *_, p in authorship.shares([[1, 1], [2, 1], [3, 1]])) == 100
    assert authorship.clean([[1, 2], [1, 3], ["x", 1], [2, 0], [True, 1], [3, 4]]) == [[1, 5], [3, 4]]
    assert authorship.pieces("abc", [[1, 2]]) == [(None, "abc")]               # a map that doesn't fit credits nobody


# --- the rules -------------------------------------------------------------------------------------------------------

def test_rules_only_take_known_values():
    assert rules.clean_rules({"decision": "dictator", "inactive_after_days": 3, "publish": "x", "proposal_days": True}) == rules.DEFAULT_RULES
    assert rules.clean_rules({"decision": "majority", "inactive_after_days": None})["inactive_after_days"] is None
    assert rules.rules_from_form({"decision": "two_thirds", "inactive_after_days": "never", "publish": "vote",
                                  "proposal_days": "7"}) == {"decision": "two_thirds", "inactive_after_days": None,
                                                             "publish": "vote", "proposal_days": 7}
    for bad in ({"decision": "x"}, {"decision": "majority", "publish": "vote", "inactive_after_days": "5", "proposal_days": "7"},
                {"decision": "majority", "publish": "vote", "inactive_after_days": "30", "proposal_days": "٣"}):
        with pytest.raises(rules.Refused):
            rules.rules_from_form(bad)
    assert rules.DEFAULT_RULES == {"decision": "unanimous", "inactive_after_days": 180, "publish": "any_author", "proposal_days": 30}
    assert any("6 months" in line for line in rules.describe({}))


def test_who_counts_and_how_decisions_pass():
    r = rules.DEFAULT_RULES
    authors = [(1, NOW), (2, NOW - 10 * DAY), (3, NOW - 200 * DAY)]           # 3 has been away 200 days
    assert rules.counted(authors, r, NOW) == [1, 2]
    assert rules.counted(authors, dict(r, inactive_after_days=None), NOW) == [1, 2, 3]
    assert rules.counted(authors, dict(r, inactive_after_days=7), NOW) == [1]
    assert rules.counted(authors, r, NOW, exclude=2) == [1]                    # the subject of a removal
    # unanimous: everyone counted, any no fails at once
    assert rules.tally(rules.ADD, r, [1, 2], {1: True}) == rules.OPEN
    assert rules.tally(rules.ADD, r, [1, 2], {1: True, 2: True}) == rules.PASSED
    assert rules.tally(rules.ADD, r, [1, 2], {1: True, 2: False}) == rules.FAILED
    assert rules.tally(rules.ADD, r, [1, 2], {1: True, 3: False}) == rules.OPEN  # an uncounted vote doesn't count
    # majority and two thirds
    m, t = dict(r, decision="majority"), dict(r, decision="two_thirds")
    assert rules.tally(rules.ADD, m, [1, 2, 3, 4], {1: True, 2: True}) == rules.OPEN
    assert rules.tally(rules.ADD, m, [1, 2, 3, 4], {1: True, 2: True, 3: True}) == rules.PASSED
    assert rules.tally(rules.ADD, m, [1, 2, 3, 4], {3: False, 4: False}) == rules.FAILED
    assert rules.tally(rules.ADD, t, [1, 2, 3], {1: True, 2: True}) == rules.PASSED
    assert rules.tally(rules.ADD, t, [1, 2, 3], {1: True, 3: False}) == rules.OPEN
    # changing the rules always needs every counted author, whatever the decision rule says
    assert rules.tally(rules.RULES, m, [1, 2, 3], {1: True, 2: True}) == rules.OPEN
    assert rules.tally(rules.RULES, m, [1, 2, 3], {1: True, 2: True, 3: True}) == rules.PASSED
    assert rules.tally(rules.ADD, r, [1], {1: True}) == rules.PASSED          # one active author decides alone
    assert rules.tally(rules.ADD, r, [], {}) == rules.FAILED


def test_who_can_be_added_never_names_a_block():
    ok = NS(username="ann", is_deleted=False, is_bot=False, is_suspended=False)
    assert rules.add_refusal(name="ann", candidate=ok, authors_now=3, already=False, blocked_with_any=False) is None
    blocked = rules.add_refusal(name="ann", candidate=ok, authors_now=3, already=False, blocked_with_any=True)
    gone = rules.add_refusal(name="ann", candidate=None, authors_now=3, already=False, blocked_with_any=False)
    assert blocked == gone == "@ann can't be added." and "block" not in blocked
    for bad in (dict(is_deleted=True), dict(is_bot=True), dict(is_suspended=True)):
        assert rules.add_refusal(name="ann", candidate=NS(**dict(vars(ok), **bad)), authors_now=1, already=False,
                                 blocked_with_any=False) == "@ann can't be added."
    assert "already" in rules.add_refusal(name="ann", candidate=ok, authors_now=3, already=True, blocked_with_any=False)
    assert rules.add_refusal(name="ann", candidate=ok, authors_now=rules.MAX_AUTHORS, already=False, blocked_with_any=False)
    assert rules.remove_refusal(subject_is_author=True, subject_is_me=True)
    assert rules.parse_name("@Ann_1") == "Ann_1"
    with pytest.raises(rules.Refused):
        rules.parse_name("ann smith")


# --- the SQL ---------------------------------------------------------------------------------------------------------

def _tables():
    sql = read("scripts", "migrations", "2026-11-01_collab_posts.sql")
    sql = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
    for s in (s.strip() for s in sql.split(";")):
        if s.startswith("CREATE"):
            yield (s.replace("public.", "").replace("IF NOT EXISTS ", "")
                   .replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT"))


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, last_active_utc INTEGER DEFAULT 0, is_deleted BOOLEAN DEFAULT 0, is_banned INTEGER DEFAULT 0, unban_utc INTEGER DEFAULT 0)"))
        conn.execute(text("INSERT INTO users (id, last_active_utc) VALUES (1, :n), (2, :n), (3, :old)"),
                     {"n": NOW, "old": NOW - 400 * DAY})
        for statement in _tables():
            conn.execute(text(statement))
    with Session(engine) as session:
        yield session


def test_concurrent_ops_are_brought_in_and_credited(db):
    d = store.create(db, 1, NOW, body="hello")
    assert store.is_author(db, d, 1) and not store.is_author(db, d, 2)
    store.add_author(db, d, 2, NOW)
    # both made their edit on revision 0
    r1 = store.commit_op(db, d, 1, "body", 0, ot.diff("hello", "hello world"), NOW)
    r2 = store.commit_op(db, d, 2, "body", 0, ot.diff("hello", "Oh hello"), NOW)
    assert (r1, r2) == (1, 2)
    row = store.doc(db, d)
    assert row[3] == "Oh hello world" and row[4] == 2
    assert store.runs_of(row)["body"] == [[2, 3], [1, 11]]
    assert [(r, u, f) for r, u, f, _ in store.ops_after(db, d, 0)] == [(1, 1, "body"), (2, 2, "body")]
    # a title op made on revision 0 isn't transformed past body ops
    store.commit_op(db, d, 2, "title", 0, ["Draft"], NOW)
    assert store.doc(db, d)[2] == "Draft" and store.runs_of(store.doc(db, d))["title"] == [[2, 5]]
    assert store.commit_op(db, d, 1, "body", 3, [14], NOW) is None             # changes nothing: nothing stored
    r = store.commit_op(db, d, 1, "body", 3, [14, "!"], NOW, client_key="k1")
    assert store.commit_op(db, d, 1, "body", 3, [14, "!"], NOW, client_key="k1") == r   # sent again: applied once
    assert store.doc(db, d)[3] == "Oh hello world!"
    with pytest.raises(store.Resync):
        store.commit_op(db, d, 1, "body", 99, [14], NOW)                         # a base in the future
    with pytest.raises(ot.OpError):
        store.commit_op(db, d, 1, "body", 4, [2], NOW)                           # doesn't fit
    with pytest.raises(ot.OpError):
        store.commit_op(db, d, 1, "script", 4, [1], NOW)
    with pytest.raises(rules.Refused):
        store.commit_op(db, d, 1, "title", 4, [5, "x" * rules.TITLE_MAX], NOW)


def test_one_draft_never_reaches_another(db):
    a = store.create(db, 1, NOW, body="aaa")
    b = store.create(db, 2, NOW, body="bbb")
    store.commit_op(db, a, 1, "body", 0, [3, "!"], NOW)
    assert store.ops_after(db, b, 0) == [] and store.doc(db, b)[3] == "bbb"
    p = store.propose(db, a, rules.ADD, 1, NOW, NOW + DAY, subject=2)
    assert store.proposal(db, b, p) is None and store.proposal(db, a, p) is not None
    assert store.author_ids(db, b) == [2]


def test_authors_proposals_votes_and_publishes(db):
    d = store.create(db, 1, NOW, title="T", body="Body")
    store.add_author(db, d, 2, NOW)
    store.add_author(db, d, 3, NOW)
    assert [(u, a) for u, a, _ in store.authors(db, d)] == [(1, NOW), (2, NOW), (3, NOW - 400 * DAY)]
    assert rules.counted([(u, a) for u, a, _ in store.authors(db, d)], store.rules_of(store.doc(db, d)), NOW) == [1, 2]
    p = store.propose(db, d, rules.ADD, 1, NOW, NOW + DAY, subject=9)
    assert store.open_like(db, d, rules.ADD, 9) == p and store.open_like(db, d, rules.ADD, 8) is None
    assert store.open_by(db, d, 1) == 1 and store.open_by(db, d, 2) == 0                # the C-06 cap counts these
    store.vote(db, p, 1, True, NOW)
    store.vote(db, p, 2, False, NOW)
    store.vote(db, p, 2, True, NOW)                                             # changing a vote keeps one row
    assert store.votes(db, p) == {1: True, 2: True}
    assert store.decide(db, p, rules.PASSED, NOW) is True and store.decide(db, p, rules.FAILED, NOW) is False
    assert store.open_by(db, d, 1) == 0
    assert [r[0] for r in store.invitations_for(db, 9, NOW - DAY)] == [p]
    assert store.answer_invite(db, p, "accepted") is True and store.answer_invite(db, p, "declined") is False
    store.remove_author(db, d, 2, NOW)
    assert store.author_ids(db, d) == [1, 3] and set(store.everyone(db, d)) == {1, 2, 3}
    store.set_rules(db, d, {"decision": "majority", "junk": 1}, NOW)
    assert store.rules_of(store.doc(db, d))["decision"] == "majority"
    n1 = store.add_publish(db, d, 0, "T", "Body", {"title": [[1, 1]], "body": [[1, 4]]}, 1, NOW)
    n2 = store.add_publish(db, d, 0, "T2", "Body2", {"title": [[1, 2]], "body": [[1, 5]]}, 3, NOW)
    assert (n1, n2) == (1, 2) and store.latest_publish(db, d)[3] == "T2"
    assert store.publish_row(db, d, 1)[5]["body"] == [[1, 4]]
    store.set_published(db, d, 555, 0)
    assert store.doc_for_post(db, 555)[0] == d and store.post_ids(db) == [555]
    fork = store.create(db, 3, NOW, title="T2", body="Body2", runs=store.publish_row(db, d, 2)[5],
                        forked_from={"doc_id": d, "post_id": 555, "publish_id": store.publish_row(db, d, 2)[0]})
    assert store.runs_of(store.doc(db, fork))["body"] == [[1, 5]]               # the original typist stays credited
    assert store.author_ids(db, fork) == [3] and store.fork_count(db, 555) == 1 and store.forks_since(db, 3, NOW) == 1


def test_deleted_and_banned_authors_dont_vote(db):
    d = store.create(db, 1, NOW)
    store.add_author(db, d, 2, NOW)
    store.add_author(db, d, 3, NOW)
    db.execute(text("UPDATE users SET last_active_utc = :n"), {"n": NOW})
    assert [u for u, _ in store.voters(db, d)] == [1, 2, 3]
    db.execute(text("UPDATE users SET is_deleted = 1 WHERE id = 2"))
    db.execute(text("UPDATE users SET is_banned = 1, unban_utc = 0 WHERE id = 3"))
    assert [u for u, _ in store.voters(db, d)] == [1]                 # audit C-04
    db.execute(text("UPDATE users SET unban_utc = :t WHERE id = 3"), {"t": NOW + 10})   # a temporary ban still counts
    assert [u for u, _ in store.voters(db, d)] == [1, 3]


def test_the_schema_mirrors_the_migration():
    migration, schema = read("scripts", "migrations", "2026-11-01_collab_posts.sql"), read("schema.sql")
    for table in ("collab_docs", "collab_authors", "collab_ops", "collab_publishes", "collab_proposals", "collab_votes"):
        assert f"CREATE TABLE IF NOT EXISTS public.{table}" in migration and f"CREATE TABLE public.{table} (" in schema
    assert "    last_active_utc integer DEFAULT 0 NOT NULL," in schema
    assert "ADD COLUMN IF NOT EXISTS last_active_utc" in migration
