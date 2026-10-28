"""A guild's category in a filter (ruqqus/helpers/category_filter.py): the one list of integers every filter, picker and
writer shares (positive = subcategory, 0 = Uncategorized, -N = the guilds filed under category N alone), the SQL condition
for it on a stand-in boards table in SQLite, and the notice that names a category. The whole category is `-N` and all of
its subcategory ids (the Categorical list ticks them with the heading). Pure: the categories are small stand-ins shaped
like classes/categories.py, installed the way the topics tests do."""
import ast
import inspect
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, column, create_engine, not_, select, table

from ruqqus.helpers import category_catalog as catalog
from ruqqus.helpers import category_filter as cf
from ruqqus.helpers import topics

ROOT = Path(__file__).resolve().parent.parent


def sub(id_, name, visible=None):
    return NS(id=id_, name=name, _visible=visible)


def cat(id_, name, subs, visible=True, nsfw=False):
    return NS(id=id_, name=name, visible=visible, is_nsfw=nsfw, subcats=subs)


CATEGORIES = [
    cat(1, "Arts", [sub(8, "Film & TV"), sub(10, "Music"), sub(106, "Visual Arts")]),
    cat(10, "Memes", [sub(44, "Offensive"), sub(50, "Dank")]),
    cat(12, "Politics", [sub(108, "Identity Politics"), sub(60, "Debate")]),
    cat(18, "Food", [sub(120, "Baking"), sub(121, "Cooking")]),
    cat(20, "Empty", []),
    cat(30, "Secret", [sub(90, "Quiet")], visible=False),
    cat(31, "Mixed", [sub(92, "Shown", True), sub(93, "Hidden by itself", False)]),
]

# id, subcat_id, cat_id
ROWS = [
    (1, 8, None),       # Arts, Film & TV
    (2, 10, None),      # Arts, Music
    (3, 0, 1),          # Arts alone
    (4, 0, None),       # Uncategorized
    (5, None, None),    # no subcategory at all
    (6, None, 18),      # Food alone, with a NULL subcategory
    (7, 44, None),      # Memes, Offensive (a row the feeds hide)
    (8, 108, None),     # Politics, Identity Politics (unlisted)
    (9, 120, None),     # Food, Baking
    (10, 0, 18),        # Food alone
    (11, 90, None),     # a hidden category's row
    (12, 50, None),     # Memes, Dank
    (13, 93, None),     # a row hidden by itself, in Mixed
    (14, 0, 10),        # Memes alone
    (15, 0, 12),        # Politics alone
]

metadata = MetaData()
boards = Table("boards", metadata, Column("id", Integer, primary_key=True), Column("subcat_id", Integer),
               Column("cat_id", Integer))
ALL = {row[0] for row in ROWS}


@pytest.fixture(scope="module")
def engine():
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(boards.insert(), [{"id": i, "subcat_id": s, "cat_id": c} for i, s, c in ROWS])
    return engine


@pytest.fixture(autouse=True)
def site_categories():
    topics.set_categories(CATEGORIES)
    yield
    topics.set_categories(None)


def found(engine, clause):
    with engine.connect() as connection:
        return {row[0] for row in connection.execute(select(boards.c.id).where(clause))}


def clause_for(ids, **kwargs):
    return cf.board_clause(ids, boards.c.subcat_id, boards.c.cat_id, **kwargs)


# --- the text of an id ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("0", 0), ("7", 7), ("305", 305), ("0007", 7), ("123456789", 123456789),
    ("-1", -1), ("-18", -18), ("-000012", -12), ("-123456789", -123456789),
    ("-0", None), ("-00", None), ("--5", None), ("-", None), ("", None), (" 5", None), ("5 ", None), ("5\n", None),
    ("+5", None), ("1.5", None), ("1e3", None), ("1234567890", None), ("-1234567890", None), ("5,6", None),
    ("²", None), ("-²", None), ("٥", None), ("５", None), ("abc", None),
    (None, None), (5, None), (b"5", None),
])
def test_an_id_is_ascii_digits_with_an_optional_minus(text, expected):
    assert cf.parse_token(text) == expected


# --- the condition ---------------------------------------------------------------------------------------------------

def test_uncategorized_excludes_the_guilds_filed_under_a_category_alone(engine):
    assert found(engine, clause_for([0])) == {4}                     # not 3 and 10 (subcat_id 0 too), not 5 (NULL)


def test_a_subcategory_is_matched_only_by_its_own_guilds(engine):
    assert found(engine, clause_for([8])) == {1}
    assert found(engine, clause_for([8, 120])) == {1, 9}
    assert found(engine, clause_for([999])) == set()


def test_a_category_alone_is_only_the_guilds_filed_under_it_alone(engine):
    assert found(engine, clause_for([-1])) == {3}                       # not 1 and 2, which are filed under Film & TV and Music
    assert found(engine, clause_for([-18])) == {6, 10}                  # 6 has a NULL subcategory: its cat_id is enough
    assert found(engine, clause_for([-10])) == {14} and found(engine, clause_for([-12])) == {15}
    assert found(engine, clause_for([-20])) == set()                    # a category with no subcategories and no such guild
    assert found(engine, clause_for([-30])) == set() and found(engine, clause_for([-31])) == set()


def test_the_whole_category_is_its_token_and_all_of_its_subcategory_ids(engine):
    assert found(engine, clause_for([-1, 8, 10, 106])) == {1, 2, 3}
    assert found(engine, clause_for([-18, 120, 121])) == {6, 9, 10}
    assert found(engine, clause_for([-1, 8, 10, 106])) == found(engine, clause_for([8, 10, 106])) | found(engine, clause_for([-1]))


def test_the_hidden_and_unlisted_rows_of_a_category_are_in_it_only_when_listed(engine):
    assert found(engine, clause_for([-10, 44, 50])) == {7, 12, 14}      # 44 is "Offensive"
    assert found(engine, clause_for([-12, 108, 60])) == {8, 15}         # 108 is unlisted from every guild list
    assert found(engine, clause_for([-30, 90])) == {11}                 # a hidden category
    assert found(engine, clause_for([-31, 92, 93])) == {13}             # a row that is hidden by itself
    assert found(engine, clause_for([-10, 50])) == {12, 14}             # not ticked, so not there
    assert found(engine, clause_for([-12])) == {15}


def test_the_heading_ticked_with_no_subcategory_is_only_the_category_alone(engine):
    assert found(engine, clause_for([-1])) == {3}
    assert found(engine, clause_for([-18, 120])) == {6, 9, 10}          # some of its subcategories: those and the category alone


def test_subcategories_ticked_without_the_heading_leave_the_category_alone_out(engine):
    assert found(engine, clause_for([8, 10, 106])) == {1, 2}            # not 3
    assert found(engine, clause_for([120, 121])) == {9}                 # not 6 and 10


def test_a_category_that_is_not_known_matches_nothing_and_is_dropped_from_a_valid_list(engine):
    known = {0, 8, 120, -1, -18}
    assert found(engine, clause_for([-999], known=known)) == set()
    assert found(engine, clause_for([-999, 8], known=known)) == {1}
    assert found(engine, clause_for([-10], known=known)) == set()         # a real category, but not one this caller offers
    assert found(engine, clause_for([-1, -999], known=known)) == {3}
    # no valid set: nothing to compare with, so a real category is taken
    assert found(engine, clause_for([-10])) == {14}
    assert found(engine, clause_for([-999])) == set()                   # no guild is filed under it


def test_a_list_with_nothing_left_matches_nothing(engine):
    for ids in ([], (), set(), ["abc"], [None], ["", "x", "y"], [float("inf")], [float("nan")]):
        assert found(engine, clause_for(ids)) == set(), ids
    assert found(engine, clause_for([-5], known={0})) == set()
    assert found(engine, clause_for([-5], known=set())) == set()
    # the same for a list of ids none of which is known: it is not "everything" and not a stand-in id
    assert found(engine, clause_for([-5, -6], known={-1})) == set()


def test_each_id_is_made_a_number_and_one_that_cannot_be_is_ignored(engine):
    assert found(engine, clause_for(["8", " 10 ", "-1", "x", None, "-18"])) == {1, 2, 3, 6, 10}
    assert found(engine, clause_for({8, 120})) == {1, 9}
    assert found(engine, clause_for(iter([8, "x", 120]))) == {1, 9}


def test_a_topics_ids_are_its_subcategories_and_the_guilds_filed_under_its_category_alone(engine):
    assert topics.find("food").ids == (120, 121, -18)
    assert found(engine, clause_for(topics.find("food").ids)) == {6, 9, 10}
    # its rows leave out the unlisted 108 and a row hidden by itself, which the Categorical list does show
    assert topics.find("politics").ids == (60, -12) and found(engine, clause_for(topics.find("politics").ids)) == {15}
    assert topics.find("mixed").ids == (92, -31) and found(engine, clause_for(topics.find("mixed").ids)) == set()
    assert found(engine, clause_for(topics.find("arts").ids)) == {1, 2, 3}


def test_the_condition_takes_no_expand_argument():
    assert list(inspect.signature(cf.board_clause).parameters) == ["ids", "subcat_col", "cat_col", "known"]
    assert not hasattr(cf, "expand")


def test_a_mixed_list_is_every_part_of_it(engine):
    assert found(engine, clause_for([8, 0, -18])) == {1, 4, 6, 10}
    assert found(engine, clause_for([0, -1, 120])) == {3, 4, 9}
    assert found(engine, clause_for([0, 0, 8, 8, -1, -1])) == {1, 3, 4}
    everything = [0] + [x.id for c in CATEGORIES for x in c.subcats] + [-c.id for c in CATEGORIES if c.subcats]
    assert found(engine, clause_for(everything)) == ALL - {5}              # only the guild with no category column at all


def test_a_guild_with_no_subcategory_is_matched_only_by_the_rules_that_name_it(engine):
    for ids in ([0], [8], [8, 10, 120], [-1], [-10], [0, -1], [999]):
        assert 5 not in found(engine, clause_for(ids)), ids
    assert 6 in found(engine, clause_for([-18])) and 6 not in found(engine, clause_for([0, -1, 120]))
    assert 6 not in found(engine, clause_for([120, 121]))


def test_the_condition_is_never_null_so_negating_it_gives_the_other_guilds(engine):
    for ids in ([0], [8], [-1], [-18], [0, -18, 8], [-18, 120, 121], [], [-5]):
        for known in (None, {0, 8, -1}):
            clause = clause_for(ids, known=known)
            inside = found(engine, clause)
            outside = found(engine, not_(clause))
            assert inside | outside == ALL and not inside & outside, (ids, known)


def test_it_takes_the_columns_of_a_hand_written_table_as_well(engine):
    plain = table("boards", column("id"), column("subcat_id"), column("cat_id"))
    clause = cf.board_clause([0, -18, 8], plain.c.subcat_id, plain.c.cat_id)
    with engine.connect() as connection:
        assert {row[0] for row in connection.execute(select(plain.c.id).where(clause))} == {1, 4, 6, 10}


# --- a guild counted under its category ------------------------------------------------------------------------------

def test_a_guild_is_counted_under_its_category_when_filed_under_it_alone():
    assert cf.token_of(8, None) == 8
    assert cf.token_of(0, 18) == -18
    assert cf.token_of(0, None) == 0
    assert cf.token_of(None, None) is None
    assert cf.token_of(None, 18) == -18


# --- choosing a category ---------------------------------------------------------------------------------------------

def test_a_choice_is_a_subcategory_of_a_category_or_a_category_that_has_some():
    assert cf.valid_choice(8, CATEGORIES) and cf.valid_choice(44, CATEGORIES) and cf.valid_choice(108, CATEGORIES)
    assert cf.valid_choice(90, CATEGORIES) and cf.valid_choice(93, CATEGORIES)    # hidden rows were choosable before
    assert cf.valid_choice(-1, CATEGORIES) and cf.valid_choice(-30, CATEGORIES)
    for token in (0, 999, -999, -20, -2, None, True, "8", 8.0):
        assert not cf.valid_choice(token, CATEGORIES), token


def test_a_choice_names_its_category_and_subcategory():
    category, subcategory = cf.choice_of(10, CATEGORIES)
    assert (category.id, subcategory.name) == (1, "Music")
    category, subcategory = cf.choice_of(-18, CATEGORIES)
    assert (category.name, subcategory) == ("Food", None)
    assert cf.choice_of(-20, CATEGORIES) is None and cf.choice_of(8, []) is None


def test_filing_a_guild_sets_both_columns_together():
    board = NS(subcat_id=8, cat_id=None)
    assert cf.file_under(board, -18) is None
    assert (board.subcat_id, board.cat_id) == (0, 18)
    cf.file_under(board, 121)
    assert (board.subcat_id, board.cat_id) == (121, None)
    cf.file_under(board, -1)
    cf.file_under(board, 10)
    assert (board.subcat_id, board.cat_id) == (10, None)               # never both
    cf.file_under(board, 0)
    assert (board.subcat_id, board.cat_id) == (0, None)


def test_the_helper_stays_pure():
    tree = ast.parse((ROOT / "ruqqus" / "helpers" / "category_filter.py").read_text(encoding="utf-8"))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module)
    assert modules == {"re", "sqlalchemy"}, modules


# --- the notice ------------------------------------------------------------------------------------------------------

NOTICE_ROWS = [("Film & TV", 8, "Arts"), ("Music", 10, "Arts"), ("Visual Arts", 106, "Arts"), ("News", 74, "Technology"),
               ("News", 62, "Sports"), ("Baking", 120, "Food"), ("Cooking", 121, "Food")]


def english(text):
    return text


def test_a_category_with_all_its_subcategories_in_the_list_is_named_by_its_name_alone():
    assert catalog.notice_names(NOTICE_ROWS, [-18, 120, 121], english, CATEGORIES) == ["Food"]
    assert catalog.notice_names(NOTICE_ROWS, [120, -18, 121], english, CATEGORIES) == ["Food"]    # wherever they sit
    assert catalog.notice_names(NOTICE_ROWS, [121, 8, -18, 120], english, CATEGORIES) == ["Film & TV", "Food"]
    assert catalog.notice_names(NOTICE_ROWS, [-1, 8, 10, 106, -18, 120, 121], english, CATEGORIES) == ["Arts", "Food"]
    spanish = {"Food": "Comida", "Baking": "Panadería", "Cooking": "Cocina"}
    assert catalog.notice_names(NOTICE_ROWS, [-18, 120, 121], lambda text: spanish.get(text, text), CATEGORIES) == ["Comida"]


def test_a_category_with_some_or_none_of_its_subcategories_in_the_list_is_named_category_only():
    assert catalog.notice_names(NOTICE_ROWS, [-18], english, CATEGORIES) == ["Food (category only)"]
    assert catalog.notice_names(NOTICE_ROWS, [-18, 120], english, CATEGORIES) == ["Food (category only)", "Baking"]
    assert catalog.notice_names(NOTICE_ROWS, [121, -18], english, CATEGORIES) == ["Cooking", "Food (category only)"]
    assert catalog.notice_names(NOTICE_ROWS, [120, -18, 8], english, CATEGORIES) == \
        ["Baking", "Food (category only)", "Film & TV"]
    # one of Arts' three is missing, so Arts stays "category only" and Food is whole
    assert catalog.notice_names(NOTICE_ROWS, [-1, 8, 10, -18, 120, 121], english, CATEGORIES) == \
        ["Arts (category only)", "Film & TV", "Music", "Food"]


def test_subcategories_without_their_category_are_named_one_by_one():
    assert catalog.notice_names(NOTICE_ROWS, [120, 121], english, CATEGORIES) == ["Baking", "Cooking"]
    assert catalog.notice_names(NOTICE_ROWS, [8, 10, 106], english, CATEGORIES) == ["Film & TV", "Music", "Visual Arts"]


def test_category_only_goes_through_the_translating_helper_with_the_translated_name(monkeypatch):
    asked = []

    def spanish_helper(message, **params):
        asked.append((message, params))
        return "%(category)s (solo la categoría)" % params

    monkeypatch.setattr(catalog, "tr", spanish_helper)
    spanish = {"Food": "Comida", "Baking": "Panadería"}
    assert catalog.notice_names(NOTICE_ROWS, [-18, 120], lambda text: spanish.get(text, text), CATEGORIES) == \
        ["Comida (solo la categoría)", "Panadería"]
    assert asked == [("%(category)s (category only)", {"category": "Comida"})]
    asked.clear()
    assert catalog.notice_names(NOTICE_ROWS, [-18, 120, 121], english, CATEGORIES) == ["Food"] and asked == []     # not asked


def test_the_category_only_wording_is_marked_for_the_catalogues():
    source = (ROOT / "ruqqus" / "helpers" / "category_catalog.py").read_text(encoding="utf-8")
    assert 'tr("%(category)s (category only)", category=' in source and "# Translators:" in source


def test_a_category_the_notice_does_not_know_is_skipped_as_an_unknown_id_is():
    assert catalog.notice_names(NOTICE_ROWS, [-999, 8], english, CATEGORIES) == ["Film & TV"]
    assert catalog.notice_names(NOTICE_ROWS, [-18], english) == []                   # no categories given
    assert catalog.notice_names(NOTICE_ROWS, [-18, 8], english, ()) == ["Film & TV"]
    assert catalog.notice_names(NOTICE_ROWS, [999, 0, -5], english, CATEGORIES) == []
    assert catalog.notice_names(NOTICE_ROWS, [-18, 120, 121, -999], english, CATEGORIES) == ["Food"]


def test_the_notice_for_subcategories_is_what_it_was():
    assert catalog.notice_names(NOTICE_ROWS, [74, 62, 120], english) == ["News (Technology)", "News (Sports)", "Baking"]
    assert catalog.notice_names(NOTICE_ROWS, [74, 62, 120], english, CATEGORIES) == \
        ["News (Technology)", "News (Sports)", "Baking"]
    assert catalog.notice_names(NOTICE_ROWS, [], english, CATEGORIES) == []


def test_a_category_named_like_a_subcategory_keeps_its_own_name_and_the_row_its_own():
    categories = CATEGORIES + [cat(24, "Music", [sub(330, "Rock")])]
    rows = NOTICE_ROWS + [("Rock", 330, "Music")]
    assert catalog.notice_names(rows, [-24], english, categories) == ["Music (category only)"]
    assert catalog.notice_names(rows, [-24, 10], english, categories) == ["Music (category only)", "Music"]    # Arts' Music row
    assert catalog.notice_names(rows, [-24, 330], english, categories) == ["Music"]                            # the whole category
    assert catalog.notice_names(rows, [10, 330], english, categories) == ["Music", "Rock"]
