"""Page-view analytics from the owner's own host (ruqqus/helpers/analytics.py), off unless set; and no page may load a
script from the old Ruqqus domain, which this site doesn't own (bug hunt 2026-10, H7)."""
import re
from pathlib import Path

from ruqqus.helpers import analytics

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "ruqqus" / "templates"


def test_off_unless_a_plain_https_address_is_given():
    assert analytics.settings({}, "laba.example") == (None, None)
    assert analytics.settings({"PLAUSIBLE_SRC": "  "}, "laba.example") == (None, None)
    for bad in ("http://stats.example.org/js/script.js", "//stats.example.org/js/script.js", "javascript:alert(1)",
                "https://user:pw@stats.example.org/js/script.js", "https:///js/script.js",
                'https://stats.example.org/x.js" onload="alert(1)', "https://stats.example.org/a b.js",
                "https://stats.example.org/<x>.js", "/js/script.js"):
        assert analytics.script_src(bad) is None, bad


def test_on_with_the_sites_own_address_or_the_name_given():
    src = "https://stats.example.org/js/script.js"
    assert analytics.settings({"PLAUSIBLE_SRC": src}, "laba.example") == (src, "laba.example")
    assert analytics.settings({"PLAUSIBLE_SRC": src}, "localhost:8000") == (None, None), "no real name to count under"
    assert analytics.settings({"PLAUSIBLE_SRC": src, "PLAUSIBLE_DOMAIN": "Laba.Example"}, "x") == (src, "laba.example")
    assert analytics.settings({"PLAUSIBLE_SRC": src, "PLAUSIBLE_DOMAIN": "a b"}, "laba.example") == (src, "laba.example")
    assert analytics.host_of(src) == "stats.example.org" and analytics.host_of(None) is None


def test_the_layouts_load_it_only_while_it_is_on_and_the_privacy_page_names_it():
    partial = (TEMPLATES / "partials" / "analytics.html").read_text(encoding="utf-8")
    block = re.search(r"\{% if 'ANALYTICS_SRC' \| app_config %\}(.*?)\{% endif %\}", partial, re.S)
    assert block and "src=\"{{ 'ANALYTICS_SRC' | app_config }}\"" in block.group(1)
    assert partial.count("<script") == 1 and partial.index("<script") > partial.index("{% if")
    for layout in ("default.html", "docs_default.html"):
        text = (TEMPLATES / layout).read_text(encoding="utf-8")
        assert text.count('{% include "partials/analytics.html" %}') == 1, layout
        assert "ANALYTICS_SRC" not in text, "drawn only through the partial"
    privacy = (TEMPLATES / "help" / "privacy.html").read_text(encoding="utf-8")
    assert re.search(r"\{% if 'ANALYTICS_SRC' \| app_config %\}\s*<h2 id=\"analytics\">", privacy)
    assert "{{ 'ANALYTICS_HOST' | app_config }}" in privacy


def test_no_page_loads_anything_from_the_old_ruqqus_domain():
    loads = re.compile(r"""<(?:script|link|img|iframe)\b[^>]*(?:src|href)\s*=\s*["']?(?:https?:)?//[^"'>\s]*ruqqus\.com""", re.I)
    found = [str(p.relative_to(ROOT)) for p in TEMPLATES.rglob("*.html") if loads.search(p.read_text(encoding="utf-8"))]
    assert found == []
