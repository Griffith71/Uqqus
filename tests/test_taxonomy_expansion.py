"""The category expansion: 12 categories and 196 subcategories (191 since the clean-up took five back out) added to the 15 and 86 there were, in seed-db.sql (fresh
databases) and scripts/migrations/2026-11-29_category_expansion.sql (existing ones). Nothing that existed may change:
guilds hold subcategory ids, and 44 (Offensive) is named in the code. The clean-up of 2026-12-08 (scripts/migrations/2026-12-08_taxonomy_cleanup.sql)
then renamed fifteen rows in place and merged sixteen into others: no id was reused or renumbered, 108 (Identity Politics) became the
ordinary subcategory Social Issues, and (2026-10-31) every subcategory got a name of its own, the News places went (their guilds are filed
under News alone) and the two Film & TV rows became one."""
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
SUBCATEGORY_COUNT = 266           # 282 less the sixteen the clean-up merged away
LAST_SUBCATEGORY = 305
CLEANUP = "2026-12-08_taxonomy_cleanup.sql"

# the clean-up: id -> (category id, old name, new name) renamed in place, and id -> (category id, old name, id merged into) retired
RENAMED = {
    125: (3, "LGBTQ", "Modern Culture"), 49: (12, "Left", "Political Ideas & Debate"),
    172: (12, "World Politics", "International Affairs"), 108: (12, "Identity Politics", "Social Issues"),
    67: (14, "Combat", "Combat Sports"), 91: (14, "Team", "Other Sports"), 98: (13, "Hard Sciences", "General Science"),
    99: (13, "Soft Sciences", "Social Sciences"), 41: (10, "Casual", "General Memes"), 75: (15, "Help", "Tech Support"),
    # four subcategories were all "News": each is named for what it is the news of (the owner, 2026-10-31: every subcategory has its own name)
    23: (5, "News", "Entertainment News"), 55: (12, "News", "Political News"), 60: (13, "News", "Science News"),
    74: (15, "News", "Tech News"), 28: (6, "Gaming news", "Gaming News"),
}
MERGED = {
    50: (12, "Right", 49), 51: (12, "Authoritarian", 49), 52: (12, "Libertarian", 49), 176: (12, "Centrist", 49),
    171: (12, "US Politics", 174), 173: (12, "Elections", 174), 194: (14, "Boxing & MMA", 67), 90: (14, "Individual", 91),
    93: (14, "Partner", 91), 100: (13, "Natural Sciences", 98), 42: (10, "Dank", 41), 165: (10, "Interesting", 41),
    96: (15, "Mechanics", 97),
    # the two Film & TV subcategories are one: Entertainment's keeps the name (the only one that changes category)
    8: (1, "Film & TV", 24),
    # a place is not a kind of news (the region filter is for that): their guilds are filed under News alone (-11 is category 11 alone)
    45: (11, "Local", -11), 46: (11, "North America", -11),
}
CROSS_CATEGORY = {8}
# the five merged rows that the expansion itself had added (ids above the old 109): its migration no longer inserts them
RETIRED_EXPANSION_IDS = {165, 171, 173, 176, 194}

# the same name on purpose: they become one topic (#music, the Explore chip, in:#music), so the chip is not empty
# and the guilds filed under the old subcategory are found
SHARED_SLUGS = {"filmtv", "music", "food", "animals", "outdoors"}

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
    assert [r[0] for r in new_subcategories] == [
        i for i in range(LAST_OLD_SUBCATEGORY + 1, LAST_SUBCATEGORY + 1) if i not in RETIRED_EXPANSION_IDS]
    seed = read("seed-db.sql")
    assert f"setval('public.categories_id_seq', {CATEGORY_COUNT}, true)" in seed
    assert f"setval('public.subcategories_id_seq', {LAST_SUBCATEGORY}, true)" in seed


def test_every_subcategory_has_a_parent():
    categories, subcategories = seed_rows()
    parents = {r[0] for r in categories}
    assert {r[1] for r in subcategories} <= parents
    new_categories, new_subcategories = new_rows()
    assert {r[0] for r in new_categories} <= {r[1] for r in new_subcategories}, "a new category with nothing in it"


def test_nothing_that_existed_was_renumbered_only_renamed_or_merged_by_the_cleanup():
    categories, subcategories = seed_rows()
    names = {i: name for i, name, icon in categories}
    parents = {i: (cat, name) for i, cat, name in subcategories}
    for cat_id, (name, children) in BEFORE.items():
        assert names[cat_id] == name
        for sub_id, sub_name in children.items():
            if sub_id in MERGED:
                assert sub_id not in parents, sub_id
                continue
            if sub_id in RENAMED:
                sub_name = RENAMED[sub_id][2]
            assert parents[sub_id] == (cat_id, sub_name)
    assert sum(len(children) for _, children in BEFORE.values()) == 86
    assert {i for i in parents if i <= LAST_OLD_SUBCATEGORY} == {i for _, c in BEFORE.values() for i in c if i not in MERGED}
    # the ids the expansion added are as that migration has them, less none (it no longer inserts the merged ones)
    expansion_categories, expansion_subcategories = expansion_rows()
    assert [r for r in categories if r[0] > LAST_OLD_CATEGORY] == expansion_categories
    assert [r for r in subcategories if r[0] > LAST_OLD_SUBCATEGORY] == expansion_subcategories
    for sub_id, (cat_id, _old, new) in RENAMED.items():
        assert parents[sub_id] == (cat_id, new), sub_id
    for sub_id, (cat_id, _old, into) in MERGED.items():
        assert sub_id not in parents, sub_id
        if into < 0:
            assert -into == cat_id and any(i == cat_id for i, _n, _icon in categories), sub_id         # filed under its own category alone
        else:
            assert (parents[into][0] == cat_id) == (sub_id not in CROSS_CATEGORY), sub_id
    # named in helpers/suggestions.py (the Child filter hides 44) and nowhere else: 108 is an ordinary row now (Social Issues)
    assert parents[44] == (10, "Offensive") and parents[108] == (12, "Social Issues")
    suggestions = read("ruqqus", "helpers", "suggestions.py")
    assert "SPECIAL_SUBCATS" not in suggestions and "CHILD_HIDDEN_SUBCATS = (44,)" in suggestions
    assert not hasattr(topics, "UNLISTED_SUBCAT")
    assert "UNLISTED_SUBCAT" not in read("ruqqus", "routes", "front.py") + read("ruqqus", "helpers", "similar_guilds_store.py")


def test_the_cleanup_migration_does_what_this_file_says():
    migration = read("scripts", "migrations", CLEANUP)
    renames = {int(i): (int(c), old, new) for i, c, old, new in re.findall(
        rf"\((\d+), (\d+), {QUOTED}, {QUOTED}\)", migration.split("taxonomy_cleanup_rename VALUES", 1)[1].split(";", 1)[0])}
    merges = {int(i): (int(c), old, int(into)) for i, c, old, into in re.findall(
        rf"\((\d+), (\d+), {QUOTED}, (-?\d+)\)", migration.split("taxonomy_cleanup_merge VALUES", 1)[1].split(";", 1)[0])}
    assert renames == RENAMED and merges == MERGED
    assert len(renames) == 15 and len(merges) == 16 and SUBCATEGORY_COUNT == 282 - len(merges)
    # the guards, the moves and the deletion come in that order, and a row is removed only once nothing holds it
    order = [migration.index(x) for x in ("RAISE EXCEPTION", "UPDATE subcategories s SET name", "UPDATE boards b SET subcat_id",
                                          "UPDATE curations c", "UPDATE users SET feed_defaults", "DELETE FROM subcategories s")]
    assert order == sorted(order)
    assert "NOT EXISTS (SELECT 1 FROM boards b WHERE b.subcat_id = s.id)" in migration
    # a negative target is a category: the guilds are filed under it alone, and a category that is missing stops the file
    assert "subcat_id = GREATEST(m.new_id, 0), cat_id = CASE WHEN m.new_id < 0 THEN -m.new_id ELSE NULL END" in migration
    assert "m.new_id < 0 AND NOT EXISTS (SELECT 1 FROM categories c WHERE c.id = -m.new_id)" in migration
    assert migration.count("CREATE TEMP TABLE") == migration.count("DROP TABLE") == 2
    # no new row and no new id: the sequences are untouched
    assert "INSERT INTO subcategories" not in migration and "setval" not in migration


def test_the_cleanup_is_in_the_ledger_after_the_others_and_the_seed_has_its_final_names():
    schema = read("schema.sql")
    listed = re.findall(r"\('(\d{4}-\d{2}-\d{2}_[\w-]+\.sql)'\)", schema.split("INSERT INTO public.schema_migrations", 1)[1])
    assert listed == sorted(listed) and listed.index(CLEANUP) > listed.index("2026-12-07_word_filter_language.sql")
    _, subcategories = seed_rows()
    seed_names = {name for _, _, name in subcategories}
    seed_ids = {i for i, _, _ in subcategories}
    for sub_id, (_cat, old, new) in RENAMED.items():
        assert new in seed_names and sub_id in seed_ids, (old, new)
        assert old not in seed_names or old in {"News"} | {n for _c, _o, n in RENAMED.values()}, old      # only a name another row now holds
    for sub_id, (_cat, old, _into) in MERGED.items():
        assert sub_id not in seed_ids, old


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


def test_the_retired_ids_and_topics_are_the_migrations_own_tables():
    """`topics.RETIRED_SUBCATS` (a session's catids, `?cats=`) and `topics.RETIRED` (`/topic/<slug>`, `in:#name`) are what the migration did,
    not a second list that can drift: every merged id goes to the row it was merged into (a negative one is a category, filed under it
    alone), and every old name's slug leads to the slug of the row that holds its guilds now."""
    assert topics.RETIRED_SUBCATS == {old: into for old, (_cat, _name, into) in MERGED.items()}
    categories, subcategories = seed_rows()
    final = {i: name for i, _cat, name in subcategories}
    category_names = {i: name for i, name, _icon in categories}
    live = {slug(name) for name in final.values()} | {slug(name) for name in category_names.values()}

    def target(into):
        return category_names[-into] if into < 0 else final[into]

    # a name that is a topic today needs no redirect: "news" (the category) and "Film & TV" stay, and "Gaming news" only changed its case
    expected = {slug(old): slug(new) for _cat, old, new in RENAMED.values() if slug(old) not in live}
    expected.update({slug(old): slug(target(into)) for _cat, old, into in MERGED.values() if slug(old) not in live})
    assert topics.RETIRED == expected
    assert topics.RETIRED["local"] == topics.RETIRED["northamerica"] == "news"
    # nothing retired is a name that exists now, or the redirect could never be reached
    assert not set(topics.RETIRED) & live
    assert set(topics.RETIRED_SUBCATS).isdisjoint(final)
    assert topics.current_ids([50, 8, 51, 171, 99, -12, 49, 45, 46]) == [49, 24, 174, 99, -12, -11]


def test_every_subcategory_has_a_name_of_its_own_and_news_is_a_kind_not_a_place():
    """The owner, 2026-10-31: "each sub category must have its own unique name, otherwise the search will turn up multiple news options but
    they're under different major categories that aren't shown", and "remove north america, local from news category ... things in regard
    to location can be set by the region filter". A category may share its name with ONE subcategory on purpose (they are one topic)."""
    categories, subcategories = seed_rows()
    counts = Counter(slug(name) for _i, _cat, name in subcategories)
    assert {name for name, count in counts.items() if count > 1} == set()
    names = {i: name for i, _cat, name in subcategories}
    assert 45 not in names and 46 not in names and 8 not in names
    news = {name for _i, cat, name in subcategories if cat == 11}
    assert news == {"World", "Upbeat", "Breaking News", "Crime & Courts", "Opinion & Analysis", "Fact Checks"}
    assert [names[i] for i in (23, 55, 60, 74, 28)] == ["Entertainment News", "Political News", "Science News", "Tech News", "Gaming News"]
