"""The vote route and the one-vote-per-person-across-copies rule (helpers/vote_copies.py).

The helper's own SQL is tested in tests/test_vote_copies.py; here it is the harness's stand-in
(`legacy.copies`: no refusal unless `copies.result` is set), so these pin what the ROUTE does with the answer:
where the check sits, what a refusal writes (nothing) and what is never checked (taking a vote back)."""
import pytest

from legacy_harness import FakeSubmission, FakeVote

REFUSAL = {"error": "You already voted on this post, in +general. Remove that vote to vote here.",
           "voted_in": {"id": "b", "guild": "general", "label": "+general"}}


@pytest.fixture
def env(legacy):
    legacy.user = legacy.add_user(id=1)
    legacy.post = legacy.db.insert(FakeSubmission(id=1))
    legacy.login(legacy.user)
    return legacy


def vote(env, x, pid="1"):
    return env.client.post(f"/api/vote/post/{pid}/{x}", data={"formkey": env.formkey(env.user)})


# --- a refusal ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("x", [1, -1])
def test_a_vote_is_refused_with_a_403_json_that_says_where_the_other_vote_is(env, x):
    env.copies.result = REFUSAL
    r = vote(env, x)
    assert r.status_code == 403
    assert r.get_json() == REFUSAL
    assert r.headers["Cache-Control"] == "private"


def test_a_refused_vote_writes_nothing_and_commits_nothing(env):
    env.copies.result = REFUSAL
    vote(env, 1)
    assert env.db.rows(FakeVote) == []
    assert (env.post.upvotes, env.post.downvotes, env.post.score_top) == (0, 0, None)
    assert env.db.commits == 0 and env.db.flushes == 0


def test_a_refused_change_leaves_the_existing_vote_as_it_was(env):
    assert vote(env, 1).status_code == 204
    commits = env.db.commits
    env.copies.result = REFUSAL                      # e.g. a person with older votes on two copies
    assert vote(env, -1).status_code == 403
    [row] = env.db.rows(FakeVote)
    assert row.vote_type == 1
    assert (env.post.upvotes, env.post.downvotes) == (1, 0) and env.db.commits == commits


def test_without_a_refusal_the_vote_is_counted_as_before(env):
    r = vote(env, 1)
    assert (r.status_code, r.data) == (204, b"")
    [row] = env.db.rows(FakeVote)
    assert (row.user_id, row.vote_type, row.submission_id) == (1, 1, 1)
    assert env.copies.calls == [(1, 1)]               # asked once, about this account and this post


# --- taking a vote back is never checked -----------------------------------------------------------------

def test_removing_a_vote_never_asks_and_is_never_refused(env):
    vote(env, 1)
    env.copies.calls.clear()
    env.copies.result = REFUSAL
    r = vote(env, 0)
    assert r.status_code == 204 and env.copies.calls == []
    assert env.db.rows(FakeVote)[0].vote_type == 0


# --- where the check sits --------------------------------------------------------------------------------

@pytest.mark.parametrize("attr,val,msg", [
    ("is_banned", True, "That post has been removed."),
    ("deleted_utc", 5, "That post has been deleted."),
    ("is_archived", True, "That post is archived and can no longer be voted on."),
    ("is_blocking", True, "You can't vote on posts made by users who you are blocking."),
    ("is_blocked", True, "You can't vote on posts made by users who are blocking you."),
])
def test_the_post_state_refusals_come_first_and_the_helper_is_not_asked(env, attr, val, msg):
    setattr(env.post, attr, val)
    env.copies.result = REFUSAL
    r = vote(env, 1)
    assert (r.status_code, r.get_json()["error"]) == (403, msg)
    assert env.copies.calls == []


def test_an_invalid_value_and_a_bot_are_refused_before_the_helper(env):
    env.copies.result = REFUSAL
    assert vote(env, 2).status_code == 400
    r = env.client.post("/api/vote/post/1/1", data={"formkey": env.formkey(env.user)}, headers={"X-User-Type": "bot"})
    assert r.status_code == 403 and r.get_json() is None
    assert env.copies.calls == []


def test_the_token_api_is_checked_by_the_same_rule(env):
    env.add_token(env.user)
    r = env.client.post("/api/v1/vote/post/1/1", headers=env.bearer())
    assert (r.status_code, r.data) == (204, b"")       # no refusal: counted as before
    assert env.copies.calls == [(1, 1)]

    env.db.rows(FakeVote)[0].vote_type = 0
    env.copies.result = REFUSAL
    r = env.client.post("/api/v1/vote/post/1/-1", headers=env.bearer())
    assert r.status_code == 403 and r.get_json() == REFUSAL
    assert env.db.rows(FakeVote)[0].vote_type == 0


def test_a_logged_out_visitor_never_reaches_it(legacy):
    legacy.db.insert(FakeSubmission(id=1))
    legacy.copies.result = REFUSAL
    r = legacy.client.post("/api/vote/post/1/1", data={"formkey": "x"})
    assert r.status_code == 401 and legacy.copies.calls == []
