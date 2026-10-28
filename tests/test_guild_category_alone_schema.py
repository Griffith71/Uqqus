"""A guild may choose its broad category alone: boards.cat_id (set only for such a guild, which then has subcat_id 0) has
to agree across the model, schema.sql, the migration and the seed. The migration also takes out the "General <Category>"
subcategories (ids 306-332) of an earlier design, and must do it safely on a database that has them, one that never had
them and a fresh one."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIGRATION = "2026-12-02_guild_category_alone.sql"
GENERAL_ROW = r"INSERT INTO public\.subcategories VALUES \(\d+, \d+, 'General "


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def migration():
    return read("scripts", "migrations", MIGRATION)


def statements():
    """The migration without its comments, one string per statement."""
    return [s.strip() for s in re.sub(r"--[^\n]*", "", migration()).split(";") if s.strip()]


def test_the_column_is_in_the_model_the_schema_and_the_migration():
    assert re.search(r"^\s*cat_id\s*=\s*Column\(Integer\b", read("ruqqus", "classes", "boards.py"), re.M)
    schema = read("schema.sql")
    table = schema.split("CREATE TABLE public.boards (")[1].split("\n);")[0]
    assert table.endswith("    is_circle boolean DEFAULT false NOT NULL,\n    cat_id integer")
    assert "CREATE INDEX boards_cat_idx ON public.boards USING btree (cat_id);" in schema
    assert "ALTER TABLE boards ADD COLUMN IF NOT EXISTS cat_id integer;" in migration()
    assert "CREATE INDEX IF NOT EXISTS boards_cat_idx ON boards (cat_id);" in migration()


def test_the_column_has_no_foreign_key_like_subcat_id():
    # a second writer of the foreign key would also fail tests/pg, which runs every migration again
    code = " ".join(statements()).upper()
    assert "REFERENCES" not in code and "FOREIGN KEY" not in code and "CONSTRAINT" not in code
    assert "boards_cat_id_fkey" not in read("schema.sql")


def test_the_migration_is_safe_to_run_twice():
    sql = migration()
    # migrate.py splits a file on ";" when it mentions this word anywhere, even in a comment
    assert "CONCURRENTLY" not in sql.upper()
    for statement in statements():
        if statement.startswith(("ALTER TABLE", "CREATE INDEX")):
            assert "IF NOT EXISTS" in statement, statement
    # the rows it moves and deletes are only the General ones, and what it leaves is the same on a second run
    assert sql.count("UPDATE boards") == sql.count("DELETE FROM subcategories") == 1
    code = " ".join(statements())
    assert "curations" not in code and "feed_defaults" not in code


def test_the_migration_moves_the_guilds_before_it_deletes_the_rows():
    update, delete, sequence = (next(s for s in statements() if s.startswith(start))
                                for start in ("UPDATE boards", "DELETE FROM subcategories", "SELECT setval"))
    assert statements().index(update) < statements().index(delete) < statements().index(sequence)
    assert statements()[-1] == sequence
    assert sequence == "SELECT setval('subcategories_id_seq', (SELECT max(id) FROM subcategories))"
    assert "SET subcat_id = 0, cat_id = s.cat_id" in update
    # a row is a General one by its id (305 + its category's id) and its name together, in both statements
    for statement in (update, delete):
        assert "s.id = 305 + s.cat_id" in statement and "s.name = 'General ' || c.name" in statement
    # and a guild still filed under one keeps it
    assert "NOT EXISTS (SELECT 1 FROM boards b WHERE b.subcat_id = s.id)" in delete
    # the UPDATE touches only the guilds filed under those rows, and the DELETE only rows of a real category
    assert "WHERE boards.subcat_id = s.id" in update and "c.id = s.cat_id" in delete


def test_the_migration_sorts_after_the_others_and_ends_the_ledger():
    names = sorted(p.name for p in (ROOT / "scripts" / "migrations").glob("*.sql"))
    assert MIGRATION in names and MIGRATION > "2026-11-30_site_feedback.sql"
    schema = read("schema.sql")
    listed = re.findall(r"\('(\d{4}-\d{2}-\d{2}_[\w-]+\.sql)'\)", schema.split("INSERT INTO public.schema_migrations", 1)[1])
    assert MIGRATION in listed
    assert not (ROOT / "scripts" / "migrations" / "2026-12-01_category_general_rows.sql").exists()
    assert "category_general_rows" not in schema


def test_the_seed_has_no_general_rows_and_the_sequence_ends_at_the_last_subcategory():
    seed = read("seed-db.sql")
    assert not re.search(GENERAL_ROW, seed)
    ids = [int(i) for i in re.findall(r"INSERT INTO public\.subcategories VALUES \((\d+), ", seed)]
    assert max(ids) == 305
    assert "SELECT pg_catalog.setval('public.subcategories_id_seq', 305, true);" in seed
