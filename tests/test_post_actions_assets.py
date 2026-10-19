"""The action row under a post, in the source: one partial for the four places, the counts in the order the owner
asked for (comments, shares, bookmarks, tips), then the "..." menu."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "ruqqus"
PARTIAL = (ROOT / "templates" / "partials" / "post_actions.html").read_text(encoding="utf-8")
JS = (ROOT / "assets" / "js" / "all_js.js").read_text(encoding="utf-8")


def read(name):
    return (ROOT / "templates" / name).read_text(encoding="utf-8")


def test_the_four_counts_come_in_order():
    body = PARTIAL[PARTIAL.index("{% macro counted_actions("):]
    order = [body.index(k) for k in ("post-action-comments", "post-action-shares", "post-action-bookmark", "post-action-tip")]
    assert order == sorted(order)
    for count in ("_n(p.space_comment_count)", "_n(shares)", "_n(base)", "_n(base + 1)", "_n(coins)"):
        assert count in body, count


def test_every_place_draws_the_row_from_the_partial_with_the_menu_after_it():
    for name in ("submission_listing.html", "submission.html"):
        page = read(name)
        assert page.count('post_actions.counted_actions(p, v, saved, "")') == 1, name
        assert page.count('post_actions.counted_actions(p, v, saved, "mobile-")') == 1, name
        assert page.count("post-action-more") == 2, name
        for call in ('counted_actions(p, v, saved, "")', 'counted_actions(p, v, saved, "mobile-")'):
            assert page.index(call) < page.index("post-action-more", page.index(call)), (name, call)
        # nothing of the four is drawn twice: no comment link or bookmark of their own, no tip in the menus
        assert "fa-comment-dots mr-1" not in page and "/save_post/" not in page and "tipModal2(" not in page, name


def test_the_bookmark_ids_are_the_ones_the_folder_sheet_turns_on():
    assert 'id="bookmark-{{ part }}{{ p.base36id }}"' in PARTIAL and 'id="unbookmark-{{ part }}{{ p.base36id }}"' in PARTIAL
    assert "showBookmarked('{{ p.base36id }}', true)" in PARTIAL and "showBookmarked('{{ p.base36id }}', false)" in PARTIAL
    fn = JS[JS.index("function showBookmarked(id, saved)"):]
    assert "['', 'mobile-', 'modal-']" in fn[:400]


def test_the_counts_say_nothing_about_who():
    for word in ("username }}</", "user_id", "| safe"):
        assert word not in PARTIAL.split("post-action-tip")[0], word
    store = (ROOT / "helpers" / "post_counts.py").read_text(encoding="utf-8")
    assert "COUNT(*)" in store and "user_id" not in store


def test_the_page_gets_its_counts_in_one_go():
    get = (ROOT / "helpers" / "get.py").read_text(encoding="utf-8")
    assert "post_counts.attach(g.db, posts)" in get
    model = (ROOT / "classes" / "submission.py").read_text(encoding="utf-8")
    assert "def share_count(self):" in model and "def bookmark_count(self):" in model


def test_both_stylesheets_style_the_row():
    for sheet in ("main.scss", "main_dark.scss"):
        css = (ROOT / "assets" / "style" / sheet).read_text(encoding="utf-8")
        for selector in (".post-actions .post-action-item {", ".post-actions .post-action-more {", ".post-action {",
                         ".post-action-count {", ".post-action-bookmark.is-on {"):
            assert selector in css, (sheet, selector)
        assert "@media (hover: hover)" in css.split(".post-action-bookmark.is-on {")[1][:400], sheet
