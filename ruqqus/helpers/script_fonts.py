"""The Noto families for the scripts Noto Sans doesn't draw, and which of their stylesheets a page links.

The site's typeface is Noto Sans (partials/theme.html `font_faces()`): Latin, Cyrillic, Greek, Vietnamese and
Devanagari. Arabic script, Urdu's Nastaliq, Hebrew, Bengali, Thai and Chinese, Japanese and Korean have Noto families of
their own, self-hosted (scripts/build_script_fonts.py): one stylesheet per family in assets/fonts/, its font files in
assets/fonts/scripts/, each face cut by unicode-range, so a page fetches only the slices its text needs.

- The interface language's own family is linked in <head> (`interface_keys`), so a page in Japanese paints in Noto
  Sans JP from the first frame once the files are cached.
- Every other family is linked only when the page holds text in its script (assets/js/script_fonts.js, which reads
  `sheets()` from its tag), so a page in a Latin language loads none of them.
- The stacks that name the families are in assets/style/_tokens.scss (`--font-sans` and the [lang|=..] rules).

Pure: no Flask, no I/O.
"""
import json

# key -> (the family, its stylesheet under assets/)
FAMILIES = {
    "arabic": ("Noto Sans Arabic", "fonts/noto-sans-arabic.css"),
    "urdu": ("Noto Nastaliq Urdu", "fonts/noto-nastaliq-urdu.css"),
    "hebrew": ("Noto Sans Hebrew", "fonts/noto-sans-hebrew.css"),
    "bengali": ("Noto Sans Bengali", "fonts/noto-sans-bengali.css"),
    "thai": ("Noto Sans Thai", "fonts/noto-sans-thai.css"),
    "sc": ("Noto Sans SC", "fonts/noto-sans-sc.css"),
    "tc": ("Noto Sans TC", "fonts/noto-sans-tc.css"),
    "jp": ("Noto Sans JP", "fonts/noto-sans-jp.css"),
    "kr": ("Noto Sans KR", "fonts/noto-sans-kr.css"),
}

TRADITIONAL = ("zh-hant", "zh-tw", "zh-hk", "zh-mo")


def family_key(lang):
    """The family a language's text is drawn in, or None when Noto Sans draws it (or the language is unknown)."""
    lang = (lang or "").strip().lower().replace("_", "-")
    primary = lang.split("-", 1)[0]
    if primary == "zh":
        return "tc" if any(lang == t or lang.startswith(t + "-") for t in TRADITIONAL) else "sc"
    return {"yue": "tc", "ja": "jp", "ko": "kr", "ar": "arabic", "fa": "arabic", "ur": "urdu", "he": "hebrew",
            "bn": "bengali", "th": "thai"}.get(primary)


def interface_keys(lang):
    """The families whose stylesheets the page links in <head> for an interface in `lang`."""
    key = family_key(lang)
    return [key] if key else []


def sheets(asset_url):
    """key -> versioned address of its stylesheet, for script_fonts.js (a JSON object in an HTML attribute)."""
    return json.dumps({key: asset_url(path) for key, (_family, path) in FAMILIES.items()}, separators=(",", ":"))
