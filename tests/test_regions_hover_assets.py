"""The Regional filter's link to the Regions page and its countries hover (the owner, 2026-10-30: "add the link to the regions page somewhere at
the top so people know the region. Also hovering over the region gives the list of countries"). The macro is rendered for real (it is the one
the sidebar, the phone sheet and the curation form share); the country lines are tested on the real app (tests/pg/test_regions_hover.py: helpers/regions.py needs the whole app); the script and the styles are read.
The tooltip on a real page is measured by scripts/ui/sidebar.spec.ts."""
import re
from pathlib import Path

from jinja2 import FileSystemLoader

from ruqqus.helpers.i18n_jinja import environment

ROOT = Path(__file__).resolve().parent.parent / "ruqqus"
TEMPLATES = ROOT / "templates"
MACROS = (TEMPLATES / "_sidebar_filter_macros.html").read_text(encoding="utf-8")
FRONT = (ROOT / "routes" / "front.py").read_text(encoding="utf-8")
JS = (ROOT / "assets" / "js" / "all_js.js").read_text(encoding="utf-8")
COMPONENTS = (ROOT / "assets" / "style" / "_components.scss").read_text(encoding="utf-8")


def macros():
    env = environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=True)
    env.install_null_translations(newstyle=True)
    env.globals["msg_text"] = lambda text: text
    return env.get_template("_sidebar_filter_macros.html").make_module()


ROWS = [{"value": "amazonian_shield", "label": "Amazonian Shield", "search": "Amazonian Shield Brazil Peru", "countries": "Brazil, Peru"},
        {"value": "nowhere", "label": "Nowhere", "search": "Nowhere", "countries": ""},
        {"value": "plain", "label": "Plain", "search": "Plain"}]


def panel(**kw):
    return str(macros().filter_panel_body("sidebar-regional-options", ROWS, set(), "Search regions", "/regional", "region", **kw))


# --- the link ---------------------------------------------------------------------------------------------------------------------

def test_the_link_is_the_first_thing_in_a_regional_panel_and_goes_to_the_regions_page():
    html = panel(regions_link=True)
    assert html.lstrip().startswith('<a href="/regions" class="regions-link">'), html[:120]
    assert html.count('href="/regions"') == 1 and ">Regions</a>" in html
    assert html.index("regions-link") < html.index("Deselect all") < html.index("filterSidebarList")


def test_no_other_panel_has_it_and_the_default_is_off():
    assert "regions-link" not in panel()
    langs = str(macros().filter_panel_body("sidebar-language-options", ROWS, set(), "Search", "/language", "codes"))
    assert "regions-link" not in langs


def test_a_curation_forms_link_sits_inside_its_form_above_the_count():
    html = str(macros().filter_panel_body("curation-regional-options", ROWS, set(), "Search", "/set", "region", apply_mode="form", formkey="k", regions_link=True))
    assert html.index("<form") < html.index("regions-link") < html.index("Deselect all")


def test_exactly_the_three_regional_panels_ask_for_it():
    for name in ("sidebar-left.html", "regional_filter_modal.html", "curations/form.html"):
        text = (TEMPLATES / name).read_text(encoding="utf-8")
        regional = [line for line in text.splitlines() if "regions_catalog" in line or "curation.region_filter_list" in line]
        assert len(regional) == 1 and "regions_link=true" in regional[0], name
    for name in ("sidebar-left.html", "language_filter_modal.html", "categorical_filter_modal.html"):
        for line in (TEMPLATES / name).read_text(encoding="utf-8").splitlines():
            if "languages_catalog" in line or "categorical_groups" in line:
                assert "regions_link" not in line, (name, line)


def test_the_link_only_uses_a_message_every_catalogue_has():
    from babel.messages.pofile import read_po
    po = read_po((ROOT / "translations" / "fr" / "LC_MESSAGES" / "messages.po").open("rb"))
    assert po.get("Regions") is not None and "_(\"Regions\")" in MACROS


# --- the hover --------------------------------------------------------------------------------------------------------------------

def test_a_row_carries_its_countries_only_when_it_has_some_and_other_rows_are_byte_for_byte_as_before():
    html = panel()
    assert html.count("data-countries=") == 1 and 'data-countries="Brazil, Peru"' in html
    plain = html.split('data-value="plain"', 1)[1].split("</li>", 1)[0]
    assert "data-countries" not in plain
    nowhere = html.split('data-value="nowhere"', 1)[1].split("</li>", 1)[0]
    assert "data-countries" not in nowhere
    # the attribute is on the row's link, so keyboard focus (the <a> is the focusable thing) can show it
    assert re.search(r'<a href="javascript:void\(0\)"[^>]*data-countries="Brazil, Peru"', html)


def test_the_countries_are_escaped():
    rows = [{"value": "x", "label": "X", "search": "X", "countries": 'A "quoted" <b>'}]
    html = str(macros().filter_panel_body("l", rows, set(), "s", "/r", "region"))
    assert 'data-countries="A &#34;quoted&#34; &lt;b&gt;"' in html and "<b>" not in html


def test_the_catalogue_fills_the_hover_and_the_search_from_the_regions_own_helpers():
    assert "country_list_text(r.code)" in FRONT and "country_search_text(r.code)" in FRONT
    row = FRONT.split("regions_catalog = [", 1)[1].split("] if getattr", 1)[0]
    assert '"countries": country_list_text(r.code)' in row and "country_search_text(r.code)" in row


def test_the_tooltip_is_drawn_in_the_body_against_the_window_with_its_own_class():
    init = JS.split("$('[data-countries]').tooltip({", 1)[1].split("});", 1)[0]
    for needle in ("container: 'body'", "boundary: 'viewport'", "placement: 'right'", "region-tooltip", "getAttribute('data-countries')"):
        assert needle in init, needle


def test_the_tooltip_and_link_are_styled_once_in_the_shared_partial_with_scale_tokens():
    tip = COMPONENTS.split(".region-tooltip .tooltip-inner {", 1)[1].split("}", 1)[0]
    assert "max-width: 18rem;" in tip and "text-align: start;" in tip and "font-size: var(--text-xs);" in tip
    link = COMPONENTS.split(".regions-link {", 1)[1].split("}", 1)[0]
    assert "min-height: var(--control-xs);" in link and "color: var(--link);" in link and "font-size: var(--text-xs);" in link


# --- the review of 2026-10-30: a finger can't hover, and the tip must never swallow a click -------------------------------------------

def test_the_phone_sheet_draws_no_countries_and_the_other_two_panels_do():
    # the sheet asks for none (a finger can't hover, and the tip sat on the list it described); the sidebar and the curation form don't ask
    modal = (TEMPLATES / "regional_filter_modal.html").read_text(encoding="utf-8")
    assert "regions_link=true, hover_countries=false)" in modal
    for name in ("sidebar-left.html", "curations/form.html"):
        assert "hover_countries=false" not in (TEMPLATES / name).read_text(encoding="utf-8"), name
    html = panel(hover_countries=False)
    assert "data-countries" not in html and "Amazonian Shield Brazil Peru" in html          # the search still finds a region by its countries
    assert 'data-countries="Brazil, Peru"' in panel() and 'data-countries="Brazil, Peru"' in panel(regions_link=True)


def test_the_tooltip_is_made_only_where_a_pointer_can_hover_and_flips_clear_of_the_list():
    block = JS.split("// a region's countries on hover", 1)[1].split("// Paste to create submission", 1)[0]
    assert "window.matchMedia('(hover: hover) and (pointer: fine)').matches" in block
    assert block.index("matchMedia") < block.index("$('[data-countries]').tooltip(")
    assert "fallbackPlacement: ['left', 'bottom', 'top']" in block


def test_a_tip_that_lands_on_the_list_never_takes_a_click():
    assert "pointer-events: none;" in COMPONENTS.split(".region-tooltip {", 1)[1].split("}", 1)[0]
