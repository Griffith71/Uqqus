"""History's mass tools, the Deleted list and what an account's deletion does to what it wrote (the Log plan's decisions 11 and 22; slice D3c):
helpers/mass_edit.py (the rules), helpers/mass_edit_store.py (the Deleted list's SQL), helpers/account_anonymise.py (the resumable job), the
migration, and the wiring of the route and the pages, on SQLite with the tables built from the migrations.

The promises: a request names at most a hundred items, once each, and its count must be the number chosen; the Deleted list shows a member their
own copies only, newest first, never a scrubbed or a legally held one; the account job anonymises every kind of row the account wrote, a batch at a
time, can stop and be asked again without redoing or losing anything, never touches anyone else's rows, and strips the items out of the entries that
pair them with their author."""
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import account_anonymise, mass_edit, mass_edit_store

import log_tables

ROOT = Path(__file__).resolve().parent.parent
NOW = 1_793_000_000


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


# --- the rules ---------------------------------------------------------------------------------------------------------------------------

def test_items_are_tokens_read_once_each_in_the_order_given():
    items, problem = mass_edit.parse_items(["p1a", "c2b", "p1a", " P3C ", "x9", "", "p", "c!", None, "p" + "a" * 14])
    assert problem is None
    assert items == [("post", "1a"), ("comment", "2b"), ("post", "3c")], "junk and repeats are dropped, a hand-made request can say what it likes"


def test_none_chosen_and_too_many_are_problems_with_the_cap_in_the_message():
    assert mass_edit.parse_items([])[1][0] is mass_edit.NOTHING and mass_edit.parse_items(["zzz", "!"])[1][0] is mass_edit.NOTHING
    many = [f"p{i:x}" for i in range(1, mass_edit.MAX_ITEMS + 2)]
    items, problem = mass_edit.parse_items(many)
    assert items == [] and problem == (mass_edit.TOO_MANY, {"max": 100}), "a hundred and one is refused, not cut short"
    assert mass_edit.parse_items(many[:-1])[1] is None, "a hundred is fine"
    assert mass_edit.MAX_ITEMS == 100 and mass_edit.PER_HOUR == "6/hour"


def test_the_confirmed_count_must_be_the_number_chosen():
    items = [("post", "a"), ("comment", "b"), ("post", "c")]
    assert mass_edit.count_matches("3", items) and mass_edit.count_matches(" 3 ", items) and mass_edit.count_matches(3, items)
    for wrong in ("2", "4", "", None, "three", "3.0", "-3"):
        assert not mass_edit.count_matches(wrong, items), wrong


def test_the_actions_are_two_and_a_summary_counts_what_changed():
    assert mass_edit.parse_action("anonymise") == "anonymise" and mass_edit.parse_action("delete") == "delete"
    for other in (None, "", "erase", "DELETE", "unlink"):
        assert mass_edit.parse_action(other) is None, other
    assert mass_edit.summary([{"ok": True}, {"ok": False}, {"ok": True}, {}]) == (2, 2)
    assert mass_edit.token("post", "1a") == "p1a" and mass_edit.token("comment", "1a") == "c1a"


# --- the Deleted list --------------------------------------------------------------------------------------------------------------------

@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for statement in log_tables.migration_statements(read("scripts", "migrations", "2026-12-09_action_log.sql")):
            conn.execute(text(statement))
        log_tables.apply(conn, modactions=True)
        conn.execute(text("CREATE TABLE submissions (id INTEGER PRIMARY KEY, audience INTEGER DEFAULT 0, author_id INTEGER, repost_id INTEGER DEFAULT 0, "
                          "is_anonymous BOOLEAN DEFAULT 0, distinguish_level INTEGER DEFAULT 0, gm_distinguish INTEGER DEFAULT 0)"))
        conn.execute(text("CREATE TABLE comments (id INTEGER PRIMARY KEY, author_id INTEGER, parent_submission INTEGER, is_anonymous BOOLEAN DEFAULT 0, distinguish_level INTEGER DEFAULT 0, gm_distinguish INTEGER DEFAULT 0)"))
        for statement in _statements(read("scripts", "migrations", "2027-01-03_account_anonymise.sql")):
            conn.execute(text(statement))
    with Session(engine) as session:
        yield session


def _statements(sql):
    sql = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
    return [part.strip().replace("public.", "").replace("IF NOT EXISTS ", "") for part in sql.split(";") if part.strip().startswith("CREATE")]


def snapshot(db, author, kind, *, post=None, comment=None, at=NOW, entry=None, scrubbed=0, hold=False, title="A title", body="body words"):
    db.execute(text("INSERT INTO log_snapshots (entry_id, author_id, kind, target_submission_id, target_comment_id, title, body, created_utc, "
                    "scrubbed_utc, legal_hold) VALUES (:e, :a, :k, :p, :c, :t, :b, :at, :s, :h)"),
               {"e": entry, "a": author, "k": kind, "p": post, "c": comment, "t": title, "b": body, "at": at, "s": scrubbed, "h": hold})


def test_a_member_sees_only_their_own_copies_newest_first_and_never_a_scrubbed_or_held_one(db):
    snapshot(db, 7, "delete", post=1, at=NOW - 30, entry=11)
    snapshot(db, 7, "grace", comment=2, at=NOW - 10)
    snapshot(db, 7, "delete", post=3, at=NOW - 20, scrubbed=NOW, title=None, body=None)           # an admin's scrub emptied it
    snapshot(db, 7, "legal", post=4, at=NOW)                                                      # not a deletion of theirs to read
    snapshot(db, 7, "delete", post=5, at=NOW, hold=True)                                          # a legal hold is kept by a duty
    snapshot(db, 7, "hide", post=6, at=NOW)                                                       # a guild's hide is the guild's record
    snapshot(db, 8, "delete", post=9, at=NOW)                                                     # somebody else's
    snapshot(db, 7, "delete", at=NOW)                                                             # nothing it points at
    rows, more = mass_edit_store.deleted_by(db, 7)
    assert [(r["kind"], r["target_submission_id"], r["target_comment_id"]) for r in rows] == [("grace", None, 2), ("delete", 1, None)]
    assert not more
    assert mass_edit_store.deleted_by(db, 8)[0][0]["target_submission_id"] == 9
    assert mass_edit_store.deleted_by(db, 99) == ([], False)


def test_the_list_pages_by_25_and_says_when_there_is_more(db):
    for n in range(30):
        snapshot(db, 7, "delete", post=n + 1, at=NOW - n)
    first, more = mass_edit_store.deleted_by(db, 7, 1)
    second, more_after = mass_edit_store.deleted_by(db, 7, 2)
    assert len(first) == 25 and more and len(second) == 5 and not more_after
    assert first[0]["target_submission_id"] == 1 and second[-1]["target_submission_id"] == 30
    assert mass_edit_store.deleted_by(db, 7, 0)[0] == first, "a page before the first is the first"


# --- the account job ---------------------------------------------------------------------------------------------------------------------

def rows_of(db, table, author):
    return [r[0] for r in db.execute(text(f"SELECT id FROM {table} WHERE author_id = :a AND NOT is_anonymous ORDER BY id"), {"a": author})]


def seed(db, posts, comments, author=7, other=8):
    for n in range(posts):
        db.execute(text("INSERT INTO submissions (id, author_id, repost_id, distinguish_level, gm_distinguish) VALUES (:i, :a, :r, 1, 1)"),
                   {"i": n + 1, "a": author, "r": 0 if n % 2 == 0 else 1})              # a copy keeps the original author's id
    for n in range(comments):
        db.execute(text("INSERT INTO comments (id, author_id, parent_submission) VALUES (:i, :a, 1)"), {"i": n + 1, "a": author})
    db.execute(text("INSERT INTO submissions (id, author_id) VALUES (900, :o)"), {"o": other})
    db.execute(text("INSERT INTO comments (id, author_id, parent_submission) VALUES (900, :o, 1)"), {"o": other})
    db.execute(text(account_anonymise.QUEUE_SQL), {"u": author, "now": NOW})


def test_the_job_anonymises_every_post_copy_and_comment_the_account_wrote_and_nobody_elses(db):
    seed(db, posts=5, comments=4)
    assert account_anonymise.run_one(db, 7, NOW) == (5, 4, True)
    assert rows_of(db, "submissions", 7) == [] and rows_of(db, "comments", 7) == []
    assert rows_of(db, "submissions", 8) == [900] and rows_of(db, "comments", 8) == [900], "another account's rows are not touched"
    assert db.execute(text("SELECT SUM(distinguish_level) + SUM(gm_distinguish) FROM submissions WHERE author_id = 7")).scalar() == 0, \
        "a distinguish and a herald would name the author's role: they end with the name"
    assert db.execute(text("SELECT done_utc, tries FROM account_anonymise_jobs WHERE user_id = 7")).first() == (NOW, 1)


def test_the_job_works_in_batches_and_can_stop_and_be_asked_again(db):
    seed(db, posts=7, comments=5)
    assert account_anonymise.run_one(db, 7, NOW, batches=1, size=3) == (3, 3, False), "one batch only: the job is not done"
    assert len(rows_of(db, "submissions", 7)) == 4 and len(rows_of(db, "comments", 7)) == 2
    assert db.execute(text("SELECT done_utc FROM account_anonymise_jobs WHERE user_id = 7")).scalar() == 0
    assert account_anonymise.pending(db) == [7]
    assert account_anonymise.run_one(db, 7, NOW + 5, batches=1, size=3) == (3, 2, False), "asked again, it carries on from what is left"
    assert account_anonymise.run_one(db, 7, NOW + 9, batches=1, size=3) == (1, 0, True)
    assert account_anonymise.run_one(db, 7, NOW + 12) == (0, 0, True), "idempotent: a finished job finds nothing"
    assert rows_of(db, "submissions", 7) == [] and account_anonymise.pending(db) == []


def test_a_run_goes_on_batch_after_batch_until_nothing_is_left_or_its_batches_are_used(db):
    seed(db, posts=7, comments=5)
    assert account_anonymise.run_one(db, 7, NOW, batches=3, size=2) == (6, 5, False), "three batches of two, and the job is not done"
    assert account_anonymise.run_one(db, 7, NOW + 1, batches=3, size=2) == (1, 0, True), "the next run finishes it and stops at the first empty batch"


def test_a_cycle_works_the_oldest_accounts_and_leaves_a_failing_one_for_the_next(db):
    seed(db, posts=2, comments=1, author=7)
    db.execute(text("INSERT INTO submissions (id, author_id) VALUES (500, 9)"))
    db.execute(text(account_anonymise.QUEUE_SQL), {"u": 9, "now": NOW + 1})
    assert account_anonymise.run_due(db, NOW + 2) == (2, 4, 0)
    db.execute(text("INSERT INTO account_anonymise_jobs (user_id, queued_utc) VALUES (13, :n)"), {"n": NOW})
    db.execute(text("DROP TABLE comments"))                                     # a batch that cannot run: it is counted and left, the rest still go
    db.commit()
    finished, changed, failed = account_anonymise.run_due(db, NOW + 3)
    assert (finished, changed, failed) == (0, 0, 1)
    assert account_anonymise.pending(db) == [13]


def test_queueing_twice_makes_one_job_and_a_closed_accounts_job_is_never_requeued_done(db):
    db.execute(text(account_anonymise.QUEUE_SQL), {"u": 7, "now": NOW})
    db.execute(text(account_anonymise.QUEUE_SQL), {"u": 7, "now": NOW + 99})
    assert db.execute(text("SELECT count(*), min(queued_utc) FROM account_anonymise_jobs")).first() == (1, NOW)


def test_the_job_takes_the_items_out_of_the_entries_that_pair_them_with_their_author(db):
    seed(db, posts=1, comments=1)
    db.execute(text("INSERT INTO log_entries (id, created_utc, log_type, action, actor_id, actor_role, target_user_id, target_submission_id, "
                    "target_comment_id) VALUES (1, :n, 'sanction', 'exile_user', 3, 'guildmaster', 7, 1, NULL), "
                    "(2, :n, 'content', 'hide_post', 3, 'guildmaster', NULL, 1, NULL), (3, :n, 'sanction', 'exile_user', 3, 'guildmaster', 8, 1, NULL)"),
               {"n": NOW})
    account_anonymise.run_one(db, 7, NOW)
    pointers = dict(db.execute(text("SELECT id, target_submission_id FROM log_entries ORDER BY id")).fetchall())
    assert pointers[1] is None, "an exile of this account that cited its post no longer says whose post it was"
    assert pointers[2] == 1 and pointers[3] == 1, "a guildmaster's hide of the post, and an exile of someone else, keep theirs"


def test_the_job_is_in_the_migration_the_schema_the_ledger_and_the_cleanup_and_the_cycle():
    migration = read("scripts", "migrations", "2027-01-03_account_anonymise.sql")
    assert "CREATE TABLE IF NOT EXISTS public.account_anonymise_jobs" in migration and "user_id integer PRIMARY KEY" in migration
    assert "FOREIGN KEY" not in migration and "REFERENCES" not in migration, "the project keeps no foreign keys to users"
    schema = read("schema.sql")
    assert "CREATE TABLE public.account_anonymise_jobs" in schema and "('2027-01-03_account_anonymise.sql')" in schema
    cleanup = read("ruqqus", "helpers", "account_cleanup.py")
    assert '("queue anonymising what they wrote", account_anonymise.QUEUE_SQL)' in cleanup
    settings = read("ruqqus", "routes", "settings.py")
    closing = settings.split("def close_own_account(")[1].split("\ndef ")[0]
    assert closing.index("account_cleanup.close_account(") < closing.index("account_anonymise.run_one(g.db, v.id") < closing.index("_leave_chat_groups(v)")
    assert "if account_anonymise.tables_ready(g.db):" in closing and "commit=False" in closing, "the first batch is part of the deletion's own transaction"
    assert "account_anonymise.run_due(db, int(time.time()))" in read("scripts", "recomputes.py")
    assert "DELETE FROM account_anonymise_jobs WHERE done_utc > 0" in read("ruqqus", "helpers", "retention.py"), "a finished job's row goes, a running one stays"


# --- the route and the pages ---------------------------------------------------------------------------------------------------------------

def test_the_mass_route_is_site_only_held_from_a_limited_sign_in_and_asks_the_password_before_anything_changes():
    source = read("ruqqus", "routes", "history_mass_pages.py")
    route = source.split('@app.route("/history/mass", methods=["POST"])')[1].split("def history_mass(v):")[0]
    for decorator in ("@limiter.limit(mass_edit.PER_HOUR, key_func=member_limit_key)", "@is_not_banned", "@not_qr_limited",
                      "@validate_formkey", '@throttle_check_ip("history_mass")'):
        assert decorator in route, decorator
    assert "@api(" not in route and "@api(" not in source, "an API app and a bot key can't reach it"
    body = source.split("def history_mass(v):")[1]
    gates = [body.index(needle) for needle in ("mass_edit.parse_action(", "mass_edit.parse_items(", "mass_edit.count_matches(", "_password_ok(v)", "begin_nested()")]
    assert gates == sorted(gates), "action, items, count, password, and only then the first item"
    assert "savepoint.rollback()" in body and "g.db.commit()" in body.split("for kind, base36id in items:")[1]
    # each item goes through the very functions the single dialog calls: never a copy of their logic
    for name in ("delete_post_as", "unlink_post", "delete_comment_as", "unlink_comment"):
        assert name in source
    assert "anonymity.is_author(post, v)" in source and "anonymity.is_author(comment, v)" in source, "only your own, as the author is told it"
    assert "_password_ok" in source and "mfa_backup_store.spend" in source and "v.accept_2fa(code)" in source, "the two-factor code where the account has one"


def test_the_manage_page_counts_confirms_in_the_sheet_and_posts_the_count():
    page = read("ruqqus", "templates", "history_manage.html")
    for needle in ('action="/history/mass"', 'name="formkey"', 'name="count"', 'name="items"', 'name="action"', 'name="password"', "{% if v.mfa_secret %}",
                   'id="history-mass-submit" disabled'):
        assert needle in page, needle
    script = read("ruqqus", "assets", "js", "history_manage.js")
    assert "confirmSheet(" in script and "count.value = String(n)" in script and "form.submit()" in script
    assert "n === 0 || n > max" in script, "nothing chosen, or more than the cap, can't be sent"
    history = read("ruqqus", "templates", "history.html")
    assert history.count('href="/history/manage"') == 2 and 'href="/history/anonymous?show=deleted"' in history
    assert 'include "history_deleted.html"' in history


def test_the_deleted_list_draws_plain_text_only_and_links_only_to_the_members_own_copy():
    page = read("ruqqus", "templates", "history_deleted.html")
    assert "| safe" not in page and "|safe" not in page, "a deleted item's words are never drawn as HTML"
    assert "{{ row.title }}" in page and "{{ row.snippet }}" in page and "row.copy_url" in page
    route = read("ruqqus", "routes", "history.py")
    assert 'request.args.get("show") == "deleted"' in route and "mass_edit_store.deleted_by(g.db, v.id, page)" in route, "built from the member's own id"
    assert route.index('request.args.get("show") == "deleted"') < route.index("v.anonymous_idlist(page=page)")
    qr = read("tests", "test_qr_login_assets.py")
    assert '"history_mass_pages.py": "history_manage history_mass"' in qr
