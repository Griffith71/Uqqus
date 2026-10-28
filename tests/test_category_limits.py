"""The limits a long category list runs into: a member's saved default (ruqqus/helpers/feed_filters.py) and a curation's
category filter (routes/curations.py `curation_set_category_filter`, read back by classes/curations.py).

The route and the property are run from their own source, as test_regions.py reads regions.py: the modules import the
whole app (db models, gevent), which the suite never does."""
import ast
import itertools
import json
import random
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from ruqqus.helpers import category_filter
from ruqqus.helpers import feed_filters as ff

ROOT = Path(__file__).resolve().parent.parent / "ruqqus"

# curations.category_filter is varchar(2000)
COLUMN_WIDTH = 2000
# the list ends at subcategory 305 (the expansion's), and 0 is "Uncategorized"
EVERY_ID = range(0, 306)
# -N is the guilds filed under category N alone (routes/front.py CATEGORY_TOKENS): one for each of the 27 categories
EVERY_CATEGORY = [-n for n in range(27, 0, -1)]
EVERY_CHOICE = [*EVERY_CATEGORY, *EVERY_ID]


# --- a member's saved default ----------------------------------------------------------------------------------------

def test_a_saved_default_keeps_all_but_a_few_of_the_categories():
    chosen = [i for i in EVERY_ID if i not in (3, 44, 108)]
    assert len(chosen) == 303
    text = ff.defaults_payload([], [], chosen, [], {})
    assert ff.read_defaults(text, known_categories=set(EVERY_ID))["categories"] == chosen


def test_a_saved_default_keeps_a_category_token_and_drops_one_that_is_gone():
    text = ff.defaults_payload([], [], [-1, 7, -27, 0], [], {})
    assert json.loads(text)["categories"] == [-1, 7, -27, 0]
    assert ff.read_defaults(text, known_categories=set(EVERY_CHOICE))["categories"] == [-1, 7, -27, 0]
    # a default saved before a category went (or edited by hand) loses that id only
    assert ff.read_defaults(text, known_categories=set(EVERY_CHOICE) - {-27})["categories"] == [-1, 7, 0]
    assert ff.read_defaults(text, known_categories=set(EVERY_ID))["categories"] == [7, 0]       # no category tokens known


def test_a_280_category_default_survives_the_round_trip():
    chosen = list(EVERY_ID)[:280]
    text = ff.defaults_payload([], [], chosen, [], {})
    assert json.loads(text)["categories"] == chosen
    assert ff.read_defaults(text, known_categories=set(EVERY_ID))["categories"] == chosen


def test_regions_and_languages_have_the_same_room():
    regions = [f"region_{n}" for n in range(280)]
    languages = [f"l{n}" for n in range(280)]
    text = ff.defaults_payload(regions, languages, [], [], {})
    saved = ff.read_defaults(text, known_regions=set(regions), known_languages=set(languages))
    assert saved["regions"] == regions and saved["languages"] == languages


def test_more_than_exist_is_still_cut_when_written_and_when_read():
    many = range(ff.MAX_DEFAULT_ITEMS * 2)
    text = ff.defaults_payload([f"r{n}" for n in many], [f"l{n}" for n in many], many, [], {})
    stored = json.loads(text)
    assert [len(stored[k]) for k in ("regions", "languages", "categories")] == [ff.MAX_DEFAULT_ITEMS] * 3
    # a longer list written by hand (or by an older build) is cut when it is read
    stored["categories"] = list(many)
    saved = ff.read_defaults(json.dumps(stored), known_categories=set(many))
    assert len(saved["categories"]) == ff.MAX_DEFAULT_ITEMS


def test_the_limit_leaves_room_for_every_category():
    assert ff.MAX_DEFAULT_ITEMS >= len(EVERY_CHOICE)


# --- a curation's category filter ------------------------------------------------------------------------------------

def _function(parts, name, **names):
    """`name` from a source file, compiled alone (decorators dropped) in a namespace holding `names`."""
    path = ROOT.joinpath(*parts)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
    node.decorator_list = []
    module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    exec(compile(module, str(path), "exec"), names)
    return names[name]


class Aborted(Exception):
    pass


def _abort(status):
    raise Aborted(status)


def _post(cats, known=EVERY_CHOICE):
    """Post `cats` to the route; returns the curation it changed, how often it was saved and what it redirected to."""
    curation = NS(id=7, permalink="/&mix", category_filter="9")
    saved, invalidated = [], []
    route = _function(("routes", "curations.py"), "curation_set_category_filter",
                      request=NS(form={"cats": cats}), abort=_abort, redirect=lambda url: url,
                      g=NS(db=NS(add=saved.append, commit=lambda: saved.append("commit"))),
                      category_filter=category_filter, ALL_CATEGORY_CHOICES=frozenset(known),
                      _owned_curation_or_404=lambda slug, v: curation, _invalidate_curation_feed=invalidated.append)
    return curation, route("mix", NS(id=1)), saved, invalidated


def _stored(cats):
    return _post(cats)[0].category_filter


def test_posted_ids_are_stored_sorted_as_plain_numbers_once_each():
    assert _stored("9,3,100,3") == "3,9,100"                    # in number order, not text order ("100" before "3")
    assert _stored("0007,7,07,0") == "0,7"
    assert _stored("12") == "12"
    assert _stored("") == "" and _stored(",,") == ""             # nothing ticked clears the filter


def test_a_saved_filter_reads_back_as_the_ids_that_were_posted():
    read = _function(("classes", "curations.py"), "category_filter_list")
    curation, *_ = _post("200,0,5,5,0005")
    assert read(curation) == [0, 5, 200]
    curation.category_filter = ""
    assert read(curation) == []


def test_every_category_fits_the_column_however_the_form_was_sent():
    ids = [str(i) for i in EVERY_CHOICE] + [f"{i:05d}" for i in EVERY_CHOICE]   # each one twice, once padded with zeros
    random.Random(1).shuffle(ids)
    stored = _stored(",".join(ids))
    assert stored == ",".join(str(i) for i in EVERY_CHOICE)
    assert len(stored) < COLUMN_WIDTH


def test_a_padded_or_repeated_list_cannot_outgrow_the_column():
    # joined as posted this is over 11,000 characters: a DataError and a generic 500
    padded = ",".join(f"{i:07d}" for i in itertools.islice(itertools.cycle(EVERY_CHOICE), 1400))
    assert len(padded) > COLUMN_WIDTH
    assert len(_stored(padded)) < COLUMN_WIDTH


def test_a_category_token_is_stored_as_a_negative_number_before_the_subcategories():
    assert _stored("3,-1,-27,0,-1") == "-27,-1,0,3"
    assert _stored("-0001") == "-1"
    assert _stored("-27") == "-27"


def test_an_unknown_or_malformed_id_is_a_404_and_saves_nothing():
    # a superscript two and a digit string past Python's int limit passed isdigit() and were a 500 at int()
    for cats in ("5,abc", "5, 6", "5,1.5", "5,999", "5,306", "5,\u00b2", "5," + "9" * 5000,
                 "-0", "--5", "-", "5,-999", "5,-28", "+5"):
        with pytest.raises(Aborted) as refused:
            _post(cats)
        assert refused.value.args == (404,), cats
    curation, _, saved, invalidated = _post("5")
    assert saved == [curation, "commit"] and invalidated == [7]


def test_saving_goes_back_to_the_edit_page_and_drops_the_curations_cached_feed():
    curation, answer, saved, invalidated = _post("3,1")
    assert answer == "/&mix/edit"
    assert curation.category_filter == "1,3"
    assert saved == [curation, "commit"] and invalidated == [7]


# --- the Categorical list in the session (routes/front.py `categorical`) ---------------------------------------------

def _categorical(text, known=EVERY_CHOICE):
    return _function(("routes", "front.py"), "_category_choices", category_filter=category_filter,
                     ALL_CATEGORY_CHOICES=frozenset(known))(text)


def test_the_session_keeps_each_real_choice_once_in_the_order_given():
    assert _categorical("9,3,-1,3,0,-1") == [9, 3, -1, 0]
    assert _categorical("0007,7,07") == [7]
    assert _categorical("") == [] and _categorical(",,") == []


def test_what_is_not_a_choice_is_left_out_of_the_session_not_stored():
    # an unknown subcategory or category, a minus on its own or doubled, "-0", other scripts' digits, a decimal, a name
    for junk in ("306", "-28", "-0", "--5", "-", "+5", "1.5", "²", "abc", " 6", "9" * 5000):
        assert _categorical(f"5,{junk},-2") == [5, -2], junk


def test_the_session_never_holds_more_than_the_choices_that_exist():
    # the session is a cookie that also carries the sign-in
    flood = ",".join(str(i) for i in itertools.islice(itertools.cycle(EVERY_CHOICE), 20000))
    assert len(flood) > 4096
    kept = _categorical(flood)
    assert sorted(kept) == EVERY_CHOICE and len(kept) == len(EVERY_CHOICE)
    assert len(json.dumps(kept)) < 2000                  # under half the 4 KB a cookie may hold
