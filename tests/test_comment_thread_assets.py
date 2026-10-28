"""The comment thread after the owner's review (2026-10-23): no box around a comment, the tint on the comment being
pointed at only, the line as the gutter beside a comment's comments (ending at the last), every comment foldable from
its header, and low-scoring comments held behind "Show N more comments" (tests/test_reply_fold.py has the rule)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "ruqqus"
TEMPLATE = (ROOT / "templates" / "comments.html").read_text(encoding="utf-8")
STYLE = (ROOT / "assets" / "style" / "_comments.scss").read_text(encoding="utf-8")
JS = (ROOT / "assets" / "js" / "all_js.js").read_text(encoding="utf-8")
JINJA = (ROOT / "helpers" / "jinja2.py").read_text(encoding="utf-8")


def test_every_branch_draws_its_comments_through_the_fold():
    # the comment, the one hidden for a reader (removed, blocked, muted) and the one removed by an admin
    assert TEMPLATE.count("{% set shown, folded = fold_comments(c, v, comment_info if comment_info is defined else None) %}") == 3
    assert TEMPLATE.count('<div id="comments-of-{{ c.base36id }}" class="comment-children">') == 3
    assert TEMPLATE.count("{% for child in shown %}") == 3 and TEMPLATE.count("{% for child in folded %}") == 3
    assert "c.nested_comments %}\n          {{ single_comment" not in TEMPLATE      # no branch skips the fold


def test_the_folded_comments_open_in_place():
    assert TEMPLATE.count('onclick="showFoldedReplies(\'{{ c.base36id }}\')"') == 3
    assert TEMPLATE.count('<div id="folded-of-{{ c.base36id }}" class="d-none">') == 3
    assert "{{ ngettext('Show %(num)s more comment', 'Show %(num)s more comments', folded | length) }}" in TEMPLATE
    body = JS.split("function showFoldedReplies(comment_id) {")[1].split("\n};")[0]
    assert 'classList.remove("d-none")' in body and "button.remove()" in body


def test_the_fold_follows_the_score_the_reader_sees():
    body = JINJA.split("def fold_comments(c, v, linked=None):")[1].split("\n\n\n")[0]
    assert "score_fuzzed if net else x.upvotes_fuzzed" in body
    assert "x.author_id == v.id" in body and "parent_comment" in body


def test_the_line_is_the_gutter_beside_the_comments_and_ends_with_them():
    gutter = STYLE.split(".comment .comment-children {")[1].split("}")[0]
    assert "border-left: 2px solid var(--border-strong);" in gutter
    # the old line was a pseudo-element as tall as the whole comment, its comments and its form, and ran past them
    assert "height: 100%" not in STYLE
    strip = STYLE.split(".comment .comment-children > .comment-collapse {")[1].split("}")[0]
    assert "top: 0;" in strip and "bottom: 0;" in strip


def test_the_click_strip_and_the_header_toggle_keep_their_hooks():
    assert TEMPLATE.count('<span class="comment-collapse d-md-block d-none" onclick="collapse_comment(\'{{ c.base36id }}\')"') == 1
    toggle = next(l for l in TEMPLATE.splitlines() if "comment-toggle" in l)
    # a real button that says whether the comment is open; collapse_comment keeps that true
    assert toggle.lstrip().startswith('<button type="button" class="comment-collapse comment-toggle"')
    assert "onclick=\"collapse_comment('{{ c.base36id }}')\"" in toggle
    assert "aria-expanded=\"{{ 'false' if starts_folded else 'true' }}\"" in toggle
    body = JS.split("function collapse_comment(comment_id) {")[1].split("\n};")[0]
    assert 'toggle.setAttribute("aria-expanded", folded ? "false" : "true");' in body and "bringBackIntoView(el)" in body
    assert re.search(r"\.comment\.collapsed \.comment-toggle::before[^{]*\{[^}]*\\f055", STYLE)


def test_a_comment_has_no_box_and_its_own_parts_take_the_tint():
    box = STYLE.split("\n.comment {")[1].split("}")[0]
    assert "border: 0;" in box and "background-color: transparent;" in box
    assert TEMPLATE.count('id="comment-{{ c.base36id }}-only" class="comment-main') == 3
    assert ".comment .comment-main:hover {\n        background-color: var(--hover);" in STYLE


def test_the_desktop_vote_row_is_spaced_by_the_row_not_by_old_margins():
    assert ".comment .comment-actions .list-inline .list-inline-item {\n    margin-right: 0 !important;" in STYLE
    assert "gap: 2px;" in STYLE.split(".comment .comment-actions .list-inline {")[1].split("}")[0]


COMMENT_CLASS = (ROOT / "classes" / "comment.py").read_text(encoding="utf-8")
COMPONENTS = (ROOT / "assets" / "style" / "_components.scss").read_text(encoding="utf-8")


def test_a_long_comment_is_drawn_whole_and_clamped_never_cut_in_two():
    # it used to be split at 280 characters into two separately rendered halves, the second starting mid-sentence
    assert "preview_body_html" not in TEMPLATE and "overflow_body_html" not in TEMPLATE
    assert "def preview_body_html" not in COMMENT_CLASS and "def overflow_body_html" not in COMMENT_CLASS
    # the clamp is also what Translate swaps (data-translate-part, assets/js/translate.js)
    assert '<div id="comment-clamp-{{ c.base36id }}" class="comment-clamp is-clamped" data-translate-part="body">{{ notice_html(c) | safe }}</div>' in TEMPLATE
    button = next(l for l in TEMPLATE.splitlines() if 'id="comment-overflow-toggle-' in l)
    assert '<button type="button"' in button and 'aria-expanded="false"' in button
    assert ".comment .comment-clamp.is-clamped {" in STYLE


def test_show_less_stays_in_reach_and_closing_brings_the_start_back():
    toggle = JS.split("function toggleCommentOverflow(comment_id, button) {")[1].split("\n};")[0]
    assert 'clamp.classList.toggle("is-clamped", !opening);' in toggle
    assert "bringBackIntoView(" in toggle and 'setAttribute("aria-expanded"' in toggle
    expand = JS.split("function onTextExpandClick(event){")[1].split("\n}")[0]
    assert "toggleClass('is-open', open)" in expand and "bringBackIntoView(document.getElementById('post-'+id))" in expand
    pinned = COMPONENTS.split(".text-expand-btn.is-open, .text-expand.is-open {")[1].split("}")[0]
    assert "position: sticky;" in pinned and "bottom:" in pinned
    assert "prefers-reduced-motion: reduce" in JS.split("function bringBackIntoView(el) {")[1].split("\n};")[0]


def test_a_comment_that_fits_its_lines_shows_no_button():
    assert "function settleCommentClamps(root) {" in JS and "\nsettleCommentClamps();" in JS
