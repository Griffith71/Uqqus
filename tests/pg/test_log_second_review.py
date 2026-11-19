"""The second review of the Log (2026-11-17) on the real app and Postgres: what the fixes promise, end to end. The rules and the SQL are in the unit tests
beside each fix and tests/test_log_second_review_assets.py; these are the ones only a running app can answer."""
import time

from sqlalchemy import text

from tests.pg.test_log_public import add_comment, add_post, address, admin_of, get, guild, guildmaster, hide, post, snapshots

BASE = "http://localhost"
HEADERS = {"User-Agent": "ci-log-second-review"}


def test_the_json_api_gives_no_title_for_a_deleted_post(make_member, real_app):
    """C00."""
    board_id, name = guild(real_app)
    author, other = make_member(), make_member()
    post_id, b36 = add_post(author, board_id, "TITLE-THE-API-MUST-NOT-SERVE", "<p>words</p>")
    assert post(author.browser(), author, f"/delete_post/{b36}").status_code in (200, 204, 302)
    answer = get(other.browser(), f"/api/v2/submissions/{b36}")
    assert "TITLE-THE-API-MUST-NOT-SERVE" not in answer.get_data(as_text=True)


def test_a_threads_page_has_the_post_pages_gates(make_member, real_app):
    """C01: a live copy in a private guild was redirected to its address (naming the guild and the forwarder), and a deleted one listed every comment."""
    private_id, private_name = guild(real_app, private=True)
    author, stranger, commenter = make_member(), make_member(), make_member()
    post_id, b36 = add_post(author, private_id, "A private post", "<p>x</p>", public=False)
    visitor = real_app[0].test_client()
    for client in (visitor, stranger.browser()):
        answer = get(client, f"/log/post/{b36}")
        assert answer.status_code == 404 and private_name not in answer.headers.get("Location", ""), answer.status_code
    comment_id, c36 = add_comment(commenter, post_id, private_id, "<p>PRIVATE-GUILD-COMMENT</p>")
    _, db_session = real_app
    db = db_session()
    db.execute(text("UPDATE submissions SET deleted_utc = :n WHERE id = :p"), {"n": int(time.time()), "p": post_id})
    db.commit()
    for client in (visitor, stranger.browser()):
        answer = get(client, f"/log/post/{b36}")
        assert answer.status_code == 404 and "PRIVATE-GUILD-COMMENT" not in answer.get_data(as_text=True)


def test_belonging_to_a_guild_does_not_open_what_another_that_went_private_logged(make_member, real_app):
    """C02."""
    board_id, name, gm, author, post_id, b36 = hide(real_app, make_member)
    other_board, _ = guild(real_app)
    other_gm = guildmaster(make_member, other_board)
    from tests.pg.test_log_public import entries
    (row,) = entries(real_app, board_id=board_id, target_submission_id=post_id)
    visitor = real_app[0].test_client()
    assert get(visitor, f"/log/{row['id']}").status_code == 200                      # public while the guild is
    _, db_session = real_app
    db = db_session()
    db.execute(text("UPDATE boards SET is_private = true WHERE id = :b"), {"b": board_id})
    db.commit()
    assert get(visitor, f"/log/{row['id']}").status_code == 404
    assert get(other_gm.browser(), f"/log/{row['id']}").status_code == 404           # a guildmaster of ANOTHER guild used to read it
    assert get(gm.browser(), f"/log/{row['id']}").status_code == 200                 # its own guildmaster still does


def test_the_gate_of_a_circle_post_made_anonymous_names_nobody(make_member, real_app):
    """C52."""
    board_id, name = guild(real_app)
    author, stranger = make_member(), make_member()
    post_id, b36 = add_post(author, 2, "A post for circles", "<p>x</p>", public=False)
    _, db_session = real_app
    db = db_session()
    db.execute(text("UPDATE submissions SET audience = 4 WHERE id = :p"), {"p": post_id})
    db.commit()
    named = get(stranger.browser(), f"/@{author.username}/post/{b36}")
    assert named.status_code == 403 and author.username in named.get_data(as_text=True)            # a live, named Circle post: the gate names its owner
    db.execute(text("UPDATE submissions SET is_anonymous = true WHERE id = :p"), {"p": post_id})
    db.commit()
    gone = get(stranger.browser(), f"/post/{b36}")
    assert gone.status_code == 404 and author.username not in gone.get_data(as_text=True)


def test_everything_logged_about_a_post_for_circles_is_the_staffs(make_member, real_app):
    """C59: an admin's removal of a post for circles was a public entry naming it."""
    from ruqqus.classes import ModAction
    admin, author = admin_of(make_member), make_member()
    post_id, b36 = add_post(author, 2, "A post for circles", "<p>x</p>", public=False)
    _, db_session = real_app
    db = db_session()
    db.execute(text("UPDATE submissions SET audience = 4 WHERE id = :p"), {"p": post_id})
    db.add(ModAction(kind="ban_post", user_id=admin.id, board_id=2, target_submission_id=post_id, note="admin action | Spam"))
    db.commit()
    from tests.pg.test_log_public import entries
    (row,) = entries(real_app, target_submission_id=post_id)
    assert row["audience"] == 2                                                       # staff: not public, not a guild's
    assert f"#{b36}" not in get(real_app[0].test_client(), "/log").get_data(as_text=True)


def test_a_deleted_post_takes_no_edit_and_no_comment_under_its_comments(make_member, real_app):
    """C07."""
    board_id, name = guild(real_app)
    author, commenter = make_member(), make_member()
    post_id, b36 = add_post(author, board_id, "To be deleted", "<p>x</p>")
    comment_id, c36 = add_comment(commenter, post_id, board_id, "<p>a comment</p>")
    assert post(author.browser(), author, f"/delete_post/{b36}").status_code in (200, 204, 302)
    edit = post(author.browser(), author, f"/edit_post/{b36}", title="New title", body="new words")
    assert edit.status_code == 403
    reply = post(commenter.browser(), commenter, "/api/comment", parent_fullname=f"t3_{c36}", body="a comment under a comment")
    assert reply.status_code == 403 and "deleted" in reply.get_data(as_text=True), (reply.status_code, reply.get_data(as_text=True)[:200])


def test_a_hidden_comments_words_are_not_in_the_chat_card(make_member, real_app):
    """C28."""
    board_id, name = guild(real_app)
    gm, author, reader = guildmaster(make_member, board_id), make_member(), make_member()
    post_id, b36 = add_post(author, board_id, "A post", "<p>x</p>")
    comment_id, c36 = add_comment(author, post_id, board_id, "<p>WORDS-A-GUILD-HID</p>")
    before = get(reader.browser(), f"/api/chat/comment_preview/{c36}").get_json()
    assert before["ok"] and "WORDS-A-GUILD-HID" in before["text"]
    _, db_session = real_app
    db = db_session()
    db.execute(text("UPDATE comments SET hidden_by_guild = true WHERE id = :c"), {"c": comment_id})
    db.commit()
    after = get(reader.browser(), f"/api/chat/comment_preview/{c36}").get_json()
    assert not after["ok"] and "WORDS-A-GUILD-HID" not in str(after)
