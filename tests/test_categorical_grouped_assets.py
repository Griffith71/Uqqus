"""The Categorical list under its category headings: the macro rendered for real, the three places that draw it, the
script that searches and ticks it (run in node on a small stand-in DOM) and both stylesheets. A heading is a row of its
own (`-N`, the guilds filed under the category alone) that ticks every row of its group with it; a row never changes its
heading, and a small label on a ticked heading says which of everything, the category alone or some of it is meant. The
groups themselves are built and tested in test_category_catalog.py."""
import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from jinja2 import FileSystemLoader

from ruqqus.helpers import category_catalog
from ruqqus.helpers.i18n_jinja import environment

ROOT = Path(__file__).resolve().parent.parent / "ruqqus"
TEMPLATES = ROOT / "templates"


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


MACROS = read("templates", "_sidebar_filter_macros.html")
JS = read("assets", "js", "all_js.js")
FRONT = read("routes", "front.py")


def option(value, label):
    return {"value": value, "label": label, "search": f"{label} {label}"}


def heading(value, label):
    return {"value": value, "label": label, "search": f"{label} {label}"}


GROUPS = [
    {"label": "", "options": [option(0, "Uncategorized")]},
    {"label": 'R&B "Soul"', "heading": heading(-2, 'R&B "Soul"'), "options": [option(2, "Blues <b>"), option(3, "Funk")]},
    {"label": "Technology", "heading": heading(-15, "Technology"), "options": [option(74, "News"), option(75, "Apps")]},
]


def template_env():
    env = environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=True)
    env.install_null_translations(newstyle=True)          # the macros' text is marked (docs/i18n.md)
    env.globals["msg_text"] = lambda text: text           # the site's Jinja global (helpers/i18n_runtime.py)
    return env


def macros():
    return template_env().get_template("_sidebar_filter_macros.html").make_module()


class Tree(HTMLParser):
    """The rendered markup as nested {"tag", "attrs", "kids", "text"} nodes."""
    VOID = {"input", "br", "img", "hr", "meta", "link"}

    def __init__(self, html):
        super().__init__()
        self.root = {"tag": "root", "attrs": {}, "kids": [], "text": ""}
        self.stack = [self.root]
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = {"tag": tag, "attrs": dict(attrs), "kids": [], "text": ""}
        self.stack[-1]["kids"].append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        if len(self.stack) > 1 and self.stack[-1]["tag"] == tag:
            self.stack.pop()

    def handle_data(self, data):
        self.stack[-1]["text"] += data


def walk(node):
    yield node
    for kid in node["kids"]:
        yield from walk(kid)


def find(node, tag, **attrs):
    return [n for n in walk(node) if n["tag"] == tag and all(k in n["attrs"] and (v is True or n["attrs"][k] == v)
                                                              for k, v in attrs.items())]


def classes(node):
    return node["attrs"].get("class", "").split()


def grouped(list_id="sidebar-categorical-options", active=(0, 74), **kw):
    html = str(macros().filter_panel_body(list_id, None, set(active), "Search categories", "/categorical", "cats",
                                          groups=GROUPS, **kw))
    return html, Tree(html).root


# --- the macro, rendered ------------------------------------------------------------------------------------------------

def test_the_grouped_list_has_a_wrapper_per_group_and_a_heading_row_for_each_but_the_lone_row():
    _html, root = grouped()
    lists = find(root, "div", id="sidebar-categorical-options")
    assert len(lists) == 1 and "data-keep-order" in lists[0]["attrs"]
    assert "max-height: 320px" in lists[0]["attrs"]["style"] and "overflow-y: auto" in lists[0]["attrs"]["style"]
    wrappers = [kid for kid in lists[0]["kids"] if "data-filter-group" in kid["attrs"]]
    assert len(wrappers) == 3 and len(find(root, "div", **{"data-filter-group": True})) == 3
    lone, *labelled = wrappers
    assert [kid["tag"] for kid in lone["kids"]] == ["ul"] and not find(lone, "li", **{"data-filter-heading": True})
    assert "aria-labelledby" not in lone["kids"][0]["attrs"]
    assert [row["attrs"]["data-value"] for row in find(lone, "li")] == ["0"]
    assert not find(root, "button", **{"class": "filter-group-heading"})                    # it is no button any more
    for wrapper, label, value in zip(labelled, ('R&B "Soul"', "Technology"), ("-2", "-15")):
        ul, = wrapper["kids"]                                                               # the heading is inside the list
        assert ul["tag"] == "ul"
        first = ul["kids"][0]
        assert first["tag"] == "li" and "data-filter-row" in first["attrs"] and "data-filter-heading" in first["attrs"]
        assert first["attrs"]["data-value"] == value and classes(first) == ["filter-group-heading"]
        assert first["attrs"]["onclick"] == "toggleSidebarFilterOption(this, 'sidebar-categorical-options')"
        link, = first["kids"]
        assert link["tag"] == "a" and link["attrs"]["role"] == "checkbox" and link["attrs"]["aria-checked"] == "false"
        icon, span, state = link["kids"]
        assert "invisible" in classes(icon) and icon["attrs"]["aria-hidden"] == "true"
        assert span["text"] == label
        assert span["attrs"]["id"].startswith("sidebar-categorical-options-")
        assert ul["attrs"]["aria-labelledby"] == span["attrs"]["id"]                        # the list is named by its category
        assert link["attrs"]["aria-labelledby"] == span["attrs"]["id"]                      # and so is its tick box
        assert state["tag"] == "span" and "data-filter-state" in state["attrs"]             # unticked, the label is empty and gone
        assert state["attrs"]["id"] == span["attrs"]["id"] + "-state" and link["attrs"]["aria-describedby"] == state["attrs"]["id"]
        assert state["text"] == "" and classes(state) == ["filter-group-state", "d-none"]
        assert len([kid for kid in ul["kids"] if "data-filter-heading" in kid["attrs"]]) == 1
        assert not any("data-filter-heading" in kid["attrs"] for kid in ul["kids"][1:])     # only the first row is one


TITLE = "Ticks all its subcategories. Untick them to keep only the guilds filed under the category itself."
HINT = ("Tick a category to tick all its subcategories. Untick them to keep only guilds filed under the category "
        "itself.")
ALL, ALONE, SOME = "everything in it", "category only", "category and some subcategories"


def test_the_heading_is_named_by_its_category_and_explained_by_its_title_with_the_name_escaped():
    html, root = grouped()
    link = find(root, "a", role="checkbox")[0]
    assert "aria-label" not in link["attrs"] and link["attrs"]["title"] == TITLE             # the name is the category's own
    assert all(a["attrs"]["title"] == TITLE for a in find(root, "a", role="checkbox"))
    assert 'id="sidebar-categorical-options-2">R&amp;B &#34;Soul&#34;</span>' in html
    assert "Blues &lt;b&gt;" in html and "<b>" not in html
    assert "(whole category)" not in html                                                    # that is the pickers' option
    assert "Select or deselect all" not in html and "toggleSidebarFilterGroup" not in html


def test_every_row_is_drawn_once_in_its_own_group_with_the_ticked_ones_marked():
    _html, root = grouped(active=(0, 74, -15))
    rows = find(root, "li", **{"data-filter-row": True})
    assert [row["attrs"]["data-value"] for row in rows] == ["0", "-2", "2", "3", "-15", "74", "75"]      # the groups' own order
    ticked = [row["attrs"]["data-value"] for row in rows if "is-selected" in classes(row)]
    assert ticked == ["0", "-15", "74"]
    for row in rows:
        on = "is-selected" in classes(row)
        link, = row["kids"]
        icon = link["kids"][0]
        assert ("invisible" in classes(icon)) == (not on)
        if "data-filter-heading" in row["attrs"]:
            # a heading is coloured by .is-selected in the stylesheets, not by the row's purple and bold
            assert link["attrs"]["aria-checked"] == ("true" if on else "false")
            assert not {"text-purple", "font-weight-bold", "text-black"} & set(classes(link))
            assert classes(row) == ["filter-group-heading"] + (["is-selected"] if on else [])
        else:
            assert ("text-purple" in classes(link)) == on
        assert row["attrs"]["onclick"] == "toggleSidebarFilterOption(this, 'sidebar-categorical-options')"
    for wrapper in find(root, "div", **{"data-filter-group": True}):
        assert len(find(wrapper, "ul")) == 1


def test_the_catalogs_own_groups_draw_one_heading_per_category_over_its_rows():
    sub = lambda id_, name: NS(id=id_, name=name)
    categories = [NS(id=14, name="Sports", subcats=[sub(60, "Golf"), sub(61, "News")]),
                  NS(id=20, name="Empty", subcats=[]),
                  NS(id=1, name="Arts", subcats=[sub(8, "Film & TV")])]
    groups = category_catalog.build(categories, lambda text: text)
    html = str(macros().filter_panel_body("sidebar-categorical-options", None, {-1, 61}, "Search", "/categorical", "cats",
                                          groups=groups))
    root = Tree(html).root
    headings = find(root, "li", **{"data-filter-heading": True})
    assert [h["attrs"]["data-value"] for h in headings] == ["-1", "-14"]                   # no heading for a category with no rows
    assert [find(h, "span")[0]["text"] for h in headings] == ["Arts", "Sports"]
    assert [h["attrs"]["data-value"] for h in headings if "is-selected" in classes(h)] == ["-1"]
    assert [r["attrs"]["data-value"] for r in find(root, "li", **{"data-filter-row": True})] == ["0", "-1", "8", "-14", "60", "61"]
    assert len(find(root, "li", **{"data-filter-heading": True})) == len(groups) - 1       # the lone Uncategorized row has none


def states(active, make=grouped):
    """The state label of each heading, by its value: (text, hidden, ticked)."""
    _html, root = make(active=active)
    found = {}
    for heading in find(root, "li", **{"data-filter-heading": True}):
        label = find(heading, "span", **{"data-filter-state": True})[0]
        found[heading["attrs"]["data-value"]] = (label["text"], "d-none" in classes(label), "is-selected" in classes(heading))
    return found


def test_a_ticked_heading_says_everything_the_category_alone_or_some_of_it_from_the_active_set():
    # R&B "Soul" is -2 over rows 2 and 3; Technology is -15 over rows 74 and 75
    assert states((-2, 2, 3))["-2"] == (ALL, False, True)                                    # the heading and every row
    assert states((-2,))["-2"] == (ALONE, False, True)                                       # the heading and no row
    assert states((-2, 3))["-2"] == (SOME, False, True)                                      # the heading and some rows
    assert states((2, 3))["-2"] == ("", True, False)                                         # rows only: the heading is not ticked
    assert states(())["-2"] == ("", True, False) and states(())["-15"] == ("", True, False)
    both = states((-2, 2, 3, -15, 74))                                                       # each group on its own rows
    assert both["-2"] == (ALL, False, True) and both["-15"] == (SOME, False, True)
    assert states((-15, 2, 3, 0))["-2"] == ("", True, False) and states((-15, 2, 3, 0))["-15"] == (ALONE, False, True)


def test_the_three_texts_are_on_every_heading_and_the_label_shows_the_one_for_its_state():
    _html, root = grouped(active=(-2, 3))
    headings = find(root, "li", **{"data-filter-heading": True})
    assert len(headings) == 2
    for heading in headings:
        assert (heading["attrs"]["data-state-all"], heading["attrs"]["data-state-alone"], heading["attrs"]["data-state-some"]) == (ALL, ALONE, SOME)
    ticked, unticked = headings
    link, = ticked["kids"]
    label = find(ticked, "span", **{"data-filter-state": True})[0]
    assert label["text"] == SOME and classes(label) == ["filter-group-state"]
    assert link["attrs"]["aria-describedby"] == label["attrs"]["id"]                         # a screen reader hears the state
    assert link["attrs"]["aria-checked"] == "true" and "is-selected" in classes(ticked)
    assert find(unticked, "span", **{"data-filter-state": True})[0]["text"] == ""
    assert find(unticked, "a", role="checkbox")[0]["attrs"]["aria-checked"] == "false"
    # a state label's id never clashes with the heading's name or another list's
    ids = [n["attrs"]["id"] for n in walk(root) if "id" in n["attrs"]]
    assert len(ids) == len(set(ids))


def test_the_state_label_works_on_the_catalogs_own_groups_and_in_the_phone_sheet():
    sub = lambda id_, name: NS(id=id_, name=name)
    categories = [NS(id=14, name="Sports", subcats=[sub(60, "Golf"), sub(61, "News")]),
                  NS(id=1, name="Arts", subcats=[sub(8, "Film & TV")])]
    groups = category_catalog.build(categories, lambda text: text)
    html = template_env().get_template("categorical_filter_modal.html").render(categorical_groups=groups, active_subcats=[-1, 8, -14, 60])
    root = Tree(html).root
    labels = {h["attrs"]["data-value"]: find(h, "span", **{"data-filter-state": True})[0]["text"]
              for h in find(root, "li", **{"data-filter-heading": True})}
    assert labels == {"-1": ALL, "-14": SOME}


def test_the_label_texts_go_through_the_translating_function():
    env = template_env()
    env.install_gettext_callables(lambda text: "[" + text + "]", lambda one, many, n: one, newstyle=True,
                                  pgettext=lambda ctx, text: text, npgettext=lambda ctx, one, many, n: one)
    env.globals["msg_text"] = lambda text: text
    html = str(env.get_template("_sidebar_filter_macros.html").make_module().filter_panel_body(
        "sidebar-categorical-options", None, {-2, 2}, "Search", "/categorical", "cats", groups=GROUPS))
    root = Tree(html).root
    heading = find(root, "li", **{"data-filter-heading": True})[0]
    assert heading["attrs"]["data-state-all"] == f"[{ALL}]" and heading["attrs"]["data-state-alone"] == f"[{ALONE}]"
    assert heading["attrs"]["data-state-some"] == f"[{SOME}]"
    assert find(heading, "span", **{"data-filter-state": True})[0]["text"] == f"[{SOME}]"
    assert find(heading, "a", role="checkbox")[0]["attrs"]["title"] == f"[{TITLE}]"
    assert HINT not in html and f"[{HINT}]" not in html                                    # the explanation under the search box is gone


def test_no_explanation_sits_under_the_search_box_of_any_list():
    # the owner (2026-10-30): "you can also remove the 'Tick a category to tick all its subcategories...' explanation". The heading's
    # own tooltip (its title, tested above) still says it on hover
    html, root = grouped()
    assert HINT not in html and not find(root, "p")
    again, again_root = grouped("mobile-categorical-options", apply_mode="form", formkey="k")
    assert HINT not in again and not find(again_root, "p")
    flat = str(macros().filter_panel_body("sidebar-language-options", FLAT, {"a"}, "Search", "/language", "codes"))
    assert HINT not in flat and not find(Tree(flat).root, "p")
    assert HINT not in str(macros().post_type_body("sidebar-post-type-options", [("text", "Text", "Content")], ["Content"], set()))
    assert "Tick a category" not in MACROS
    # the search box carries the hook the left sidebar's own (shorter) height is written against
    box = find(root, "input", type="text")[0]
    assert classes(box) == ["form-control", "form-control-sm", "filter-search", "mb-2"]


def test_the_count_line_and_the_empty_notice_are_the_flat_lists():
    html, root = grouped(active=(0, 74))
    assert find(root, "span", id="sidebar-categorical-options-count")[0]["text"] == "2 selected"
    assert "d-none" not in classes(find(root, "span", id="sidebar-categorical-options-count")[0])
    _html, with_heading = grouped(active=(0, -15))                                         # a heading counts as one
    assert find(with_heading, "span", id="sidebar-categorical-options-count")[0]["text"] == "2 selected"
    assert find(root, "div", id="sidebar-categorical-options-empty")
    assert "filterSidebarList(this, 'sidebar-categorical-options')" in html
    assert "applySidebarFilter('sidebar-categorical-options', '/categorical', 'cats')" in html


def test_the_sidebar_and_the_phone_sheet_do_not_share_an_id():
    _a, one = grouped("sidebar-categorical-options")
    _b, two = grouped("mobile-categorical-options")
    ids_one = [n["attrs"]["id"] for n in walk(one) if "id" in n["attrs"]]
    ids_two = [n["attrs"]["id"] for n in walk(two) if "id" in n["attrs"]]
    assert len(ids_one) == len(set(ids_one)) and len(ids_two) == len(set(ids_two))
    assert not set(ids_one) & set(ids_two)


def test_the_curation_form_variant_keeps_its_form_and_its_grouped_list():
    html, root = grouped("curation-categorical-options", apply_mode="form", formkey="k")
    form = find(root, "form", id="curation-categorical-options-form")[0]
    assert find(form, "div", id="curation-categorical-options") and "applyCurationFilter(" in html
    assert find(form, "input", name="formkey")[0]["attrs"]["value"] == "k"


def test_the_phone_sheet_draws_the_groups():
    html = template_env().get_template("categorical_filter_modal.html").render(
        categorical_groups=GROUPS, active_subcats=[3, -15])
    root = Tree(html).root
    assert find(root, "div", id="mobile-categorical-options") and len(find(root, "li", **{"data-filter-heading": True})) == 2
    assert [r["attrs"]["data-value"] for r in find(root, "li", **{"class": "py-1 is-selected"})] == ["3"]
    assert [r["attrs"]["data-value"] for r in find(root, "li", **{"class": "filter-group-heading is-selected"})] == ["-15"]


FLAT = [{"value": "a", "label": "Alpha & Co", "search": 'alpha "q"'}, {"value": "b", "label": "Beta", "search": "beta"}]


def test_a_flat_list_still_draws_exactly_what_it_drew_before_the_rows_were_shared():
    html = str(macros().filter_panel_body("sidebar-regional-options", FLAT, {"b"}, "Search regions", "/regional", "region"))
    expected = (
        '<ul class="no-bullets pl-0 mb-1" id="sidebar-regional-options" style="max-height: 220px; overflow-y: auto;">\n'
        '  \n'
        '  <li data-filter-row data-filter-text="beta" data-value="b" class="py-1 is-selected" onclick="toggleSidebarFilterOption(this, \'sidebar-regional-options\')">\n'
        '    <a href="javascript:void(0)" class="text-decoration-none text-purple font-weight-bold d-flex align-items-center"><i class="fas fa-check mr-1"></i><span class="sidebar-filter-text" onmouseenter="startSidebarFilterMarquee(this)" onmouseleave="stopSidebarFilterMarquee(this)">Beta</span></a>\n'
        '  </li>\n'
        '  \n'
        '  \n'
        '  <li data-filter-row data-filter-text="alpha &#34;q&#34;" data-value="a" class="py-1" onclick="toggleSidebarFilterOption(this, \'sidebar-regional-options\')">\n'
        '    <a href="javascript:void(0)" class="text-decoration-none text-black d-flex align-items-center"><i class="fas fa-check mr-1 invisible"></i><span class="sidebar-filter-text" onmouseenter="startSidebarFilterMarquee(this)" onmouseleave="stopSidebarFilterMarquee(this)">Alpha &amp; Co</span></a>\n'
        '  </li>\n'
        '  \n'
        '</ul>\n'
        '<div id="sidebar-regional-options-empty"')
    assert expected in html
    assert "data-filter-group" not in html and "filter-group-heading" not in html and "data-keep-order" not in html
    assert "data-state" not in html and "data-filter-state" not in html and HINT not in html and "<p" not in html


# --- the places that draw it ---------------------------------------------------------------------------------------------

def test_the_row_markup_is_written_once_for_the_flat_and_the_grouped_lists():
    assert MACROS.count("data-filter-text") == 1
    assert MACROS.index("macro filter_row(") < MACROS.index("macro filter_panel_body(")
    body = MACROS.split("macro filter_panel_body(", 1)[1].split("{% endmacro %}", 1)[0]
    assert body.count("filter_row(opt, list_id,") == 3                                       # grouped, then ticked and unticked
    group = body.split("<div data-filter-group>", 1)[1].split("</div>", 1)[0]
    # the heading is the first row of the group's list (a <li> straight under the <div> is invalid HTML), a tick box
    assert group.index("<ul") < group.index("data-filter-heading") < group.index("filter_row(opt, list_id,")
    assert "<button" not in group and 'role="checkbox"' in group and "filter-group-heading" in group
    assert "onclick=\"toggleSidebarFilterOption(this, '{{ list_id }}')\"" in group
    assert 'aria-describedby="{{ list_id }}-{{ loop.index }}-state"' in group and "data-filter-state" in group
    assert "data-state-all" in group and "data-state-alone" in group and "data-state-some" in group
    assert "(whole category)" not in MACROS
    assert "toggleSidebarFilterGroup" not in MACROS and "Select or deselect all" not in MACROS


def test_the_three_categorical_lists_are_given_the_groups_and_nothing_reads_the_flat_catalog():
    for name, list_id in (("sidebar-left.html", "sidebar-categorical-options"),
                          ("categorical_filter_modal.html", "mobile-categorical-options"),
                          ("curations/form.html", "curation-categorical-options")):
        page = read("templates", name)
        call = re.search(r"filters\.filter_panel_body\('" + list_id + r"'.*?\) \}\}", page).group(0)
        assert "groups=categorical_groups" in call and ", none," in call, name
    for path in (ROOT / "templates").rglob("*.html"):
        assert "categorical_catalog" not in path.read_text(encoding="utf-8"), path.name
    # Regional and Language keep the flat list
    assert "groups=" not in re.search(r"filter_panel_body\('sidebar-regional-options'.*?\) \}\}", read("templates", "sidebar-left.html")).group(0)


def test_the_route_gives_the_groups_and_keeps_the_ids_other_code_validates_against():
    processor = FRONT.split("def inject_sidebar_filter_data(", 1)[1].split("\n@app", 1)[0]
    assert "category_catalog.cached(" in processor and '"categorical_groups": categorical_groups' in processor
    assert "categorical_catalog" not in FRONT
    for name in ("ALL_SUBCATS = sorted(", "SUBCAT_NAME_BY_ID = {", "ALL_SUBCAT_IDS = set("):
        assert name in FRONT, name
    notice = FRONT.split("def _filter_display_context(", 1)[1].split("\ndef ", 1)[0]
    assert '"subcat_names": category_catalog.notice_names(ALL_SUBCATS, subcat_ids, msg, CATEGORIES)' in notice


# --- the script ----------------------------------------------------------------------------------------------------------

def test_the_search_hides_a_group_with_no_row_left_and_reads_every_script():
    fn = JS.split("var filterSidebarList = function(", 1)[1].split("\n};", 1)[0]
    assert "[data-filter-group]" in fn and "data-filter-heading" in fn
    assert "[data-filter-row]:not(.d-none)" not in fn                                        # a heading is never one of the rows
    assert "/[^\\p{L}\\p{N}]/gu" in fn and "a-z0-9" not in fn
    assert "/categor/i.test(listId)" in fn and "replace(/^#/, '')" in fn                     # tests/test_topics.py pins these


def test_one_helper_ticks_a_row_for_the_click_and_deselect_all_and_a_heading_keeps_its_own_look():
    helper = JS.split("var setSidebarFilterRow = function(", 1)[1].split("\n};", 1)[0]
    for part in ("'is-selected'", "'text-purple'", "'font-weight-bold'", "'text-black'", "aria-checked", "'invisible'"):
        assert part in helper, part
    # the colour classes belong to the rows: a heading is coloured by .is-selected
    assert helper.index("data-filter-heading") < helper.index("'text-purple'") < helper.index("aria-checked")
    for name in ("toggleSidebarFilterOption", "deselectAllSidebarFilter"):
        fn = JS.split(f"var {name} = function(", 1)[1].split("\n};", 1)[0]
        assert "setSidebarFilterRow(" in fn and "updateSidebarFilterHeading(" in fn, name
        assert "classList.toggle('text-purple'" not in fn and "aria-checked" not in fn, name
    toggle = JS.split("var toggleSidebarFilterOption = function(", 1)[1].split("\n};", 1)[0]
    assert "sidebarFilterGroupRows(" in toggle                                               # a heading's group, hidden rows too
    rows = JS.split("var sidebarFilterGroupRows = function(", 1)[1].split("\n};", 1)[0]
    assert "[data-filter-group]" in rows and "d-none" not in rows                            # a search never narrows it
    assert "toggleSidebarFilterGroup" not in JS                                              # a heading is a row now
    # all the new code sits in the span the node scenario runs
    assert JS.index("var filterSidebarList = function") < JS.index("var sidebarFilterGroupRows = function") < JS.index(
        "var updateSidebarFilterHeading = function") < JS.index("var applySidebarFilter = function")


FAKE_DOM = r"""
class El {
  constructor(tag, attrs, kids) {
    this.tag = tag; this.attrs = Object.assign({}, attrs); this.kids = kids || []; this.parent = null; this.textContent = '';
    this.cls = new Set((this.attrs.class || '').split(/\s+/).filter(Boolean));
    this.kids.forEach((k) => { k.parent = this; });
  }
  get classList() {
    const set = this.cls;
    return {
      toggle(name, force) { const on = force === undefined ? !set.has(name) : !!force; if (on) set.add(name); else set.delete(name); return on; },
      add(...names) { names.forEach((n) => set.add(n)); },
      remove(...names) { names.forEach((n) => set.delete(n)); },
      contains(name) { return set.has(name); },
    };
  }
  getAttribute(name) { return name in this.attrs ? this.attrs[name] : null; }
  hasAttribute(name) { return name in this.attrs; }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  descendants() { return this.kids.flatMap((k) => [k, ...k.descendants()]); }
  matches(selector) {
    const m = /^([a-z]+)?((?:\[[\w-]+\])*)((?:\.[\w-]+)*)(?::not\(\.([\w-]+)\))?$/.exec(selector);
    if (!m) throw new Error('selector ' + selector);
    if (m[1] && m[1] !== this.tag) return false;
    for (const a of (m[2].match(/[\w-]+/g) || [])) if (!(a in this.attrs)) return false;
    for (const c of (m[3].match(/[\w-]+/g) || [])) if (!this.cls.has(c)) return false;
    return !(m[4] && this.cls.has(m[4]));
  }
  closest(selector) { let el = this; while (el) { if (el.matches(selector)) return el; el = el.parent; } return null; }
  querySelectorAll(selector) { return this.descendants().filter((e) => e.matches(selector)); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}
const row = (value, text, on) => new El('li', {class: 'py-1' + (on ? ' is-selected' : ''), 'data-filter-row': '', 'data-value': value, 'data-filter-text': text}, [
  new El('a', {class: 'text-decoration-none ' + (on ? 'text-purple font-weight-bold' : 'text-black'), 'aria-checked': on ? 'true' : 'false'}, [new El('i', {class: 'fas fa-check' + (on ? '' : ' invisible')})])]);
// the three texts are the macro's data attributes; these are not the real ones, so a text the script made up would show
const head = (value, on) => new El('li', {class: 'filter-group-heading' + (on ? ' is-selected' : ''), 'data-filter-row': '', 'data-filter-heading': '', 'data-value': value,
                                          'data-state-all': 'all of it', 'data-state-alone': 'only itself', 'data-state-some': 'itself and some'}, [
  new El('a', {role: 'checkbox', 'aria-checked': on ? 'true' : 'false'}, [new El('i', {class: 'fas fa-check' + (on ? '' : ' invisible')}),
    new El('span', {}), new El('span', {class: 'filter-group-state d-none', 'data-filter-state': ''})])]);
const group = (heading, rows) => new El('div', {'data-filter-group': ''}, [new El('ul', {}, (heading ? [heading] : []).concat(rows))]);
const sportsHead = head('-14'), techHead = head('-15'), russianHead = head('-16');
const lone = group(null, [row('0', 'Uncategorized Uncategorized')]);
const sports = group(sportsHead, [row('60', 'Soccer Soccer Sports Sports'), row('61', 'Golf Golf Sports Sports'), row('62', 'News News Sports Sports')]);
const tech = group(techHead, [row('74', 'News News Technology Technology', true), row('75', 'Apps Apps Technology Technology')]);
const russian = group(russianHead, [row('90', 'Новости Новости Политика Политика')]);
const list = new El('div', {id: 'sidebar-categorical-options', 'data-keep-order': ''}, [lone, sports, tech, russian]);
const count = new El('span', {class: 'd-none'});
const empty = new El('div', {class: 'd-none'});
const hiddenInput = {value: ''};
let submitted = false;
const registry = {'sidebar-categorical-options': list, 'sidebar-categorical-options-count': count, 'sidebar-categorical-options-empty': empty,
                  'curation-value': hiddenInput, 'curation-form': {submit() { submitted = true; }}};
const document = {getElementById: (id) => registry[id] || null, documentElement: {}};
const window = {location: {href: ''}};
const trn = (singular, plural, n) => n + ' selected';
const LIST = 'sidebar-categorical-options';
const isHeading = (r) => r.hasAttribute('data-filter-heading');
const rowsOf = (g) => g.querySelectorAll('[data-filter-row]').filter((r) => !isHeading(r));
const search = (text) => filterSidebarList({value: text}, LIST);
const hidden = () => [lone, sports, tech, russian].map((g) => g.cls.has('d-none'));
const ticked = () => list.querySelectorAll('[data-filter-row].is-selected').map((r) => r.attrs['data-value']);
const seen = () => rowsOf(list).filter((r) => !r.cls.has('d-none')).map((r) => r.attrs['data-value']);
const headingsHidden = () => list.querySelectorAll('[data-filter-heading]').filter((r) => r.cls.has('d-none')).length;
const click = (el) => toggleSidebarFilterOption(el, LIST);
const label = (h) => { const s = h.querySelector('[data-filter-state]'); return [s.textContent, s.cls.has('d-none')]; };
const checked = (h) => h.querySelector('a').attrs['aria-checked'];
const look = (r) => [...r.querySelector('a').cls].sort().join(' ') + '|' + r.querySelector('a').attrs['aria-checked'] + '|' + r.querySelector('i').cls.has('invisible');
const out = {};
"""

SCENARIO = r"""
search('news');
out.news = {groups: hidden(), rows: seen(), empty: empty.cls.has('d-none'), headings: headingsHidden()};
search('#Apps');
out.apps = {groups: hidden(), rows: seen()};
search('technology');                           // a category's name finds its rows, and not its heading
out.technology = {groups: hidden(), rows: seen()};
search('zzz');
out.nothing = {groups: hidden(), empty: empty.cls.has('d-none'), headings: headingsHidden()};
search('новости');
out.russian = {groups: hidden(), rows: seen()};
search('');
out.cleared = {groups: hidden(), rows: seen().length, empty: empty.cls.has('d-none')};

search('news');                                 // 60 and 61 are hidden by the search, 62 and 74 shown
click(sportsHead);                              // a heading ticks every row of its group, the hidden ones too
out.cascade = {ticked: ticked(), rows: rowsOf(sports).map((r) => r.cls.has('is-selected')), looks: rowsOf(sports).map(look),
               hiddenRows: rowsOf(sports).filter((r) => r.cls.has('d-none')).map((r) => r.attrs['data-value']),
               count: count.textContent, countHidden: count.cls.has('d-none'), label: label(sportsHead), other: label(techHead),
               head: {link: checked(sportsHead), icon: sportsHead.querySelector('i').cls.has('invisible'),
                      cls: [...sportsHead.cls].sort(), linkCls: [...sportsHead.querySelector('a').cls]}};
click(sportsHead);                              // unticked with the search still on: every row goes with it
out.uncascade = {ticked: ticked(), rows: rowsOf(sports).map(look), label: label(sportsHead), link: checked(sportsHead),
                 icon: sportsHead.querySelector('i').cls.has('invisible'), count: count.textContent};
search('');

click(sportsHead);
click(rowsOf(sports)[1]);                       // one row off: the heading stays ticked
out.some = {ticked: ticked(), label: label(sportsHead), link: checked(sportsHead), cls: [...sportsHead.cls].sort()};
click(rowsOf(sports)[0]);
click(rowsOf(sports)[2]);                       // the last row off: the category alone
out.alone = {ticked: ticked(), label: label(sportsHead), link: checked(sportsHead), count: count.textContent};
click(rowsOf(sports)[0]);
click(rowsOf(sports)[1]);
click(rowsOf(sports)[2]);                       // every row back by hand: everything
out.byHand = {ticked: ticked(), label: label(sportsHead), link: checked(sportsHead)};

click(sportsHead);                              // unticked: all of its rows go
out.unticked = {ticked: ticked(), label: label(sportsHead), link: checked(sportsHead)};
click(rowsOf(sports)[0]);
click(rowsOf(sports)[1]);
click(rowsOf(sports)[2]);                       // every row ticked by hand: the heading stays as it was
out.rowsOnly = {ticked: ticked(), label: label(sportsHead), link: checked(sportsHead), icon: sportsHead.querySelector('i').cls.has('invisible')};
click(sportsHead);                              // now it ticks, and the rows stay ticked
out.headingOver = {ticked: ticked(), label: label(sportsHead), link: checked(sportsHead)};
click(rowsOf(sports)[0]);                       // some rows
click(sportsHead);                              // a ticked heading unticks all of them, whatever is ticked
out.partial = {ticked: ticked(), label: label(sportsHead), link: checked(sportsHead)};

click(rowsOf(tech)[1]);                         // 75, beside Technology's unticked heading and its ticked 74
out.techRow = {ticked: ticked(), label: label(techHead), link: checked(techHead)};
click(techHead);
out.techHead = {ticked: ticked(), label: label(techHead), link: checked(techHead), count: count.textContent,
                row: look(rowsOf(tech)[0]), other: label(sportsHead)};
click(sportsHead);

deselectAllSidebarFilter(LIST);                 // headings are cleared with the rows, and their labels with them
out.deselected = {ticked: ticked(), countHidden: count.cls.has('d-none'), sports: [label(sportsHead), checked(sportsHead)],
                  tech: [label(techHead), checked(techHead)], icon: sportsHead.querySelector('i').cls.has('invisible'),
                  rows: [...rowsOf(sports), ...rowsOf(tech)].map(look)};

click(rowsOf(lone)[0]);
click(sportsHead);
click(rowsOf(tech)[1]);
out.single = {ticked: ticked(), count: count.textContent};
applySidebarFilter(LIST, '/categorical', 'cats');
out.applied = window.location.href;
applyCurationFilter(LIST, 'curation-value', 'curation-form');
out.curation = {value: hiddenInput.value, submitted: submitted};
rowsOf(sports).forEach((r) => click(r));        // the category alone: only its heading goes with Uncategorized and 75
applySidebarFilter(LIST, '/categorical', 'cats');
out.categoryOnly = {ticked: ticked(), applied: window.location.href, label: label(sportsHead)};
console.log(JSON.stringify(out));
"""


def run_script():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    # from the search to the two Apply functions, which read the ticked rows
    start, end = JS.index("var filterSidebarList = function"), JS.index("var startSidebarFilterMarquee = function")
    result = subprocess.run([node, "-e", FAKE_DOM + JS[start:end] + SCENARIO], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def test_the_search_shows_only_the_groups_that_still_have_a_row_and_never_hides_a_heading_itself():
    out = run_script()
    # lone, Sports, Technology, the Russian one
    assert out["news"] == {"groups": [True, False, False, True], "rows": ["62", "74"], "empty": True, "headings": 0}
    assert out["apps"] == {"groups": [True, True, False, True], "rows": ["75"]}
    assert out["technology"] == {"groups": [True, True, False, True], "rows": ["74", "75"]}
    assert out["cleared"] == {"groups": [False, False, False, False], "rows": 7, "empty": True}


def test_the_empty_notice_ignores_the_headings_which_are_always_there():
    out = run_script()
    assert out["nothing"] == {"groups": [True, True, True, True], "empty": False, "headings": 0}


def test_a_search_in_another_script_finds_its_row_and_not_every_row():
    out = run_script()
    assert out["russian"] == {"groups": [True, True, True, False], "rows": ["90"]}          # it used to squash to "" and match all


ROW_ON = "font-weight-bold text-decoration-none text-purple|true|false"
ROW_OFF = "text-black text-decoration-none|false|true"


def test_a_heading_ticks_every_row_of_its_group_the_ones_a_search_hides_included():
    cascade = run_script()["cascade"]
    assert cascade["hiddenRows"] == ["60", "61"]                                             # the search for "news" hides them
    assert cascade["ticked"] == ["-14", "60", "61", "62", "74"]
    assert cascade["rows"] == [True, True, True] and cascade["looks"] == [ROW_ON] * 3        # drawn by the one row function
    assert cascade["count"] == "5 selected" and cascade["countHidden"] is False              # the heading counts as one
    assert cascade["label"] == ["all of it", False] and cascade["other"] == ["", True]       # the text is the heading's attribute
    # the heading keeps its own look: coloured by .is-selected, not by the row's classes
    assert cascade["head"] == {"link": "true", "icon": False, "cls": ["filter-group-heading", "is-selected"], "linkCls": []}


def test_unticking_a_heading_unticks_every_row_of_its_group_with_the_search_on():
    out = run_script()["uncascade"]
    assert out["ticked"] == ["74"] and out["rows"] == [ROW_OFF] * 3                          # only the other group's row is left
    assert out["label"] == ["", True] and out["link"] == "false" and out["icon"] is True
    assert out["count"] == "1 selected"


def test_a_row_never_changes_its_heading_but_the_label_on_it_follows_the_rows():
    out = run_script()
    assert out["some"] == {"ticked": ["-14", "60", "62", "74"], "label": ["itself and some", False], "link": "true",
                           "cls": ["filter-group-heading", "is-selected"]}                   # one row off: still ticked
    assert out["alone"] == {"ticked": ["-14", "74"], "label": ["only itself", False], "link": "true", "count": "2 selected"}
    assert out["byHand"] == {"ticked": ["-14", "60", "61", "62", "74"], "label": ["all of it", False], "link": "true"}


def test_ticking_every_row_by_hand_leaves_an_unticked_heading_unticked_and_ticking_it_then_keeps_them():
    out = run_script()
    assert out["unticked"] == {"ticked": ["74"], "label": ["", True], "link": "false"}
    assert out["rowsOnly"] == {"ticked": ["60", "61", "62", "74"], "label": ["", True], "link": "false", "icon": True}
    assert out["headingOver"] == {"ticked": ["-14", "60", "61", "62", "74"], "label": ["all of it", False], "link": "true"}
    assert out["partial"] == {"ticked": ["74"], "label": ["", True], "link": "false"}        # a ticked heading unticks all, some or none
    assert out["techRow"] == {"ticked": ["74", "75"], "label": ["", True], "link": "false"}  # a row beside an unticked heading


def test_each_group_has_its_own_label_and_a_row_takes_the_look_of_a_ticked_row():
    out = run_script()["techHead"]
    assert out == {"ticked": ["-15", "74", "75"], "label": ["all of it", False], "link": "true", "count": "3 selected",
                   "row": ROW_ON, "other": ["", True]}


def test_deselect_all_clears_the_headings_and_their_labels_with_the_rows():
    out = run_script()["deselected"]
    assert out["ticked"] == [] and out["countHidden"] is True and out["icon"] is True
    assert out["sports"] == [["", True], "false"] and out["tech"] == [["", True], "false"]
    assert out["rows"] == [ROW_OFF] * 5


def test_both_apply_functions_send_the_headings_negative_id_with_the_ids_of_its_rows():
    out = run_script()
    assert out["single"] == {"ticked": ["0", "-14", "60", "61", "62", "75"], "count": "6 selected"}
    assert out["applied"] == "/categorical?cats=0,-14,60,61,62,75"
    assert out["curation"] == {"value": "0,-14,60,61,62,75", "submitted": True}


def test_a_heading_with_none_of_its_rows_sends_its_token_alone():
    out = run_script()["categoryOnly"]                                                       # the category alone
    assert out == {"ticked": ["0", "-14", "75"], "applied": "/categorical?cats=0,-14,75", "label": ["only itself", False]}


def test_the_script_writes_no_label_text_of_its_own():
    for text in (ALL, ALONE, SOME):
        assert text not in JS
    fn = JS.split("var updateSidebarFilterHeading = function(", 1)[1].split("\n};", 1)[0]
    for name in ("data-state-all", "data-state-alone", "data-state-some", "data-filter-state"):
        assert name in fn, name
    assert "trn(" not in fn and "tr(" not in fn


# --- the stylesheets -------------------------------------------------------------------------------------------------------

def test_both_stylesheets_draw_the_heading_as_a_bold_row_like_the_ones_under_it_the_same_way():
    rules = []
    for name in ("main.scss", "main_dark.scss"):
        css = read("assets", "style", name)
        assert css.count("\n.filter-group-heading {") == 1, name
        rule = css.split("\n.filter-group-heading {", 1)[1].split("\n}", 1)[0]
        # it is a <li> (no button resets, no :first-child rule: the heading is a row of the group's list)
        assert "first-child" not in rule and "list-style: none" in rule and "cursor: pointer" in rule, name
        assert not {"display: block", "width: 100%", "border: 0", "background: none", "text-align"} & {
            line.strip().rstrip(";") for line in rule.splitlines()}, name
        # the owner (2026-10-30): "the exact same font except bolded": a row's own size (inherited), face, case and spacing, the
        # sheet's own text colour (.text-black is $black), and only the weight is different
        head = rule.split(".filter-group-state {", 1)[0]
        assert "font-weight: 700;" in head and "color: $black;" in head and "padding: .25rem 0;" in head and "margin: 0;" in head, name
        for never in ("font-size", "text-transform", "letter-spacing", "$muted", "var(--text)", "var(--link);\n    }\n\n    &:hover"):
            assert never not in head, (name, never)
        # ticked, it takes the link colour, as a ticked row does (.text-purple)
        assert "&.is-selected {\n        color: var(--link);\n    }" in rule, name
        # the label of what a ticked heading stands for: on a line of its own under the name and in step with it (beside it, a long one
        # squeezed the name mid-word), small and muted, and its padding flips in a right-to-left page
        assert "a {\n        color: inherit;\n        flex-wrap: wrap;\n    }" in rule, name
        state = rule.split(".filter-group-state {", 1)[1].split("\n    }", 1)[0]
        assert "flex: 1 0 100%" in state and "padding-left: 1.125rem" in state and "text-transform: none" in state, name
        assert "font-weight: 400" in state and "font-size: .875rem" in state and "color: $muted" in state and "first-child" not in state, name
        # a group after another has room above its heading, and the rows under a heading are one tick step in from it (never the lone
        # Uncategorized row, which has no heading to be under)
        assert "[data-filter-group] ~ [data-filter-group] > ul > .filter-group-heading {\n    margin-top: .5rem;\n}" in css, name
        assert "[data-filter-group] > ul > .filter-group-heading ~ [data-filter-row] {\n    padding-left: 1.125rem;\n}" in css, name
        # the post-type heading keeps its own rule, and the new one sits before the marker guild CSS reads from
        assert ".post-type-group {" in css and css.index("\n.filter-group-heading {") < css.index("// Create post card"), name
        rules.append(rule)
    assert rules[0] == rules[1]
