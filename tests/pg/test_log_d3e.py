"""Anonymous aliases on the real app and Postgres (the Log's slice D3e; the numbering rules are tested without Postgres in tests/test_anon_aliases.py).

The promises: "Anonymous #N" is given in the order people become anonymous in a thread, the post's author is the same number on the post and on their
anonymous comments, another thread numbers its own people, "Remove my name", a delete and an account's deletion give a number at that moment, the Log's
lines draw the number, and nobody but the author and admins is ever told the account."""
import re
import secrets
import time

from sqlalchemy import text

from pg.test_log_d3c import add_comment, add_post, comment_on, get, guild, one, post, rows

PASSWORD = "ci-Password-1"


def submit_anonymous(member, title=None):
    """An anonymous post made through the real submit route (which gives its author a number), returned as (id, base36)."""
    client = member.browser()
    title = title or f"Aliased post {secrets.token_hex(3)}"
    served = post(client, member, "/api/vue/submit", title=title, body="words about gardens", post_to_profile="1", anonymous="true")
    assert served.status_code == 200, served.get_data(as_text=True)[:300]
    found = one(real_app_of(member), "SELECT id FROM submissions WHERE author_id = :a ORDER BY id DESC LIMIT 1", a=member.id)["id"]
    from ruqqus.helpers.base36 import base36encode
    return found, base36encode(found)


def real_app_of(member):
    return member.app, member.db_session


def numbers(real_app, post_id):
    return {r["user_id"]: r["n"] for r in rows(real_app, "SELECT user_id, n FROM anon_aliases WHERE scope_kind = 'post' AND scope_id = :s", s=post_id)}


def visible_names(page):
    """The `Anonymous #N` names a page draws, in order."""
    return re.findall(r"Anonymous #(\d+)", page)


def test_numbers_follow_the_order_people_become_anonymous_and_the_author_is_one_number(real_app, make_member):
    author, second, third, stranger = (make_member() for _ in range(4))
    board_id, _ = guild(real_app)
    post_id, base36 = submit_anonymous(author)
    assert numbers(real_app, post_id) == {author.id: 1}, "the author counts from their post"
    assert comment_on(second.browser(), second, base36, anonymous="true").status_code == 200
    assert comment_on(third.browser(), third, base36, anonymous="true").status_code == 200
    assert comment_on(author.browser(), author, base36).status_code == 200                 # anonymous by default on their own anonymous post
    assert comment_on(second.browser(), second, base36, anonymous="true").status_code == 200
    assert numbers(real_app, post_id) == {author.id: 1, second.id: 2, third.id: 3}, "in order, once each, and the author is the same number on their comment"
    page = get(stranger.browser(), f"/post/{base36}").get_data(as_text=True)
    assert sorted(visible_names(page)) and set(visible_names(page)) == {"1", "2", "3"}
    for member in (author, second, third):
        assert member.username not in page, "the account is never told"
    # the author sees their own post under their name (the existing rule); an admin always sees the account
    assert author.username in get(author.browser(), f"/post/{base36}").get_data(as_text=True)


def test_another_thread_numbers_its_own_people(real_app, make_member):
    first, second = make_member(), make_member()
    one_id, one36 = submit_anonymous(first)
    two_id, two36 = submit_anonymous(second)
    assert comment_on(first.browser(), first, two36, anonymous="true").status_code == 200
    assert comment_on(second.browser(), second, one36, anonymous="true").status_code == 200
    assert numbers(real_app, one_id) == {first.id: 1, second.id: 2}
    assert numbers(real_app, two_id) == {second.id: 1, first.id: 2}, "the same two people, the other way round: never a global id"


def test_remove_my_name_and_a_delete_give_a_number_at_that_moment_and_the_log_draws_it(real_app, make_member):
    author, commenter = make_member(), make_member()
    board_id, _ = guild(real_app)
    post_id, base36 = add_post(author, board_id)                                             # a named post, so nobody has a number yet
    comment_id, comment36 = add_comment(commenter, post_id, board_id, body="my words ZZED", age=7200)
    assert numbers(real_app, post_id) == {}
    assert post(commenter.browser(), commenter, f"/delete/comment/{comment36}").status_code == 204        # after the window: public, anonymous at once
    assert numbers(real_app, post_id) == {commenter.id: 1}
    # then the post's author takes their name off: the next number
    assert post(author.browser(), author, f"/delete_post/{base36}", how="unlink").status_code == 204
    assert numbers(real_app, post_id) == {commenter.id: 1, author.id: 2}
    app, _ = real_app
    deleted = get(app.test_client(), "/log?type=deletion").get_data(as_text=True)
    start = deleted.index("my words ZZED")
    assert "Anonymous #1" in deleted[max(start - 2500, 0):start], "the Deleted tab's line draws the number its author has in that thread"
    assert commenter.username not in deleted[max(start - 2500, 0):start + 500]


def test_an_account_deleted_later_takes_the_next_number_and_the_numbers_outlive_it(real_app, make_member):
    author, early, leaver, stranger = (make_member() for _ in range(4))
    board_id, _ = guild(real_app)
    post_id, base36 = submit_anonymous(author)
    assert comment_on(early.browser(), early, base36, anonymous="true").status_code == 200
    named, _ = add_comment(leaver, post_id, board_id, body="written under a name ZZLV")
    db = leaver.db_session()
    db.execute(text("UPDATE users SET created_utc = :old WHERE id = :u"), {"old": int(time.time()) - 90 * 86400, "u": leaver.id})
    db.commit()
    assert post(leaver.browser(), leaver, "/settings/delete_account", password=PASSWORD, delete_reason="leaving").status_code == 200
    assert numbers(real_app, post_id) == {author.id: 1, early.id: 2, leaver.id: 3}, "given when the account was deleted, after the others"
    page = get(stranger.browser(), f"/post/{base36}").get_data(as_text=True)
    assert "Anonymous #3" in page and "ZZLV" in page and leaver.username not in page
    assert rows(real_app, "SELECT 1 FROM anon_aliases WHERE user_id = :u", u=leaver.id), "the number outlives the account"
    assert one(real_app, "SELECT is_anonymous FROM comments WHERE id = :i", i=named)["is_anonymous"] is True


def test_an_admin_always_sees_the_account_and_the_json_carries_the_number(real_app, make_member):
    author, commenter, admin = make_member(), make_member(), make_member()
    db = admin.db_session()
    db.execute(text("UPDATE users SET admin_level = 3 WHERE id = :u"), {"u": admin.id})
    db.commit()
    post_id, base36 = submit_anonymous(author)
    assert comment_on(commenter.browser(), commenter, base36, anonymous="true").status_code == 200
    page = get(admin.browser(), f"/post/{base36}").get_data(as_text=True)
    assert commenter.username in page and author.username in page, "admins see the accounts"
    comment_id = one(real_app, "SELECT id FROM comments WHERE author_id = :a", a=commenter.id)["id"]
    from ruqqus.classes import Comment
    app, db_session = real_app
    with app.test_request_context("/"):
        session = db_session()
        from flask import g
        g.db = session
        g.v = None
        comment = session.get(Comment, comment_id)
        assert comment.json_raw["alias"] == 2 and comment.json_raw["author_name"] is None
