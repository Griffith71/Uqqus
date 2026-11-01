"""The Regional filter's countries and link on the real app (helpers/regions.py `country_list_text` / `country_search_text`, routes/front.py
`regions_catalog`, templates/_sidebar_filter_macros.html): every region says where it is, in the reader's language, and the page that draws
the filter carries the link to the Regions page. The macro and the script are pinned without the app in tests/test_regions_hover_assets.py;
the tooltip on a page is measured by scripts/ui/sidebar.spec.ts."""
import re
import secrets


def region_helpers():
    from ruqqus.helpers import regions
    return regions


def test_every_region_has_a_sorted_line_of_countries_and_the_longest_has_twenty_eight(real_app):
    regions = region_helpers()
    assert regions.country_list_text("nowhere-at-all") == ""
    for code, codes in regions.REGION_COUNTRIES.items():
        text = regions.country_list_text(code)
        assert text and text.count(", ") == len(codes) - 1, code
        names = text.split(", ")
        assert names == sorted(names, key=str.casefold), code
    assert regions.country_list_text("caribbean").count(", ") == 27


def test_the_search_text_holds_every_name_in_english_and_in_the_readers_language(real_app):
    from ruqqus.helpers.i18n_runtime import force_locale
    regions = region_helpers()
    some = next(iter(regions.REGION_COUNTRIES))
    assert regions.country_search_text("nowhere-at-all") == ""
    text = regions.country_search_text(some)
    assert all(regions.ENGLISH_COUNTRY_NAMES[c] in text for c in regions.REGION_COUNTRIES[some])
    with force_locale("fr"):
        french = regions.country_search_text(some)
    assert all(regions.ENGLISH_COUNTRY_NAMES[c] in french for c in regions.REGION_COUNTRIES[some])      # typing the English still finds it


def test_another_language_names_the_countries_in_its_own_words(real_app):
    from ruqqus.helpers.i18n_runtime import force_locale
    regions = region_helpers()
    code = next(c for c, codes in regions.REGION_COUNTRIES.items() if "DE" in codes)
    english = regions.country_list_text(code)
    with force_locale("de"):
        german = regions.country_list_text(code)
    assert "Germany" in english and "Deutschland" in german and "Germany" not in german


def test_every_language_separates_the_countries_with_its_own_mark(real_app):
    """Review 2026-10-30: Babel's `unit` list style joined Chinese names with nothing ("古巴圣卢西亚...") and Russian, Korean and Turkish ones with
    a bare space, so "Antigua ve Barbuda" read as two countries. Each language now has a visible separator."""
    from ruqqus.helpers.i18n_runtime import force_locale
    regions = region_helpers()
    code = "caribbean"
    count = len(regions.REGION_COUNTRIES[code])
    for key, mark in (("en", ", "), ("zh", "、"), ("ja", "、"), ("ar", "، "), ("fa", "، "), ("ru", ", "), ("ko", ", "), ("tr", ", "), ("de", ", ")):
        with force_locale(key):
            text = regions.country_list_text(code)
            names = sorted((regions.country_name(c) for c in regions.REGION_COUNTRIES[code]), key=str.casefold)
        # (a name can hold a comma itself: CLDR's Russian "Бонэйр, Синт-Эстатиус и Саба", so the marks are not counted)
        assert text == mark.join(names) and len(names) == count, (key, text[:80])
        assert mark.strip() in text and not text.startswith(mark) and not text.endswith(mark), key


def test_the_page_has_the_link_first_in_the_regional_panel_and_the_countries_on_its_rows(make_member, real_app):
    member = make_member()
    here = member.browser()
    html = here.get("/all", base_url="http://localhost", headers={"User-Agent": "ci-regions-hover"},
                    environ_base={"REMOTE_ADDR": f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"}).get_data(as_text=True)
    panel = html.split('id="sidebar-filter-regional"', 1)[1].split('id="sidebar-filter-language"', 1)[0]
    assert panel.index('class="regions-link"') < panel.index("Deselect all")
    assert 'href="/regions"' in panel
    assert len(re.findall(r'data-countries="[^"]+"', panel)) == len(region_helpers().REGION_COUNTRIES)          # every region, once
    # the language panel has neither
    language = html.split('id="sidebar-filter-language"', 1)[1].split('id="sidebar-filter-categorical"', 1)[0]
    assert "regions-link" not in language and "data-countries" not in language
