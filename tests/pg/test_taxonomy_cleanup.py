"""scripts/migrations/2026-12-08_taxonomy_cleanup.sql on a real Postgres: the database is put back to what a site had before it (the old
names, the sixteen retired rows, guilds, curations and members' saved filters on them), the migration runs, and then runs again.
tests/test_taxonomy_expansion.py pins the id table; test_schema_and_migrations.py runs the file a second time on a database that is
already clean. Here the data moves."""
import json
import secrets
import sys
from pathlib import Path

import pytest
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import migrate  # noqa: E402
from test_taxonomy_expansion import MERGED, RENAMED, seed_rows  # noqa: E402

NAME = "2026-12-08_taxonomy_cleanup.sql"
FIRST_BOARD = 9_100_000


def state_before(conn):
    """The old names and the retired rows, as a site that never ran the clean-up has them."""
    for sub_id, (cat_id, old, _new) in RENAMED.items():
        conn.execute(text("UPDATE subcategories SET name = :n WHERE id = :i"), {"n": old, "i": sub_id})
    for sub_id, (cat_id, old, _into) in MERGED.items():
        conn.execute(text("INSERT INTO subcategories (id, cat_id, name, _visible) VALUES (:i, :c, :n, true) ON CONFLICT (id) DO NOTHING"),
                     {"i": sub_id, "c": cat_id, "n": old})


def add_board(conn, offset, subcat_id):
    conn.execute(text(
        "INSERT INTO boards (id, name, has_banner, has_profile, profile_nonce, banner_nonce, subcat_id) "
        "VALUES (:i, :n, false, false, 0, 0, :s)"), {"i": FIRST_BOARD + offset, "n": f"ci_tax_{secrets.token_hex(4)}", "s": subcat_id})
    return FIRST_BOARD + offset


def subcat_of(conn, board_id):
    return conn.execute(text("SELECT subcat_id FROM boards WHERE id = :i"), {"i": board_id}).scalar()


def filed_under(conn, board_id):
    """(subcat_id, cat_id): a guild filed under a category alone is (0, N), one under a subcategory (N, NULL)."""
    return tuple(conn.execute(text("SELECT subcat_id, cat_id FROM boards WHERE id = :i"), {"i": board_id}).one())


def test_the_cleanup_moves_guilds_filters_and_defaults_then_removes_the_retired_rows(pg_engine, make_member, real_app):
    members = [make_member() for _ in range(5)]
    real_app[1].remove()          # the app's session would sit 'idle in transaction' on users, and a later migration test would wait for it
    with pg_engine.connect() as conn:
        state_before(conn)
        # a guild on every retired row, one on a row that is kept and one that is renamed
        boards = {sub_id: add_board(conn, k, sub_id) for k, sub_id in enumerate([*MERGED, 49, 108, 125, 9])}
        conn.execute(text("UPDATE boards SET cat_id = 12 WHERE id = :i"), {"i": boards[50]})         # a stale category beside a subcategory
        curations = {
            "mixed": (FIRST_BOARD + 1, "50,51,171,-12,5,176,49,8"),
            "none": (FIRST_BOARD + 2, "9,10"),
            "empty": (FIRST_BOARD + 3, ""),
            "twice": (FIRST_BOARD + 4, " 50 ,50,0007"),
            "news": (FIRST_BOARD + 5, "47, 45,46,48"),            # Local and North America become the guilds filed under News alone
            "film": (FIRST_BOARD + 6, "8,24,9"),
        }
        for key, (cid, filt) in curations.items():
            conn.execute(text("INSERT INTO curations (id, owner_id, name, slug, category_filter) VALUES (:i, :o, :n, :s, :f)"),
                         {"i": cid, "o": members[0].id, "n": f"ci {key}", "s": f"ci-tax-{key}-{secrets.token_hex(2)}", "f": filt})
        defaults = {
            members[0].id: json.dumps({"regions": [], "languages": [], "categories": [50, 8, 51, 171, 99, -12]}),
            members[1].id: json.dumps({"regions": [], "languages": [], "categories": [9, 10, 49]}),
            members[2].id: "this is not json 50",
            members[3].id: "",
            members[4].id: json.dumps({"regions": [], "languages": [], "categories": [45, 47, 46]}),
        }
        for uid, blob in defaults.items():
            conn.execute(text("UPDATE users SET feed_defaults = :b WHERE id = :i"), {"b": blob, "i": uid})
        try:
            migrate.run_file(conn, NAME)
            after_once = snapshot(conn, boards, curations, defaults)
            migrate.run_file(conn, NAME)
            assert snapshot(conn, boards, curations, defaults) == after_once, "the second run changed something"

            # what the migration leaves is what seed-db.sql says: every subcategory row, to the last name and parent (not the tables above)
            _categories, seeded = seed_rows()
            assert {tuple(r) for r in conn.execute(text("SELECT id, cat_id, name FROM subcategories")).all()} == set(seeded)

            # the guilds
            for sub_id, (cat_id, old, into) in MERGED.items():
                # a negative target is a category: the guild is filed under it alone (subcat_id 0, cat_id N)
                assert filed_under(conn, boards[sub_id]) == ((0, -into) if into < 0 else (into, None)), old
            assert subcat_of(conn, boards[49]) == 49 and subcat_of(conn, boards[108]) == 108
            assert subcat_of(conn, boards[125]) == 125 and subcat_of(conn, boards[9]) == 9
            # the rows
            gone = {r[0] for r in conn.execute(text("SELECT id FROM subcategories WHERE id = ANY(:ids)"), {"ids": list(MERGED)})}
            assert gone == set()
            names = dict(conn.execute(text("SELECT id, name FROM subcategories WHERE id = ANY(:ids)"), {"ids": list(RENAMED)}).all())
            assert names == {i: new for i, (_c, _old, new) in RENAMED.items()}
            # the curations: remapped, sorted and without repeats; the others as they were
            filters = {k: conn.execute(text("SELECT category_filter FROM curations WHERE id = :i"), {"i": cid}).scalar()
                       for k, (cid, _f) in curations.items()}
            assert filters == {"mixed": "-12,5,24,49,174", "none": "9,10", "empty": "", "twice": "7,49", "news": "-11,47,48",
                               "film": "9,24"}
            # the members' saved filters
            saved = {uid: conn.execute(text("SELECT feed_defaults FROM users WHERE id = :i"), {"i": uid}).scalar() for uid in defaults}
            assert json.loads(saved[members[0].id])["categories"] == [49, 24, 174, 99, -12]
            assert json.loads(saved[members[4].id])["categories"] == [-11, 47]
            assert json.loads(saved[members[0].id])["regions"] == [] and json.loads(saved[members[0].id])["languages"] == []
            assert saved[members[1].id] == defaults[members[1].id]               # nothing to change: not even rewritten
            assert saved[members[2].id] == defaults[members[2].id] and saved[members[3].id] == ""
        finally:
            conn.execute(text("DELETE FROM boards WHERE id >= :f AND id < :t"), {"f": FIRST_BOARD, "t": FIRST_BOARD + 100})
            conn.execute(text("DELETE FROM curations WHERE id >= :f AND id < :t"), {"f": FIRST_BOARD, "t": FIRST_BOARD + 100})
            conn.execute(text("UPDATE users SET feed_defaults = '' WHERE id = ANY(:ids)"), {"ids": [m.id for m in members]})
            migrate.run_file(conn, NAME)             # whatever the test left, the database ends clean


def snapshot(conn, boards, curations, defaults):
    return (
        sorted(conn.execute(text("SELECT id, subcat_id, cat_id FROM boards WHERE id >= :f AND id < :t"), {"f": FIRST_BOARD, "t": FIRST_BOARD + 100}).all()),
        sorted(conn.execute(text("SELECT id, category_filter FROM curations WHERE id >= :f AND id < :t"), {"f": FIRST_BOARD, "t": FIRST_BOARD + 100}).all()),
        sorted(conn.execute(text("SELECT id, feed_defaults FROM users WHERE id = ANY(:ids)"), {"ids": list(defaults)}).all()),
        sorted(conn.execute(text("SELECT id, name FROM subcategories ORDER BY id")).all()),
    )


def test_a_row_held_by_a_guild_the_cleanup_does_not_move_is_not_deleted(pg_engine):
    """The deletion asks `NOT EXISTS (SELECT 1 FROM boards ...)`: if a guild still holds a retired id when the delete runs (a guild made
    by a worker still running the old code between the moves and the delete), the row stays and the guild keeps its category."""
    with pg_engine.connect() as conn:
        state_before(conn)
        board = add_board(conn, 50, 52)
        try:
            # a trigger-free way to hold the row: the guild is moved by the file, so make the move impossible by taking the row out of
            # the merge table's reach, i.e. run only the deletion step as the file words it
            sql = (ROOT / "scripts" / "migrations" / NAME).read_text(encoding="utf-8")
            delete = sql[sql.index("DELETE FROM subcategories s"):sql.index("DROP TABLE taxonomy_cleanup_merge")]
            conn.execute(text("CREATE TEMP TABLE taxonomy_cleanup_merge (old_id integer, cat_id integer, old_name character varying(64), new_id integer)"))
            conn.execute(text("INSERT INTO taxonomy_cleanup_merge VALUES (52, 12, 'Libertarian', 49)"))
            conn.connection.cursor().execute(delete)
            assert conn.execute(text("SELECT count(*) FROM subcategories WHERE id = 52")).scalar() == 1
            assert subcat_of(conn, board) == 52
        finally:
            conn.execute(text("DROP TABLE IF EXISTS taxonomy_cleanup_merge"))
            conn.execute(text("DELETE FROM boards WHERE id = :i"), {"i": board})
            migrate.run_file(conn, NAME)


def test_a_row_at_a_renamed_id_with_another_name_stops_the_migration_and_changes_nothing(pg_engine):
    with pg_engine.connect() as conn:
        conn.execute(text("UPDATE subcategories SET name = 'Something Else' WHERE id = 125"))
        try:
            with pytest.raises(Exception) as caught:
                migrate.run_file(conn, NAME)
            assert "subcategory 125" in str(caught.value)
            assert conn.execute(text("SELECT name FROM subcategories WHERE id = 125")).scalar() == "Something Else"
            assert conn.execute(text("SELECT to_regclass('pg_temp.taxonomy_cleanup_merge')")).scalar() is None      # rolled back whole
        finally:
            conn.execute(text("UPDATE subcategories SET name = 'Modern Culture' WHERE id = 125"))


def test_a_row_at_a_merged_id_with_another_name_stops_the_migration_before_it_moves_anything(pg_engine):
    with pg_engine.connect() as conn:
        state_before(conn)
        board = add_board(conn, 60, 50)
        conn.execute(text("UPDATE subcategories SET name = 'Something Else' WHERE id = 50"))
        try:
            with pytest.raises(Exception) as caught:
                migrate.run_file(conn, NAME)
            assert "subcategory 50" in str(caught.value)
            assert subcat_of(conn, board) == 50                                                   # nothing moved
            assert conn.execute(text("SELECT name FROM subcategories WHERE id = 125")).scalar() == "LGBTQ"      # nor was anything renamed
        finally:
            conn.execute(text("UPDATE subcategories SET name = 'Right' WHERE id = 50"))
            migrate.run_file(conn, NAME)                      # whatever the test left, the database ends clean
            conn.execute(text("DELETE FROM boards WHERE id = :i"), {"i": board})


def test_a_merge_into_a_row_that_does_not_exist_stops_the_migration_whole(pg_engine):
    with pg_engine.connect() as conn:
        state_before(conn)
        target = conn.execute(text("SELECT id, cat_id, name, description, _visible FROM subcategories WHERE id = 174")).one()
        board = add_board(conn, 61, 171)
        conn.execute(text("DELETE FROM subcategories WHERE id = 174"))
        try:
            with pytest.raises(Exception) as caught:
                migrate.run_file(conn, NAME)
            assert "174 to merge into" in str(caught.value)
            assert subcat_of(conn, board) == 171                                                   # the guild is where it was
            assert conn.execute(text("SELECT count(*) FROM subcategories WHERE id = ANY(:ids)"), {"ids": list(MERGED)}).scalar() == len(MERGED)
            assert conn.execute(text("SELECT name FROM subcategories WHERE id = 125")).scalar() == "LGBTQ"      # the renames went with it
        finally:
            conn.execute(text("INSERT INTO subcategories (id, cat_id, name, description, _visible) VALUES (:i, :c, :n, :d, :v)"),
                         {"i": target[0], "c": target[1], "n": target[2], "d": target[3], "v": target[4]})
            migrate.run_file(conn, NAME)
            conn.execute(text("DELETE FROM boards WHERE id = :i"), {"i": board})


def test_a_guild_filed_under_a_removed_news_place_ends_up_under_news_alone_and_a_missing_category_stops_the_migration(pg_engine):
    """-11 is "the guilds filed under News alone": the target of Local and North America. A target category that does not exist is a stop,
    not a guild left with a category that is not there (the file is run with -99 in place of -11, a category nobody has)."""
    with pg_engine.connect() as conn:
        state_before(conn)
        board = add_board(conn, 62, 45)
        sql = (ROOT / "scripts" / "migrations" / NAME).read_text(encoding="utf-8")
        assert sql.count(", -11)") == 2
        try:
            with pytest.raises(Exception) as caught:
                conn.connection.cursor().execute(sql.replace(", -11)", ", -99)"))
            assert "-99 to merge into" in str(caught.value)
            assert filed_under(conn, board) == (45, None)                                         # the guild is where it was
            migrate.run_file(conn, NAME)
            assert filed_under(conn, board) == (0, 11)
        finally:
            migrate.run_file(conn, NAME)
            conn.execute(text("DELETE FROM boards WHERE id = :i"), {"i": board})
