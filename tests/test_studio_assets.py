"""Creator Studio crosses the route, the counting, the templates, the menus and the stylesheets. These checks keep the
promises: Premium only (checked without spending a coin), counts only, and never anything about ad money."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


ROUTE = read("ruqqus", "routes", "studio.py")
RULES = read("ruqqus", "helpers", "studio.py")
STORE = read("ruqqus", "helpers", "studio_store.py")
PAGE = read("ruqqus", "templates", "studio.html")
LOCKED = read("ruqqus", "templates", "studio_locked.html")
NAV = read("ruqqus", "templates", "partials", "studio_nav.html")


def test_the_studio_is_for_premium_and_never_spends_a_coin_to_find_out():
    body = ROUTE.split("def studio(")[1]
    head = ROUTE.split("def studio(")[0].rsplit("\n\n\n", 1)[1]
    assert '@app.route("/studio", methods=["GET"])' in head and "@auth_required" in head
    assert body.index("if not v.has_premium_no_renew:") < body.index("studio_store.earnings(")
    assert 'return render_template("studio_locked.html", v=v)' in body
    assert not re.search(r"\.has_premium\b(?!_)", ROUTE)
    assert "from .studio import *" in read("ruqqus", "routes", "__init__.py")


def test_nothing_in_the_studio_reads_ad_figures_or_talks_about_ad_money():
    for name, source in (("route", ROUTE), ("rules", RULES), ("store", STORE)):
        assert "helpers.ads" not in source and "from ruqqus.helpers import ads" not in source and "import ads" not in source, name
    for name, html in (("page", PAGE), ("locked", LOCKED), ("nav", NAV)):
        shown = re.sub(r"\{#.*?#\}", " ", html, flags=re.S)                     # what a member reads, not the notes
        words = re.sub(r"<[^>]+>|\{[{%].*?[}%]\}", " ", shown, flags=re.S).lower()
        assert not re.search(r"\bads?\b|advert|adsense", words), name


def test_only_counts_ever_leave_the_store():
    for statement in re.findall(r'text\("""(.*?)"""\)|text\("([^"]*)"\)', STORE, re.S):
        sql = (statement[0] or statement[1]).strip()
        assert sql.upper().startswith("SELECT COALESCE(SUM(") or sql.upper().startswith("SELECT COUNT("), sql
    assert "username" not in STORE and "username" not in PAGE


def test_the_tabs_put_insights_in_the_studio_and_keep_its_old_address():
    insights = read("ruqqus", "routes", "insights.py")
    head = insights.split("def insights_overview(")[0].rsplit("\n\n\n", 1)[1]
    assert '@app.route("/studio/insights", methods=["GET"])' in head and '@app.route("/insights", methods=["GET"])' in head
    assert 'studio_tab="insights"' in insights
    assert '{% include "partials/studio_nav.html" %}' in read("ruqqus", "templates", "insights_overview.html")
    assert '{% include "partials/studio_nav.html" %}' in PAGE
    for href in ('href="/studio"', 'href="/studio/insights"', 'href="/circles#circle-paid"', 'href="/settings/creator_fund"'):
        assert href in NAV, href
    assert 'aria-current="page"' in NAV


def test_every_way_in_leads_to_the_studio():
    for name in ("default.html", "header.html"):
        menu = read("ruqqus", "templates", name)
        assert menu.count('href="/studio"><i class="fas fa-chart-pie fa-fw text-left mr-3"></i>Creator Studio</a>') == 1, name
    premium = read("ruqqus", "templates", "settings_premium.html")
    assert 'href="/studio"' in premium and 'href="/insights"' in premium
    assert 'href="/settings/premium"' in LOCKED


def test_the_page_draws_nothing_as_html_and_numbers_line_up():
    for html in (PAGE, LOCKED, NAV):
        assert "| safe" not in html
    for sheet in ("main.scss", "main_dark.scss"):
        css = read("ruqqus", "assets", "style", sheet)
        block = css[css.index("// Creator Studio (studio.html"):css.index("// Create post card (templates/submit.html")]
        assert ".studio-cards {" in block and "font-variant-numeric: tabular-nums;" in block and "$cc-" not in block, sheet


def test_claude_md_describes_the_studio():
    doc = read("CLAUDE.md")
    section = doc.split("## Creator Studio (legacy app)")[1].split("\n## ")[0]
    for needle in ("/studio", "has_premium_no_renew", "studio_store", "AdSense", "counts", "/studio/insights", "/insights"):
        assert needle in section, needle
