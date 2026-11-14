"""Anonymous credits on a post written with others, on the real app and Postgres (the Log's slice D3f, decision 21).

The promises: a post made anonymous from the start gives every co-author an anonymous credit; any co-author may hide theirs (only theirs); an account's deletion
hides its credits; an anonymous credit is told to nobody but admins and the co-author themselves (the post's author sees it in their Co-authors sheet), so the
byline, the profile, the Following feed, the counts, the live-thread header and the shared-post pages show "Anonymous #N" or nothing; a block never leaves a post out
for an anonymous co-author; and the number is the person's number in that thread."""
import secrets
import time

from sqlalchemy import text

from pg.test_log_d3c import add_comment, add_post, get, guild, one, post, rows
from pg.test_log_d3d import admin_of
from pg.test_log_d3e import numbers

PASSWORD = "ci-Password-1"


def invite(author, base36, *names):
    client = author.browser()
    return post(client, author, f"/api/coauthor/{base36}/invite", usernames=" ".join(names))


def accept(member, base36, **fields):
    return post(member.browser(), member, f"/api/coauthor/{base36}/accept", **fields)


def forget_cache():
    """The worker's 30-second list of posts that have co-authors: rows put in by hand need it read again (the routes do this themselves)."""
    from ruqqus.helpers import coauthors
    coauthors.forget()


def credit(real_app, post_id, member):
    return one(real_app, "SELECT status, is_anonymous FROM post_coauthors WHERE post_id = :p AND user_id = :u", p=post_id, u=member.id)


def submit(member, **extra):
    served = post(member.browser(), member, "/api/vue/submit", title=f"Shared post {secrets.token_hex(3)}", body="words about gardens",
                  post_to_profile="1", **extra)
    assert served.status_code == 200, served.get_data(as_text=True)[:300]
    from ruqqus.helpers.base36 import base36encode
    found = one((member.app, member.db_session), "SELECT id FROM submissions WHERE author_id = :a ORDER BY id DESC LIMIT 1", a=member.id)["id"]
    return found, base36encode(found)


def test_a_post_made_anonymous_gives_every_co_author_an_anonymous_credit_and_names_nobody(real_app, make_member):
    author, second, stranger = (make_member() for _ in range(3))
    post_id, base36 = submit(author, anonymous="true")
    invited = invite(author, base36, second.username)
    assert invited.status_code == 200, invited.get_data(as_text=True)[:300]
    assert credit(real_app, post_id, second) == {"status": "pending", "is_anonymous": True}, "anonymous from the start sets the flag"
    notice = rows(real_app, "SELECT a.body FROM notifications n JOIN comments_aux a ON a.id = n.comment_id WHERE n.user_id = :u ORDER BY n.id DESC LIMIT 1", u=second.id)[0]["body"]
    assert author.username not in notice and "anonymous post" in notice, "the invitation never names the author of an anonymous post"
    page = get(second.browser(), f"/coauthor/{base36}").get_data(as_text=True)
    assert author.username not in page and "anonymous post" in page
    assert accept(second, base36).status_code in (200, 302)
    assert numbers(real_app, post_id) == {author.id: 1, second.id: 2}, "each credit's number is given when it is made: the author's first, then theirs"
    seen = get(stranger.browser(), f"/post/{base36}").get_data(as_text=True)
    assert "Anonymous #1" in seen and "Anonymous #2" in seen
    assert author.username not in seen and second.username not in seen
    # the co-author sees their own credit; an admin sees the account; the profile and the counts leave it out
    assert second.username in get(second.browser(), f"/post/{base36}").get_data(as_text=True)
    admin = admin_of(make_member)
    assert second.username in get(admin.browser(), f"/post/{base36}").get_data(as_text=True)
    assert "Shared post" not in get(stranger.browser(), f"/@{second.username}").get_data(as_text=True), "not on the co-author's profile"


def test_a_co_author_can_hide_only_their_own_credit_and_it_cannot_be_undone(real_app, make_member):
    author, second, third, stranger = (make_member() for _ in range(4))
    board_id, _ = guild(real_app)
    post_id, base36 = add_post(author, board_id, title=f"Shared named {secrets.token_hex(3)}")
    for m in (second, third):
        db = m.db_session()
        db.execute(text("INSERT INTO post_coauthors (post_id, user_id, invited_by_id, status, created_utc, accepted_utc, is_anonymous) "
                        "VALUES (:p, :u, :a, 'accepted', :n, :n, false)"), {"p": post_id, "u": m.id, "a": author.id, "n": int(time.time())})
        db.commit()
    forget_cache()
    page = get(stranger.browser(), f"/post/{base36}").get_data(as_text=True)
    assert second.username in page and third.username in page, "named credits are named"
    assert post(second.browser(), second, f"/api/coauthor/{base36}/hide_name").status_code in (200, 302)
    assert credit(real_app, post_id, second)["is_anonymous"] is True and credit(real_app, post_id, third)["is_anonymous"] is False
    page = get(stranger.browser(), f"/post/{base36}").get_data(as_text=True)
    assert second.username not in page and "Anonymous #1" in page and third.username in page, "only their credit changed"
    assert author.username in page, "the post's own author is still named"
    assert post(second.browser(), second, f"/api/coauthor/{base36}/hide_name").status_code in (200, 302), "asking again changes nothing"
    assert numbers(real_app, post_id) == {second.id: 1}
    assert post(stranger.browser(), stranger, f"/api/coauthor/{base36}/hide_name").status_code == 404, "only a co-author may"
    # the co-author's own sheet is told, the author's list names them, a stranger's shows the alias
    mine = get(second.browser(), f"/api/coauthor/{base36}/list").get_json()
    assert {c["username"] for c in mine["coauthors"]} == {second.username, third.username}
    authors = get(author.browser(), f"/api/coauthor/{base36}/list").get_json()
    assert second.username in {c["username"] for c in authors["coauthors"]}, "the author invited them: they are told"
    others = get(stranger.browser(), f"/api/coauthor/{base36}/list").get_json()
    assert second.username not in str(others) and any(c["alias"] and c["username"] == "Anonymous #1" for c in others["coauthors"])


def test_a_hidden_credit_is_found_again_in_history_anonymous_and_only_by_the_member_it_belongs_to(real_app, make_member):
    author, second, third = (make_member() for _ in range(3))
    board_id, _ = guild(real_app)
    title = f"Credit in history {secrets.token_hex(3)}"
    post_id, base36 = add_post(author, board_id, title=title)
    for m in (second, third):
        db = m.db_session()
        db.execute(text("INSERT INTO post_coauthors (post_id, user_id, invited_by_id, status, created_utc, accepted_utc, is_anonymous) "
                        "VALUES (:p, :u, :a, 'accepted', :n, :n, false)"), {"p": post_id, "u": m.id, "a": author.id, "n": int(time.time())})
        db.commit()
    forget_cache()
    listed = lambda member: title in get(member.browser(), "/history/anonymous").get_data(as_text=True)
    assert not listed(second) and not listed(author), "a named credit is not an anonymous one: nothing to find there"
    assert post(second.browser(), second, f"/api/coauthor/{base36}/hide_name").status_code in (200, 302)
    assert listed(second), "once they hide their credit, the post is in their own History > Anonymous"
    assert not listed(third) and not listed(author), "nobody else's list gains it"
    # a credit that is not accepted yet, or a post that is gone, is not listed
    db = third.db_session()
    db.execute(text("UPDATE post_coauthors SET status = 'pending', is_anonymous = true WHERE post_id = :p AND user_id = :u"), {"p": post_id, "u": third.id})
    db.commit()
    forget_cache()
    assert not listed(third), "an invitation not yet accepted is not a credit"


def test_an_anonymous_credit_is_left_off_the_profile_the_feed_and_the_counts_and_never_costs_a_block(real_app, make_member):
    author, hidden, named, follower = (make_member() for _ in range(4))
    board_id, _ = guild(real_app)
    post_id, base36 = add_post(author, board_id, title=f"Credit listing {secrets.token_hex(3)}")
    db = hidden.db_session()
    for member, flagged in ((hidden, True), (named, False)):
        db.execute(text("INSERT INTO post_coauthors (post_id, user_id, invited_by_id, status, created_utc, accepted_utc, is_anonymous) "
                        "VALUES (:p, :u, :a, 'accepted', :n, :n, :f)"), {"p": post_id, "u": member.id, "a": author.id, "n": int(time.time()), "f": flagged})
    db.commit()
    forget_cache()
    app, db_session = real_app
    from ruqqus.classes import User
    from ruqqus.helpers.blocks import coauthored_with
    from ruqqus.classes import Submission
    with app.test_request_context("/"):
        from flask import g
        session = db_session()
        g.db = session
        g.v = None
        hidden_user, named_user = session.get(User, hidden.id), session.get(User, named.id)
        assert hidden_user.public_post_count == 0 and named_user.public_post_count == 1, "an anonymous credit isn't counted on a profile"
        # a viewer who blocked the anonymous co-author still sees the post (it would tell them who it is); one who blocked the named one does not
        assert session.query(Submission.id).filter(Submission.id == post_id, ~coauthored_with(Submission, [hidden.id])).first() is not None
        assert session.query(Submission.id).filter(Submission.id == post_id, ~coauthored_with(Submission, [named.id])).first() is None
    assert "Credit listing" not in get(follower.browser(), f"/@{hidden.username}").get_data(as_text=True)
    assert "Credit listing" in get(follower.browser(), f"/@{named.username}").get_data(as_text=True)
    admin = admin_of(make_member)
    assert "Credit listing" in get(admin.browser(), f"/@{hidden.username}").get_data(as_text=True), "admins see it, as they see an anonymous post"


def test_deleting_the_account_hides_that_credit_and_gives_it_the_next_number(real_app, make_member):
    author, leaver, stranger = (make_member() for _ in range(3))
    board_id, _ = guild(real_app)
    post_id, base36 = add_post(author, board_id, title=f"Leaving soon {secrets.token_hex(3)}", is_anonymous=False)
    db = leaver.db_session()
    db.execute(text("INSERT INTO post_coauthors (post_id, user_id, invited_by_id, status, created_utc, accepted_utc) VALUES (:p, :u, :a, 'accepted', :n, :n)"),
               {"p": post_id, "u": leaver.id, "a": author.id, "n": int(time.time())})
    db.execute(text("UPDATE users SET created_utc = :old WHERE id = :u"), {"old": int(time.time()) - 90 * 86400, "u": leaver.id})
    db.commit()
    forget_cache()
    assert leaver.username in get(stranger.browser(), f"/post/{base36}").get_data(as_text=True)
    assert post(leaver.browser(), leaver, "/settings/delete_account", password=PASSWORD, delete_reason="leaving").status_code == 200
    assert credit(real_app, post_id, leaver) == {"status": "accepted", "is_anonymous": True}, "the credit stays, anonymous: it is not deleted with the account"
    page = get(stranger.browser(), f"/post/{base36}").get_data(as_text=True)
    assert leaver.username not in page and "Anonymous #1" in page and author.username in page, "only that person's credit became Anonymous"
    assert numbers(real_app, post_id) == {leaver.id: 1}


def test_the_public_pages_of_a_shared_post_draw_an_anonymous_credit_by_its_alias(real_app, make_member):
    author, second, stranger = (make_member() for _ in range(3))
    board_id, _ = guild(real_app)
    post_id, _ = add_post(author, board_id, is_anonymous=False)
    db = second.db_session()
    db.execute(text("INSERT INTO post_coauthors (post_id, user_id, invited_by_id, status, created_utc, accepted_utc, is_anonymous) "
                    "VALUES (:p, :u, :a, 'accepted', :n, :n, true)"), {"p": post_id, "u": second.id, "a": author.id, "n": int(time.time())})
    db.execute(text("INSERT INTO anon_aliases (scope_kind, scope_id, user_id, n, created_utc) VALUES ('post', :p, :u, 1, :n)"),
               {"p": post_id, "u": second.id, "n": int(time.time())})
    db.commit()
    app, db_session = real_app
    from ruqqus.routes import collab
    from ruqqus.classes import Submission, User
    with app.test_request_context("/"):
        from flask import g
        session = db_session()
        g.db = session
        post_row = session.get(Submission, post_id)
        user_row = session.get(User, second.id)
        stranger_row = session.get(User, stranger.id)
        admin_row = type("Admin", (), {"id": 0, "admin_level": 3, "qr_limited": False})()
        hidden = collab._hidden_credits(post_row, stranger_row)
        assert hidden == {second.id: "Anonymous #1"}
        assert collab._name_of(user_row, stranger_row, hidden) == {"name": "Anonymous #1", "handle": "", "link": None}
        assert collab._hidden_credits(post_row, user_row) == {}, "the co-author is told their own"
        assert collab._hidden_credits(post_row, admin_row) == {}, "an admin is told"
        session.execute(text("UPDATE users SET is_deleted = true WHERE id = :u"), {"u": second.id})
        session.flush()
        assert collab._name_of(session.get(User, second.id), stranger_row, hidden)["name"] == "Anonymous #1", "a deleted account is never 'Deleted account' here: its alias"
        session.rollback()
