"""Who sees what under the word filter (ruqqus/helpers/visibility.py).

These cover the per-object rules used for direct pages and comment threads.
The query filters apply the same rule in SQL and are exercised end to end
against a real database (they need the real models)."""
from types import SimpleNamespace as NS

import pytest

from ruqqus.helpers import visibility as vis

OFF, STANDARD, CHILD = 0, 1, 2


def viewer(level, id=100):
    return NS(id=id, filter_level=level)


def user(id=1, name_severity=0):
    return NS(id=id, name_severity=name_severity)


def board(name_severity=0, is_sensitive=False):
    return NS(id=5, name_severity=name_severity, is_sensitive=is_sensitive)


def post(severity=0, sensitive=False, author=None, guild=None):
    author = author or user()
    return NS(author_id=author.id, author=author, board=guild or board(), word_severity=severity, is_sensitive=sensitive)


def comment(severity=0, sensitive=False, author=None, parent=None):
    author = author or user()
    return NS(author_id=author.id, author=author, word_severity=severity, is_sensitive=sensitive,
              parent_comment=parent, parent_comment_id=1 if parent else None)


def test_viewer_level_defaults_to_standard():
    assert vis.viewer_level(None) == STANDARD            # logged out
    assert vis.viewer_level(NS(id=1, filter_level=None)) == STANDARD
    assert vis.viewer_level(viewer(OFF)) == OFF
    assert vis.viewer_level(viewer(CHILD)) == CHILD


@pytest.mark.parametrize("level, severity, hidden", [
    (OFF, 2, False),
    (STANDARD, 0, False), (STANDARD, 1, False), (STANDARD, 2, True),
    (CHILD, 0, False), (CHILD, 1, True), (CHILD, 2, True),
])
def test_posts_and_comments_follow_severity(level, severity, hidden):
    v = viewer(level)
    assert vis.post_hidden(post(severity), v) is hidden
    assert vis.comment_hidden(comment(severity), v) is hidden


def test_logged_out_visitors_get_standard():
    assert vis.post_hidden(post(2), None) is True
    assert vis.post_hidden(post(1), None) is False


def test_child_also_hides_sensitive_content_and_guilds():
    assert vis.post_hidden(post(0, sensitive=True), viewer(CHILD)) is True
    assert vis.comment_hidden(comment(0, sensitive=True), viewer(CHILD)) is True
    assert vis.post_hidden(post(0, guild=board(is_sensitive=True)), viewer(CHILD)) is True
    assert vis.board_hidden(board(is_sensitive=True), viewer(CHILD)) is True
    # Standard leaves sensitive content alone
    assert vis.post_hidden(post(0, sensitive=True), viewer(STANDARD)) is False
    assert vis.board_hidden(board(is_sensitive=True), viewer(STANDARD)) is False


def test_authors_always_see_their_own_content():
    me = viewer(CHILD, id=7)
    mine = user(id=7, name_severity=2)
    assert vis.post_hidden(post(2, sensitive=True, author=mine), me) is False
    assert vis.comment_hidden(comment(2, author=mine), me) is False
    assert vis.user_hidden(mine, me) is False


def test_a_filtered_username_hides_everything_from_that_user():
    bad_name = user(id=3, name_severity=2)
    assert vis.user_hidden(bad_name, viewer(STANDARD)) is True
    assert vis.post_hidden(post(0, author=bad_name), viewer(STANDARD)) is True
    assert vis.comment_hidden(comment(0, author=bad_name), viewer(STANDARD)) is True
    assert vis.post_hidden(post(0, author=bad_name), viewer(OFF)) is False
    # a mildly rated name is only hidden for Child
    mild_name = user(id=4, name_severity=1)
    assert vis.user_hidden(mild_name, viewer(STANDARD)) is False
    assert vis.user_hidden(mild_name, viewer(CHILD)) is True


def test_a_filtered_guild_name_hides_the_guild_and_its_posts():
    bad_guild = board(name_severity=2)
    assert vis.board_hidden(bad_guild, viewer(STANDARD)) is True
    assert vis.post_hidden(post(0, guild=bad_guild), viewer(STANDARD)) is True
    assert vis.board_hidden(bad_guild, viewer(OFF)) is False
    assert vis.board_hidden(None, viewer(CHILD)) is False


def test_a_comment_is_hidden_with_everything_under_it():
    root = comment(2)
    child = comment(0, parent=root)
    grandchild = comment(0, parent=child)
    assert vis.comment_thread_hidden(grandchild, viewer(STANDARD)) is True
    assert vis.comment_thread_hidden(grandchild, viewer(OFF)) is False
    assert vis.comment_thread_hidden(comment(0, parent=comment(0)), viewer(CHILD)) is False


def test_bio_and_description_text():
    assert vis.text_hidden(1, viewer(STANDARD)) is False
    assert vis.text_hidden(1, viewer(CHILD)) is True
    assert vis.text_hidden(2, None) is True
    assert vis.text_hidden(2, viewer(CHILD, id=9), owner_id=9) is False   # your own bio
