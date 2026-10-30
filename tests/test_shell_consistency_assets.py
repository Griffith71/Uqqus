"""One look across every page shell (the owner, 2026-10-29: "make sure that all of the aesthetic changes have been made consistently across all pages of the
site as I've noticed for some pages they haven't"; CLAUDE.md "One look across the page shells").

What the audit found, each pinned here: the settings, Create post and guild settings pages (header.html) had no `#navbar-icon-row`, so the 36px targets and the
zero gap were never applied to their icons (28px targets at an uneven pitch); the help and legal pages, About and Donate each drew their own plain bar (white,
57px or 49px, not fixed, the site's name as text, "ruqqus" in lowercase on two of them) with an old purple photo under it; the forgot-password pages printed the
name in lowercase; sign-up loaded its stylesheet by a bare relative path with no content hash; two hero buttons carried a drop shadow; the email kept a
blue-tinted grey. The numbers are measured in a real browser by scripts/ui/shells.spec.ts (`npm run shells`)."""
import colorsys
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "ruqqus"
TEMPLATES = APP / "templates"
STYLE = APP / "assets" / "style"


def read(*parts):
    return APP.joinpath(*parts).read_text(encoding="utf-8")


NAV = read("assets", "style", "_nav.scss")

# the sign-in pages' own link row (transparent, over the photo) is not a bar
AUTH_LINK_ROWS = {"login.html", "login_2fa.html", "authforms.html", "sign_up.html"}


def templates():
    return sorted(TEMPLATES.rglob("*.html"))


def test_every_top_bar_is_the_one_navbar():
    """A `<nav class="navbar ...">` with no `id="navbar"` gets none of the 56px height, the hairline or the glass (all keyed on the id)."""
    for path in templates():
        if path.name in AUTH_LINK_ROWS:
            continue
        for match in re.finditer(r"<nav\b[^>]*class=\"[^\"]*\bnavbar\b[^\"]*\"[^>]*>", path.read_text(encoding="utf-8")):
            assert 'id="navbar"' in match.group(0), (path.relative_to(TEMPLATES).as_posix(), match.group(0)[:100])


def test_the_member_shells_draw_the_same_icon_row():
    """The 36px targets, the gap and the account's spacing in _nav.scss are written for `#navbar-icon-row`: header.html had no such id."""
    assert 'id="navbar-icon-row"' in read("templates", "default.html")
    header = read("templates", "header.html")
    row = re.search(r"<ul class=\"navbar-nav[^\"]*\"[^>]*id=\"navbar-icon-row\"", header)
    assert row and "d-md-flex" in row.group(0), "header.html's desktop icon row"


def test_help_legal_about_and_donate_draw_the_site_bar_and_nothing_of_their_own():
    bar = read("templates", "partials", "site_bar.html")
    assert 'id="navbar"' in bar and "fixed-top" in bar
    # the mark alone, the site's name as its accessible name (never written out, never lowercased), the one copy of the logo's style
    assert '<span class="brand-main" aria-hidden="true">ɸ</span>' in bar
    assert "aria-label=\"{{ 'SITE_NAME' | app_config }}\"" in bar and '{% include "partials/logo_style.html" %}' in bar
    assert "navbar_height.js" in bar                                   # the page below it starts from the bar's measured height
    assert "{{ site_bar(\"/help\", back=True) }}" in read("templates", "docs_header.html")
    for page in ("about.html", "donate.html"):
        text = read("templates", "help", page)
        assert '{{ site_bar("/") }}' in text and "navbar-brand" not in text and "<nav" not in text, page
    docs = read("templates", "docs_default.html")
    assert '{% include "docs_header.html" %}' in docs


def test_no_template_prints_the_name_written_out():
    """The site's name comes from SITE_NAME (the owner's rule): a template that prints it as text is wrong the day the name changes. Links to the old
    accounts and addresses, and the marketing pages' code samples, are not this."""
    for path in templates():
        for line in path.read_text(encoding="utf-8").splitlines():
            assert not re.search(r">\s*ruqqus\s*<", line), (path.relative_to(TEMPLATES).as_posix(), line.strip()[:100])


def test_the_pages_under_the_fixed_bars_start_below_them():
    for sheet in ("main.scss", "main_dark.scss"):
        css = (STYLE / sheet).read_text(encoding="utf-8")
        assert re.search(r"#article \{\s*padding-top: var\(--navbar-height, 56px\);", css), sheet      # help and legal
        assert re.search(r"#page \{\s*padding-top: var\(--navbar-height, 55px\);", css), sheet         # About and Donate


def test_the_site_bar_is_styled_from_the_tokens_and_never_shows_the_toggler_on_a_desktop():
    links = NAV.split("#navbar.site-bar .navbar-nav .nav-link {")[1].split("}")[0]
    assert "min-height: var(--control-h);" in links and "color: var(--text-muted);" in links
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", NAV.split("// --- the plain site bar")[1].split("// --- the left sidebar")[0])
    # the menu button is a 36px control on a phone, in the phone block only: a `display` here would beat Bootstrap's `display: none` from 768px
    toggler = NAV.split("#navbar .navbar-toggler {")[1].split("}")[0]
    assert "min-height: var(--control-h);" in toggler and "display" not in toggler
    phone = NAV.split("@media (max-width: 767.98px) {")[1]
    assert "#navbar .navbar-toggler {" in phone.split("// --- the left sidebar")[0] or "#navbar .navbar-toggler {" in phone


def test_every_page_loads_its_stylesheet_through_asset_url():
    """A content hash in the address is what lets the stylesheet be cached for a year and still change; a bare relative path (sign-up had one) has neither."""
    for path in templates():
        for line in path.read_text(encoding="utf-8").splitlines():
            if 'rel="stylesheet"' in line and "style/main" in line:
                assert "asset_url(" in line, (path.relative_to(TEMPLATES).as_posix(), line.strip()[:120])


def test_buttons_are_flat_on_the_marketing_pages_too():
    """The owner's rule (2026-10-29): buttons are flat fills. About and Donate's hero buttons carried `shadow-md`."""
    for path in templates():
        for match in re.finditer(r"class=\"([^\"]*\bbtn\b[^\"]*)\"", path.read_text(encoding="utf-8")):
            assert not re.search(r"(?<![\w-])shadow(-sm|-md|-lg)?(?![\w-])", match.group(1)), (path.relative_to(TEMPLATES).as_posix(), match.group(1))


def test_the_logo_style_has_one_copy():
    mark = read("templates", "partials", "logo_style.html")
    assert ".logo-gradient .brand-main {" in mark
    for path in templates():
        if path.name == "logo_style.html":
            continue
        assert ".logo-gradient .brand-main {" not in path.read_text(encoding="utf-8"), path.name


def test_the_email_greys_are_neutral():
    """The greys have no blue tint (the owner, 2026-10-28): the email kept Tailwind's #EDF2F7 for its page and its boxes."""
    email = read("templates", "email", "default.html")
    for value in set(re.findall(r"#([0-9a-fA-F]{6})\b", email)):
        r, g, b = (int(value[i:i + 2], 16) for i in (0, 2, 4))
        if max(r, g, b) - min(r, g, b) < 40:                                # a grey, not a colour
            assert abs(b - r) <= 8, f"#{value} is a tinted grey"


def test_the_docs_photo_is_the_brands_blue_not_the_old_purple():
    Image = pytest.importorskip("PIL.Image")
    ImageStat = pytest.importorskip("PIL.ImageStat")
    picture = Image.open(APP / "assets" / "images" / "jumbotron" / "docs_jumbotron_bg.jpg").convert("RGB")
    r, g, b = ImageStat.Stat(picture).mean
    hue = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)[0] * 360
    assert 185 <= hue <= 225 and b > r + 30, (round(hue), (round(r), round(g), round(b)))        # the old one was hue ~265, mean (88, 66, 129)


def test_the_proxys_error_page_names_the_mark_and_the_site_not_the_old_name():
    page = read("templates", "errors", "502.html")
    assert "ɸ" in page and "aria-label=\"{{ 'SITE_NAME' | app_config }}\"" in page and "ruqqus</a>" not in page
    assert "@media (prefers-color-scheme: dark)" in page                    # self-contained: no site stylesheet to carry the dark page
    page = read("templates", "errors", "503.html")
    assert '{% include "partials/logo_style.html" %}' in page and "theme_links(" in page and "font_faces()" in page
