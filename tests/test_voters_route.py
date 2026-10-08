"""The "who upvoted" routes (routes/voters.py) on the legacy harness. The list itself (the SQL) is tested in
tests/test_voters.py; here it is a stand-in that records how it was asked, so these pin who gets an answer."""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from legacy_harness import FakeComment, FakeSubmission, FakeUser

ROOT = Path(__file__).resolve().parent.parent

BOB = NS(username="bob", permalink="/@bob", profile_url="/assets/images/bob.png")
CAROL = NS(username="carol", permalink="/@carol", profile_url="/assets/images/carol.png")


@pytest.fixture
def env(legacy, monkeypatch):
    """routes/voters.py on the harness app, with the list replaced by a recorder."""
    asked = []

    def friends_who_upvoted(db, kind, item_id, author_id, viewer, limit=100):
        asked.append((kind, item_id, author_id, viewer.id))
        return [BOB, CAROL]

    monkeypatch.setattr("ruqqus.helpers.voters.friends_who_upvoted", friends_who_upvoted)
    spec = importlib.util.spec_from_file_location("voters_route_under_test", ROOT / "ruqqus" / "routes" / "voters.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "voters_route_under_test", module)
    spec.loader.exec_module(module)

    legacy.asked = asked
    legacy.me = legacy.db.insert(FakeUser(id=1))
    legacy.login(legacy.me)
    return legacy


def post(env, **kw):
    return env.db.insert(FakeSubmission(id=1, **kw))


def comment(env, **kw):
    return env.db.insert(FakeComment(id=1, **kw))


# --- the author gets the list -------------------------------------------------------------------------------

def test_the_author_of_a_post_gets_the_list_and_nothing_else(env):
    post(env, author_id=1)
    r = env.client.get("/api/post/1/voters")
    assert r.status_code == 200
    assert r.get_json() == {
        "people": [{"username": "bob", "permalink": "/@bob", "avatar": "/assets/images/bob.png"},
                   {"username": "carol", "permalink": "/@carol", "avatar": "/assets/images/carol.png"}],
        "limit": 100,
        "note": "Only people you follow who follow you back are listed. Downvotes are never shown.",
    }
    assert env.asked == [("post", 1, 1, 1)]


def test_the_answer_is_private_and_not_cached(env):
    post(env, author_id=1)
    cache = env.client.get("/api/post/1/voters").headers["Cache-Control"]
    assert "private" in cache and "no-store" in cache


def test_the_author_of_a_comment_gets_the_list(env):
    comment(env, author_id=1)
    r = env.client.get("/api/comment/1/voters")
    assert r.status_code == 200 and [p["username"] for p in r.get_json()["people"]] == ["bob", "carol"]
    assert env.asked == [("comment", 1, 1, 1)]


def test_asking_changes_nothing(env):
    post(env, author_id=1)
    env.client.get("/api/post/1/voters")
    assert env.db.commits == 0 and env.db.added == []


# --- everyone else ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("make,url", [(post, "/api/post/1/voters"), (comment, "/api/comment/1/voters")])
def test_anyone_who_is_not_the_author_gets_a_403_and_the_list_is_never_built(env, make, url):
    make(env, author_id=99)
    r = env.client.get(url)
    assert r.status_code == 403
    assert r.get_json() == {"error": "Only the author can see who upvoted."}
    assert env.asked == []


def test_the_forwarder_of_a_copy_is_not_the_author_but_the_original_author_is(env):
    # a forward copy keeps the ORIGINAL author's id; whoever forwarded it is somebody else
    post(env, author_id=2, repost_id=7)
    assert env.client.get("/api/post/1/voters").status_code == 403           # the forwarder (user 1)
    assert env.asked == []
    original_author = env.db.insert(FakeUser(id=2, username="dana"))
    env.login(original_author)
    assert env.client.get("/api/post/1/voters").status_code == 200           # the original author, on the copy
    assert env.asked == [("post", 1, 2, 2)]


@pytest.mark.parametrize("attr,value", [("is_banned", True), ("deleted_utc", 5)])
def test_a_removed_or_deleted_item_is_a_404_even_for_its_author(env, attr, value):
    post(env, author_id=1, **{attr: value})
    comment(env, author_id=1, **{attr: value})
    assert env.client.get("/api/post/1/voters").status_code == 404
    assert env.client.get("/api/comment/1/voters").status_code == 404
    assert env.asked == []


def test_an_unknown_or_malformed_id_is_a_404(env):
    post(env, author_id=1)
    assert env.client.get("/api/post/zzzz/voters").status_code == 404
    assert env.client.get("/api/post/!!/voters").status_code == 404
    assert env.client.get("/api/post/ABC/voters").status_code == 404
    assert env.client.get("/api/comment/zzzz/voters").status_code == 404
    assert env.asked == []


def test_a_visitor_gets_nothing(legacy, monkeypatch):
    asked = []
    monkeypatch.setattr("ruqqus.helpers.voters.friends_who_upvoted", lambda *a, **k: asked.append(a) or [])
    spec = importlib.util.spec_from_file_location("voters_route_visitor", ROOT / "ruqqus" / "routes" / "voters.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "voters_route_visitor", module)
    spec.loader.exec_module(module)
    legacy.db.insert(FakeSubmission(id=1, author_id=1))
    r = legacy.client.get("/api/post/1/voters")
    assert r.status_code != 200 and b"bob" not in r.data and asked == []


def test_it_is_read_only_over_get(env):
    post(env, author_id=1)
    assert env.client.post("/api/post/1/voters").status_code == 405


def test_the_template_helper_is_the_real_rule(env):
    from ruqqus.helpers import voters
    assert env.app.jinja_env.globals["may_see_voters"] is voters.may_see
