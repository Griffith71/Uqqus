"""The history page of a deleted item on the real app and Postgres (the second review of the Log, C78; the template is rendered for every state without
Postgres in tests/test_log_history_note_assets.py). The promises, end to end:

- the page of an item its author deleted never says "removed by the site's administrators" for text the site cleared by itself 90 days later
  (`scripts/recomputes.py` sets `purged_utc`), for a post, a comment, an erase inside the grace window and a child account's line-only delete;
- an item an administrator removed or purged while it was live still names them, and a guild's hide keeps its own sentence;
- a forwarded copy a delete reached has no row of its own and reads "Deleted", never "nothing recorded";
- a deleted item has no "(view current)", and a live one keeps it;
- who may open the page is unchanged: a deleted item's is the author's and the admins' alone."""
import html
import time

from sqlalchemy import text

from tests.pg.test_log_public import add_comment, add_post, admin_of, entries, get, guild, post, snapshots

CLEARED = "The earlier versions aren't shown: this was deleted and its text has been cleared."
ADMINS = "The text isn't shown: this was removed by the site's administrators."
NOTHING = "No edits or removals recorded for this"
DELETED_CARD = "<strong>Deleted</strong>"
DONE = (200, 204, 302)


def run(real_app, statement, **params):
    _, db_session = real_app
    db = db_session()
    db.rollback()
    db.execute(text(statement), params)
    db.commit()


def one(real_app, statement, **params):
    _, db_session = real_app
    db = db_session()
    db.rollback()
    db.expire_all()
    return db.execute(text(statement), params).first()


def history(client, kind, b36):
    """The page, unescaped (the template escapes an apostrophe), or None when the route says no."""
    answer = get(client, f"/{kind}/{b36}/history")
    if answer.status_code != 200:
        assert answer.status_code == 404, answer.status_code
        return None
    return html.unescape(answer.get_data(as_text=True))


def edited(real_app, author, b36, post_id):
    """One edit of an hour-old post, so the history has a version to show or to hide."""
    assert post(author.browser(), author, f"/edit_post/{b36}", title="NEW-TITLE", body="NEW-WORDS", edit_summary="rewrite").status_code in (200, 302)
    row = one(real_app, "SELECT previous_title FROM content_edit_history WHERE target_submission_id = :p AND action = 'edit'", p=post_id)
    assert row is not None and row[0].startswith("OLD-TITLE"), "the edit left the version edited away on its row"


def test_a_deleted_posts_history_says_cleared_and_not_removed_by_the_administrators_once_the_site_has_cleared_it(make_member, real_app):
    board_id, name = guild(real_app)
    author, admin, stranger = make_member(), admin_of(make_member), make_member()
    title = "OLD-TITLE a post to be deleted"
    post_id, b36 = add_post(author, board_id, title, "<p>OLD-WORDS</p>")
    edited(real_app, author, b36, post_id)
    assert post(author.browser(), author, f"/delete_post/{b36}").status_code in DONE
    for member in (author, admin):
        page = history(member.browser(), "post", b36)
        assert page is not None and "Deleted by" in page and "OLD-TITLE" in page, member.username      # the versions are shown until the clearing
        assert CLEARED not in page and ADMINS not in page and "(view current)" not in page
    # what scripts/recomputes.py does to every post its author deleted 90 days ago
    run(real_app, "UPDATE submissions SET purged_utc = :n WHERE id = :p", n=int(time.time()), p=post_id)
    for member in (author, admin):
        page = history(member.browser(), "post", b36)
        assert page is not None, member.username
        assert page.count(CLEARED) == 1 and ADMINS not in page, member.username
        assert "OLD-TITLE" not in page and "OLD-WORDS" not in page, "the cleared text is not shown to anyone"
        assert "Deleted by" in page and "(view current)" not in page and f"/post/{b36}/log" in page
    assert history(stranger.browser(), "post", b36) is None and history(real_app[0].test_client(), "post", b36) is None, "a stranger still gets a 404"


def test_a_deleted_comments_history_says_the_same(make_member, real_app):
    board_id, name = guild(real_app)
    author, commenter, admin, stranger = make_member(), make_member(), admin_of(make_member), make_member()
    post_id, b36 = add_post(author, board_id, "A post", "<p>words</p>")
    comment_id, c36 = add_comment(commenter, post_id, board_id, "<p>OLD-COMMENT-WORDS</p>")
    assert post(commenter.browser(), commenter, f"/edit_comment/{c36}", body="NEW-COMMENT-WORDS", edit_summary="rewrite").status_code in (200, 204, 302, 303)
    row = one(real_app, "SELECT previous_body_html FROM content_edit_history WHERE target_comment_id = :c AND action = 'edit'", c=comment_id)
    assert row is not None and "OLD-COMMENT-WORDS" in row[0]
    assert post(commenter.browser(), commenter, f"/delete/comment/{c36}").status_code in DONE
    for member in (commenter, admin):
        page = history(member.browser(), "comment", c36)
        assert page is not None and "OLD-COMMENT-WORDS" in page and CLEARED not in page and "(view current)" not in page, member.username
    run(real_app, "UPDATE comments SET purged_utc = :n WHERE id = :c", n=int(time.time()), c=comment_id)
    for member in (commenter, admin):
        page = history(member.browser(), "comment", c36)
        assert page is not None and page.count(CLEARED) == 1 and ADMINS not in page, member.username
        assert "OLD-COMMENT-WORDS" not in page and "Deleted by" in page, member.username
    assert history(stranger.browser(), "comment", c36) is None


def add_version(member, post_id, board_id, title):
    """A version the author edited away before the account was locked (a child account can't edit: an edit is something other members read)."""
    from ruqqus.classes import ContentEditHistory
    db = member.db_session()
    db.rollback()
    db.add(ContentEditHistory(actor_id=member.id, target_submission_id=post_id, board_id=board_id, action="edit", summary="rewrite",
                              previous_title=title, previous_body="before", previous_body_html="<p>before</p>"))
    db.commit()


def test_an_item_erased_inside_the_grace_window_is_not_blamed_on_the_administrators_either(make_member, real_app):
    board_id, name = guild(real_app)
    author, admin = make_member(), admin_of(make_member)
    post_id, b36 = add_post(author, board_id, "ERASED-TITLE", "<p>ERASED-WORDS</p>", age=60)
    assert post(author.browser(), author, f"/delete_post/{b36}").status_code in DONE
    assert not entries(real_app, target_submission_id=post_id), "inside the window it is an erase: no entry"
    assert [s["kind"] for s in snapshots(real_app, target_submission_id=post_id)] == ["grace"]
    for stage in ("deleted", "cleared"):
        if stage == "cleared":
            run(real_app, "UPDATE submissions SET purged_utc = :n WHERE id = :p", n=int(time.time()), p=post_id)
        for member in (author, admin):
            page = history(member.browser(), "post", b36)
            assert page is not None and page.count("Deleted by") == 1 and DELETED_CARD not in page, (stage, member.username)
            assert f'href="/post/{b36}/copy"' in page, "the private copy the page offers: an erase leaves no entry and no thread, so no current page either"
            assert "(view current)" not in page and ADMINS not in page, (stage, member.username)


def test_a_childs_line_only_delete_reads_cleared_once_the_site_has_cleared_it(make_member, real_app):
    board_id, name = guild(real_app)
    child, admin = make_member(), admin_of(make_member)
    post_id, b36 = add_post(child, board_id, "LINE-TITLE", "<p>LINE-WORDS</p>")
    add_version(child, post_id, board_id, "OLD-TITLE-OF-THE-CHILD")
    run(real_app, "UPDATE users SET age_declared = 2, age_declared_utc = 5, filter_level = 2, age_lock = 1 WHERE id = :u", u=child.id)
    assert post(child.browser(), child, f"/delete_post/{b36}").status_code in DONE
    (kept,) = snapshots(real_app, target_submission_id=post_id)
    assert kept["kind"] == "grace", "a child's delete keeps its line and a private copy only"
    for member in (child, admin):
        page = history(member.browser(), "post", b36)
        assert page is not None and "OLD-TITLE-OF-THE-CHILD" in page and CLEARED not in page, member.username
    run(real_app, "UPDATE submissions SET purged_utc = :n WHERE id = :p", n=int(time.time()), p=post_id)
    for member in (child, admin):
        page = history(member.browser(), "post", b36)
        assert page is not None and page.count(CLEARED) == 1 and ADMINS not in page, member.username
        assert "OLD-TITLE-OF-THE-CHILD" not in page and "Deleted by" in page and "(view current)" not in page, member.username


def test_something_an_administrator_removed_or_purged_while_it_was_live_still_names_them(make_member, real_app):
    board_id, name = guild(real_app)
    author, admin = make_member(), admin_of(make_member)
    post_id, b36 = add_post(author, board_id, "OLD-TITLE a live post", "<p>OLD-WORDS</p>")
    edited(real_app, author, b36, post_id)
    run(real_app, "UPDATE submissions SET purged_utc = :n WHERE id = :p", n=int(time.time()), p=post_id)
    for member in (author, admin):
        page = history(member.browser(), "post", b36)
        assert page is not None and page.count(ADMINS) == 1 and CLEARED not in page and "OLD-TITLE" not in page, member.username
        assert "(view current)" in page, "a live item keeps its link to the item"
    run(real_app, "UPDATE submissions SET purged_utc = 0, is_banned = true WHERE id = :p", p=post_id)
    admin_page, author_page = history(admin.browser(), "post", b36), history(author.browser(), "post", b36)
    assert "OLD-TITLE" in admin_page and ADMINS not in admin_page, "an admin reads what an admin removed"
    assert author_page.count(ADMINS) == 1 and "OLD-TITLE" not in author_page
    # removed by an administrator and then deleted and cleared: an administrator is still why the text is not shown
    run(real_app, "UPDATE submissions SET purged_utc = :n, deleted_utc = :n WHERE id = :p", n=int(time.time()), p=post_id)
    for member in (author, admin):
        page = history(member.browser(), "post", b36)
        assert page is not None and page.count(ADMINS) == 1 and CLEARED not in page, member.username


def test_a_guilds_hide_keeps_its_own_sentence_on_a_live_posts_history(make_member, real_app):
    board_id, name = guild(real_app)
    author = make_member()
    post_id, b36 = add_post(author, board_id, "OLD-TITLE a hidden post", "<p>OLD-WORDS</p>")
    edited(real_app, author, b36, post_id)
    run(real_app, "UPDATE submissions SET hidden_by_guild = true WHERE id = :p", p=post_id)
    stranger = make_member()
    page = history(stranger.browser(), "post", b36)
    assert page is not None and page.count("The earlier versions aren't shown while this is hidden.") == 1
    assert ADMINS not in page and CLEARED not in page and "OLD-TITLE" not in page


def test_a_forwarded_copy_a_delete_reached_reads_deleted_and_never_nothing_recorded(make_member, real_app):
    from ruqqus.classes import Submission, SubmissionAux
    from ruqqus.helpers.base36 import base36encode
    board_id, name = guild(real_app)
    other_board, _ = guild(real_app)
    author, stranger, admin = make_member(), make_member(), admin_of(make_member)
    post_id, b36 = add_post(author, board_id, "A post forwarded", "<p>words</p>")
    db = author.db_session()
    db.rollback()
    with author.app.test_request_context("/"):
        copy = Submission(author_id=author.id, board_id=other_board, original_board_id=board_id, repost_id=post_id,
                          created_utc=int(time.time()) - 3600, post_public=True)
    db.add(copy)
    db.flush()
    # read the id while the session is live: the requests below tear the scoped session down, and `copy` is then a detached instance
    copy_id = copy.id
    db.add(SubmissionAux(id=copy_id, title="A post forwarded", body="words", body_html="<p>words</p>"))
    db.commit()
    copy36 = base36encode(copy_id)
    live = history(author.browser(), "post", copy36)
    assert NOTHING in live and DELETED_CARD not in live and "(view current)" in live, "while it is up the page is as it always was"
    assert post(author.browser(), author, f"/delete_post/{b36}").status_code in DONE
    assert one(real_app, "SELECT deleted_utc FROM submissions WHERE id = :c", c=copy_id)[0] > 0, "the delete reached the copy"
    assert one(real_app, "SELECT count(*) FROM content_edit_history WHERE target_submission_id = :c", c=copy_id)[0] == 0, "and left it no row of its own"
    for member in (author, admin):
        page = history(member.browser(), "post", copy36)
        assert page is not None and page.count(DELETED_CARD) == 1 and NOTHING not in page, member.username
        assert "(view current)" not in page and "Deleted by" not in page, "the page does not say who deleted a copy it has no row for"
    assert history(stranger.browser(), "post", copy36) is None
    primary = history(author.browser(), "post", b36)
    assert "Deleted by" in primary and DELETED_CARD not in primary, "the original has its delete row and no second card"


def test_a_live_posts_history_keeps_view_current_and_the_empty_line(make_member, real_app):
    board_id, name = guild(real_app)
    author, stranger = make_member(), make_member()
    post_id, b36 = add_post(author, board_id, "A live post", "<p>words</p>")
    for member in (author, stranger):
        page = history(member.browser(), "post", b36)
        assert page is not None and "(view current)" in page and f"{NOTHING} post yet." in page, member.username
        assert DELETED_CARD not in page and CLEARED not in page and ADMINS not in page
