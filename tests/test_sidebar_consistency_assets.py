"""The right sidebar reads the same on the feeds and a profile: each page's own sections, then
partials/sidebar_common.html (Trending, Who to follow, the sidebar ad, Guilds, Support). A profile swaps Who to follow
for Similar accounts and Support for its About box (tests/test_profile_sidebar_assets.py).

A guild and a post have their own sidebar, as on reddit.com (the owner, 2026-10-23): a guild page and a post in a
guild show the guild's alone (partials/guild_sidebar.html: About Guild, the next event, the Guildmaster Tools, Similar
guilds, which the owner asked for on 2026-10-26 and is about the guild itself, then the sidebar ad); a post shows Post Info
first, and a post that lives only on a profile its author's About box after it.

The pages about one account that are not its profile (a follow list, its channels) set `profile_user` as the profile does,
so they read as the account's: Similar accounts in place of Who to follow (tests/test_profile_sidebar_assets.py)."""
import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "ruqqus" / "templates"
PAGES = ("default.html", "home.html", "userpage.html")


def read(name):
    return (TEMPLATES / name).read_text(encoding="utf-8")


def sidebar(name):
    src = read(name)
    start = src.index("{% block sidebarblock %}")
    return src[start:src.index("{% endblock %}", start)]


def test_every_page_with_posts_ends_its_sidebar_with_the_shared_part():
    for name in PAGES:
        assert sidebar(name).rstrip().endswith('{% include "partials/sidebar_common.html" %}'), name


def test_a_guild_and_a_post_have_their_own_sidebar():
    guild = sidebar("board.html")
    assert guild.strip() == '{% block sidebarblock %}\n{% include "partials/guild_sidebar.html" %}'
    post = sidebar("submission.html")
    assert "sidebar_common.html" not in post and "late_boxes.html" not in post
    assert post.index("Post Info") < post.index('{% include "partials/guild_sidebar.html" %}')
    assert '{% if not p.is_profile_post %}' in post
    assert "{% set info_author = post_author_box(p, v) %}" in post
    assert '{% with u = info_author, about_identity = true %}{% include "partials/profile_about.html" %}{% endwith %}' in post
    own = read("partials/guild_sidebar.html")
    assert "About Guild" in own and "Guildmaster Tools" in own and "sidebar_common" not in own
    assert own.rstrip().endswith('{{ ad_slot("sidebar") }}')
    # Similar guilds (the owner, 2026-10-26): a box about THIS guild, above the ad and never a site-wide one
    assert own.index("Guildmaster Tools") < own.index('{% include "partials/similar_guilds_slot.html" %}') < own.index('{{ ad_slot("sidebar") }}')


def test_the_shared_part_has_everything_once_and_nothing_else_copies_it():
    shared = read("partials/sidebar_common.html")
    assert '{% include "partials/late_boxes.html" %}' in shared and "Create Guild" in shared and "fa-heart mr-2" in shared
    for path in TEMPLATES.rglob("*.html"):
        if path.name in ("sidebar_common.html", "late_boxes.html"):
            continue
        src = path.read_text(encoding="utf-8")
        assert 'include "partials/late_boxes.html"' not in src, path.name
        assert not re.search(r'fa-heart mr-2"></i>(\{\{ _\(")?Support', src), path.name
