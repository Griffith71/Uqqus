"""The site's colour is VS Code's Light Blue, #23a9f2 (the owner's choice, 2026-10-29; Telegram's #2aabee and VS Code's
Blue #0085d0 were tried on the way): the one default, the stored guild colours that followed the old defaults, and the
earlier blues that were written out by hand."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "ruqqus"


def read(*parts):
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def test_the_default_site_colour_is_vs_codes_light_blue():
    assert '(environ.get("SITE_COLOR") or "23a9f2").strip()' in read("ruqqus", "__main__.py")


def test_guilds_that_held_an_earlier_default_take_the_new_one():
    sql = read("scripts", "migrations", "2026-12-06_brand_blue_default.sql")
    assert "SET color = '23a9f2'" in sql and "lower(color) IN ('148fd5', '2aabee', '0085d0')" in sql
    assert "color_nonce = COALESCE(color_nonce, 0) + 1" in sql        # the cached guild stylesheets are fetched again
    assert "('2026-12-06_brand_blue_default.sql')" in read("schema.sql")
    assert not (ROOT / "scripts" / "migrations" / "2026-12-06_telegram_blue_default.sql").exists()


def test_no_template_or_icon_config_still_writes_an_earlier_blue_by_hand():
    # the old Ruqqus blue and its gradient, the old default, X's blue, and every Telegram blue tried the day before
    old = re.compile(r"2ca1ea|148fd5|1d9bf0|168eea|91deff|1a7fba|2aabee|229ed9|37aee2|1e96c8|a8e2fb|0c73a7", re.I)
    allowed = {"help/press.html"}      # its sentence is a translated msgid in 45 catalogues: changed with a catalogue run
    for path in (APP / "templates").rglob("*.html"):
        if path.relative_to(APP / "templates").as_posix() in allowed:
            continue
        assert not old.search(path.read_text(encoding="utf-8")), path
    for path in (APP / "assets" / "images" / "favicons").glob("*"):
        if path.suffix in (".xml", ".html"):
            assert not old.search(path.read_text(encoding="utf-8")), path


def test_the_sheets_carry_the_three_blues():
    for sheet in ("main.scss", "main_dark.scss"):
        css = read("ruqqus", "assets", "style", sheet)
        assert "$upvote: #23A9F2;" in css and "$info: #0076B8;" in css, sheet           # Light Blue, Dark Blue
        for gone in ("rgba(60, 173, 243", "rgba(99, 80, 205", "#2aabee", "#229ED9"):
            assert gone not in css, (sheet, gone)
    for page in ("call.html", "space.html"):
        assert "--accent: #23a9f2;" in read("ruqqus", "templates", page), page
    assert "--accent-fill" not in read("ruqqus", "templates", "space.html")
    assert "#23a9f2, #23a9f2, #0085d0, #23a9f2" in read("ruqqus", "templates", "partials", "logo_style.html")        # the mark's gradient: one copy, every bar includes it
    for icon_config in ("templates/default.html", "templates/errors/502.html", "assets/images/favicons/browserconfig.xml"):
        assert "#23a9f2" in read("ruqqus", *icon_config.split("/")), icon_config


def test_fills_under_a_white_label_use_the_brand_fill_and_text_keeps_the_deep_shade():
    style = APP / "assets" / "style"
    components = (style / "_components.scss").read_text(encoding="utf-8")
    assert re.search(r"\.btn-primary,[^{]*\{\s*background-color: var\(--brand-fill\);", components)
    assert "background-color: color-mix(in srgb, var(--brand-fill) 86%, #000);" in components
    # nothing that is text takes the fill
    for f in style.glob("_*.scss"):
        for n, line in enumerate(f.read_text(encoding="utf-8").split("\n"), 1):
            if re.match(r"\s*color:", line):
                assert "--brand-fill" not in line, (f.name, n)
    tokens = (style / "_tokens.scss").read_text(encoding="utf-8")
    assert "@function fill-under-white" in tokens
    assert "$brand-fill: if($primary == $purple and contrast(#fff, $primary) >= 2.4, $primary, fill-under-white($primary));" in tokens


def test_older_rules_do_not_write_the_brand_variables_as_text_except_for_icons_and_the_logo():
    for sheet in ("main.scss", "main_dark.scss"):
        css = read("ruqqus", "assets", "style", sheet)
        assert "$primary-text" in css and "$purple-text" in css, sheet
        lines = css.split("\n")
        raw = [i for i, line in enumerate(lines) if re.match(r"\s*color:\s*\$(primary|purple)(?![\w-])", line)]
        for i in raw:
            j = i
            while not lines[j].rstrip().endswith("{"):
                j -= 1
            selector = lines[j].strip()
            assert re.search(r"\.fa\b|\.fas\b|\.far\b|\.fab\b|-icon|-avatar|> i|^i \{|navbar-brand|composer-tool|comment-collapse|fa-arrow", selector + " " + lines[j - 1]), (sheet, i + 1, selector)


def test_the_two_factor_code_is_drawn_dark_enough_to_scan():
    assert "qr_colour.on_white" in read("ruqqus", "routes", "users.py")


# the discs behind a white glyph: not text, so they keep the bright brand colour (3:1 is enough)
ICON_DISCS = (".story-add", ".circle-mark", ".studio-card-icon", ".explore-avatar-icon")


def test_no_older_rule_puts_a_white_label_on_the_raw_brand_colour():
    bg = re.compile(r"^\s*background(-color)?:\s*\$(primary|purple)\b")
    white = re.compile(r"^\s*color:\s*(#fff|#ffffff|white|\$white)\b", re.I)
    for sheet in ("main.scss", "main_dark.scss"):
        lines = read("ruqqus", "assets", "style", sheet).split("\n")
        for i, line in enumerate(lines):
            if not bg.match(line):
                continue
            j = i
            while not lines[j].rstrip().endswith("{"):
                j -= 1
            depth, k = 0, j
            while k < len(lines):
                depth += lines[k].count("{") - lines[k].count("}")
                if depth <= 0:
                    break
                k += 1
            if any(white.match(b) for b in lines[j:k + 1]):
                assert lines[j].strip().startswith(ICON_DISCS), (sheet, i + 1, lines[j].strip())


def test_bootstraps_own_brand_fills_sit_on_the_aa_fill_too():
    components = read("ruqqus", "assets", "style", "_components.scss")
    assert ".bg-primary, .bg-purple {\n    background-color: var(--brand-deep) !important;" in components      # running white text: the AA shade
    for token in (".badge-primary, .badge-purple", ".list-group-item.active", ".page-item.active .page-link",
                  ".btn-outline-primary:not(:disabled):not(.disabled):active"):
        assert token in components, token
    assert 'accent-color: var(--brand-fill);' in components


def test_the_docs_footer_text_is_not_dimmed_by_an_alpha():
    for sheet in ("main.scss", "main_dark.scss"):
        css = read("ruqqus", "assets", "style", sheet)
        assert "#page .footer a, #article .footer a {\n    color: $white;\n}" in css, sheet
    assert "opacity-50" not in read("ruqqus", "templates", "footer_article.html")


def test_the_email_links_have_a_dark_mode_colour_that_reads_on_the_dark_page():
    mail = read("ruqqus", "templates", "email", "default.html")
    assert "a {\n        color: #23a9f2 !important;" in mail      # the Light Blue on #121213 (the Dark Blue is 3.8:1 there)
    assert "color: #0076b8!important;" in mail         # light mode: the Dark Blue, AA on white
    assert "background-color: #23a9f2;" in mail        # the button: the brand, as the site's own


def test_the_icon_discs_are_the_brand_itself_in_both_themes_not_the_link_shade():
    components = read("ruqqus", "assets", "style", "_components.scss")
    assert ".fa-stack-2x.fa-circle.text-purple, .fa-stack-2x.fa-circle.text-primary {\n    color: var(--brand) !important;" in components
    assert components.index(".text-purple, .text-primary, .text-info {") < components.index(".fa-stack-2x.fa-circle.text-purple")


def test_a_pressed_dropdown_item_keeps_the_sites_own_tint_and_the_touch_rule_spares_disabled_buttons():
    components = read("ruqqus", "assets", "style", "_components.scss")
    # Bootstrap's solid brand fill on .dropdown-item.active/:active put the page text on the bright blue (2.3:1 in dark)
    brand = components[components.index(".badge-primary, .badge-purple {"):]
    assert ".dropdown-item" not in brand[:brand.index("}")]
    assert ".dropdown-item:active {\n    color: var(--text);\n    background-color: var(--press);" in components
    assert ".badge-primary, .badge-purple {\n    background-color: var(--brand-deep);\n    color: var(--on-brand);" in components
    assert ".btn-outline-primary:not(:disabled):not(.disabled):hover, .btn-outline-purple:not(:disabled):not(.disabled):hover" in components


def test_switches_and_radios_are_the_brand_when_on_not_green():
    for sheet in ("main.scss", "main_dark.scss"):
        css = read("ruqqus", "assets", "style", sheet)
        assert ".custom-control-input:checked ~ .custom-control-label::before {\n    opacity: 1;\n    background-color: $brand-fill;" in css, sheet


def test_an_icon_beside_text_is_the_brand_itself_and_an_empty_site_colour_is_the_default():
    components = read("ruqqus", "assets", "style", "_components.scss")
    assert ".fas.text-purple:not(.fa-stack-1x)" in components and ".fas.text-primary:not(.fa-stack-1x)" in components
    assert 'app.config["SITE_COLOR"]=(environ.get("SITE_COLOR") or "23a9f2").strip()' in read("ruqqus", "__main__.py")
    for tile in ("settings_premium.html", "tip_modal.html"):
        assert 'fa-image fa-stack-1x' in read("ruqqus", "templates", tile) and "color: #0085d0!important;" in read("ruqqus", "templates", tile), tile


def test_a_surface_with_running_white_text_is_the_aa_deep_fill_and_a_control_label_is_the_bright_one():
    """An own chat bubble (.bg-primary), the docs footer, a table head, a badge and a sidebar box's title bar carry text
    to be read, not a short label: 2.6:1 is the owner's exception for a button, a pill and a tab only."""
    components = read("ruqqus", "assets", "style", "_components.scss")
    assert ".list-group-item.active {\n    background-color: var(--brand-deep);" in components
    assert ".page-item.active .page-link {\n    background-color: var(--brand-fill);" in components
    for sheet in ("main.scss", "main_dark.scss"):
        css = read("ruqqus", "assets", "style", sheet)
        for selector in (".sidebar .sidebar-section .title {", ".events-calendar-day.is-today .events-calendar-num {"):
            block = css[css.index(selector):]
            assert "$brand-deep" in block[:block.index("}")], (sheet, selector)
    tokens = read("ruqqus", "assets", "style", "_tokens.scss")
    assert "$brand-deep: fill-under-white($primary);" in tokens and "--brand-deep: #{$brand-deep};" in tokens


def test_the_left_sidebars_icons_are_plain_glyphs_in_the_text_colour_not_blue_discs():
    """The owner (2026-10-30, after Reddit): the brand blue is an accent, and a column of white glyphs on blue discs (the feeds, the filters,
    curations, Spaces and the folded rail) was too much of it. Plain glyphs in the text colour, in the discs' old box so nothing moves."""
    sidebar = read("ruqqus", "templates", "sidebar-left.html")
    assert "fa-stack-2x" not in sidebar and "fa-inverse" not in sidebar and "text-white" not in sidebar
    assert "text-purple fa-stack" not in sidebar
    # 22 rows' glyphs, and the two ellipsis glyphs of the "See all" row (the open sidebar's and the rail's tile: one macro, two shapes)
    assert sidebar.count('<span class="sidebar-icon"><i class="fas ') == 24 and sidebar.count('aria-hidden="true"></i></span>') >= 24
    nav = read("ruqqus", "assets", "style", "_nav.scss")
    box = nav.split("#sidebar-left .sidebar-icon {", 1)[1].split("}", 1)[0]
    assert "color: var(--text);" in box and "width: 2.5em;" in box and "height: 2em;" in box      # the disc's box: labels, rows and the rail stay put
    glyph = nav.split("#sidebar-left .guild-recommendations-item .sidebar-icon .fas {", 1)[1].split("}", 1)[0]
    assert "color: inherit;" in glyph and "margin-right: 0;" in glyph and "font-size: px2rem(18px);" in glyph
    # the older rule that greys and indents every .fas in these lists has less weight than the icon's own (an id beats it)
    assert ".guild-recommendations-list .guild-recommendations-item .fas" in read("ruqqus", "assets", "style", "main.scss")
    assert not re.search(r"var\(--brand|var\(--link|\$primary|\$purple|#[0-9a-fA-F]{3,8}\b", box + glyph)       # no brand colour, no hex
