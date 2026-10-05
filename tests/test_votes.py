"""Characterization tests for ruqqus/routes/votes.py (api_vote_post and
api_vote_comment). They pin what the code does TODAY, including oddities;
tests flagged "suspected bug" document behaviour that probably should change.
See tests/legacy_harness.py for how the app/DB are faked.
"""
import time

import pytest

from legacy_harness import (
    FakeApplication, FakeComment, FakeCommentVote, FakeSubmission, FakeUser,
    FakeVote,
)

POST = "/api/vote/post/1/{x}"
COMMENT = "/api/vote/comment/1/{x}"


@pytest.fixture
def env(legacy):
    legacy.user = legacy.add_user(id=1)
    legacy.post = legacy.db.insert(FakeSubmission(id=1))
    legacy.comment = legacy.db.insert(FakeComment(id=1))
    legacy.comment.post = legacy.post
    legacy.login(legacy.user)
    return legacy


def vote(env, kind, x, pid="1", **form):
    form.setdefault("formkey", env.formkey(env.user))
    path = {"post": "/api/vote/post/{}/{}", "comment": "/api/vote/comment/{}/{}"}
    return env.client.post(path[kind].format(pid, x), data=form)


def votes_of(env, cls=FakeVote):
    return env.db.rows(cls)


# ----------------------------------------------------------- post: happy path
def test_post_upvote_creates_vote_and_updates_counts(env):
    r = vote(env, "post", 1)
    assert (r.status_code, r.data) == (204, b"")
    [row] = votes_of(env)
    assert (row.user_id, row.vote_type, row.submission_id) == (1, 1, 1)
    assert row.creation_ip == "127.0.0.1"
    assert row.app_id is None
    p = env.post
    assert (p.upvotes, p.downvotes) == (1, 0)
    assert p.score_top == 1
    # rank_* are taken from the post object (fake constants here)
    assert (p.score_disputed, p.score_best) == (111, 222)
    assert env.db.commits == 1


def test_post_downvote_creates_vote(env):
    assert vote(env, "post", -1).status_code == 204
    [row] = votes_of(env)
    assert row.vote_type == -1
    assert (env.post.upvotes, env.post.downvotes, env.post.score_top) == (0, 1, -1)


def test_post_base36_id_is_decoded_for_new_vote_row(env):
    env.post.id = 35  # "z"
    assert vote(env, "post", 1, pid="z").status_code == 204
    assert votes_of(env)[0].submission_id == 35


def test_post_vote_response_headers(env):
    r = vote(env, "post", 1)
    assert r.headers["Cache-Control"] == "private"
    assert r.headers["Access-Control-Allow-Origin"] == "localhost"


def test_post_up_then_down_reuses_existing_row(env):
    vote(env, "post", 1)
    assert vote(env, "post", -1).status_code == 204
    [row] = votes_of(env)
    assert row.vote_type == -1
    assert (env.post.upvotes, env.post.downvotes) == (0, 1)


def test_post_zero_clears_vote_but_keeps_row(env):
    vote(env, "post", 1)
    assert vote(env, "post", 0).status_code == 204
    [row] = votes_of(env)
    assert row.vote_type == 0
    assert (env.post.upvotes, env.post.downvotes, env.post.score_top) == (0, 0, 0)


def test_post_zero_with_no_prior_vote_creates_a_zero_row(env):
    assert vote(env, "post", 0).status_code == 204
    [row] = votes_of(env)
    assert row.vote_type == 0


def test_post_duplicate_vote_is_idempotent_204(env):
    vote(env, "post", 1)
    assert vote(env, "post", 1).status_code == 204
    assert len(votes_of(env)) == 1
    assert env.post.upvotes == 1


def test_post_two_users_counts_add_up(env):
    vote(env, "post", 1)
    other = env.add_user(id=2, username="bob")
    env.login(other)
    r = env.client.post("/api/vote/post/1/-1",
                        data={"formkey": env.formkey(other)})
    assert r.status_code == 204
    assert (env.post.upvotes, env.post.downvotes, env.post.score_top) == (1, 1, 0)


def test_post_vote_on_own_post_is_allowed(env):
    """CURRENT BEHAVIOUR: no own-post restriction at all."""
    env.post.author_id = env.user.id
    assert vote(env, "post", 1).status_code == 204
    assert env.post.upvotes == 1


def test_post_vote_has_no_guild_ban_or_membership_check(env):
    """CURRENT BEHAVIOUR: nothing about guild bans/membership is consulted."""
    env.post.board_id = 12345
    assert vote(env, "post", -1).status_code == 204


# --------------------------------------------------------- post: bad input
@pytest.mark.parametrize("x", ["2", "-2", "01", "+1", "abc", "1.0", "-0"])
def test_post_invalid_vote_value_is_400(env, x):
    assert vote(env, "post", x).status_code == 400
    assert votes_of(env) == []


def test_post_bot_header_is_403_any_case(env):
    for val in ("bot", "BoT"):
        r = env.client.post(POST.format(x=1), headers={"X-User-Type": val},
                            data={"formkey": env.formkey(env.user)})
        assert r.status_code == 403
    assert votes_of(env) == []


def test_post_non_bot_user_type_header_is_fine(env):
    r = env.client.post(POST.format(x=1), headers={"X-User-Type": "human"},
                        data={"formkey": env.formkey(env.user)})
    assert r.status_code == 204


def test_post_invalid_value_checked_before_bot_header(env):
    r = env.client.post(POST.format(x=7), headers={"X-User-Type": "bot"},
                        data={"formkey": env.formkey(env.user)})
    assert r.status_code == 400


def test_post_get_method_not_allowed(env):
    assert env.client.get(POST.format(x=1)).status_code == 405


def test_post_nonexistent_post_is_404(env):
    assert vote(env, "post", 1, pid="zzzz").status_code == 404


# ---------------------------------------------------------- post: auth/gates
def test_post_logged_out_is_401(legacy):
    legacy.db.insert(FakeSubmission(id=1))
    assert legacy.client.post(POST.format(x=1)).status_code == 401


def test_post_suspended_user_is_403_permanent_and_future_temp(env):
    env.user.is_banned = 1
    assert vote(env, "post", 1).status_code == 403
    env.user.unban_utc = int(time.time()) + 1000
    assert vote(env, "post", 1).status_code == 403
    assert votes_of(env) == []


def test_post_expired_temp_ban_can_vote(env):
    env.user.is_banned = 1
    env.user.unban_utc = int(time.time()) - 10
    assert vote(env, "post", 1).status_code == 204


def test_post_negative_balance_is_402_json_even_without_formkey(env):
    env.user.negative_balance_cents = 5
    r = env.client.post(POST.format(x=1))  # no formkey at all
    assert r.status_code == 402
    assert r.get_json()["error"].startswith(
        "You can't do that while your account balance is negative.")


def test_post_missing_formkey_is_401(env):
    assert env.client.post(POST.format(x=1)).status_code == 401


def test_post_wrong_formkey_is_401(env):
    assert vote(env, "post", 1, formkey="deadbeef").status_code == 401


def test_post_empty_formkey_is_401(env):
    assert vote(env, "post", 1, formkey="").status_code == 401


def test_post_formkey_from_other_session_is_401(env):
    assert vote(env, "post", 1,
                formkey=env.formkey(env.user, session_id="other")).status_code == 401


def test_post_formkey_accepted_from_query_string(env):
    r = env.client.post(POST.format(x=1) + "?formkey=" + env.formkey(env.user))
    assert r.status_code == 204


def test_post_session_without_session_id_is_500(env):
    """CURRENT BEHAVIOUR (suspected bug): a logged-in session that has no
    session_id (never rendered a formkey) makes validate_formkey raise
    KeyError -> 500 instead of 401."""
    with env.client.session_transaction() as s:
        del s["session_id"]
    assert vote(env, "post", 1, formkey="anything").status_code == 500


def test_post_non_ascii_formkey_is_500(env):
    """CURRENT BEHAVIOUR (suspected bug): hmac.compare_digest raises
    TypeError for non-ASCII str, giving 500 instead of 401."""
    assert vote(env, "post", 1, formkey="kéy").status_code == 500


# ---------------------------------------------------------- post: content
@pytest.mark.parametrize("attr,val,msg", [
    ("is_banned", True, "That post has been removed."),
    ("deleted_utc", 5, "That post has been deleted."),
    ("is_archived", True,
     "That post is archived and can no longer be voted on."),
])
def test_post_unavailable_states_are_403_json(env, attr, val, msg):
    setattr(env.post, attr, val)
    r = vote(env, "post", 1)
    assert (r.status_code, r.get_json()) == (403, {"error": msg})
    assert votes_of(env) == [] and env.db.commits == 0


def test_post_blocking_author_message(env):
    env.post.is_blocking = True
    r = vote(env, "post", 1)
    assert (r.status_code, r.get_json()["error"]) == (
        403, "You can't vote on posts made by users who you are blocking.")


def test_post_blocked_by_author_message(env):
    env.post.is_blocked = True
    r = vote(env, "post", 1)
    assert (r.status_code, r.get_json()["error"]) == (
        403, "You can't vote on posts made by users who are blocking you.")


def test_post_block_checks_precede_removed_check(env):
    env.post.is_blocking = True
    env.post.is_banned = True
    assert "blocking" in vote(env, "post", 1).get_json()["error"]


def test_post_removed_check_precedes_deleted_and_archived(env):
    env.post.is_banned = True
    env.post.deleted_utc = 5
    env.post.is_archived = True
    assert vote(env, "post", 1).get_json()["error"] == "That post has been removed."


def test_post_existing_vote_on_archived_post_cannot_be_changed(env):
    vote(env, "post", 1)
    env.post.is_archived = True
    assert vote(env, "post", 0).status_code == 403
    assert votes_of(env)[0].vote_type == 1


# ---------------------------------------------------- post: downvote limiter
def add_downvotes(env, n, age=0, user_id=1, cls=FakeVote):
    for i in range(n):
        env.db.insert(cls(user_id=user_id, vote_type=-1, submission_id=500 + i,
                          created_utc=int(time.time()) - age))


def test_post_downvote_limit_15_per_hour_is_403(env):
    add_downvotes(env, 15)
    r = vote(env, "post", -1)
    assert (r.status_code, r.get_json()) == (
        403, {"error": "You're doing that too much. Try again later."})
    assert env.post.upvotes == 0


def test_post_downvote_14_in_hour_still_allowed(env):
    add_downvotes(env, 14)
    assert vote(env, "post", -1).status_code == 204


def test_post_downvotes_older_than_hour_not_counted(env):
    add_downvotes(env, 15, age=3700)
    assert vote(env, "post", -1).status_code == 204


def test_post_downvote_limit_counts_alt_accounts(env):
    env.user.alts = [FakeUser(id=2), FakeUser(id=3)]
    add_downvotes(env, 8, user_id=2)
    add_downvotes(env, 7, user_id=3)
    assert vote(env, "post", -1).status_code == 403


def test_post_downvote_limit_ignores_other_users(env):
    add_downvotes(env, 15, user_id=2)
    assert vote(env, "post", -1).status_code == 204


def test_post_upvote_and_clear_not_limited(env):
    add_downvotes(env, 15)
    assert vote(env, "post", 1).status_code == 204
    assert vote(env, "post", 0).status_code == 204


def test_post_limit_applies_even_when_changing_existing_downvote(env):
    add_downvotes(env, 15)
    env.db.insert(FakeVote(user_id=1, vote_type=-1, submission_id=1))  # 16th
    assert vote(env, "post", -1).status_code == 403


def test_post_rate_limit_checked_before_post_state(env):
    """CURRENT BEHAVIOUR: limiter runs before the post is even loaded."""
    add_downvotes(env, 15)
    env.post.is_banned = True
    assert "too much" in vote(env, "post", -1).get_json()["error"]


# ------------------------------------------------------- post: flush failure
def test_post_flush_failure_is_422_without_commit_or_rollback(env):
    """CURRENT BEHAVIOUR (suspected bug): bare except; no rollback so the
    failed Vote stays pending in the session."""
    env.db.fail_flush = True
    r = vote(env, "post", 1)
    assert (r.status_code, r.get_json()) == (422, {"error": "Vote already exists."})
    assert env.db.commits == 0 and env.db.rollbacks == 0


# ------------------------------------------------------------- post: OAuth
V1_POST = "/api/v1/vote/post/1/{x}"


def test_v1_post_token_vote_needs_no_formkey_and_records_app(env):
    env.add_token(env.user)
    r = env.client.post(V1_POST.format(x=1), headers=env.bearer())
    assert (r.status_code, r.data) == (204, b"")
    [row] = votes_of(env)
    assert row.app_id == 77 and row.vote_type == 1
    # headers are added twice (api() and is_not_banned both add them)
    assert r.headers.getlist("Cache-Control") == ["private", "private"]


def test_v1_post_missing_scope_is_403_json(env):
    env.add_token(env.user, scope_vote=False)
    r = env.client.post(V1_POST.format(x=1), headers=env.bearer())
    assert (r.status_code, r.get_json()) == (
        403, {"error": "401 Not Authorized. Scope `vote` is required."})


def test_v1_post_banned_app_is_403_json(env):
    env.add_token(env.user, application=FakeApplication(is_banned=True))
    r = env.client.post(V1_POST.format(x=1), headers=env.bearer())
    assert (r.status_code, r.get_json()) == (
        403, {"error": "403 Forbidden. The application `TestApp` is suspended."})


@pytest.mark.parametrize("headers", [
    {}, {"Authorization": "Bearer"}, {"Authorization": "tokonly"},
    {"Authorization": "Bearer unknown"},
])
def test_v1_post_bad_or_missing_token_is_401(env, headers):
    env.add_token(env.user)
    r = env.client.post(V1_POST.format(x=1), headers=headers)
    assert r.status_code == 401


def test_v1_post_expired_token_is_401(env):
    env.add_token(env.user, expires=int(time.time()) - 1)
    assert env.client.post(V1_POST.format(x=1),
                           headers=env.bearer()).status_code == 401


def test_v1_post_ignores_session_cookie(env):
    """CURRENT BEHAVIOUR: /api/v1 paths only honour bearer tokens."""
    assert env.client.post(V1_POST.format(x=1)).status_code == 401


def test_v1_post_suspended_user_is_403(env):
    env.add_token(env.user)
    env.user.is_banned = 1
    assert env.client.post(V1_POST.format(x=1),
                           headers=env.bearer()).status_code == 403


def test_v1_post_unversioned_route_with_token_ignores_token(env):
    """The /api/vote/... route is not an /api/v1 path: token is ignored and
    the session is used instead (formkey required)."""
    env.add_token(env.user)
    r = env.client.post(POST.format(x=1), headers=env.bearer())
    assert r.status_code == 401  # session user, but no formkey


def test_v2_post_route_session_user_gets_oauth_required_401(env):
    """CURRENT BEHAVIOUR (suspected bug): the v2 route can never succeed.
    get_logged_in_user only reads bearer tokens for /api/v1, so v2 falls back
    to the session, and api() then demands an OAuth client."""
    r = env.client.post("/api/v2/submissions/1/votes/1",
                        data={"formkey": env.formkey(env.user)})
    assert (r.status_code, r.get_json()) == (401, {
        "error": "401 Not Authorized. You must use an OAuth access token to "
                 "create or edit content."})
    assert votes_of(env) == []


def test_v2_post_route_with_bearer_token_is_401_abort(env):
    env.add_token(env.user)
    r = env.client.post("/api/v2/submissions/1/votes/1", headers=env.bearer())
    assert r.status_code == 401
    assert votes_of(env) == []


# ------------------------------------------------------------------ comments
def test_comment_upvote_creates_vote_and_updates_counts(env):
    r = vote(env, "comment", 1)
    assert (r.status_code, r.data) == (204, b"")
    [row] = votes_of(env, FakeCommentVote)
    assert (row.user_id, row.vote_type, row.comment_id) == (1, 1, 1)
    assert row.creation_ip == "127.0.0.1" and row.app_id is None
    c = env.comment
    assert (c.upvotes, c.downvotes, c.score_top, c.score_hot) == (1, 0, 1, 333)
    assert env.db.commits == 1


def test_comment_transitions_up_down_clear(env):
    vote(env, "comment", 1)
    vote(env, "comment", -1)
    [row] = votes_of(env, FakeCommentVote)
    assert row.vote_type == -1
    assert (env.comment.upvotes, env.comment.downvotes) == (0, 1)
    vote(env, "comment", 0)
    assert votes_of(env, FakeCommentVote)[0].vote_type == 0
    assert (env.comment.upvotes, env.comment.downvotes) == (0, 0)


def test_comment_duplicate_vote_idempotent(env):
    vote(env, "comment", 1)
    assert vote(env, "comment", 1).status_code == 204
    assert len(votes_of(env, FakeCommentVote)) == 1


def test_comment_headers_and_vote_tables_are_separate(env):
    r = vote(env, "comment", 1)
    assert r.headers["Cache-Control"] == "private"
    assert votes_of(env) == []  # no post Vote rows created


@pytest.mark.parametrize("x", ["2", "abc", "01", "-2"])
def test_comment_invalid_vote_value_is_400(env, x):
    assert vote(env, "comment", x).status_code == 400


def test_comment_bot_header_403(env):
    r = env.client.post(COMMENT.format(x=1), headers={"X-User-Type": "BOT"},
                        data={"formkey": env.formkey(env.user)})
    assert r.status_code == 403


def test_comment_logged_out_401(legacy):
    legacy.db.insert(FakeComment(id=1))
    assert legacy.client.post(COMMENT.format(x=1)).status_code == 401


def test_comment_bad_formkey_401_and_negative_balance_402(env):
    assert vote(env, "comment", 1, formkey="nope").status_code == 401
    env.user.negative_balance_cents = 1
    assert vote(env, "comment", 1).status_code == 402


@pytest.mark.parametrize("setup,msg", [
    (lambda e: setattr(e.comment, "is_banned", True),
     "That comment has been removed."),
    (lambda e: setattr(e.comment, "deleted_utc", 9),
     "That comment has been deleted."),
    (lambda e: setattr(e.post, "is_archived", True),
     "This post and its comments are archived and can no longer be voted on."),
    (lambda e: setattr(e.comment, "is_blocking", True),
     "You can't vote on comments made by users who you are blocking."),
    (lambda e: setattr(e.comment, "is_blocked", True),
     "You can't vote on comments made by users who are blocking you."),
])
def test_comment_unavailable_states_are_403_json(env, setup, msg):
    setup(env)
    r = vote(env, "comment", 1)
    assert (r.status_code, r.get_json()) == (403, {"error": msg})
    assert votes_of(env, FakeCommentVote) == []


def test_comment_vote_allowed_when_parent_post_removed_or_deleted(env):
    """CURRENT BEHAVIOUR (suspected gap): only the post's archived flag is
    checked, not whether the parent post is banned or deleted."""
    env.post.is_banned = True
    env.post.deleted_utc = 5
    assert vote(env, "comment", 1).status_code == 204


def test_comment_downvotes_have_no_rate_limit(env):
    """CURRENT BEHAVIOUR (suspected gap): the 15/hour downvote limiter exists
    only on posts."""
    for i in range(20):
        env.db.insert(FakeCommentVote(user_id=1, vote_type=-1,
                                      comment_id=900 + i))
    assert vote(env, "comment", -1).status_code == 204


def test_comment_own_comment_allowed(env):
    env.comment.author_id = env.user.id
    assert vote(env, "comment", 1).status_code == 204


def test_comment_flush_failure_is_422(env):
    env.db.fail_flush = True
    r = vote(env, "comment", 1)
    assert (r.status_code, r.get_json()) == (422, {"error": "Vote already exists."})
    assert env.db.commits == 0 and env.db.rollbacks == 0


def test_v1_comment_token_vote_records_app_id(env):
    env.add_token(env.user)
    r = env.client.post("/api/v1/vote/comment/1/-1", headers=env.bearer())
    assert r.status_code == 204
    assert votes_of(env, FakeCommentVote)[0].app_id == 77


def test_v1_comment_missing_scope(env):
    env.add_token(env.user, scope_vote=False)
    r = env.client.post("/api/v1/vote/comment/1/1", headers=env.bearer())
    assert r.status_code == 403


def test_v2_comment_route_is_unreachable_like_post(env):
    r = env.client.post("/api/v2/comments/1/votes/1",
                        data={"formkey": env.formkey(env.user)})
    assert r.status_code == 401
    assert votes_of(env, FakeCommentVote) == []
