"""History > Anonymous (routes/history.py history_anonymous, the owner, 2026-10-27): your own anonymous posts and comments,
which no profile lists to their author any more. Only ever the signed-in member's own: built from v.id, never a name."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "ruqqus"


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


HISTORY = read("routes", "history.py")
USER = read("classes", "user.py")
PAGE = read("templates", "history.html")


def test_the_tab_is_the_members_own_and_needs_a_sign_in():
    route = HISTORY.split("def history_anonymous(")[0].rsplit("@app.route", 1)[1] + HISTORY.split("def history_anonymous(")[1].split("\n@app.")[0]
    assert '"/history/anonymous"' in route and "@auth_required" in route
    assert "v.anonymous_idlist(page=page)" in route and "v.anonymous_comment_idlist(page=page)" in route
    assert "request.args.get(\"user\"" not in route and "username" not in route
    assert "organise_bookmarks" not in route


def test_the_lists_are_the_authors_own_anonymous_items_once_each():
    posts = USER.split("def anonymous_idlist(")[1].split("def anonymous_comment_idlist(")[0]
    for needle in ("Submission.author_id == self.id", "Submission.is_anonymous == True", "Submission.deleted_utc == 0",
                   "Submission.is_banned == False", "or_(Submission.repost_id.is_(None), Submission.repost_id == 0)"):
        assert needle in posts, needle
    comments = USER.split("def anonymous_comment_idlist(")[1].split("def history_idlist(")[0]
    for needle in ("Comment.author_id == self.id", "Comment.is_anonymous == True", "Comment.deleted_utc == 0"):
        assert needle in comments, needle


def test_both_tab_bars_have_it_with_the_posts_and_comments_switch():
    # the two tab bars, and the Live | Deleted chips under them (the Log plan's slice D3c)
    assert PAGE.count('href="/history/anonymous"') == 3 and PAGE.count("fa-user-secret") == 2
    assert "{% if active_tab in ('bookmarked', 'upvoted', 'downvoted', 'anonymous', 'manage') and deleted_rows is none %}" in PAGE
    assert "active_tab == 'anonymous'" in read("templates", "history_empty.html")


def test_a_profile_no_longer_lists_them_to_their_author():
    rules = read("helpers", "anonymity.py").split("def off_profile(")[1].split("\ndef ")[0]
    assert "return not_(model.is_anonymous)" in rules and "ADMIN_LEVEL" in rules and "author_id" not in rules
