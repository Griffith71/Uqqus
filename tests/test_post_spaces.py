"""Comment spaces of a post made for guilds (helpers/post_spaces.py, helpers/space_counts.py, routes/post_spaces.py)."""
import re
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import post_spaces as sp
from ruqqus.helpers import space_counts
from ruqqus.helpers.comment_permission import GUILDS

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def copy(name, hot=0, top=0, disputed=0, created=0, comments=0):
    return NS(board=NS(name=name), score_hot=hot, score_top=top, score_disputed=disputed, created_utc=created, comment_count=comments)


COPIES = [copy("a", hot=1, top=5, disputed=9, created=10), copy("b", hot=9, top=1, disputed=2, created=30),
          copy("c", hot=5, top=9, disputed=5, created=20)]


def names(sort):
    return [c.board.name for c in sp.ranked(COPIES, sort)]


def test_guilds_are_ranked_by_the_sort_the_page_uses():
    assert names("hot") == ["b", "c", "a"]
    assert names("top") == ["c", "a", "b"]
    assert names("new") == ["b", "c", "a"] and names("old") == ["a", "c", "b"]
    assert names("disputed") == ["a", "c", "b"]
    assert names("nonsense") == names("top") and sp.sort_of(None) == "top"


def test_ties_go_to_the_newer_copy():
    tied = [copy("old", top=1, created=1), copy("new", top=1, created=2)]
    assert [c.board.name for c in sp.ranked(tied, "top")] == ["new", "old"]


def test_only_the_first_guilds_are_listed_up_front():
    many = [copy(f"g{i}") for i in range(14)]
    shown, more = sp.listed(many)
    assert len(shown) == sp.LISTED and len(more) == 4


def comment(score=0, hot=0, disputed=0, created=0, pinned=False):
    return NS(score_top=score, score_hot=hot, score_disputed=disputed, created_utc=created, is_pinned=pinned)


def test_all_guilds_is_one_list_in_the_chosen_sort_with_each_thread_keeping_its_guild():
    ga, gb = copy("a"), copy("b")
    merged = sp.merge_threads([(ga, [comment(score=3), comment(score=1)]), (gb, [comment(score=2)])], "top")
    assert [(c.board.name, t.score_top) for c, t in merged] == [("a", 3), ("b", 2), ("a", 1)]
    merged = sp.merge_threads([(ga, [comment(created=1)]), (gb, [comment(created=2)])], "new")
    assert [c.board.name for c, _ in merged] == ["b", "a"]
    pinned = sp.merge_threads([(ga, [comment(score=9)]), (gb, [comment(score=1, pinned=True)])], "top")
    assert pinned[0][0].board.name == "b"


# --- the counts of a post made for guilds only -----------------------------------------------------------------------

SCHEMA = [
    "CREATE TABLE boards (id INTEGER PRIMARY KEY, is_banned BOOLEAN DEFAULT 0)",
    """CREATE TABLE submissions (id INTEGER PRIMARY KEY, author_id INTEGER, board_id INTEGER, repost_id INTEGER DEFAULT 0,
       comment_count INTEGER DEFAULT 0, upvotes INTEGER DEFAULT 1, downvotes INTEGER DEFAULT 0, deleted_utc INTEGER DEFAULT 0,
       is_banned BOOLEAN DEFAULT 0, hidden_by_guild BOOLEAN DEFAULT 0, post_public BOOLEAN DEFAULT 1)""",
    "CREATE TABLE forwardrels (id INTEGER PRIMARY KEY, primary_submission_id INTEGER, board_id INTEGER, forward_submission_id INTEGER, forwarded_by_id INTEGER)",
]


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for statement in SCHEMA:
            conn.execute(text(statement))
        conn.execute(text("INSERT INTO boards (id, is_banned) VALUES (1, 0), (2, 0), (3, 0), (4, 1)"))
        # hub 100 by author 7; copies 101 (+1 guild), 102 (+2), 103 (private guild), 104 (deleted), 105 (forwarded by someone
        # else), 106 (banned guild)
        rows = [(100, 7, 0, 0, 1, 0, 0, 1), (101, 7, 1, 100, 4, 3, 1, 1), (102, 7, 2, 100, 2, 1, 0, 1),
                (103, 7, 3, 100, 50, 9, 9, 0), (104, 7, 1, 100, 8, 8, 0, 1), (105, 7, 2, 100, 6, 6, 0, 1),
                (106, 7, 4, 100, 5, 5, 0, 1)]
        for pid, author, board, repost, comments, ups, downs, public in rows:
            conn.execute(text("INSERT INTO submissions (id, author_id, board_id, repost_id, comment_count, upvotes, downvotes, post_public, deleted_utc) "
                              "VALUES (:i, :a, :b, :r, :c, :u, :d, :p, :x)"),
                         {"i": pid, "a": author, "b": board, "r": repost, "c": comments, "u": ups, "d": downs, "p": public,
                          "x": 5 if pid == 104 else 0})
            if repost:
                conn.execute(text("INSERT INTO forwardrels (primary_submission_id, board_id, forward_submission_id, forwarded_by_id) "
                                  "VALUES (100, :b, :i, :by)"), {"b": board, "i": pid, "by": 8 if pid == 105 else 7})
    with Session(engine) as session:
        yield session


def hub(id_=100, mode=GUILDS, repost=0):
    return NS(id=id_, repost_id=repost, comment_permission=mode)


def test_only_a_post_made_for_guilds_gets_totals_and_only_from_live_public_copies_the_author_posted(db):
    found = space_counts.totals(db, [hub(), hub(200, mode=0), hub(201, repost=100)])
    # 101 and 102 only: 103 is in a private guild, 104 is deleted, 105 was forwarded by someone else, 106 is in a banned guild
    assert found == {100: (6, 4, 1)}


def test_attach_gives_the_hub_its_totals_and_leaves_other_posts_alone(db):
    page = [hub(), hub(200, mode=0)]
    space_counts.attach(db, page)
    assert (page[0]._space_comments, page[0]._space_ups, page[0]._space_downs) == (6, 4, 1)
    assert not hasattr(page[1], "_space_comments")


# --- the wiring --------------------------------------------------------------------------------------------------------

def test_the_routes_start_with_the_checks_every_post_route_makes():
    routes = read("ruqqus", "routes", "post_spaces.py")
    body = routes.split("def post_space(", 1)[1]
    assert "get_post(pid, v=v)" in body and "in_private_guild_for(v) or post_hidden(hub, v)" in body
    # a space is only a live copy the author posted, in a guild the viewer may see and their filters leave in
    spaces = routes.split("def spaces_of(", 1)[1].split("\nclass Spaces", 1)[0]
    assert "forwards_shown_to(v)" in spaces and "copy.forwarded_by is not None" in spaces
    assert "in_private_guild_for(v) or post_hidden(copy, v)" in spaces
    # "All guilds" has no comment box; a guild's own space uses that guild's rules
    template = read("ruqqus", "templates", "post_space.html")
    assert "is_allowed_to_comment=false" in template
    assert "loaded.board.can_comment(v)" in body


def test_the_post_page_shows_the_spaces_and_the_profile_keeps_no_box_when_it_has_no_space():
    page = read("ruqqus", "templates", "submission.html")
    assert "{% elif not (spaces.copies and guilds_only) %}" in page and '<div id="space-profile">' in page
    assert '{% include "partials/comment_spaces.html" %}' in page and '{% include "partials/comment_form.html" %}' in page
    assert "guilds_only=cperm.is_guilds_only(self)" in read("ruqqus", "classes", "submission.py")


def test_a_guild_post_is_made_by_where_to_post():
    posts = read("ruqqus", "routes", "posts.py")
    assert 'flag(request.form, "post_to_profile", True)' in posts
    assert "(cperm.GUILDS if guilds_only else comment_permission)" in posts
    assert "Choose where to post." in posts
    # a post for Circles is always on the profile
    assert "True if for_circles else flag(request.form" in posts
    menu = read("ruqqus", "routes", "post_commenting.py")
    assert "spaces_of(post, v)" in menu and "cperm.parse(raw, allow_guilds=True)" in menu


def test_the_composers_ask_where_to_post_and_hide_who_can_comment_without_the_profile():
    macro = read("ruqqus", "templates", "partials", "post_options.html")
    assert 'name="post_to_profile" value="0"' in macro and 'name="post_to_profile" value="1"' in macro
    script = read("ruqqus", "assets", "js", "where_to_post.js")
    assert "[data-where-comment]" in script and "where:circles" in script and "/api/guild_search?q=" in script
    assert "innerHTML" not in script
    search = read("ruqqus", "routes", "search.py")
    guild_search = search.split("def guild_search(", 1)[1].split("\ndef sort_of(", 1)[0]
    assert "Board.is_circle == False" in guild_search and "listable_boards(boards, v)" in guild_search
    assert "board.has_ban(v)" in guild_search and "board.can_forward(v)" in guild_search
