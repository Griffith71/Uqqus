"""The Categorical filter's list (ruqqus/helpers/category_catalog.py): categories as headings over their subcategories
(each heading a row of its own, the whole category as `-N`), sorted by the reader's language, and the names the "Showing
posts in" notice prints. Pure: the categories are small
stand-ins shaped like classes/categories.py, and the seed file gives the real ones."""
import re
from pathlib import Path
from types import SimpleNamespace as NS

from ruqqus.helpers import category_catalog as catalog

ROOT = Path(__file__).resolve().parent.parent


def sub(id_, name):
    return NS(id=id_, name=name)


def cat(id_, name, subs):
    return NS(id=id_, name=name, subcats=subs)


CATEGORIES = [
    cat(14, "Sports", [sub(60, "soccer"), sub(61, "Golf"), sub(62, "News")]),
    cat(15, "Technology", [sub(74, "News"), sub(75, "Apps")]),
    cat(20, "Empty", []),
    cat(1, "arts", [sub(8, "Film & TV"), sub(10, "Music")]),
]


def english(text):
    return text


def seed_categories():
    """The site's own categories, read from seed-db.sql the way the database holds them."""
    seed = (ROOT / "seed-db.sql").read_text(encoding="utf-8")
    names = {int(i): n.replace("''", "'") for i, n in
             re.findall(r"INSERT INTO public\.categories VALUES \((\d+), '[0-9a-f]+', (?:true|false), '((?:[^']|'')*)'", seed)}
    subs = {}
    for id_, cat_id, name in re.findall(r"INSERT INTO public\.subcategories VALUES \((\d+), (\d+), '((?:[^']|'')*)'", seed):
        subs.setdefault(int(cat_id), []).append(sub(int(id_), name.replace("''", "'")))
    return [cat(id_, name, subs.get(id_, [])) for id_, name in names.items()]


def test_groups_and_their_rows_are_sorted_by_name_ignoring_case():
    groups = catalog.build(CATEGORIES, english)
    assert [g["label"] for g in groups] == ["", "arts", "Sports", "Technology"]
    assert [o["label"] for o in groups[2]["options"]] == ["Golf", "News", "soccer"]       # "soccer" is not last for being lower case
    assert [o["label"] for o in groups[1]["options"]] == ["Film & TV", "Music"]


def test_the_order_follows_the_readers_language_not_the_english_one():
    german = {"Sports": "Zucker", "arts": "Kunst", "Technology": "Technik", "Golf": "Zebra", "soccer": "Fussball"}
    groups = catalog.build(CATEGORIES, lambda text: german.get(text, text))
    assert [g["label"] for g in groups] == ["", "Kunst", "Technik", "Zucker"]
    assert [o["label"] for o in groups[3]["options"]] == ["Fussball", "News", "Zebra"]


def test_accents_do_not_move_a_name_out_of_its_place():
    french = {"Sports": "\u00c9ducation", "Technology": "Divertissement", "arts": "Danse", "Golf": "\u00c9checs",
              "soccer": "Ecole", "News": "Zoo"}
    groups = catalog.build(CATEGORIES, lambda text: french.get(text, text))
    assert [g["label"] for g in groups] == ["", "Danse", "Divertissement", "\u00c9ducation"]
    assert [o["label"] for o in groups[3]["options"]] == ["\u00c9checs", "Ecole", "Zoo"]       # not "Zoo" first, as code points go


def test_a_name_under_two_parents_is_a_row_in_each_group():
    groups = catalog.build(CATEGORIES, english)
    holding = [g["label"] for g in groups if any(o["label"] == "News" for o in g["options"])]
    assert holding == ["Sports", "Technology"]
    ids = sorted(o["value"] for g in groups for o in g["options"] if o["label"] == "News")
    assert ids == [62, 74]


def test_uncategorized_is_a_lone_row_before_the_first_heading():
    groups = catalog.build(CATEGORIES, english)
    assert groups[0] == {"label": "", "options": [{"value": 0, "label": "Uncategorized", "search": "Uncategorized Uncategorized"}]}
    assert all(g["label"] for g in groups[1:])
    translated = catalog.build(CATEGORIES, lambda text: {"Uncategorized": "Sin categoria"}.get(text, text))
    assert translated[0]["options"][0]["label"] == "Sin categoria"
    assert translated[0]["options"][0]["search"] == "Sin categoria Uncategorized"


def test_a_category_with_no_subcategories_has_no_heading():
    groups = catalog.build(CATEGORIES, english)
    assert "Empty" not in [g["label"] for g in groups]
    assert -20 not in [g["heading"]["value"] for g in groups[1:]]


def test_each_group_has_a_heading_that_is_the_whole_category_and_no_row():
    groups = catalog.build(CATEGORIES, english)
    assert [g["heading"] for g in groups[1:]] == [
        {"value": -1, "label": "arts", "search": "arts arts"},
        {"value": -14, "label": "Sports", "search": "Sports Sports"},
        {"value": -15, "label": "Technology", "search": "Technology Technology"}]
    for group in groups[1:]:
        assert set(group) == {"label", "heading", "options"}
        assert group["heading"]["value"] not in [o["value"] for o in group["options"]]       # the options are the subcategories
        assert all(o["value"] > 0 for o in group["options"])
    assert sorted(o["value"] for g in groups for o in g["options"]) == [0, 8, 10, 60, 61, 62, 74, 75]


def test_the_heading_is_named_in_the_readers_language_and_found_by_both_names():
    spanish = {"Technology": "Tecnologia", "Sports": "Deportes"}
    groups = catalog.build(CATEGORIES, lambda text: spanish.get(text, text))
    assert [g["heading"]["label"] for g in groups[1:]] == ["arts", "Deportes", "Tecnologia"]
    technology = next(g for g in groups if g["label"] == "Tecnologia")
    assert technology["heading"] == {"value": -15, "label": "Tecnologia", "search": "Tecnologia Technology"}


def test_a_name_that_starts_with_general_is_an_ordinary_row():
    # a category's "General" subcategory is no longer first (the guild's choice of the category alone is its heading)
    categories = [cat(14, "Sports", [sub(60, "Golf"), sub(319, "General Sports"), sub(62, "Combat")])]
    group = catalog.build(categories, english)[1]
    assert [o["label"] for o in group["options"]] == ["Combat", "General Sports", "Golf"]
    zzz = catalog.build(categories, lambda text: {"General Sports": "Zzz"}.get(text, text))[1]
    assert [o["value"] for o in zzz["options"]] == [62, 60, 319]


def test_a_row_is_found_by_its_name_and_its_parents_in_both_languages():
    spanish = {"Technology": "Tecnologia", "Apps": "Aplicaciones"}
    groups = catalog.build(CATEGORIES, lambda text: spanish.get(text, text))
    apps = next(o for g in groups for o in g["options"] if o["value"] == 75)
    assert apps["search"] == "Aplicaciones Apps Tecnologia Technology"
    film = next(o for g in groups for o in g["options"] if o["value"] == 8)
    assert film["search"] == "Film & TV Film & TV arts arts"


def test_rows_that_read_alike_in_a_group_carry_their_english_name():
    # a machine-translated catalogue maps different names to one string
    tangled = {"Golf": "Sport", "soccer": "Sport", "News": "News"}
    group = catalog.build(CATEGORIES, lambda text: tangled.get(text, text))[2]
    assert [o["label"] for o in group["options"]] == ["News", "Sport [Golf]", "Sport [soccer]"]
    assert [o["value"] for o in group["options"]] == [62, 61, 60]
    assert group["options"][1]["search"].startswith("Sport Golf ")                          # the search text stays plain


def test_the_row_that_already_is_the_english_name_stays_bare():
    tangled = {"Golf": "News"}
    group = catalog.build(CATEGORIES, lambda text: tangled.get(text, text))[2]
    assert [o["label"] for o in group["options"]] == ["News [Golf]", "News", "soccer"]


def test_the_same_label_in_two_groups_is_left_alone():
    same = catalog.build(CATEGORIES, english)
    assert [o["label"] for g in same for o in g["options"]].count("News") == 2


def test_every_subcategory_is_in_exactly_one_row():
    groups = catalog.build(CATEGORIES, english)
    ids = [o["value"] for g in groups for o in g["options"]]
    assert sorted(ids) == sorted([0] + [s.id for c in CATEGORIES for s in c.subcats])
    assert len(ids) == len(set(ids))


def test_the_sites_own_categories_group_without_losing_or_repeating_a_row():
    categories = seed_categories()
    groups = catalog.build(categories, english)
    ids = [o["value"] for g in groups for o in g["options"]]
    assert sorted(ids) == sorted([0] + [s.id for c in categories for s in c.subcats])
    assert len(ids) == len(set(ids))
    assert groups[0] == {"label": "", "options": groups[0]["options"]} and [o["value"] for o in groups[0]["options"]] == [0]
    for group in groups[1:]:
        labels = [o["label"] for o in group["options"]]
        assert len(labels) == len(set(labels)), group["label"]                               # nothing repeats inside a group
        assert labels == sorted(labels, key=str.casefold), group["label"]
    assert [g["label"] for g in groups[1:]] == sorted((g["label"] for g in groups[1:]), key=str.casefold)
    # one heading per category with rows, for the whole category
    with_rows = {c.id: c.name for c in categories if c.subcats}
    assert {g["heading"]["value"]: g["heading"]["label"] for g in groups[1:]} == {-id_: name for id_, name in with_rows.items()}
    # the repeat the grouping is for: News is a subcategory of more than one category
    assert sum(any(o["label"] == "News" for o in g["options"]) for g in groups) >= 2


def test_the_groups_are_built_once_per_language_and_again_when_the_catalogue_is():
    catalog._built.clear()
    calls = []

    def translate(text):
        calls.append(text)
        return text

    first_catalogue, second_catalogue = object(), object()
    found = catalog.cached("en", first_catalogue, CATEGORIES, translate)
    built = len(calls)
    assert built and catalog.cached("en", first_catalogue, CATEGORIES, translate) is found and len(calls) == built
    other = catalog.cached("es", first_catalogue, CATEGORIES, translate)
    assert other is not found and catalog.cached("en", first_catalogue, CATEGORIES, translate) is found
    again = catalog.cached("en", second_catalogue, CATEGORIES, translate)       # the language's catalogue was read again
    assert again is not found and catalog.cached("en", second_catalogue, CATEGORIES, translate) is again
    catalog._built.clear()


ROWS = [("News", 74, "Technology"), ("News", 62, "Sports"), ("Golf", 61, "Sports"), ("Uncategorized", 0, "")]


def test_a_name_under_two_parents_carries_its_parent_in_the_notice():
    assert catalog.notice_names(ROWS, [74, 62, 61], english) == ["News (Technology)", "News (Sports)", "Golf"]


def test_a_single_choice_of_a_shared_name_still_says_which():
    assert catalog.notice_names(ROWS, [62], english) == ["News (Sports)"]


def test_the_notice_names_are_in_the_readers_language_and_follow_the_order_given():
    spanish = {"News": "Noticias", "Technology": "Tecnologia", "Golf": "Golf"}
    assert catalog.notice_names(ROWS, [61, 74], lambda text: spanish.get(text, text)) == ["Golf", "Noticias (Tecnologia)"]


def test_the_notice_skips_an_id_it_does_not_know_and_names_uncategorized_plainly():
    assert catalog.notice_names(ROWS, [999, 0, 61], english) == ["Uncategorized", "Golf"]
