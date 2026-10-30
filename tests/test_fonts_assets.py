"""The site's typeface is Noto Sans (the owner's choice, 2026-10-29; it was Inter): self-hosted variable fonts in the
subsets Noto Sans itself covers, a fallback measured against Arial, the system fonts after it, and nothing fetched from
another site. The scripts Noto Sans doesn't draw have Noto families of their own, self-hosted too (the owner, 2026-10-29:
Arabic, Urdu's Nastaliq, Hebrew, Bengali, Thai, Chinese, Japanese, Korean; helpers/script_fonts.py), loaded only on a
page that holds their script."""
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from ruqqus.helpers import script_fonts

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "ruqqus"
SUBSETS = ("latin", "latin-ext", "cyrillic", "cyrillic-ext", "greek", "greek-ext", "vietnamese", "devanagari")


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def test_every_subset_is_declared_and_its_file_is_a_real_woff2():
    theme = (APP / "templates" / "partials" / "theme.html").read_text(encoding="utf-8")
    macro = theme[theme.index("{% macro font_faces() %}"):theme.index("{% endmacro %}", theme.index("{% macro font_faces() %}"))]
    for subset in SUBSETS:
        assert f"asset_url('fonts/noto-sans-{subset}.woff2')" in macro, subset
        data = (APP / "assets" / "fonts" / f"noto-sans-{subset}.woff2").read_bytes()
        assert data[:4] == b"wOF2" and 5_000 < len(data) < 200_000, (subset, len(data))
    # the eight subsets, the logo's one glyph and the code font's Latin: each with its own range
    assert macro.count('font-family: "Noto Sans"; font-style: normal; font-weight: 100 900; font-display: swap;') == len(SUBSETS) + 1
    assert macro.count("unicode-range:") == len(SUBSETS) + 2
    # the first paint fetches only the Latin subset, ahead of the stylesheet
    assert '<link rel="preload" href="{{ asset_url(\'fonts/noto-sans-latin.woff2\') }}" as="font" type="font/woff2" crossorigin>' in macro


def test_the_logo_mark_and_the_code_have_noto_faces_of_their_own():
    """Noto Sans replaces every font (the owner, 2026-10-29): the logo's ɸ (U+0278, outside Latin, inside Latin Extended's 168 KB)
    has a 1.3 KB face declared AFTER Latin Extended, which wins an overlapping range, and code is Noto Sans Mono."""
    theme = read("ruqqus", "templates", "partials", "theme.html")
    macro = theme[theme.index("{% macro font_faces() %}"):theme.index("{% endmacro %}", theme.index("{% macro font_faces() %}"))]
    logo = "src: url(\"{{ asset_url('fonts/noto-sans-logo.woff2') }}\") format(\"woff2\");\n  unicode-range: U+0278; }"
    assert logo in macro and macro.index(logo) > macro.index("noto-sans-latin-ext.woff2")
    assert "asset_url('fonts/noto-sans-mono-latin.woff2')" in macro and 'font-family: "Noto Sans Mono"' in macro
    for name, low, high in (("noto-sans-logo.woff2", 500, 5_000), ("noto-sans-mono-latin.woff2", 5_000, 200_000)):
        data = (APP / "assets" / "fonts" / name).read_bytes()
        assert data[:4] == b"wOF2" and low < len(data) < high, name
    assert "SIL OPEN FONT LICENSE" in read("ruqqus", "assets", "fonts", "NotoSansMono-OFL.txt")
    for sheet in ("main.scss", "main_dark.scss"):
        css = read("ruqqus", "assets", "style", sheet)
        assert '$font-family-monospace: "Noto Sans Mono", ' in css and "font-family: monospace" not in css, sheet
        assert "font-family: $font-family-monospace;" in css
    # the navbar mark is drawn in Noto Sans, not the Impact it once was: one copy of its style (partials/logo_style.html), included by every bar that draws it
    mark = read("ruqqus", "templates", "partials", "logo_style.html")
    css = mark[mark.index(".logo-gradient .brand-main {"):][:400]
    assert 'font-family: "Noto Sans", "Noto Sans Fallback", sans-serif;' in css and "Impact" not in mark
    for shell in ("default.html", "header.html"):
        page = read("ruqqus", "templates", shell)
        assert '{% include "partials/logo_style.html" %}' in page and ".logo-gradient .brand-main {" not in page, shell
    assert '{% include "partials/logo_style.html" %}' in read("ruqqus", "templates", "partials", "site_bar.html")
    assert 'font-family:&quot;Noto Sans Mono&quot;,monospace' in read("ruqqus", "templates", "guild", "css.html")


def test_the_places_that_draw_their_own_text_name_noto_sans():
    """The email (a mail app that can't load web fonts shows its fallback, but asks for ours from the site's own address, not
    Google) and the Space recording's canvas (it draws names into the video)."""
    email = read("ruqqus", "templates", "email", "default.html")
    assert "fonts.googleapis" not in email and "@import" not in email and "Roboto" not in email
    assert "https://{{ 'SERVER_NAME' | app_config }}/assets/fonts/noto-sans-latin.woff2" in email
    assert '"Noto Sans", Helvetica, Arial, sans-serif' in email and '"Noto Sans", Arial, sans-serif' in email
    space = read("ruqqus", "assets", "chat_src", "src", "space.js")
    assert "const CANVAS_SANS = '\"Noto Sans\", \"Noto Sans Arabic\"," in space and "system-ui, sans-serif';" in space
    assert 'ctx.font = "600 40px " + CANVAS_SANS;' in space and 'ctx.font = "28px " + CANVAS_SANS;' in space
    assert "Helvetica" not in space and "Arial" not in space


def test_the_fallback_is_arial_stretched_to_noto_sans_so_the_swap_moves_nothing():
    theme = (APP / "templates" / "partials" / "theme.html").read_text(encoding="utf-8")
    m = re.search(r'@font-face \{ font-family: "Noto Sans Fallback"; src: local\("Arial"\);\s*'
                  r'ascent-override: ([\d.]+)%; descent-override: ([\d.]+)%; line-gap-override: 0%; size-adjust: ([\d.]+)%; \}', theme)
    assert m, "the fallback face is missing"
    ascent, descent, size = (float(x) for x in m.groups())
    # measured from the fonts: Noto Sans (1069 / -293 per 1000 em) against Arial's letter widths
    assert 105 < size < 108.5 and 99 < ascent < 101 and 26.5 < descent < 28.5, (ascent, descent, size)


def compiled_tokens():
    sass = pytest.importorskip("sass")
    scss = read("ruqqus", "assets", "style", "main.scss")
    scss = scss.replace("{boardcolor}", "805ad5").replace("{maincolor}", "805ad5").replace("{ruqquspath}", str(APP))
    return sass.compile(string=scss, output_style="expanded")


SCRIPT_FAMILIES = ["Noto Sans Arabic", "Noto Nastaliq Urdu", "Noto Sans Hebrew", "Noto Sans Bengali", "Noto Sans Thai",
                   "Noto Sans SC", "Noto Sans KR", "Noto Sans JP", "Noto Sans TC"]


def stack_of(css, selector):
    block = css[css.index(selector + " {"):]
    return [f.strip().strip('"') for f in re.search(r"--font-sans: ([^;]+);", block).group(1).split(",")]


def test_the_stacks_start_with_noto_sans_then_each_scripts_noto_family_then_the_system_fonts():
    tokens = read("ruqqus", "assets", "style", "_tokens.scss")
    css = compiled_tokens()
    default = stack_of(css, ":root")
    # Noto Sans, the Noto family of every other script (Han with no language: SC first), the Arial fallback, the system
    assert default[:len(SCRIPT_FAMILIES) + 2] == ["Noto Sans", *SCRIPT_FAMILIES, "Noto Sans Fallback"], default
    assert default[-4:] == ["sans-serif", "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"]
    # body and Bootstrap's $font-family-base read it, so a [lang] that redefines it reaches everything under it
    for sheet in ("main.scss", "main_dark.scss"):
        assert "$font-family-sans-serif: var(--font-sans);" in read("ruqqus", "assets", "style", sheet), sheet
    # every language: Noto Sans first (its Latin letters), its own Noto family, the fallback, then the rest
    own = {"ja": "Noto Sans JP", "zh": "Noto Sans SC", "ko": "Noto Sans KR", "ar": "Noto Sans Arabic", "fa": "Noto Sans Arabic",
           "ur": "Noto Nastaliq Urdu", "he": "Noto Sans Hebrew", "hi": "Noto Sans", "bn": "Noto Sans Bengali", "th": "Noto Sans Thai"}
    for lang, family in own.items():
        selector = next(sel for sel in re.findall(r"^(\[lang[^{]+) \{", css, re.M) if f'[lang|="{lang}"]' in sel)
        stack = stack_of(css, selector)
        assert stack[:3] == ["Noto Sans", family, "Noto Sans Fallback"], (lang, stack)
        assert set(SCRIPT_FAMILIES) <= set(stack), lang
        block = css[css.index(selector + " {"):]
        assert block[:block.index("}")].rstrip().endswith("font-family: var(--font-sans);"), lang
    # Traditional Chinese and Cantonese: TC, after the zh rule (which matches zh-Hant too)
    tc = '[lang|="zh-Hant"], [lang|="zh-TW"], [lang|="zh-HK"], [lang|="zh-MO"], [lang|="yue"]'
    assert stack_of(css, tc)[:2] == ["Noto Sans", "Noto Sans TC"] and css.index(tc) > css.index('[lang|="zh"] {')
    # Nastaliq needs taller lines than Noto Sans's: headings, running text and controls in Urdu; a control's height is
    # never set (the size ladder's min-height stays the floor, so it only grows)
    assert "h1:lang(ur)" in tokens and "p:lang(ur), li:lang(ur), blockquote:lang(ur) {\n    line-height: 1.9;" in tokens
    controls = tokens[tokens.index(".btn:lang(ur)"):]
    controls = controls[:controls.index("}")]
    assert "line-height: 2.1;" in controls and "height:" not in controls.replace("line-height", "")
    assert '"Inter"' not in tokens
    for sheet in ("main.scss", "main_dark.scss"):
        css = read("ruqqus", "assets", "style", sheet)
        assert '$font-family-monospace: "Noto Sans Mono", "Noto Sans Arabic",' in css


def test_the_windows_with_their_own_css_use_it_too():
    stack = ", ".join(f'"{f}"' for f in ["Noto Sans", *SCRIPT_FAMILIES, "Noto Sans Fallback"])
    for page in ("call.html", "space.html", "media_connected.html"):
        text = read("ruqqus", "templates", page)
        assert stack + ', system-ui, -apple-system, "Segoe UI", sans-serif' in text and "{{ font_faces() }}" in text, page


def test_no_page_asks_another_site_for_a_font():
    """Emails keep their own stack (a mail app that can't load web fonts shows its own): they are not pages."""
    for path in (APP / "templates").rglob("*.html"):
        if "email" in path.relative_to(APP / "templates").parts:
            continue
        text = path.read_text(encoding="utf-8")
        assert "fonts.googleapis.com" not in text and "fonts.gstatic.com" not in text, path


def test_the_license_is_shipped_and_inter_is_gone():
    licence = read("ruqqus", "assets", "fonts", "NotoSans-OFL.txt")
    assert "SIL OPEN FONT LICENSE" in licence and "Noto Project Authors" in licence
    assert not list((APP / "assets" / "fonts").glob("inter-*")) and not (APP / "assets" / "fonts" / "Inter-OFL.txt").exists()


# --- the scripts Noto Sans doesn't draw --------------------------------------------------------------------------------

FONTS = APP / "assets" / "fonts"
CJK = ("sc", "tc", "jp", "kr")
# what a CJK slice may hold (scripts/build_script_fonts.py CJK_BLOCKS): never Latin, emoji or the Private Use Area
CJK_BLOCKS = ((0x1100, 0x11FF), (0x2E80, 0x2FDF), (0x2FF0, 0x303F), (0x3040, 0x30FF), (0x3100, 0x312F), (0x3130, 0x318F),
              (0x3190, 0x31FF), (0x3200, 0x32FF), (0x3300, 0x33FF), (0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xA960, 0xA97F),
              (0xAC00, 0xD7FF), (0xF900, 0xFAFF), (0xFE10, 0xFE1F), (0xFE30, 0xFE4F), (0xFF00, 0xFFEF),
              (0x1AFF0, 0x1B16F), (0x20000, 0x3134F))
# the blocks joined where they touch (the build merges a slice's ranges across them)
CJK_SPANS = []
for _lo, _hi in CJK_BLOCKS:
    if CJK_SPANS and _lo == CJK_SPANS[-1][1] + 1:
        CJK_SPANS[-1] = (CJK_SPANS[-1][0], _hi)
    else:
        CJK_SPANS.append((_lo, _hi))
FACE = re.compile(r'@font-face\{font-family:"([^"]+)";font-style:normal;font-weight:100 900;font-display:swap;'
                  r'src:url\("scripts/([a-z0-9-]+\.woff2)\?v=([0-9a-f]{10})"\) format\("woff2"\);unicode-range:([^}]+)\}')


def ranges(text):
    out = []
    for part in text.split(","):
        lo, _, hi = part.strip()[2:].partition("-")
        out.append((int(lo, 16), int(hi or lo, 16)))
    return out


def test_every_script_family_is_a_stylesheet_of_swap_faces_cut_by_unicode_range_naming_real_files():
    names = set()
    for key, (family, path) in script_fonts.FAMILIES.items():
        css = (APP / "assets" / path).read_text(encoding="utf-8")
        # one folder up from the files: /assets/<a>/<b>/<x>.css is a guild's stylesheet (routes/boards.py)
        assert path.count("/") == 1 and path.startswith("fonts/"), path
        faces = FACE.findall(css)
        assert faces and css.count("@font-face") == len(faces), key   # every face: swap, a range, a versioned file
        assert len(faces) >= (60 if key in CJK else 1), (key, len(faces))
        for name, file, version, unicode_range in faces:
            assert name == family, (key, name)
            data = (FONTS / "scripts" / file).read_bytes()
            assert data[:4] == b"wOF2" and hashlib.sha256(data).hexdigest()[:10] == version, file
            cut = ranges(unicode_range)
            assert cut and all(lo <= hi for lo, hi in cut), file
            if key in CJK:
                assert all(any(b_lo <= lo and hi <= b_hi for b_lo, b_hi in CJK_SPANS) for lo, hi in cut), file
            else:
                # the script's own subset only: never the Latin, Cyrillic or Greek Noto Sans already serves
                assert not any(lo < 0x0590 and hi >= 0x0041 and lo <= 0x024F for lo, hi in cut), file
            names.add(file)
    # nothing shipped that no face names, and the build manifest agrees
    shipped = {p.name for p in (FONTS / "scripts").glob("*.woff2")}
    assert shipped == names
    manifest = json.loads((FONTS / "scripts" / "manifest.json").read_text(encoding="utf-8"))
    assert {f for m in manifest.values() for f in m["files"]} == names
    assert all(re.fullmatch(r"\d+\.\d+\.\d+", m["version"]) for m in manifest.values())


def test_each_script_familys_licence_is_shipped_beside_its_files():
    for key, (family, path) in script_fonts.FAMILIES.items():
        css = (APP / "assets" / path).read_text(encoding="utf-8")
        licence = re.search(r"SIL OFL 1\.1, scripts/([A-Za-z]+-OFL\.txt)", css.splitlines()[0]).group(1)
        text = (FONTS / "scripts" / licence).read_text(encoding="utf-8")
        assert "SIL Open Font License" in text and "Version 1.1" in text, licence
        assert licence == family.replace(" ", "") + "-OFL.txt"


def test_a_latin_page_links_no_script_stylesheet_and_nothing_new_blocks_the_first_paint():
    theme = read("ruqqus", "templates", "partials", "theme.html")
    macro = theme[theme.index("{% macro font_faces() %}"):theme.index("{% endmacro %}", theme.index("{% macro font_faces() %}"))]
    # only the interface language's own family is a stylesheet in <head>; none for a Latin language
    assert "{% for key, href in script_font_links(ui_lang()) %}<link rel=\"stylesheet\" href=\"{{ href }}\" data-script-font=\"{{ key }}\">" in macro
    for lang in ("en", "fr", "de", "ru", "el", "vi", "hi", "pl", "tr", "uk", "en-XA"):
        assert script_fonts.interface_keys(lang) == [], lang
    # the rest are linked by a deferred script, never a stylesheet or @import of their own here
    assert "<script src=\"{{ asset_url('js/script_fonts.js') }}\" defer data-sheets=\"{{ script_font_sheets() }}\"></script>" in macro
    assert "noto-sans-sc" not in macro and "@import" not in macro and "fonts/scripts" not in macro
    # the Latin faces are as they were: no preload but Latin's and the logo's
    assert macro.count('rel="preload"') == 2


def test_the_loader_skips_the_options_of_a_closed_select_until_it_opens():
    # Settings > Content's language list holds names in their own scripts: a Latin page must not fetch five families
    # for options nobody has opened
    js = read("ruqqus", "assets", "js", "script_fonts.js")
    assert "OPTION: 1, OPTGROUP: 1, DATALIST: 1" in js
    assert 'if (node.tagName === "SELECT") { chosen(node); return NodeFilter.FILTER_REJECT; }' in js
    assert 'document.addEventListener("focusin", onOpen, true);' in js
    assert 'document.addEventListener("pointerdown", onOpen, true);' in js


def test_the_interface_language_picks_its_family():
    cases = {"ja": "jp", "ko": "kr", "zh": "sc", "zh-Hans": "sc", "zh-Hant": "tc", "zh-TW": "tc", "zh-hk": "tc",
             "zh_MO": "tc", "yue": "tc", "ar": "arabic", "ar-XB": "arabic", "fa": "arabic", "ur": "urdu", "he": "hebrew",
             "bn": "bengali", "th": "thai", "hi": None, "en": None, "": None, None: None}
    for lang, key in cases.items():
        assert script_fonts.family_key(lang) == key, lang
    sheets = json.loads(script_fonts.sheets(lambda path: "/assets/" + path))
    assert set(sheets) == set(script_fonts.FAMILIES) and sheets["jp"] == "/assets/fonts/noto-sans-jp.css"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_loader_links_a_family_by_the_text_and_the_language_around_it():
    code = ("const f = require(%s).familiesFor; console.log(JSON.stringify([" % json.dumps(str(APP / "assets" / "js" / "script_fonts.js")) +
            ",".join(f"f({json.dumps(t)}, {json.dumps(lang)})" for t, lang in CASES) + "]))")
    got = json.loads(subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=60, check=True).stdout)
    for (text, lang), want, out in zip(CASES, [w for *_x, w in LOADER], got):
        assert out == want, (text, lang, out)


LOADER = [
    ("Hello, world → ★ ✓ 😀 \uf0c9", "en", []),          # Latin, symbols, emoji, Font Awesome's code points: nothing
    ("日本語のテキスト", "ja", ["jp"]),
    ("今天天气很好", "zh-Hans", ["sc"]),
    ("今天天氣很好", "zh-Hant", ["tc"]),
    ("廣東話", "yue", ["tc"]),
    ("今天天气很好", "en", ["sc"]),                      # Han in no CJK language: SC, the stack's first CJK family
    ("한국어", "", ["sc", "kr"]),
    ("한국어", "ko-KR", ["kr"]),
    ("مرحبا", "ar", ["arabic"]),
    ("سلام", "fa", ["arabic"]),
    ("اردو", "ur", ["urdu"]),
    ("نور الهدى", "en", ["arabic"]),
    ("שלום", "he", ["hebrew"]),
    ("বাংলা", "bn", ["bengali"]),
    ("ภาษาไทย", "th", ["thai"]),
    ("हिन्दी", "hi", []),                                 # Devanagari is Noto Sans's own
    ("Ελληνικά Русский", "el", []),
    ("𠀋", "zh", ["sc"]),                                # a supplementary ideograph (surrogate pair)
]
CASES = [(t, lang) for t, lang, _w in LOADER]


def test_the_loader_never_reads_layout_and_skips_what_is_not_shown():
    js = read("ruqqus", "assets", "js", "script_fonts.js")
    for banned in ("innerText", "getComputedStyle", "getBoundingClientRect", "offsetHeight", "innerHTML"):
        assert banned not in js, banned
    # a closed <details> (the footer's language list, every language in its own name) is read when it opens
    assert 'details.tagName === "DETAILS" && !details.open && el.tagName !== "SUMMARY"' in js
    assert 'document.addEventListener("toggle", onToggle, true)' in js
    assert "SCRIPT: 1, STYLE: 1, TEMPLATE: 1, NOSCRIPT: 1" in js
    assert 'observer.observe(document.body, { childList: true, subtree: true, characterData: true })' in js
    assert 'link[data-script-font]' in js   # what <head> already linked is not linked twice
