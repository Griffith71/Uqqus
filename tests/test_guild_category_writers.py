"""A guild may choose its broad category alone (`boards.cat_id`, helpers/category_filter.py): the three routes that file a
guild, the three pickers that offer the choice and the tooltip that shows it. The real route functions are compiled out
of their files and run on stand-ins (the test suite never imports the app); the templates are drawn for real."""
import ast
import html as htmllib
import re
import time
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from ruqqus.helpers import category_filter
from ruqqus.helpers.i18n_jinja import environment

ROOT = Path(__file__).resolve().parent.parent
RUQQUS = ROOT / "ruqqus"


def read(*parts):
    return RUQQUS.joinpath(*parts).read_text(encoding="utf-8")


def _function(parts, name, **names):
    """`name` from a source file under ruqqus/, compiled alone (decorators dropped) in a namespace holding `names`."""
    path = RUQQUS.joinpath(*parts)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
    node.decorator_list = []
    module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    exec(compile(module, str(path), "exec"), names)
    return names[name]


def sub(id_, name, category=None):
    return NS(id=id_, name=name, category=category)


def cat(id_, name, subs):
    category = NS(id=id_, name=name, subcats=list(subs))
    for each in subs:
        each.category = category
    return category


ARTS = cat(1, "Arts", [sub(8, "Film & TV"), sub(10, "Music")])
EMPTY = cat(20, "Empty", [])
GAMING = cat(6, "Gaming", [sub(25, "PC"), sub(44, "Offensive")])
CATEGORIES = [ARTS, EMPTY, GAMING]


class Aborted(Exception):
    pass


# --- guildmasters: the category route ------------------------------------------------------------------------------------

def _changed(category, locked=False, subcat_id=0, cat_id=None):
    """change_guild_category on stand-ins: what it answers, the notes it logged, and the board."""
    notes = []
    board = NS(id=5, is_locked_category=locked, subcat_id=subcat_id, cat_id=cat_id)
    route = _function(("routes", "boards.py"), "change_guild_category", category_filter=category_filter,
                      CATEGORIES=CATEGORIES, jsonify=lambda data: data, tr=lambda text, **values: text % values,
                      ModAction=lambda **fields: NS(**fields),
                      g=NS(db=NS(add=lambda row: notes.append(getattr(row, "note", None)), flush=lambda: None)))
    answer = route(NS(id=1), board, "abc", category)
    return answer, [n for n in notes if n], board


def test_a_whole_category_is_filed_as_subcategory_zero_and_the_category_and_named_alone():
    answer, notes, board = _changed("-1", subcat_id=8)
    assert answer == {"message": "Category changed to Arts"}                    # the toast is the category's name only
    assert notes == ["category=Arts"]
    assert (board.subcat_id, board.cat_id) == (0, 1)


def test_a_subcategory_clears_the_category_alone_and_keeps_the_two_name_text():
    answer, notes, board = _changed("8", subcat_id=0, cat_id=1)
    assert answer == {"message": "Category changed to Arts / Film & TV"}
    assert notes == ["category=Arts / Film & TV"]
    assert (board.subcat_id, board.cat_id) == (8, None)


def test_a_hidden_row_and_a_category_of_its_own_are_choices_like_the_picker_lists_them():
    assert _changed("44")[1] == ["category=Gaming / Offensive"]
    assert _changed("-6")[1] == ["category=Gaming"]


@pytest.mark.parametrize("bad", ["abc", "", "-", "-0", "--5", "+8", " 8", "0", "00", "²", "٣", "999", "-999", "-20",
                                 "1234567890", "-1234567890"])
def test_a_category_that_is_not_a_choice_is_a_400_and_changes_nothing(bad):
    # `int("abc")` was a 500, and "²".isdigit() is True; -20 is a category with no subcategory to choose
    answer, notes, board = _changed(bad, subcat_id=8)
    assert answer == ({"error": "Invalid category id"}, 400)
    assert notes == [] and (board.subcat_id, board.cat_id) == (8, None)


def test_the_answer_for_a_bad_id_comes_before_the_lock():
    assert _changed("abc", locked=True)[0] == ({"error": "Invalid category id"}, 400)
    answer, notes, board = _changed("-1", locked=True, subcat_id=8)
    assert answer == ({"error": "You can't do that right now."}, 403)
    assert notes == [] and (board.subcat_id, board.cat_id) == (8, None)


# --- admins: the category lock -------------------------------------------------------------------------------------------

def _locked(form, locked=False):
    """admin_category_lock on stand-ins: where it redirects, the notes it logged, and the board."""
    notes = []
    board = NS(id=5, is_locked_category=locked, subcat_id=8, cat_id=None, permalink="/+pets")

    def abort(status):
        raise Aborted(status)

    route = _function(("routes", "admin.py"), "admin_category_lock", category_filter=category_filter,
                      CATEGORIES=CATEGORIES, get_guild=lambda name: board, abort=abort, redirect=lambda url: url,
                      ModAction=lambda **fields: NS(**fields), request=NS(form=form),
                      g=NS(db=NS(add=lambda row: notes.append(getattr(row, "note", None)))))
    try:
        where = route(NS(id=1))
    except Aborted as stop:
        where = stop.args[0]
    return where, [n for n in notes if n], board


def test_the_admin_note_names_a_whole_category_alone_and_keeps_admin_action():
    where, notes, board = _locked({"board": "pets", "category": "-1"})
    assert where == "/+pets/mod/log"
    assert notes == ["category=Arts | admin action"]
    assert (board.subcat_id, board.cat_id) == (0, 1)
    _where, notes, board = _locked({"board": "pets", "category": "10"})
    assert notes == ["category=Arts / Music | admin action"] and (board.subcat_id, board.cat_id) == (10, None)


def test_the_admin_can_lock_and_unlock_beside_the_category():
    _where, notes, board = _locked({"board": "pets", "category": "-1", "lock": "on"})
    assert notes == ["category=Arts | admin action", "category_locked=True | admin action"] and board.is_locked_category
    _where, notes, board = _locked({"board": "pets", "category": "-1", "lock": "on"}, locked=True)
    assert notes == ["category=Arts | admin action"]                            # the lock did not change


def test_a_locked_guilds_form_has_no_category_and_unlocking_it_is_no_500():
    # the select is disabled while the guild is locked, so the browser sends no `category` (`int(None)` was a 500)
    where, notes, board = _locked({"board": "pets"}, locked=True)
    assert where == "/+pets/mod/log"
    assert notes == ["category_locked=False | admin action"] and not board.is_locked_category
    assert (board.subcat_id, board.cat_id) == (8, None)


@pytest.mark.parametrize("bad", ["abc", "", "-0", "--5", "0", "²", "999", "-999", "-20"])
def test_a_category_that_is_not_a_choice_is_a_400_for_the_admin_too(bad):
    where, notes, board = _locked({"board": "pets", "category": bad, "lock": "on"})
    assert where == 400 and notes == []
    assert (board.subcat_id, board.cat_id) == (8, None) and not board.is_locked_category


# --- members: the create form's route ------------------------------------------------------------------------------------

class Column:
    """A model column in the recent-guilds query, which the stand-in database ignores."""
    def in_(self, values):
        return True

    def __ge__(self, other):
        return True


class FakeBoard:
    """The model, as far as the route builds it: the columns it was given, the ones it leaves to file_under unset."""
    creator_id, created_utc = Column(), Column()

    def __init__(self, **fields):
        self.__dict__.update(fields)
        self.id, self.permalink = 7, "/+pets"
        self.subcat_id = self.cat_id = None


class Renderer:
    def __enter__(self):
        return NS(render=lambda document: "<p>about pets</p>")

    def __exit__(self, *exc):
        return False


def _created(category):
    """create_board_post on stand-ins: its answer, and the board it added."""
    added = []
    member = NS(id=1, alts=[], admin_level=0, can_make_guild=True, gm_limit_refusal=lambda: None)
    form = {"name": "pets", "description": "about pets"}
    if category is not None:
        form["category"] = category
    route = _function(("routes", "boards.py"), "create_board_post", category_filter=category_filter,
                      CATEGORIES=CATEGORIES, request=NS(form=form), re=re, time=time,
                      valid_board_regex=re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_]{2,24}\Z"), PROFILE_BOARD_NAME="systemprofile",
                      DESCRIPTION_MAX=1500, DESCRIPTION_HTML_MAX=5000, Board=FakeBoard, get_guild=lambda name, graceful=False: None,
                      render_template=lambda template, **context: ("rendered", template, context),
                      tr=lambda text, **values: text % values, trn=lambda one, many, num: one, guild_limits=NS(),
                      CustomRenderer=Renderer, mistletoe=NS(Document=lambda text: text),
                      sanitize=lambda markup, linkgen=False: markup, apply_board_severity=lambda board: None,
                      ModRelationship=lambda **fields: NS(**fields), Subscription=lambda **fields: NS(**fields),
                      ModAction=lambda **fields: NS(**fields), cache=NS(delete_memoized=lambda *a, **kw: None),
                      guild_ids=None, redirect=lambda url: ("redirect", url),
                      g=NS(db=NS(query=lambda model: NS(filter=lambda *conditions: NS(all=lambda: [])),
                                 add=lambda row: added.append(row), commit=lambda: None)))
    return route(member), [row for row in added if isinstance(row, FakeBoard)]


def test_a_new_guild_can_be_filed_under_a_whole_category():
    answer, boards = _created("-1")
    assert answer == ("redirect", "/+pets")
    board, = boards
    assert (board.subcat_id, board.cat_id) == (0, 1)


def test_a_new_guild_under_a_subcategory_has_no_category_of_its_own():
    answer, boards = _created(" 10\n")                                           # the route trims what was typed
    assert answer == ("redirect", "/+pets")
    board, = boards
    assert (board.subcat_id, board.cat_id) == (10, None)


@pytest.mark.parametrize("bad", [None, "", "abc", "0", "-0", "--5", "²", "999", "-999", "-20", "1e3"])
def test_a_missing_or_unknown_category_returns_the_form_with_a_400_and_adds_nothing(bad):
    answer, boards = _created(bad)
    (kind, template, context), status = answer
    assert (kind, template, status) == ("rendered", "make_board.html", 400)
    assert context["error"] == "Choose a category for the guild." and context["description"] == "about pets"
    assert boards == []


def test_the_create_routes_docstring_documents_the_category():
    doc = ast.get_docstring(next(n for n in ast.walk(ast.parse(read("routes", "boards.py")))
                                 if isinstance(n, ast.FunctionDef) and n.name == "create_board_post"))
    assert "* `category` - " in doc and "`-<id>`" in doc


# --- the pickers -------------------------------------------------------------------------------------------------------

def picker(template):
    """The category <select> of a template, drawn on its own."""
    select = re.search(r'<select id="category-selector".*?</select>', read("templates", *template), re.S).group(0)
    env = environment(autoescape=True)
    env.install_null_translations(newstyle=True)
    env.globals["msg_text"] = lambda text: text
    return lambda **context: env.from_string(select).render(**context)


def options(markup):
    """[(value, text, selected)] of a rendered <select>."""
    return [(re.search(r'value="([^"]*)"', attrs).group(1) if "value=" in attrs else None,
             htmllib.unescape(text).strip(), " selected" in attrs)
            for attrs, text in re.findall(r"<option([^>]*)>([^<]*)</option>", markup)]


def test_the_create_form_offers_each_category_as_a_choice_over_its_subcategories():
    got = options(picker(("make_board.html",))(categories=CATEGORIES))
    assert got == [("", "Select a Category", True),
                   ("-1", "Arts (whole category)", False), ("8", "- Film & TV", False), ("10", "- Music", False),
                   (None, "Empty", False),                                       # nothing to choose under it: still a heading
                   ("-6", "Gaming (whole category)", False), ("25", "- PC", False), ("44", "- Offensive", False)]


def test_the_create_form_still_lists_the_categories_after_an_error_re_render():
    # create_board_post renders the form again with no `categories` on every error; the context processor in
    # routes/front.py hands every template CATEGORIES
    draw = picker(("make_board.html",))
    assert '"CATEGORIES": CATEGORIES,' in read("routes", "front.py")
    again = options(draw(CATEGORIES=[ARTS, GAMING]))
    assert [text for _value, text, _selected in again] == ["Select a Category", "Arts (whole category)", "- Film & TV",
                                                           "- Music", "Gaming (whole category)", "- PC", "- Offensive"]
    # a route that passes its own list is still believed
    assert [t for _v, t, _s in options(draw(categories=[GAMING], CATEGORIES=[ARTS]))] == [
        "Select a Category", "Gaming (whole category)", "- PC", "- Offensive"]


def guild(subcat_id=0, cat_id=None, locked=False):
    # what Board.category answers: the category held, else the one holding the subcategory, else None
    category = next((c for c in CATEGORIES if c.id == cat_id or any(s.id == subcat_id for s in c.subcats)), None)
    return NS(base36id="abc", subcat_id=subcat_id, cat_id=cat_id, category=category, is_locked_category=locked)


def settings_options(**fields):
    markup = picker(("guild", "settings.html"))(categories=CATEGORIES, b=guild(**fields))
    return markup, options(markup)


def test_the_settings_picker_selects_the_whole_category_of_a_guild_filed_under_one_alone():
    _markup, got = settings_options(cat_id=1)
    assert [(v, t) for v, t, _s in got] == [("-1", "Arts (whole category)"), ("8", "- Film & TV"), ("10", "- Music"),
                                            (None, "Empty"), ("-6", "Gaming (whole category)"), ("25", "- PC"),
                                            ("44", "- Offensive")]                    # no placeholder: it has a category
    assert [v for v, _t, selected in got if selected] == ["-1"]


def test_the_settings_picker_selects_the_subcategory_and_no_heading_for_an_ordinary_guild():
    _markup, got = settings_options(subcat_id=10)
    assert [v for v, _t, selected in got if selected] == ["10"]
    assert got[0][0] == "-1"                                                          # no placeholder here either


def test_the_settings_picker_asks_for_a_category_first_when_the_guild_has_none():
    markup, got = settings_options()
    assert got[0] == ("0", "Select a Category", True) and [v for v, _t, selected in got if selected] == ["0"]
    assert "disabled" not in re.match(r"<select[^>]*>", markup).group(0)
    assert "disabled" in re.match(r"<select[^>]*>", settings_options(cat_id=1, locked=True)[0]).group(0)


def test_the_admin_picker_offers_the_same_choices_and_selects_the_guilds_own():
    template = read("templates", "admin", "category.html")
    select = re.search(r'<select id="category-selector".*?</select>', template, re.S).group(0)
    env = environment(autoescape=True)
    draw = lambda b: options(env.from_string(select).render(categories=CATEGORIES, b=b))
    got = draw(guild(cat_id=1))
    assert [(v, t) for v, t, _s in got] == [("0", "Select a Category"), ("-1", "Arts (whole category)"), ("8", "- Film & TV"),
                                            ("10", "- Music"), (None, "Empty"), ("-6", "Gaming (whole category)"),
                                            ("25", "- PC"), ("44", "- Offensive")]
    assert [v for v, _t, selected in got if selected] == ["-1"]
    assert [v for v, _t, selected in draw(guild(subcat_id=25)) if selected] == ["25"]
    # no guild chosen yet, or one with no category: the placeholder, so a lock-only submit does not post the first
    # enabled option (a disabled one is never submitted)
    assert [v for v, _t, selected in draw(None) if selected] == ["0"]
    assert [v for v, _t, selected in draw(guild()) if selected] == ["0"]


def test_every_picker_value_is_one_the_writers_accept():
    for template in (("make_board.html",), ("guild", "settings.html")):
        markup = picker(template)(categories=CATEGORIES, b=guild(), CATEGORIES=CATEGORIES)
        values = [v for v, _t, _s in options(markup) if v not in (None, "", "0")]
        assert values and all(category_filter.valid_choice(category_filter.parse_token(v), CATEGORIES) for v in values), template


# --- the tooltip ---------------------------------------------------------------------------------------------------------

def tooltip(b):
    """The category icon of partials/guild_sidebar.html, drawn on its own."""
    env = environment(autoescape=True)
    env.install_null_translations(newstyle=True)
    icon = re.search(r'<i class="fas fa-filter"[^>]*></i>', read("templates", "partials", "guild_sidebar.html")).group(0)
    return htmllib.unescape(re.search(r'title="([^"]*)"', env.from_string(icon).render(b=b)).group(1))


def test_the_tooltip_names_the_category_alone_for_a_guild_filed_under_one():
    assert tooltip(NS(cat_id=1, category=ARTS, subcat=None)) == "Category: Arts"
    assert tooltip(NS(cat_id=None, category=ARTS, subcat=ARTS.subcats[0])) == "Category: Arts / Film & TV"
    assert tooltip(NS(cat_id=None, category=None, subcat=None)) == "Category: Uncategorized"


def test_nothing_reads_the_general_row_helper_any_more():
    for path in (("routes", "boards.py"), ("routes", "admin.py"), ("templates", "partials", "guild_sidebar.html"),
                 ("helpers", "category_catalog.py")):
        text = read(*path)
        assert "category_shown" not in text and "is_general" not in text, path
    assert "from ruqqus.helpers import category_filter\n" in read("routes", "boards.py")
    assert "from ruqqus.helpers import category_filter\n" in read("routes", "admin.py")
