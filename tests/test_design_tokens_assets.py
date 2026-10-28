"""Design tokens (assets/style/_tokens.scss, the UI modernisation of 2026-10): both theme sheets carry them, and the
colours they give meet WCAG AA where they are used: text 4.5:1 on the surfaces it sits on, white on the brand fill.
The owner chose AA over keeping the old blue and greys for text (2026-10-23); the brand colour itself stays for
icons, fills and the focus ring. Checked on the compiled CSS, with the site's colour and with guild colours."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent / "ruqqus"
SITE_COLOUR = "148fd5"
# guild colours that are hard for a derived text colour: very light, very dark, saturated warm ones
GUILD_COLOURS = ("ffd60a", "fcd5ce", "1a1a2e", "e85d04", "2c7be5", "805ad5", "07c20c", "ffffff", "000000")


def compile_sheet(name, colour):
    sass = pytest.importorskip("sass")
    scss = (ROOT / "assets" / "style" / name).read_text(encoding="utf-8")
    scss = scss.replace("{boardcolor}", colour).replace("{maincolor}", SITE_COLOUR).replace("{ruqquspath}", str(ROOT))
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


def named(value):
    value = value.strip()
    return {"#fff": "#ffffff", "white": "#ffffff", "#000": "#000000"}.get(value, value)


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
    assert contrast(named(t["--on-brand"]), named(t["--brand-strong"])) >= 4.5, name


@pytest.mark.parametrize("name", ["main.scss", "main_dark.scss"])
@pytest.mark.parametrize("colour", GUILD_COLOURS)
def test_a_guild_colour_still_gives_readable_links_and_buttons(name, colour):
    t = tokens(compile_sheet(name, colour))
    assert t["--brand"].strip() == f"#{colour}" or t["--brand"].strip() in ("white", "black"), t["--brand"]
    for surface in ("--surface", "--bg"):
        ratio = contrast(named(t["--link"]), named(t[surface]))
        assert ratio >= 4.5, (name, colour, surface, round(ratio, 2))
    ratio = contrast(named(t["--on-brand"]), named(t["--brand-strong"]))
    assert ratio >= 4.5, (name, colour, round(ratio, 2))


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
