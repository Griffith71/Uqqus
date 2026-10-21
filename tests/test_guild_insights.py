"""Guild analytics (helpers/guild_insights.py, helpers/guild_insights_store.py): the rules, the SQL on SQLite, and the promise
that only counts leave: no statement selects a user, a name or an address. The page on a real app is
tests/pg/test_guild_insights.py."""
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import guild_insights as rules
from ruqqus.helpers import guild_insights_store as store

ROOT = Path(__file__).resolve().parent.parent
NOW = 1_800_000_000 + 12 * 3600          # in the middle of a day
DAY = 86400


# ---- the rules ----

def test_only_7_and_28_days_and_28_is_the_default():
    assert [rules.parse_days(x) for x in ("7", 28, "28", "30", "", None, "x", "-7", "0")] == [7, 28, 28, 28, 28, 28, 28, 28, 28]


def test_the_window_is_the_last_n_whole_days_and_today():
    lo, hi = rules.bounds(NOW, 7)
    assert hi - lo == 7 * DAY and lo % DAY == 0 and lo <= NOW - 6 * DAY < lo + DAY and hi > NOW


def test_a_small_guild_gets_totals_and_no_day_by_day():
    assert not rules.chart_allowed(0) and not rules.chart_allowed(24) and rules.chart_allowed(25) and rules.chart_allowed(5000)
    assert rules.MIN_CHART_MEMBERS == 25


def test_every_day_of_the_window_is_drawn_with_zero_where_nothing_was_counted():
    today = rules.day_of(NOW)
    rows = rules.fill_days({today: {"joins": 2, "views": 5}, today - 3: {"posts": 1}}, NOW, 7)
    assert [r["day"] for r in rows] == list(range(today - 6, today + 1))
    assert rows[-1]["joins"] == 2 and rows[-1]["views"] == 5 and rows[-1]["leaves"] == 0
    assert rows[3]["posts"] == 1 and sum(r["comments"] for r in rows) == 0
    assert all(set(r) == {"day", "label", "joins", "leaves", "views", "posts", "comments"} for r in rows)


def test_net_growth_reads_with_its_sign():
    assert rules.net(5, 2) == 3 and rules.net(0, 4) == -4
    assert [rules.signed(n) for n in (3, 0, -4)] == ["+3", "0", "-4"]


def mod(**perms):
    return SimpleNamespace(**{"perm_full": False, "perm_config": False, "perm_content": False, "perm_access": False,
                              "perm_appearance": False, **perms})


def test_configuration_content_or_full_may_read_and_nothing_else_may():
    assert rules.may_read(mod(perm_config=True)) and rules.may_read(mod(perm_content=True))
    assert rules.may_read(SimpleNamespace(perm_full=True))
    assert not rules.may_read(mod(perm_access=True)) and not rules.may_read(mod(perm_appearance=True))
    assert not rules.may_read(None) and not rules.may_read(False)


def test_the_page_says_the_numbers_start_when_it_shipped_and_that_it_never_names_anyone():
    assert "start from the day" in rules.STARTED_NOTE and "never who" in rules.PRIVACY_NOTE
    assert "top contributors" not in rules.PRIVACY_NOTE.lower()


# ---- the SQL ----

TABLES = (
    "CREATE TABLE guild_day_stats (board_id INTEGER NOT NULL, day INTEGER NOT NULL, joins INTEGER NOT NULL DEFAULT 0, "
    "leaves INTEGER NOT NULL DEFAULT 0, page_views INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (board_id, day))",
    "CREATE TABLE subscriptions (id INTEGER PRIMARY KEY, user_id INTEGER, board_id INTEGER, created_utc INTEGER, is_active BOOLEAN)",
    "CREATE TABLE submissions (id INTEGER PRIMARY KEY, board_id INTEGER, created_utc INTEGER, deleted_utc INTEGER, is_banned BOOLEAN)",
    "CREATE TABLE comments (id INTEGER PRIMARY KEY, parent_submission INTEGER, created_utc INTEGER, deleted_utc INTEGER, is_banned BOOLEAN)",
    "CREATE TABLE votes (id INTEGER PRIMARY KEY, user_id INTEGER, submission_id INTEGER, created_utc INTEGER, vote_type INTEGER)",
    "CREATE TABLE modactions (id INTEGER PRIMARY KEY, user_id INTEGER, board_id INTEGER, created_utc INTEGER, kind TEXT)",
    "CREATE TABLE flags (id INTEGER PRIMARY KEY, user_id INTEGER, post_id INTEGER, created_utc INTEGER)",
    "CREATE TABLE commentflags (id INTEGER PRIMARY KEY, user_id INTEGER, comment_id INTEGER, created_utc INTEGER)",
)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for ddl in TABLES:
            conn.execute(text(ddl))
    with Session(engine) as session:
        yield session


def run(db, sql, **params):
    db.execute(text(sql), params)


def test_the_counters_add_up_per_guild_and_per_day(db):
    for _ in range(3):
        store.bump(db, 1, "joins", NOW)
    store.bump(db, 1, "leaves", NOW)
    store.bump(db, 1, "page_views", NOW)
    store.bump(db, 1, "page_views", NOW + DAY)
    store.bump(db, 2, "joins", NOW)
    rows = db.execute(text("SELECT board_id, day, joins, leaves, page_views FROM guild_day_stats ORDER BY board_id, day")).all()
    today = rules.day_of(NOW)
    assert rows == [(1, today, 3, 1, 1), (1, today + 1, 0, 0, 1), (2, today, 1, 0, 0)]


def test_a_counter_that_fails_never_raises():
    class Broken:
        def begin_nested(self):
            raise RuntimeError("no table")
    store.bump(Broken(), 1, "joins", NOW)           # swallowed


def test_the_report_counts_the_window_and_nothing_outside_it_or_in_another_guild(db):
    today = rules.day_of(NOW)
    inside, outside = NOW - 2 * DAY, NOW - 8 * DAY                          # a 7-day window holds 6 days back and today
    for day, joins, leaves, views in ((today, 2, 1, 10), (today - 6, 1, 0, 4), (today - 7, 9, 9, 9)):
        run(db, "INSERT INTO guild_day_stats VALUES (1, :d, :j, :l, :v)", d=day, j=joins, l=leaves, v=views)
    run(db, "INSERT INTO guild_day_stats VALUES (2, :d, 50, 50, 50)", d=today)
    run(db, "INSERT INTO subscriptions (user_id, board_id, created_utc, is_active) VALUES (1,1,0,1), (2,1,0,1), (3,1,0,0), (4,2,0,1)")
    # posts: two live in the window, one deleted, one removed, one outside, one in another guild
    run(db, "INSERT INTO submissions VALUES (1,1,:t,0,0), (2,1,:t,0,0), (3,1,:t,5,0), (4,1,:t,0,1), (5,1,:o,0,0), (6,2,:t,0,0)", t=inside, o=outside)
    run(db, "INSERT INTO comments VALUES (1,1,:t,0,0), (2,6,:t,0,0), (3,1,:t,9,0), (4,1,:o,0,0), (5,6,:t,0,0)", t=inside, o=outside)
    run(db, "INSERT INTO votes (user_id, submission_id, created_utc, vote_type) VALUES (9,1,:t,1), (8,1,:t,1), (7,1,:t,-1), (6,6,:t,1), (5,1,:o,1)", t=inside, o=outside)
    run(db, "INSERT INTO modactions (user_id, board_id, created_utc, kind) VALUES (1,1,:t,'exile_user'), (1,1,:t,'exile_user'), "
            "(1,1,:t,'ban_post'), (1,2,:t,'ban_post'), (1,1,:o,'exile_user')", t=inside, o=outside)
    run(db, "INSERT INTO flags (user_id, post_id, created_utc) VALUES (1,1,:t), (2,1,:t), (3,6,:t), (4,1,:o)", t=inside, o=outside)
    run(db, "INSERT INTO commentflags (user_id, comment_id, created_utc) VALUES (1,1,:t), (1,2,:t), (1,4,:o)", t=inside, o=outside)

    data = store.report(db, 1, NOW, 7)
    assert data["members"] == 2
    assert (data["joined"], data["left"], data["net"], data["views"]) == (3, 1, 2, 14)
    assert data["posts"] == 2 and data["comments"] == 1 and data["upvotes"] == 2
    assert data["mod_actions"] == {"ban_post": 1, "exile_user": 2} and data["exiles"] == 2
    assert data["reports"] == 3, "two reports of the guild's post and one of a comment in it"
    assert len(data["series"]) == 7 and sum(d["posts"] for d in data["series"]) == 2
    wide = store.report(db, 1, NOW, 28)
    assert wide["joined"] == 12 and wide["comments"] == 2 and wide["posts"] == 3


def test_an_empty_guild_reports_zeros(db):
    data = store.report(db, 99, NOW, 28)
    assert (data["members"], data["joined"], data["posts"], data["reports"], data["mod_actions"]) == (0, 0, 0, 0, {})
    assert len(data["series"]) == 28


def test_counters_older_than_400_days_are_pruned(db):
    today = rules.day_of(NOW)
    run(db, "INSERT INTO guild_day_stats VALUES (1, :d, 1, 0, 0), (1, :e, 1, 0, 0)", d=today - 401, e=today - 399)
    assert store.prune(db, NOW) == 1
    assert [r[0] for r in db.execute(text("SELECT day FROM guild_day_stats"))] == [today - 399]


def test_the_migration_matches_the_schema_and_only_counts_are_selected():
    schema = (ROOT / "schema.sql").read_text(encoding="utf-8")
    migration = (ROOT / "scripts" / "migrations" / "2026-11-20_guild_day_stats.sql").read_text(encoding="utf-8")

    def body(sql, opening):
        start = sql.index("(", sql.index(opening)) + 1
        return sql[start:sql.index("\n);", start)]
    assert body(schema, "CREATE TABLE public.guild_day_stats").strip() == body(migration, "CREATE TABLE IF NOT EXISTS public.guild_day_stats").strip()
    assert "('2026-11-20_guild_day_stats.sql')" in schema

    source = (ROOT / "ruqqus" / "helpers" / "guild_insights_store.py").read_text(encoding="utf-8")
    for select in re.findall(r'text\(\s*"(SELECT .*?)"\s*\)', source, re.S):
        columns = select.split(" FROM ")[0].lower()
        assert not any(word in columns for word in ("user_id", "username", "author", "ip", "target_user")), select
    assert "top contributors" not in source.lower()
