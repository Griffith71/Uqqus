"""The left sidebar's state and rhythm (the owner, 2026-10-30): the selected feed is a light shade of the site's blue (it was Reddit's grey for a day)
with the blue bar kept, a filter that is narrowing the feed shows a ring in the site's blue round its symbol and the same wash over its row, rows are 40px with one heading and one gap
everywhere, and a long list stops at five rows with "See all". What these keep, without a database: that the ring is drawn from the
notices' own variables (so it exists exactly when its notice does, and never for a region nobody chose), that nothing in it can move the
layout or leak into the pinned icon rules, that every list is capped, and that it all uses messages the catalogues already have (a new
one would lower every language's share until it was translated). The numbers (40px, 8px, the ring's centre) are measured in a real browser
by scripts/ui/sidebar.spec.ts; the real app by tests/pg/test_sidebar.py."""
import re
from pathlib import Path

from babel.messages.pofile import read_po

ROOT = Path(__file__).resolve().parent.parent / "ruqqus"
SIDEBAR = (ROOT / "templates" / "sidebar-left.html").read_text(encoding="utf-8")
NAV = (ROOT / "assets" / "style" / "_nav.scss").read_text(encoding="utf-8")
LIGHT = (ROOT / "assets" / "style" / "_light.scss").read_text(encoding="utf-8")
TOKENS = (ROOT / "assets" / "style" / "_tokens.scss").read_text(encoding="utf-8")
USER = (ROOT / "classes" / "user.py").read_text(encoding="utf-8")
CATALOGUE = read_po((ROOT / "translations" / "fr" / "LC_MESSAGES" / "messages.po").open("rb"))

FILTERS = ("regional", "language", "categorical", "post_type", "advanced")


def rule(css, selector):
    """The body of the first rule that starts with exactly `selector {`."""
    return css.split(selector + " {", 1)[1].split("\n}", 1)[0]


# --- which filters are on --------------------------------------------------------------------------------------------------------

def test_a_ring_is_drawn_from_the_notices_own_variables():
    # home.html draws "Showing posts from..." from these, and only the three feed routes pass them: so a ring exists exactly when its
    # notice does (never on a guild or a profile, never for a choice only ticked in an open panel and not applied)
    head = SIDEBAR.split('<div class="col sidebar', 1)[0]
    for name, source in (("n_regional", "current_regions"), ("n_language", "current_languages"), ("n_categorical", "current_subcats"),
                         ("n_post_type", "post_type_names")):
        assert f"{{% set {name} = ({source} or [])|length %}}" in head, name
    assert "{% set n_advanced = feed_filter_count if (feed_filter_chips or feed_filter_notices) else 0 %}" in head
    for never in ("active_regions", "active_languages", "active_subcats", "active_post_types", "display_region"):
        assert never not in head, never                                # those are what is ticked in a panel, on every page


def test_each_filter_row_and_rail_symbol_carries_the_class_for_its_own_count():
    for name in FILTERS:
        n = f"n_{name}"
        assert SIDEBAR.count(f'{{% if {n} %}} class="is-filtering"{{% endif %}}') == 2, name      # the open row and its rail twin
    assert SIDEBAR.count('class="is-filtering"') == 10
    # the three feeds are not filters: they carry the selected state (aria-current), never the ring
    for feed in ('href="/all"', 'href="/for_you"', 'href="/following"'):
        for anchor in re.findall(r"<a " + re.escape(feed) + r"[^>]*>", SIDEBAR):
            assert "is-filtering" not in anchor, anchor


def test_the_state_is_said_in_words_too_with_messages_the_catalogues_have():
    # the three open rows that show nothing else say how many are chosen (the ring is only a picture); Post type already shows its
    # choice as text and Advanced its count as a badge
    for name in ("n_regional", "n_language", "n_categorical"):
        assert f'<span class="sr-only"> {{{{ ngettext("%(num)s selected", "%(num)s selected", {name}) }}}}</span>' in SIDEBAR, name
    # the rail has only the symbol: its name carries the state
    for name, label in (("n_regional", "Regional"), ("n_language", "Language"), ("n_categorical", "Categorical")):
        assert f"aria-label=\"{{{{ _('{label}') }}}}{{% if {name} %}} · {{{{ ngettext('%(num)s selected', '%(num)s selected', {name}) }}}}{{% endif %}}\"" in SIDEBAR, name
    assert "{% if n_post_type %} · {{ post_type_summary }}{% endif %}" in SIDEBAR
    assert "{% if n_advanced %} · {{ ngettext('%(num)s set', '%(num)s set', n_advanced) }}{% endif %}" in SIDEBAR


def test_only_messages_the_catalogues_already_have_are_used():
    found = []
    for quote, text in re.findall(r"\b_\(\s*([\"'])(.+?)\1\s*[,)]", SIDEBAR):
        found.append((None, text))
    for quote, text in re.findall(r"\bngettext\(\s*([\"'])(.+?)\1", SIDEBAR):
        found.append((None, text))
    for _quote, context, _quote2, text in re.findall(r"\bpgettext\(\s*([\"'])(.+?)\1\s*,\s*([\"'])(.+?)\3", SIDEBAR):
        found.append((context, text))
    assert len(found) > 30
    missing = [(c, t) for c, t in found if CATALOGUE.get(t, c) is None]
    assert not missing, missing


# --- the look ----------------------------------------------------------------------------------------------------------------------

def test_the_selected_feed_is_a_light_blue_wash_and_keeps_the_blue_bar():
    selected = rule(NAV, '#sidebar-left .guild-recommendations-item > a[aria-current="page"]')
    assert "background-color: var(--selected);" in selected and "brand" not in selected
    # the bar at the far left is the owner's to keep (_light.scss): the wash replaced only the row's tint
    bar = LIGHT.split('#sidebar-left .guild-recommendations-item > a[aria-current="page"]::before {', 1)[1].split("}", 1)[0]
    assert "inset-inline-start: 0;" in bar and "background: var(--glow);" in bar
    assert SIDEBAR.count('aria-current="page"') == 6                  # the feeds only, open and on the rail


def test_the_wash_is_the_sites_blue_tint_and_the_ring_the_blue_itself_in_both_themes():
    # the owner (2026-10-30): "a light shade of the system blue rather than the grey", and the ring "a light blue matching the site main
    # colour": --selected is the tint the site's pills and quotes already use (--brand-soft), --filter-on the brand colour as it is
    assert TOKENS.count("--selected: var(--brand-soft);") == 2 and TOKENS.count("--filter-on: var(--brand);") == 2
    assert "rgba(0, 0, 0" not in TOKENS.split("--selected:", 1)[1].split(";", 1)[0] and "--brand-deep" not in TOKENS.split("--filter-on:", 1)[1].split(";", 1)[0]
    # under more contrast the light theme's wash is stronger (20% is the share the link colour is derived against); the dark one is at that share
    contrast = TOKENS.split("@media (prefers-contrast: more) {", 1)[1].split("\n}\n", 1)[0]
    assert '@if $theme == "light"' in TOKENS and "--selected: #{rgba($primary, 0.2)};" in contrast


def test_the_search_box_in_a_filters_panel_is_shorter_than_the_row_it_opens_from():
    # the owner (2026-10-30): "quite high": the global field is 40px, the row it sits under is 40px. 32px is the ladder's --hit-min, which is
    # 44px under a finger; only the left sidebar's (the phone sheets and the curation form keep the field they had)
    box = rule(NAV, "#sidebar-left .filter-search")
    assert "height: var(--hit-min);" in box and "min-height: var(--hit-min);" in box
    macro = (ROOT / "templates" / "_sidebar_filter_macros.html").read_text(encoding="utf-8")
    assert macro.count("filter-search") == 1 and 'class="form-control form-control-sm filter-search mb-2"' in macro
    assert ".filter-search" not in (ROOT / "assets" / "style" / "main.scss").read_text(encoding="utf-8")


def test_a_highlighted_rows_label_takes_the_blue_as_well_as_the_wash():
    # the owner (2026-10-30): "make sure the font color changes when it's highlighted as well". The rows' own `.text-black` is !important, so
    # these are too; the selected feed keeps its semibold, a narrowing filter keeps its own weight (bold would widen the label)
    selected = rule(NAV, '#sidebar-left .guild-recommendations-item > a[aria-current="page"] .text-black')
    assert "color: var(--link) !important;" in selected and "font-weight: var(--weight-semibold) !important;" in selected
    filtering = rule(NAV, '#sidebar-left .guild-recommendations-item > a.is-filtering:not([aria-expanded="true"]) .text-black')
    assert "color: var(--link) !important;" in filtering and "font-weight" not in filtering
    assert "var(--text) !important" not in selected                                  # it was the plain text colour until then


def test_the_ring_is_a_pseudo_element_centred_without_a_transform_and_outside_the_pinned_icon_rules():
    ring = rule(NAV, '#sidebar-left .is-filtering:not([aria-expanded="true"]) .sidebar-icon::after')
    assert 'content: "";' in ring and "position: absolute;" in ring and "inset: 0;" in ring and "margin: auto;" in ring
    assert "width: px2rem(34px);" in ring and "height: px2rem(34px);" in ring
    assert "border: 2px solid var(--filter-on);" in ring and "border-radius: 50%;" in ring and "pointer-events: none;" in ring
    assert "translate" not in ring and "transform" not in ring       # the right-to-left mirror flips a translate's sign; auto margins need none
    # tests/test_brand_blue_assets.py pins the two .sidebar-icon rules to no colour: the ring is its own rule, not inside them
    assert "::after" not in rule(NAV, "#sidebar-left .sidebar-icon") and "border" not in rule(NAV, "#sidebar-left .sidebar-icon")
    assert "#sidebar-left .is-filtering .sidebar-icon {\n    position: relative;" in NAV
    forced = NAV.split("@media (forced-colors: active) {", 1)[1].split("\n}\n", 1)[0]
    assert "border-color: Highlight;" in forced and "::after" in forced


def test_a_row_whose_panel_is_open_stays_plain_and_its_hover_still_works():
    # while the panel is open the ticks are the state; `:not([aria-expanded="true"])` rather than a second rule, so the hover tint
    # (less specific than an extra rule would be) is not lost on an open filtering row
    wash = rule(NAV, '#sidebar-left .guild-recommendations-item > a.is-filtering:not([aria-expanded="true"])')
    assert "background-color: var(--selected);" in wash
    assert NAV.count('.is-filtering:not([aria-expanded="true"])') == 4                # the wash, the ring, its forced-colours twin and the blue label


def test_the_keyboard_ring_stays_inside_the_row():
    focus = rule(NAV, "body:not(.using-mouse) #sidebar-left .guild-recommendations-item > a:focus-visible")
    assert "outline-offset: -2px;" in focus


def test_rows_are_forty_pixels_on_the_control_ladder_and_a_finger_makes_them_forty_four():
    row = rule(NAV, "#sidebar-left .guild-recommendations-item > a")
    assert 'min-height: #{"max(var(--control-lg), var(--hit-min))"};' in row and "border-radius: var(--radius-sm);" in row
    assert "padding: 6px 12px;" in row and "margin: 2px -12px;" in row          # the highlight keeps its reach to the sidebar's far left
    assert "@media (pointer: coarse) {\n    #sidebar-left .guild-recommendations-item > a {\n        padding-block: 8px;" in NAV


def test_one_heading_one_gap():
    head = rule(NAV, "#sidebar-left .sidebar-head")
    assert "min-height: var(--control-sm);" in head and "margin-bottom: var(--space-3);" in head
    assert "margin-bottom: var(--space-5);" in rule(NAV, "#sidebar-left .sidebar-block")
    # the eight hand-built headings, the rule between the first two sections and the stray margins are gone
    assert "letter-spacing: 0.025rem" not in SIDEBAR and "<hr" not in SIDEBAR
    assert not re.search(r'class="[^"]*\b(mb-4|mt-4)\b', SIDEBAR)
    assert SIDEBAR.count('class="sidebar-head"') >= 7
    # `.sidebar-head` is a flex row: on an element that also folds away on the rail its `display` would beat the fold (an id outweighs it)
    for tag in re.findall(r"<[a-z]+ [^>]*>", SIDEBAR):
        classes = re.search(r'class="([^"]*)"', tag)
        if classes and "sidebar-head" in classes.group(1).split():
            assert "sidebar-collapsed-hidden" not in classes.group(1) and "sidebar-collapsed-visible" not in classes.group(1), tag
    assert "#sidebar-left .guild-recommendations-list .feed-defaults {\n    margin-top: var(--space-3);" in NAV      # it lost to the rows' margin-top: 0


def test_the_rails_save_button_is_a_plain_glyph_so_a_circle_means_one_thing():
    button = rule(NAV, "#sidebar-left .feed-defaults-rail-btn")
    assert "border: 0;" in button and "border-radius: var(--radius-sm);" in button and "50%" not in button


def test_guild_and_curation_rows_have_the_icon_box_and_a_label_that_is_cut_not_wrapped():
    assert SIDEBAR.count('<span class="sidebar-icon"><img alt="" src="{{ board.profile_url }}" class="profile-pic profile-pic-28"></span>') == 2
    # a member-written name says its own direction: the ellipsis cuts its end, in the name's direction and not the interface's (an Arabic
    # name in an English page showed its tail, and an English one in an Arabic page lost its start and its "+")
    assert SIDEBAR.count('class="my-auto ml-2 text-black font-weight-normal sidebar-row-label" dir="auto" title="+{{ board.name }}"') == 2
    assert SIDEBAR.count('sidebar-row-label" dir="auto" title="{{ curation.name }}"') == 2
    assert "profile-pic-28 ml-1 mr-2" not in SIDEBAR and "profile-pic-28 mr-2" not in SIDEBAR      # the avatars' own margins put every label at its own x


# --- the lists -----------------------------------------------------------------------------------------------------------------------

def test_every_list_stops_at_five_and_none_is_unbounded():
    assert "{% set cap = 5 %}" in SIDEBAR
    assert "[0:10]" not in SIDEBAR
    # the seven places a list is cut: guilds (open, rail), guildmaster of (open, rail), owned and followed curations, the rail's curations
    assert SIDEBAR.count("[0:cap]") == 7
    assert not re.search(r"for \w+ in (v\.|modded\b|subscribed\b|owned\b|followed\b)[\w.]*\s*%\}", SIDEBAR)      # every loop is sliced
    assert "View more" not in SIDEBAR                                    # the always-there button went with the cap


def test_see_all_appears_only_over_the_cap_has_no_number_and_leads_to_the_whole_list():
    macro = SIDEBAR.split("{% macro see_all(", 1)[1].split("{% endmacro %}", 1)[0]
    assert "{% if total > cap %}" in macro
    assert "{{ total" not in macro and "{{ cap" not in macro               # no count: /mine/guilds lists moderated and contributed guilds too
    assert macro.count('_("See all")') == 1 and macro.count("_('See all')") == 4
    assert SIDEBAR.count("see_all('/mine/guilds'") == 4 and SIDEBAR.count("see_all('/curations'") == 3
    assert "rail=true" in SIDEBAR and "label=_('My guilds')" in SIDEBAR and "label=_('Guildmaster of')" in SIDEBAR
    # every call names its list, open or on the rail: three links all called "See all" told a screen reader nothing of where each went
    calls = re.findall(r"\{\{ see_all\([^}]*\) \}\}", SIDEBAR)
    assert len(calls) == 7 and all("label=_(" in c for c in calls), calls
    assert macro.count('aria-label="{{ label }} · {{ _(\'See all\') }}"') == 2         # the open link and the rail's tile
    # the curations' two lists share ONE row at the end, in the last list that is drawn
    assert "{% if not followed %}{{ see_all('/curations', most, cap, label=_('My Curations')) }}{% endif %}" in SIDEBAR
    assert "{% set most = [owned|length, followed|length]|max %}" in SIDEBAR


def test_the_lists_are_in_an_order_a_member_can_predict():
    subscribed = USER.split("def boards_subscribed(self):", 1)[1].split("@property", 1)[0]
    assert "sorted(boards, key=lambda x: x.name.lower())" in subscribed
    modded = USER.split("def boards_modded(self):", 1)[1].split("@property", 1)[0]
    assert "sorted(z, key=lambda x: x.name.lower())" in modded           # capitals do not sort before every lowercase name, in any of the guild lists
    followed = USER.split("def curations_followed(self):", 1)[1].split("@property", 1)[0]
    assert "order_by(func.lower(Curation.name), Curation.id)" in followed


def test_the_rail_names_its_links_and_the_spaces_tooltip_has_balanced_quotes():
    rail = SIDEBAR.split("<!-- Show this list when sidebar is collapsed-->", 1)[1].split("</ul>", 1)[0]
    for feed in ("/all", "/for_you", "/following"):
        assert re.search(r'<a href="' + feed + r'"[^>]*aria-label="[^"]+" title="[^"]+">', rail), feed
    assert "aria-label=\"{{ curation.name }}\" title=\"{{ curation.name }}\"" in SIDEBAR
    assert "' aria-label='" not in SIDEBAR
    assert "title=\"{{ _('Spaces') }}\" aria-label=\"{{ _('Spaces') }}\"" in SIDEBAR


# --- found by the independent review (2026-10-30) -------------------------------------------------------------------------------------

def test_the_folded_rails_expand_button_has_a_name():
    # axe: button-name, critical. It is the only control that unfolds the rail, and held a glyph drawn by CSS and nothing else
    button = SIDEBAR.split('onclick="toggle_sidebar_expand();"', 1)[0].rsplit("<button", 1)[1]
    assert "aria-label=\"{{ _('%(site)s Feeds') }}\"" in button and 'aria-expanded="false"' in button


def test_the_guild_blocks_follow_the_lists_drawn_not_the_properties_that_count_banned_guilds():
    # subscribed_to_anything and mods_anything count a banned guild; boards_subscribed and boards_modded leave it out: a member whose only
    # guilds were banned got a heading (and a Mod Queue link) with no row under it, and not the invitation to join one
    head = SIDEBAR.split('<div class="col sidebar', 1)[0]
    assert "{% set subscribed = v.boards_subscribed if v else [] %}" in head and "{% set modded = v.boards_modded if v else [] %}" in head
    assert "{% if subscribed %}" in SIDEBAR and "{% elif v %}" in SIDEBAR and "{% if modded %}" in SIDEBAR
    assert "v.subscribed_to_anything" not in SIDEBAR and "v.mods_anything" not in SIDEBAR


def test_the_fold_button_is_on_the_ladder_so_the_first_heading_is_28px_in_every_mode():
    button = rule(NAV, "#sidebar-left .sidebar-head .btn")
    assert "height: var(--control-h);" in button and "min-height: 0;" in button and "padding-block: 0;" in button
    assert "margin-block: calc((var(--control-sm) - var(--control-h)) / 2);" in button
    reach = NAV.split("@media (pointer: coarse) {\n    #sidebar-left .sidebar-head .btn::after {", 1)[1].split("}", 1)[0]
    assert 'content: "";' in reach and "inset: calc((var(--control-h) - var(--hit-min)) / 2 - 1px) 0;" in reach      # 44px under a finger, as the nav icons


def test_the_rails_rows_have_no_side_padding_so_its_icons_stay_on_the_centre_line():
    rail = rule(NAV, "#sidebar-left .sidebar-collapsed-visible .guild-recommendations-item > a")
    assert "padding-inline: 0;" in rail


def test_the_rails_save_button_is_centred_and_shows_its_saved_state():
    button = rule(NAV, "#sidebar-left .feed-defaults-rail-btn")
    assert "width: 100%;" in button and "max-width: var(--control-h);" in button
    glyph = rule(NAV, "#sidebar-left .feed-defaults-rail-btn i")
    assert "margin-right: 0;" in glyph and "color: inherit;" in glyph          # the legacy rule that margins and greys every .fa in these lists
    assert ".guild-recommendations-list .guild-recommendations-item .fas" in (ROOT / "assets" / "style" / "main.scss").read_text(encoding="utf-8")


def test_the_uppercase_labels_are_all_one_size():
    # the owner (2026-10-30): the headings (12px) and the Post type group labels (9.6px) read as different sizes: "make them all that size", the headings'
    assert "font-size" not in rule(NAV, "#sidebar-left .sidebar-head-title")                          # they keep `.text-small` as the markup has it
    assert "font-size: $small-font-size;" in rule(NAV, "#sidebar-left .post-type-group")
    # the sheet draws the same labels with the same macro, and the legacy rule left them at 9.6px there (review 2026-10-30)
    assert "font-size: $small-font-size;" in rule(NAV, "#postTypeFilterModal .post-type-group")
    assert "text-small" in SIDEBAR.split('class="sidebar-head-title', 1)[1].split('"', 1)[0]


def test_the_gap_under_an_open_panel_holds_while_it_opens_and_closes_too():
    # Bootstrap swaps .collapse for .collapsing during the 350ms height animation: a margin on .collapse alone pops at both ends (review 2026-10-30)
    css = NAV.split("#sidebar-left .guild-recommendations-item > .collapse,", 1)[1].split("\n}", 1)[0]
    assert "#sidebar-left .guild-recommendations-item > .collapsing" in css and "margin-bottom: .5rem;" in css
