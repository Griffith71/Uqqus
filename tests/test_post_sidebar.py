"""A post page's right sidebar (templates/submission.html, the owner's 2026-10-23 asks): Post Info first, then the
guild's own sidebar for a post in a guild, or the author's About box for a post that lives only on a profile.

Post Info holds only what the post above doesn't show: where it stands among the posts made around it
(helpers/post_standing.py), the other places it is discussed (helpers/post_places.py; moved here from the main area,
which keeps its list on phones only) and one line of facts. Counts only, never who."""
from pathlib import Path
from types import SimpleNamespace as NS

from ruqqus.helpers import post_places, post_standing
from ruqqus.helpers.profile_about import author_box_shown

ROOT = Path(__file__).resolve().parent.parent
PAGE = (ROOT / "ruqqus" / "templates" / "submission.html").read_text(encoding="utf-8")
SIDEBAR = PAGE.split("{% block sidebarblock %}")[1].split("{% endblock %}")[0]
INFO = SIDEBAR.split('<div class="sidebar-section sidebar-info post-info">')[1].split("{% if not p.is_profile_post %}")[0]


def account(id=7, deleted=False, suspended=False, private=False, shown=True):
    return NS(id=id, is_deleted=deleted, is_suspended=suspended, is_private=private, bot_shown_to=lambda v: shown)


def post(anonymous=False):
    return NS(is_anonymous=anonymous)


def test_the_author_box_shows_for_an_ordinary_post():
    assert author_box_shown(post(), account(), None)
    assert author_box_shown(post(), account(), NS(id=9, admin_level=0))


def test_never_on_an_anonymous_post_whoever_looks():
    for viewer in (None, NS(id=7, admin_level=0), NS(id=1, admin_level=6)):        # a visitor, the author, an admin
        assert not author_box_shown(post(anonymous=True), account(), viewer)


def test_not_for_an_account_whose_profile_the_viewer_couldnt_open():
    reader = NS(id=9, admin_level=0)
    assert not author_box_shown(post(), account(deleted=True), NS(id=1, admin_level=6))
    assert not author_box_shown(post(), account(suspended=True), reader)
    assert not author_box_shown(post(), account(private=True), reader)
    assert not author_box_shown(post(), account(private=True), None)
    assert not author_box_shown(post(), account(shown=False), reader)            # a bot not approved yet
    # as on the profile page: its owner and the admins still see a suspended or private account's
    assert author_box_shown(post(), account(private=True), NS(id=7, admin_level=0))
    assert author_box_shown(post(), account(suspended=True), NS(id=1, admin_level=3))


def test_post_info_holds_what_the_post_doesnt_show_and_nothing_private():
    for gone in ("<dt>Upvotes</dt>", "<dt>Shares</dt>", "<dt>Bookmarks</dt>", "<dt>Tips</dt>", "<dt>Posted</dt>",
                 "<dt>Who sees it</dt>", "Insights", "Shortlink", "creation_region", "forwarded_by"):
        assert gone not in INFO, gone
    assert "views" not in INFO.lower() and ".author" not in SIDEBAR and "author_of" not in INFO
    assert "{% set standing = post_standing(p) %}" in SIDEBAR and "{% set places = post_places(p, v) %}" in SIDEBAR
    # three places, then the rest behind one button that opens and closes them (all_js.js togglePostPlaces)
    assert "place_row(pl, extra=loop.index > 3)" in INFO and 'aria-expanded="false"' in INFO
    assert "onclick=\"togglePostPlaces(this)\"" in INFO
    js = (ROOT / "ruqqus" / "assets" / "js" / "all_js.js").read_text(encoding="utf-8")
    assert "function togglePostPlaces(button)" in js


def test_the_main_areas_forwarded_list_is_for_phones_only():
    list_ = PAGE.split('href="#forwards-{{ p.base36id }}"')[0].rsplit("<div", 1)[1]
    assert "d-lg-none" in list_


def place(label, comments, ups, hub=False):
    return post_places.Place(url="/x", label=label, comments=comments, ups=ups, is_hub=hub)


def test_places_with_the_most_traction_come_first():
    rows = post_places.ranked([place("+a", 1, 50), place("+b", 9, 0), place("+c", 1, 80), place("+d", 1, 80)])
    assert [r.label for r in rows] == ["+b", "+c", "+d", "+a"]       # comments, then upvotes, then the name


def test_the_original_post_is_never_named_after_its_author():
    hub = NS(permalink="/post/1", board=NS(name="systemprofile"), comment_count=3, upvotes_fuzzed=4)
    assert post_places.place_of(hub, is_hub=True).label == "Original post"
    source = (ROOT / "ruqqus" / "helpers" / "post_places.py").read_text(encoding="utf-8")
    assert "author" not in source.split('"""', 2)[2]


def test_standing_lines_say_something_or_nothing():
    day = 20749 * 86400 + 3600
    assert post_standing.rank_line(0, 1, "+a", day) == ""                         # alone that day
    assert post_standing.rank_line(2, 41, "+a", day) == "#3 of 41 posts in +a on 23 October"
    assert post_standing.top_share(0, 9) is None                                    # too few to say
    assert post_standing.top_share(0, 100) == 1 and post_standing.top_share(4, 100) == 5
    assert post_standing.top_share(60, 100) is None                                 # the bottom half isn't news
    assert post_standing.day_window(day) == (20749 * 86400, 20750 * 86400)
    assert post_standing.week_window(day) == (20743 * 86400, 20750 * 86400)


def test_a_removed_deleted_or_space_post_has_no_standing():
    def p(**kw):
        return NS(is_banned=kw.get("banned", False), deleted_utc=kw.get("deleted", 0), is_space=kw.get("space", False))
    for post in (p(banned=True), p(deleted=5), p(space=True)):
        assert not post_standing.standing_for(None, post, "Laba", counted=lambda *a: (0, 50))


def test_the_counts_on_sqlite():
    from sqlalchemy import create_engine, text
    engine = create_engine("sqlite://")
    with engine.connect() as db:
        db.execute(text("CREATE TABLE submissions (id INTEGER, board_id INTEGER, created_utc INTEGER, score_top INTEGER,"
                        " is_banned BOOLEAN, deleted_utc INTEGER, post_public BOOLEAN, repost_id INTEGER,"
                        " is_space BOOLEAN)"))
        rows = [(1, 5, 100, 10, 0, 0, 1, 0, 0), (2, 5, 200, 30, 0, 0, 1, 0, 0), (3, 5, 300, 50, 1, 0, 1, 0, 0),
                (4, 5, 400, 40, 0, 9, 1, 0, 0), (5, 5, 500, 70, 0, 0, 0, 0, 0), (6, 5, 600, 80, 0, 0, 1, 2, 0),
                (7, 5, 700, 90, 0, 0, 1, 0, 1), (8, 6, 800, 99, 0, 0, 1, 0, 0), (9, 5, 99999, 99, 0, 0, 1, 0, 0)]
        for r in rows:
            db.execute(text("INSERT INTO submissions VALUES (:a,:b,:c,:d,:e,:f,:g,:h,:i)"),
                       dict(zip("abcdefghi", r)))
        # in board 5 that day: live posts 1, 2, 5, 6, 7 (3 removed, 4 deleted, 9 another day); 3 score over 20
        assert post_standing.counts(db, post_standing.GUILD_SQL, 5, 0, 86400, 20) == (4, 5)
        # the site: only public originals that aren't Spaces (1 and 2)
        assert post_standing.counts(db, post_standing.SITE_SQL, 5, 0, 86400, 20) == (1, 2)


def test_the_author_box_says_whose_it_is_and_its_region_sheet_comes_with_it():
    about = (ROOT / "ruqqus" / "templates" / "partials" / "profile_about.html").read_text(encoding="utf-8")
    assert "{% if about_identity %}" in about and 'href="{{ u.permalink }}"' in about
    modal = PAGE.split("{% block aboutAccountModal %}")[1].split("{% endblock %}")[0]
    assert "{% set info_author = post_author_box(p, v) %}" in modal
    assert '{% with u = info_author %}{% include "about_account_modal.html" %}{% endwith %}' in modal
    helper = (ROOT / "ruqqus" / "helpers" / "jinja2.py").read_text(encoding="utf-8").split("def post_author_box(")[1]
    helper = helper.split("\n@app.")[0]
    assert "author_of(p, v)" in helper and ".author\n" not in helper and "p.author " not in helper
