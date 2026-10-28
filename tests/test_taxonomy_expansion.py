"""The category expansion: 12 categories and 196 subcategories added to the 15 and 86 there were, in seed-db.sql (fresh
databases) and scripts/migrations/2026-11-29_category_expansion.sql (existing ones). Nothing that existed may change:
guilds hold subcategory ids, and 44 (Offensive) and 108 (Identity Politics) are named in the code."""
import re
from collections import Counter
from pathlib import Path

from ruqqus.helpers import topics
from ruqqus.helpers.topics import slug

ROOT = Path(__file__).resolve().parent.parent
MIGRATION = "2026-11-29_category_expansion.sql"
QUOTED = r"'((?:[^']|'')*)'"

LAST_OLD_CATEGORY = 15
LAST_OLD_SUBCATEGORY = 109
CATEGORY_COUNT = 27
SUBCATEGORY_COUNT = 282
LAST_SUBCATEGORY = 305

# the same name on purpose: they become one topic (#music, the Explore chip, in:#music), so the chip is not empty
# and the guilds filed under the old subcategory are found
SHARED_SLUGS = {"news", "filmtv", "music", "food", "animals", "outdoors"}

# the rows that existed before the expansion: category id -> (name, {subcategory id: name})
BEFORE = {
    1: ("Arts", {
        8: "Film & TV", 9: "Photography", 10: "Music", 78: "Sculpture", 80: "Dance", 81: "Literature", 82: "Theater",
        106: "Visual Arts",
    }),
    2: ("Business", {
        11: "Finance", 13: "Entrepreneurship", 83: "Management", 84: "Product", 85: "Startups",
    }),
    3: ("Culture", {
        14: "History", 15: "Language", 16: "Religion", 76: "Counter-Culture", 86: "Architecture", 87: "Philosophy",
        88: "Cuisine",
    }),
    4: ("Discussion", {
        17: "Casual Discussion", 18: "Serious", 19: "Drama", 20: "Ruqqus Meta", 21: "Q&A",
    }),
    5: ("Entertainment", {
        22: "Celebrities", 23: "News", 24: "Film & TV", 89: "Influencers",
    }),
    6: ("Gaming", {
        25: "PC", 26: "Console", 27: "Tabletop", 28: "Gaming news", 29: "Development", 107: "Puzzle",
    }),
    7: ("Hobbies", {
        30: "Crafts", 31: "Outdoors", 32: "DIY", 33: "Skills",
    }),
    8: ("Health", {
        34: "Medical", 35: "Fitness", 36: "Mental Health", 105: "Support",
    }),
    9: ("Lifestyle", {
        37: "Fashion", 38: "Beauty", 39: "Food", 40: "Relationships", 79: "Animals", 102: "Career",
        103: "Personal Finance",
    }),
    10: ("Memes", {
        41: "Casual", 42: "Dank", 43: "Political", 44: "Offensive", 104: "Wholesome",
    }),
    11: ("News", {
        45: "Local", 46: "North America", 47: "World", 48: "Upbeat",
    }),
    12: ("Politics", {
        49: "Left", 50: "Right", 51: "Authoritarian", 52: "Libertarian", 53: "Activism", 55: "News",
        108: "Identity Politics",
    }),
    13: ("Science", {
        60: "News", 98: "Hard Sciences", 99: "Soft Sciences", 100: "Natural Sciences", 101: "Mathematics",
        109: "Space",
    }),
    14: ("Sports", {
        67: "Combat", 90: "Individual", 91: "Team", 92: "Extreme", 93: "Partner",
    }),
    15: ("Technology", {
        69: "Gadgets", 70: "Programming", 71: "Hardware", 72: "Software", 74: "News", 75: "Help", 95: "Computers",
        96: "Mechanics", 97: "Engineering",
    }),
}


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def unquote(value):
    return value.replace("''", "'")


def seed_rows():
    """The seed's category rows (id, name, icon) and subcategory rows (id, category id, name), in file order."""
    seed = read("seed-db.sql")
    categories = [(int(i), unquote(name), unquote(icon)) for i, color, visible, name, description, icon, nsfw in re.findall(
        rf"INSERT INTO public\.categories VALUES \((\d+), '([0-9a-f]{{6}})', (true|false), {QUOTED}, {QUOTED}, {QUOTED}, "
        rf"(true|false)\);", seed)]
    subcategories = [(int(i), int(cat), unquote(name)) for i, cat, name in re.findall(
        rf"INSERT INTO public\.subcategories VALUES \((\d+), (\d+), {QUOTED}, (?:'(?:[^']|'')*'|NULL), (?:NULL|true|false)\);",
        seed)]
    return categories, subcategories


def values_block(migration, table):
    return re.search(rf"INSERT INTO {table} VALUES\n(.*?);\n", migration, re.S).group(1)


def subcategory_values(block):
    return [(int(i), int(cat), unquote(name)) for i, cat, name in re.findall(rf"\((\d+), (\d+), {QUOTED}\)", block)]


def expansion_rows():
    """What the category expansion adds: category rows (id, name, icon) and subcategory rows (id, category id, name)."""
    migration = read("scripts", "migrations", MIGRATION)
    categories = [(int(i), unquote(name), unquote(icon)) for i, name, icon in re.findall(
        rf"\((\d+), {QUOTED}, {QUOTED}\)", values_block(migration, "category_expansion_categories"))]
    return categories, subcategory_values(values_block(migration, "category_expansion_subcategories"))


def new_rows():
    categories, subcategories = seed_rows()
    return ([r for r in categories if r[0] > LAST_OLD_CATEGORY],
            [r for r in subcategories if r[0] > LAST_OLD_SUBCATEGORY])


def test_seed_and_migration_add_the_same_rows():
    assert new_rows() == expansion_rows()


def test_the_new_seed_rows_use_the_columns_the_old_ones_do():
    # categories: id, colour, visible, name, description, icon, is_nsfw; subcategories: id, category, name, NULL, NULL
    seed = read("seed-db.sql")
    for i in re.findall(r"INSERT INTO public\.categories VALUES \((\d+), ", seed):
        if int(i) > LAST_OLD_CATEGORY:
            assert re.search(rf"VALUES \({i}, '805ad5', true, {QUOTED}, '', 'fa-[a-z-]+', false\);", seed), i
    for i in re.findall(r"INSERT INTO public\.subcategories VALUES \((\d+), ", seed):
        if int(i) > LAST_OLD_SUBCATEGORY:
            assert re.search(rf"VALUES \({i}, \d+, {QUOTED}, NULL, NULL\);", seed), i


def test_ids_are_unique_and_run_on_from_the_old_ones():
    categories, subcategories = seed_rows()
    assert len({r[0] for r in categories}) == len(categories) == CATEGORY_COUNT
    assert len({r[0] for r in subcategories}) == len(subcategories) == SUBCATEGORY_COUNT
    new_categories, new_subcategories = expansion_rows()
    assert [r[0] for r in new_categories] == list(range(LAST_OLD_CATEGORY + 1, CATEGORY_COUNT + 1))
    assert [r[0] for r in new_subcategories] == list(range(LAST_OLD_SUBCATEGORY + 1, LAST_SUBCATEGORY + 1))
    seed = read("seed-db.sql")
    assert f"setval('public.categories_id_seq', {CATEGORY_COUNT}, true)" in seed
    assert f"setval('public.subcategories_id_seq', {LAST_SUBCATEGORY}, true)" in seed


def test_every_subcategory_has_a_parent():
    categories, subcategories = seed_rows()
    parents = {r[0] for r in categories}
    assert {r[1] for r in subcategories} <= parents
    new_categories, new_subcategories = new_rows()
    assert {r[0] for r in new_categories} <= {r[1] for r in new_subcategories}, "a new category with nothing in it"


def test_nothing_that_existed_changed():
    categories, subcategories = seed_rows()
    names = {i: name for i, name, icon in categories}
    parents = {i: (cat, name) for i, cat, name in subcategories}
    for cat_id, (name, children) in BEFORE.items():
        assert names[cat_id] == name
        for sub_id, sub_name in children.items():
            assert parents[sub_id] == (cat_id, sub_name)
    assert sum(len(children) for _, children in BEFORE.values()) == 86
    assert {i for i in parents if i <= LAST_OLD_SUBCATEGORY} == {i for _, c in BEFORE.values() for i in c}
    # what the expansion added is as that migration has it
    expansion_categories, expansion_subcategories = expansion_rows()
    assert [r for r in categories if r[0] > LAST_OLD_CATEGORY] == expansion_categories
    assert [r for r in subcategories if r[0] > LAST_OLD_SUBCATEGORY] == expansion_subcategories
    # named in helpers/suggestions.py (the Child filter hides 44, /browse leaves out 108) and helpers/topics.py
    assert parents[44] == (10, "Offensive") and parents[108] == (12, "Identity Politics")
    suggestions = read("ruqqus", "helpers", "suggestions.py")
    assert "SPECIAL_SUBCATS = (108,)" in suggestions and "CHILD_HIDDEN_SUBCATS = (44,)" in suggestions
    assert topics.UNLISTED_SUBCAT == 108


def test_names_are_plain_and_short():
    # every name becomes a #hashtag, so no apostrophes or other punctuation (Q&A, Film & TV, Hip-Hop are fine)
    new_categories, new_subcategories = new_rows()
    names = [r[1] for r in new_categories] + [r[2] for r in new_subcategories]
    for name in names:
        assert len(name) < 64, name
        assert re.fullmatch(r"[A-Za-z0-9 &-]+", name), name
        assert name == name.strip() and "  " not in name, name


def test_slugs_are_the_right_length_and_only_the_chosen_ones_repeat():
    categories, subcategories = seed_rows()
    names = [r[1] for r in categories] + [r[2] for r in subcategories]
    new_categories, new_subcategories = new_rows()
    for name in [r[1] for r in new_categories] + [r[2] for r in new_subcategories]:
        assert 2 <= len(slug(name)) <= 25, name
    repeated = {s for s, count in Counter(slug(n) for n in names).items() if count > 1}
    assert repeated == SHARED_SLUGS


def test_the_new_categories_that_share_a_slug_merge_with_a_subcategory_that_was_there():
    categories, subcategories = seed_rows()
    for name in ("Music", "Film & TV", "Food", "Animals", "Outdoors"):
        assert [i for i, n, icon in categories if n == name and i > LAST_OLD_CATEGORY], name
        assert [i for i, cat, n in subcategories if n == name and i <= LAST_OLD_SUBCATEGORY], name


def test_every_name_is_marked_for_the_catalogues():
    from ruqqus.helpers.taxonomy_names import CATEGORIES, SUBCATEGORIES
    categories, subcategories = seed_rows()
    assert list(CATEGORIES) == sorted({r[1] for r in categories})
    assert list(SUBCATEGORIES) == sorted({r[2] for r in subcategories})


def test_the_migration_is_safe_to_run_twice():
    migration = read("scripts", "migrations", MIGRATION)
    assert migration.count("\nON CONFLICT (id) DO NOTHING;\n") == 2
    # a row at one of the ids with another name or parent stops the migration, naming the row
    assert "RAISE EXCEPTION" in migration
    assert "c.name IS DISTINCT FROM n.name" in migration
    assert "s.name IS DISTINCT FROM n.name OR s.cat_id IS DISTINCT FROM n.cat_id" in migration
    assert migration.index("RAISE EXCEPTION") < migration.index("INSERT INTO categories")
    assert "SELECT setval('categories_id_seq', (SELECT max(id) FROM categories));" in migration
    assert "SELECT setval('subcategories_id_seq', (SELECT max(id) FROM subcategories));" in migration
    assert migration.count("CREATE TEMP TABLE") == migration.count("DROP TABLE") == 2


def test_the_ledger_lists_the_migrations_in_order():
    schema = read("schema.sql")
    listed = re.findall(r"\('(\d{4}-\d{2}-\d{2}_[\w-]+\.sql)'\)", schema.split("INSERT INTO public.schema_migrations", 1)[1])
    assert listed.index(MIGRATION) > listed.index("2026-11-28_notice_text.sql")
    assert (ROOT / "scripts" / "migrations" / MIGRATION).exists()


def test_a_curation_can_name_every_subcategory():
    schema = read("schema.sql")
    curations = schema.split("CREATE TABLE public.curations (", 1)[1].split("\n);", 1)[0]
    assert "category_filter character varying(2000)" in curations
    assert "category_filter = Column(String(2000)" in read("ruqqus", "classes", "curations.py")
    assert "ALTER TABLE curations ALTER COLUMN category_filter TYPE character varying(2000);" in read(
        "scripts", "migrations", MIGRATION)
    _, subcategories = seed_rows()
    assert len(",".join(str(r[0]) for r in subcategories)) < 2000
