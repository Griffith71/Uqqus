"""Design tokens (assets/style/_tokens.scss, the UI modernisation of 2026-10): both theme sheets carry them, and the
colours they give meet WCAG AA where they are used: text 4.5:1 on the surfaces it sits on, white on the brand fill.
The owner chose AA over keeping the old blue and greys for text (2026-10-23); the brand colour itself stays for
icons, fills and the focus ring. The site's colour is VS Code's Light Blue, #23a9f2 (the owner's choice, 2026-10-29), and
the one exception, the owner's: it is the fill of its buttons as it is (--brand-fill, 2.6:1 under a white label); a
guild's colour, or a site colour too pale for white, is darkened until the label holds AA. Links and text stay AA.
Checked on the compiled CSS, with the site's colour and with guild colours."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent / "ruqqus"
SITE_COLOUR = "23a9f2"
# guild colours that are hard for a derived text colour: very light, very dark, saturated warm ones
GUILD_COLOURS = ("ffd60a", "fcd5ce", "1a1a2e", "e85d04", "2c7be5", "805ad5", "07c20c", "ffffff", "000000")


def compile_sheet(name, colour, site=SITE_COLOUR):
    sass = pytest.importorskip("sass")
    scss = (ROOT / "assets" / "style" / name).read_text(encoding="utf-8")
    scss = scss.replace("{boardcolor}", colour).replace("{maincolor}", site).replace("{ruqquspath}", str(ROOT))
    return sass.compile(string=scss)


def tokens(css):
    """The custom properties of the first :root block that holds the tokens."""
    start = css.index(":root {\n  --font-sans")
    block = css[start:css.index("}", start)]
    return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", block))


def luminance(hex_colour):
    h = hex_colour.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    channels = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# the names libsass writes in place of a hex where the name is shorter
NAMES = {"white": "#ffffff", "black": "#000000", "red": "#ff0000", "blue": "#0000ff", "lime": "#00ff00",
         "yellow": "#ffff00", "aqua": "#00ffff", "fuchsia": "#ff00ff", "gray": "#808080", "green": "#008000",
         "maroon": "#800000", "navy": "#000080", "olive": "#808000", "purple": "#800080", "silver": "#c0c0c0",
         "teal": "#008080", "#fff": "#ffffff", "#000": "#000000"}


def named(value):
    value = value.strip()
    return NAMES.get(value, value)


TEXT_ON = {
    "--text": ("--surface", "--bg", "--surface-raised", "--surface-sunken"),
    "--text-muted": ("--surface", "--bg", "--surface-raised", "--panel"),     # (the sidebar boxes' headings sit on the panel)
    "--link": ("--surface", "--bg", "--surface-raised", "--panel"),
    "--upvote-text": ("--surface", "--bg"),
    "--downvote-text": ("--surface", "--bg"),
    "--danger": ("--surface", "--bg"),
    "--success": ("--surface", "--bg"),
}


@pytest.mark.parametrize("name", ["main.scss", "main_dark.scss"])
def test_text_tokens_meet_aa_on_their_surfaces(name):
    t = tokens(compile_sheet(name, SITE_COLOUR))
    for fg, surfaces in TEXT_ON.items():
        for surface in surfaces:
            ratio = contrast(named(t[fg]), named(t[surface]))
            assert ratio >= 4.5, (name, fg, surface, round(ratio, 2))


@pytest.mark.parametrize("name", ["main.scss", "main_dark.scss"])
def test_the_sites_own_colour_is_its_button_fill_as_it_is_in_both_themes(name):
    t = tokens(compile_sheet(name, SITE_COLOUR))
    assert t["--brand"].strip() == f"#{SITE_COLOUR}", t["--brand"]
    assert t["--brand-fill"].strip() == t["--brand"].strip()
    # the owner's exception to AA: a white label on the bright blue is not 4.5:1, but it must stay readable
    assert contrast(named(t["--on-brand"]), named(t["--brand-fill"])) >= 2.4, name
    # a surface with running white text on it (a chat bubble, the footer) is the AA shade, the site's own colour included
    deep = named(t["--brand-deep"])
    assert contrast(named(t["--on-brand"]), deep) >= 4.5 and deep != named(t["--brand"]), (name, deep)
    # the text and links on the page are not the bright blue in the light theme (2.3:1), they stay AA
    assert contrast(named(t["--link"]), named(t["--surface"])) >= 4.5, name


def test_the_link_is_the_bright_blue_itself_in_the_dark_theme():
    t = tokens(compile_sheet("main_dark.scss", SITE_COLOUR))
    assert named(t["--link"]) == f"#{SITE_COLOUR}", t["--link"]


@pytest.mark.parametrize("name", ["main.scss", "main_dark.scss"])
def test_a_site_colour_too_pale_for_a_white_label_falls_back_to_the_aa_fill(name):
    t = tokens(compile_sheet(name, "fcd5ce", site="fcd5ce"))
    assert t["--brand-fill"].strip() != "#fcd5ce"
    assert contrast(named(t["--on-brand"]), named(t["--brand-fill"])) >= 4.5, name


@pytest.mark.parametrize("name", ["main.scss", "main_dark.scss"])
@pytest.mark.parametrize("colour", GUILD_COLOURS)
def test_a_guild_colour_still_gives_readable_links_and_buttons(name, colour):
    t = tokens(compile_sheet(name, colour))
    assert t["--brand"].strip() == f"#{colour}" or t["--brand"].strip() in ("white", "black"), t["--brand"]
    for surface in ("--surface", "--bg"):
        ratio = contrast(named(t["--link"]), named(t[surface]))
        assert ratio >= 4.5, (name, colour, surface, round(ratio, 2))
    for token in ("--brand-fill", "--brand-deep"):
        ratio = contrast(named(t["--on-brand"]), named(t[token]))
        assert ratio >= 4.5, (name, colour, token, round(ratio, 2))


def test_both_sheets_import_the_tokens_after_bootstrap():
    for name, theme in (("main.scss", "light"), ("main_dark.scss", "dark")):
        text = (ROOT / "assets" / "style" / name).read_text(encoding="utf-8")
        assert f'$theme: "{theme}";' in text.split("@import")[0], name
        bootstrap = text.index('@import "{ruqquspath}/assets/style/node_modules/bootstrap/scss/bootstrap.scss";')
        assert text.index('@import "{ruqquspath}/assets/style/_tokens";') > bootstrap, name


def test_density_and_reduced_motion_change_the_tokens_not_every_rule():
    css = compile_sheet("main.scss", SITE_COLOUR)
    assert re.search(r'html\[data-density="compact"\]\s*\{[^}]*--card-pad:\s*10px', css)
    reduce = css.split("@media (prefers-reduced-motion: reduce)")
    assert any(re.search(r":root\s*\{\s*--dur-fast:\s*0ms;\s*--dur-base:\s*0ms;\s*--dur-sheet:\s*0ms;", part)
               for part in reduce[1:])


SHELLS = ("default", "media_connected", "login", "call", "submit", "authforms", "login_2fa", "settings", "sign_up",
          "docs_default", "space", "embeds/embed_default", "help/about", "help/donate", "guild/guild_settings")


def test_every_page_shell_names_the_density_before_the_first_paint():
    theme = (ROOT / "templates" / "partials" / "theme.html").read_text(encoding="utf-8")
    assert "{% macro html_attrs() %}" in theme and "request.cookies.get('density') == 'compact'" in theme
    # the interface language and its direction come with it (helpers/i18n_runtime.py)
    assert 'lang="{{ ui_lang() }}" dir="{{ ui_dir() }}"' in theme
    for name in SHELLS:
        page = (ROOT / "templates" / f"{name}.html").read_text(encoding="utf-8")
        assert '<html{{ html_attrs() }}' in page and '<html lang="en"' not in page, name
        assert "html_attrs" in page.split("<html")[0], name    # imported before it is used
    # every template with its own <html> is a shell above, an email, or an error page served when the app is down
    for path in (ROOT / "templates").rglob("*.html"):
        rel = path.relative_to(ROOT / "templates").with_suffix("").as_posix()
        if "<html lang" in path.read_text(encoding="utf-8") and not rel.startswith(("email/", "errors/50")):
            assert rel in SHELLS, rel


def compile_tokens(theme, colour, site=None):
    """Just the tokens partial, for any colour: quick enough to try a grid of them. `site` is the site's own colour
    ($purple): the page's colour is a guild's unless it is the same."""
    sass = pytest.importorskip("sass")
    site = site or ("23a9f2" if colour != "23a9f2" else "000000")
    src = (f'$theme: "{theme}"; $primary: #{colour}; $purple: #{site}; $upvote: #23a9f2; $downvote: #e8452c;\n'
           f'@import "{ROOT}/assets/style/_tokens";')
    return tokens(sass.compile(string=src))


def mixed(top, bottom, share):
    """`top` laid over `bottom` at `share` (0-1): a tint of a colour on a surface."""
    a = [int(top.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    b = [int(bottom.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    return "#" + "".join(f"{round(x * share + y * (1 - share)):02x}" for x, y in zip(a, b))


GRID = [f"{r:02x}{g:02x}{b:02x}" for r in range(0, 256, 51) for g in range(0, 256, 51) for b in range(0, 256, 51)]


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_fill_under_a_white_label_is_aa_for_any_colour_after_the_css_rounds_it(theme):
    for colour in GRID + ["0085d0", "2aabee", "148fd5", "ffd60a", "ffffff", "000000"]:
        t = compile_tokens(theme, colour)
        for token in ("--brand-fill", "--brand-deep"):
            fill = named(t[token])
            assert contrast("#ffffff", fill) >= 4.5, (theme, colour, token, fill, round(contrast("#ffffff", fill), 3))


# pills, quotes and hovers are brand-tinted: the link and vote-score shades must read on their tint too
TINTS = {"light": [("--surface", 0.16), ("--panel", 0.16), ("--surface-sunken", 0.12)],
         "dark": [("--surface", 0.18), ("--surface-raised", 0.18), ("--panel", 0.18)]}


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("colour", (SITE_COLOUR,) + GUILD_COLOURS)
def test_a_link_reads_on_its_own_tint(theme, colour):
    t = compile_tokens(theme, colour)
    raw = named(t["--brand"])
    for surface, share in TINTS[theme]:
        tint = mixed(raw, named(t[surface]), share)
        ratio = contrast(named(t["--link"]), tint)
        assert ratio >= 4.5, (theme, colour, surface, tint, round(ratio, 2))


# the left sidebar's wash (a chosen row: the selected feed, a filter that is narrowing the feed) is a light shade of the site's blue and the ring
# round a narrowing filter's symbol is the blue itself (the owner, 2026-10-30). Only on the feeds, which are the site's own pages, so only the site's
# colour is drawn. The ring is a graphic: #23a9f2 is 2.6:1 on white, the exception the site colour already has on every graphic (CLAUDE.md, Colour
# contrast); the wash, the words "N selected" and the notice above the feed say the same thing. Dark is well over 3:1.
RING_FLOOR = {"light": (2.5, 2.2), "dark": (3.0, 3.0)}                      # on the page, on the wash


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_a_filters_ring_is_the_sites_blue_and_shows_on_the_page_and_on_the_wash(theme):
    t = compile_tokens(theme, SITE_COLOUR)
    assert t["--filter-on"] == "var(--brand)" and t["--selected"] == "var(--brand-soft)"
    ring = named(t["--brand"])
    assert ring == "#23a9f2"
    share = float(re.findall(r"[\d.]+", t["--brand-soft"])[3])
    on_page, on_wash = RING_FLOOR[theme]
    for surface in ("--bg", "--surface"):                                    # (the left sidebar is on the page itself, not on the grey --panel of the right one's boxes)
        page = named(t[surface])
        assert contrast(ring, page) >= on_page, (theme, surface, round(contrast(ring, page), 2))
        wash = mixed(ring, page, share)
        assert contrast(ring, wash) >= on_wash, (theme, surface, wash, round(contrast(ring, wash), 2))


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_text_on_the_blue_wash_stays_aa(theme):
    # the row labels, the muted values beside them and the link-coloured ones ("Polls", a ticked row) sit on the wash, in the default tint and,
    # in the light theme, the stronger one the more-contrast query asks for
    t = compile_tokens(theme, SITE_COLOUR)
    shares = [float(re.findall(r"[\d.]+", t["--brand-soft"])[3])] + ([0.2] if theme == "light" else [])
    for share in shares:
        for surface in ("--bg", "--surface", "--panel"):
            wash = mixed(named(t["--brand"]), named(t[surface]), share)
            for text in ("--text", "--text-muted", "--link"):
                assert contrast(named(t[text]), wash) >= 4.5, (theme, share, surface, text, wash, round(contrast(named(t[text]), wash), 2))
