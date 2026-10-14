"""The right sidebar reads the same on every page with posts (home and the feeds, a profile, a guild, a post): each
page's own sections, then partials/sidebar_common.html (Trending, Who to follow, the sidebar ad, Guilds, Support).
A profile once showed only Support, and a guild or a post had no Trending or Who to follow."""
import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "ruqqus" / "templates"
PAGES = ("default.html", "home.html", "userpage.html", "board.html", "submission.html")


def read(name):
    return (TEMPLATES / name).read_text(encoding="utf-8")


def sidebar(name):
    src = read(name)
    start = src.index("{% block sidebarblock %}")
    return src[start:src.index("{% endblock %}", start)]


def test_every_page_with_posts_ends_its_sidebar_with_the_shared_part():
    for name in PAGES:
        assert sidebar(name).rstrip().endswith('{% include "partials/sidebar_common.html" %}'), name


def test_the_shared_part_has_everything_once_and_nothing_else_copies_it():
    shared = read("partials/sidebar_common.html")
    assert '{% include "partials/late_boxes.html" %}' in shared and "Create Guild" in shared and "fa-heart mr-2" in shared
    for path in TEMPLATES.rglob("*.html"):
        if path.name in ("sidebar_common.html", "late_boxes.html"):
            continue
        src = path.read_text(encoding="utf-8")
        assert 'include "partials/late_boxes.html"' not in src, path.name
        assert not re.search(r'fa-heart mr-2"></i>Support', src), path.name
