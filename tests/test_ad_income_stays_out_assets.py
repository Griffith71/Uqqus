"""Ad income is the owner's and goes one way (the owner, 2026-10-30): the admins' money page may read helpers/ads.py to say whether ads are
on, and nothing reads a figure of ad income, because the site holds none. The creator fund, the Studio and members never reach any of it:
AdSense forbids passing its earnings on without Google's written permission (support.google.com/adsense/answer/1348725).
tests/test_creator_fund_assets.py and tests/test_studio_assets.py pin what those modules' own source says; this follows their imports."""
import ast
import re
from pathlib import Path

from ruqqus.helpers import ads, money_stats

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "ruqqus"
FUND_AND_STUDIO = ("ruqqus.helpers.creator_fund", "ruqqus.helpers.creator_fund_store", "ruqqus.routes.creator_fund",
                   "ruqqus.helpers.studio", "ruqqus.helpers.studio_store", "ruqqus.routes.studio")
ADS = "ruqqus.helpers.ads"
MONEY = "ruqqus.helpers.money_stats"
# the only code that reads helpers/ads.py: a new one is a decision, and never a figure
READS_ADS = {"ruqqus.__main__", "ruqqus.routes.ads", "ruqqus.routes.settings", "ruqqus.routes.login", "ruqqus.routes.admin",
             "ruqqus.routes.qr_login_pages"}
MONEY_PAGE = (PACKAGE / "templates" / "admin" / "money.html").read_text(encoding="utf-8")


def _file(module):
    path = ROOT.joinpath(*module.split("."))
    if path.is_dir():
        return path / "__init__.py"
    return path.with_suffix(".py")


def _imports(module):
    """Every module of the site that `module` imports, at any depth of its code. A name in `from ruqqus.helpers import x` may be a module."""
    is_package = _file(module).name == "__init__.py"
    found = set()
    for node in ast.walk(ast.parse(_file(module).read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = module.split(".")
                parts = parts if is_package else parts[:-1]
                parts = parts[:len(parts) - (node.level - 1)]
                base = ".".join(parts + ([node.module] if node.module else []))
            found.add(base)
            found.update(f"{base}.{alias.name}" for alias in node.names)
    return {name for name in found if name.startswith("ruqqus") and _file(name).exists()}


def _every_module():
    for path in PACKAGE.rglob("*.py"):
        parts = path.relative_to(ROOT).with_suffix("").parts
        yield ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def test_nothing_the_fund_or_the_studio_imports_leads_to_ads_or_the_money_page():
    """`ruqqus.__main__` is the one stop: everything takes `app` from it, it loads every route, and it asks ads only for a response header."""
    for start in FUND_AND_STUDIO:
        seen, waiting = set(), [start]
        while waiting:
            module = waiting.pop()
            if module in seen or module == "ruqqus.__main__":
                continue
            seen.add(module)
            waiting.extend(_imports(module))
        for forbidden in (ADS, MONEY, "ruqqus.routes.ads", "ruqqus.routes.admin"):
            assert forbidden not in seen, (start, forbidden)


def test_only_these_read_helpers_ads_and_only_the_money_page_reads_its_figures():
    readers = {module for module in _every_module() if ADS in _imports(module)}
    assert readers == READS_ADS
    assert {module for module in _every_module() if MONEY in _imports(module)} == {"ruqqus.routes.admin"}


def test_the_fund_and_the_studio_do_not_link_to_the_money_page():
    for folder, name in (("templates", "settings_creator_fund.html"), ("templates", "studio.html"), ("templates", "studio_locked.html"),
                         ("templates/partials", "studio_nav.html"), ("templates/admin", "creator_fund.html")):
        text = re.sub(r"\{#.*?#\}", " ", (PACKAGE / folder / name).read_text(encoding="utf-8"), flags=re.S)
        assert "/admin/money" not in text, name


def test_the_site_holds_no_figure_of_ad_income():
    assert not [key for _, rows in money_stats.SECTIONS for key, *_ in rows if re.search(r"(^|_)(ads?|adsense)(_|$)", key)]
    schema = (ROOT / "schema.sql").read_text(encoding="utf-8").lower()
    assert not re.search(r"\b(ad|ads|adsense)_(income|revenue|earnings|impressions|clicks|rpm|figures?)\b", schema)
    assert not re.search(r"adsense_\w+", schema)


def test_the_page_takes_no_typed_figure_and_links_to_adsense_only_for_a_person():
    body = MONEY_PAGE.lower()
    for tag in ("<form", "<input", "<textarea", "<select", "<button"):
        assert tag not in body, tag
    assert re.search(r'@app\.route\("/admin/money", methods=\["GET"\]\)', (PACKAGE / "routes" / "admin.py").read_text(encoding="utf-8"))
    assert money_stats.ADSENSE_REPORTS.startswith("https://adsense.google.com/")
    assert 'href="{{ adsense_reports }}"' in MONEY_PAGE and 'rel="noopener noreferrer"' in MONEY_PAGE


def test_no_code_of_the_site_talks_to_adsenses_api():
    for path in list(PACKAGE.rglob("*.py")) + list((PACKAGE / "assets" / "js").glob("*.js")) + list((ROOT / "scripts").glob("*.py")):
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        assert "adsense.googleapis" not in text and "adsense/v2" not in text and "adsensehost" not in text, path


def test_only_the_money_page_talks_about_ad_income():
    words = re.compile(r"\bad[- ](income|revenue|earnings|money)\b", re.I)
    for path in (PACKAGE / "templates").rglob("*.html"):
        shown = re.sub(r"\{#.*?#\}", " ", path.read_text(encoding="utf-8"), flags=re.S)
        if path.name != "money.html":
            assert not words.search(shown), path


def test_the_status_names_a_state_and_places_and_never_an_id():
    secret = {"ADSENSE_CLIENT": "ca-pub-1234567890123456", "ADSENSE_SLOT_FEED": "5555555555", "ADSENSE_SLOT_POST": "6666666666",
              "ADSENSE_FEED_LAYOUT_KEY": "-fb+5w+4e-db+86"}
    status = ads.status(ads.config(secret))
    assert status == {"state": "live", "places": ("feed", "post"), "unset": ("sidebar",), "feed_layout_key": True}
    assert not any(value in repr(status) for value in secret.values())
    for name in ("cfg", "config(", ".client", ".publisher", ".slots", "feed_layout_key }}"):
        assert name not in MONEY_PAGE, name


def test_the_three_states():
    assert ads.status(ads.config({}))["state"] == "off"
    assert ads.status(ads.config({"ADSENSE_CLIENT": "not-a-client"}))["state"] == "off"
    client_only = ads.status(ads.config({"ADSENSE_CLIENT": "ca-pub-1234567890123456"}))
    assert client_only["state"] == "client_only" and client_only["places"] == ()
    every = {"ADSENSE_CLIENT": "ca-pub-1234567890123456", "ADSENSE_SLOT_FEED": "11111", "ADSENSE_SLOT_POST": "22222", "ADSENSE_SLOT_SIDEBAR": "33333"}
    live = ads.status(ads.config(every))
    assert live["state"] == "live" and live["places"] == ads.PAGES and live["unset"] == () and live["feed_layout_key"] is False
    assert set(ads.PLACE_LABELS) == set(ads.PAGES)
