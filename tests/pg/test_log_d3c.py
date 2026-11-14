"""Comments under your own anonymous post, the tick box of "Remove my name", History's mass tools and Deleted list, and what deleting an account does
to what it wrote, on the real app and Postgres (the Log's slice D3c; the rules and the SQL are tested without Postgres in tests/test_mass_edit.py).

The promises: on your own anonymous post a comment is anonymous unless you say "comment as @me", which shows your name and no OP mark, and a limited QR
sign-in can't say it; the tick box counts and changes only your own comments under that post; the mass tools do exactly what the dialog does, once per
item, behind the password, the count, the cap and the rate limit, and report what could not be changed; the Deleted list is yours alone and plain text;
a deleted account's posts and comments are all Anonymous and stay."""
import html
import re
import secrets
import time

from sqlalchemy import text

BASE = "http://localhost"
AGENT = "ci-log-d3c"
PASSWORD = "ci-Password-1"


def address():
    return f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"


def get(client, path, country="US"):
    """A GET that follows the canonical redirect a post's address answers with (it names the guild)."""
    headers = {"User-Agent": AGENT, "Accept-Language": "en", "CF-IPCountry": country}
    return client.get(path, base_url=BASE, headers=headers, environ_base={"REMOTE_ADDR": address()}, follow_redirects=True)


def post(client, member, path, **fields):
    return client.post(path, base_url=BASE, headers={"User-Agent": AGENT}, data={"formkey": member.formkey(client), **fields},
                       environ_base={"REMOTE_ADDR": address()})


def guild(real_app, private=False, circle=False):
    from ruqqus.classes import Board
    _, db_session = real_app
    db = db_session()
    board = Board(name="ci_d3c_" + secrets.token_hex(3), description="a guild for the D3c tests", description_html="<p>a guild</p>", creator_id=1,
                  subcat_id=0, is_private=private, is_circle=circle, created_utc=int(time.time()), color="23a9f2")
    db.add(board)
    db.commit()
    return board.id, board.name


def add_post(member, board_id, title=None, age=3600, **flags):
    from ruqqus.classes import Submission, SubmissionAux
    from ruqqus.helpers.base36 import base36encode
    title = title or f"D3c post {secrets.token_hex(3)}"
    db = member.db_session()
    db.rollback()
    with member.app.test_request_context("/"):
        row = Submission(author_id=member.id, board_id=board_id, original_board_id=board_id, created_utc=int(time.time()) - age,
                         post_public=flags.pop("post_public", True), **flags)
    db.add(row)
    db.flush()
    db.add(SubmissionAux(id=row.id, title=title, body="words about gardens", body_html="<p>words about gardens</p>"))
    db.commit()
    return row.id, base36encode(row.id)


def add_comment(member, post_id, board_id, body="A comment.", age=3600, **flags):
    from ruqqus.classes import Comment, CommentAux
    from ruqqus.helpers.base36 import base36encode
    db = member.db_session()
    db.rollback()
    with member.app.test_request_context("/"):
        row = Comment(author_id=member.id, parent_submission=post_id, level=1, original_board_id=board_id, created_utc=int(time.time()) - age, **flags)
    db.add(row)
    db.flush()
    db.add(CommentAux(id=row.id, body=body, body_html=f"<p>{body}</p>"))
    db.commit()
    return row.id, base36encode(row.id)


def rows(real_app, sql, **params):
    _, db_session = real_app
    db = db_session()
    db.rollback()
    db.expire_all()
    return [dict(r._mapping) for r in db.execute(text(sql), params)]


def one(real_app, sql, **params):
    return rows(real_app, sql, **params)[0]


def flags(real_app, table, ids):
    return {r["id"]: r["is_anonymous"] for r in rows(real_app, f"SELECT id, is_anonymous FROM {table} WHERE id = ANY(:ids)", ids=list(ids))}


def app_client(real_app):
    app, _ = real_app
    return app.test_client()


def limited_browser(member):
    client = member.browser()
    with client.session_transaction() as session:
        session["qr_limited"] = True
        session["qr_expires_utc"] = int(time.time()) + 3600
    return client


def comment_on(client, member, base36, **fields):
    return post(client, member, "/api/comment", parent_fullname=f"t2_{base36}", submission=base36, body=f"Hello there, a comment {secrets.token_hex(4)}", **fields)


# ---------------------------------------------------------------- comment as @me

def test_on_your_own_anonymous_post_a_comment_is_anonymous_unless_you_comment_as_yourself(real_app, make_member):
    author = make_member()
    board_id, _ = guild(real_app)
    post_id, base36 = add_post(author, board_id, is_anonymous=True)
    client = author.browser()
    assert comment_on(client, author, base36).status_code == 200
    assert comment_on(client, author, base36, anonymous="").status_code == 200
    assert comment_on(client, author, base36, anonymous="true", as_me="true").status_code == 200       # the locked toggle is checked: as_me wins
    assert comment_on(client, author, base36, as_me="true").status_code == 200
    made = rows(real_app, "SELECT is_anonymous FROM comments WHERE parent_submission = :p AND author_id = :a ORDER BY id", p=post_id, a=author.id)
    assert [m["is_anonymous"] for m in made] == [True, True, False, False], "anonymous by default; named only when asked, and as_me beats the toggle"


def submitter_marks(page):
    return page.count('data-original-title="Submitter"')


def test_the_op_mark_follows_what_the_reader_may_be_told(real_app, make_member):
    author, stranger = make_member(), make_member()
    board_id, _ = guild(real_app)
    post_id, base36 = add_post(author, board_id, is_anonymous=True)
    add_comment(author, post_id, board_id, body="anonymous words ZZQ1", is_anonymous=True)
    add_comment(author, post_id, board_id, body="named words ZZQ2", is_anonymous=False)
    page = get(stranger.browser(), f"/post/{base36}").get_data(as_text=True)
    assert "anonymous words ZZQ1" in page and "named words ZZQ2" in page and author.username in page, "the named comment shows its name"
    assert submitter_marks(page) == 1, "the anonymous comment is the unnamed post author's again; the named one is linked to the post by nothing"
    # the author sees their own identity on both, so both are theirs to be marked
    own = get(author.browser(), f"/post/{base36}").get_data(as_text=True)
    assert submitter_marks(own) == 2
    # a visitor is told no more than a stranger
    assert submitter_marks(get(app_client(real_app), f"/post/{base36}").get_data(as_text=True)) == 1


def test_a_limited_sign_in_can_not_comment_as_yourself_and_a_post_that_is_not_yours_ignores_it(real_app, make_member):
    author, other = make_member(), make_member()
    board_id, _ = guild(real_app)
    post_id, base36 = add_post(author, board_id, is_anonymous=True)
    refused = comment_on(limited_browser(author), author, base36, as_me="true")
    assert refused.status_code == 403 and refused.get_json().get("need_password") is True
    assert rows(real_app, "SELECT 1 FROM comments WHERE parent_submission = :p AND author_id = :a", p=post_id, a=author.id) == []
    # someone else's anonymous post: as_me means nothing, the comment is as anonymous as asked
    other_post, other_base36 = add_post(other, board_id, is_anonymous=True)
    assert comment_on(author.browser(), author, other_base36, as_me="true").status_code == 200
    assert comment_on(author.browser(), author, other_base36, anonymous="true", as_me="true").status_code == 200
    made = rows(real_app, "SELECT is_anonymous FROM comments WHERE parent_submission = :p AND author_id = :a ORDER BY id", p=other_post, a=author.id)
    assert [m["is_anonymous"] for m in made] == [False, True]


# ---------------------------------------------------------------- the tick box

def test_the_tick_box_counts_and_changes_only_your_own_named_comments_under_that_post(real_app, make_member):
    author, other = make_member(), make_member()
    board_id, _ = guild(real_app)
    post_id, base36 = add_post(author, board_id)
    mine = [add_comment(author, post_id, board_id, body=f"mine {n}")[0] for n in range(2)]
    already, _ = add_comment(author, post_id, board_id, body="already anonymous", is_anonymous=True)
    theirs, _ = add_comment(other, post_id, board_id, body="theirs")
    elsewhere_post, _ = add_post(author, board_id)
    elsewhere, _ = add_comment(author, elsewhere_post, board_id, body="on another post")
    client = author.browser()
    assert get(client, f"/api/post/{base36}/own_comment_count").get_json() == {"count": 2}
    assert get(other.browser(), f"/api/post/{base36}/own_comment_count").status_code == 404, "only the author is told"
    assert get(client, "/api/post/zzzzzz/own_comment_count").status_code == 404
    # unticked: the post loses its name and the comments keep theirs
    assert post(client, author, f"/delete_post/{base36}", how="unlink").status_code == 204
    assert flags(real_app, "comments", mine) == {mine[0]: False, mine[1]: False}
    assert one(real_app, "SELECT is_anonymous FROM submissions WHERE id = :i", i=post_id)["is_anonymous"] is True
    # ticked, on a second post: the author's own comments under it go, nobody else's and nothing on another post
    second, second36 = add_post(author, board_id)
    more = [add_comment(author, second, board_id, body="more mine")[0]]
    their_more, _ = add_comment(other, second, board_id, body="theirs too")
    assert post(client, author, f"/delete_post/{second36}", how="unlink", with_comments="1").status_code == 204
    assert flags(real_app, "comments", more + [their_more]) == {more[0]: True, their_more: False}
    assert flags(real_app, "comments", [elsewhere, theirs, already]) == {elsewhere: False, theirs: False, already: True}


# ---------------------------------------------------------------- the mass tools

def mass(client, member, items, action="delete", password=PASSWORD, count=None, **extra):
    fields = {"action": action, "password": password, "count": str(len(items) if count is None else count), **extra}
    return client.post("/history/mass", base_url=BASE, headers={"User-Agent": AGENT},
                       data={"formkey": member.formkey(client), "items": items, **fields}, environ_base={"REMOTE_ADDR": address()})


def test_the_manage_page_lists_your_own_items_with_a_box_each(real_app, make_member):
    author, other = make_member(), make_member()
    board_id, _ = guild(real_app)
    title = f"Listed {secrets.token_hex(3)}"
    post_id, base36 = add_post(author, board_id, title=title)
    comment_id, comment36 = add_comment(author, post_id, board_id, body="my comment words")
    add_post(other, board_id, title="Somebody elses post")
    page = get(author.browser(), "/history/manage").get_data(as_text=True)
    assert title in page and "my comment words" in page and f'value="p{base36}"' in page and f'value="c{comment36}"' in page
    assert "Somebody elses post" not in page
    assert get(limited_browser(author), "/history/manage").status_code == 403, "a limited sign-in is a stranger to which items are anonymous"
    visitor = app_client(real_app).get("/history/manage", base_url=BASE, headers={"User-Agent": AGENT})
    assert visitor.status_code in (301, 302, 401, 403) and "/history/mass" not in visitor.get_data(as_text=True)


def test_mass_make_anonymous_and_delete_do_what_the_dialog_does_item_by_item(real_app, make_member):
    author, other = make_member(), make_member()
    board_id, _ = guild(real_app)
    posts = [add_post(author, board_id) for _ in range(3)]
    comment_post, _ = add_post(other, board_id)
    comments = [add_comment(author, comment_post, board_id, body=f"my {n}") for n in range(2)]
    tokens = [f"p{b}" for _, b in posts[:2]] + [f"c{b}" for _, b in comments]
    client = author.browser()
    page = mass(client, author, tokens, action="anonymise").get_data(as_text=True)
    assert "4 items changed" in page
    assert flags(real_app, "submissions", [posts[0][0], posts[1][0], posts[2][0]]) == {posts[0][0]: True, posts[1][0]: True, posts[2][0]: False}
    assert flags(real_app, "comments", [c[0] for c in comments]) == {c[0]: True for c in comments}
    assert rows(real_app, "SELECT 1 FROM log_entries WHERE action LIKE 'author_delete%%' AND actor_id = :a", a=author.id) == [], "anonymising writes no entry"
    # delete: after the window, so each leaves its words in the Log, as a single delete does
    page = mass(client, author, [f"p{posts[2][1]}", f"c{comments[0][1]}"], action="delete").get_data(as_text=True)
    assert "2 items changed" in page
    assert one(real_app, "SELECT deleted_utc FROM submissions WHERE id = :i", i=posts[2][0])["deleted_utc"] > 0
    assert one(real_app, "SELECT deleted_utc FROM comments WHERE id = :i", i=comments[0][0])["deleted_utc"] > 0
    entries = rows(real_app, "SELECT action FROM log_entries WHERE action IN ('author_delete_post', 'author_delete_comment') "
                             "AND (target_submission_id = :p OR target_comment_id = :c)", p=posts[2][0], c=comments[0][0])
    assert sorted(e["action"] for e in entries) == ["author_delete_comment", "author_delete_post"]


def test_what_cannot_be_changed_is_listed_with_its_reason_and_the_rest_still_goes(real_app, make_member):
    author, other = make_member(), make_member()
    board_id, _ = guild(real_app)
    circle_id, _ = guild(real_app, circle=True)
    plain = add_post(author, board_id)
    circle = add_post(author, circle_id, audience=4, post_public=False)
    tipped = add_post(author, board_id, tip_coins=5)
    notmine = add_post(other, board_id)
    gone = add_post(author, board_id, deleted_utc=int(time.time()) - 5000)
    page = mass(author.browser(), author, [f"p{plain[1]}", f"p{circle[1]}", f"p{tipped[1]}", f"p{notmine[1]}", f"p{gone[1]}"], action="anonymise")
    text_ = html.unescape(page.get_data(as_text=True))
    assert "1 item changed" in text_ and "4 items couldn't be changed" in text_
    assert "can't have its name removed" in text_ and "has been tipped" in text_ and "isn't yours to change" in text_
    assert flags(real_app, "submissions", [plain[0], circle[0], tipped[0], notmine[0]]) == {plain[0]: True, circle[0]: False, tipped[0]: False, notmine[0]: False}


def test_nothing_changes_without_the_password_the_count_and_a_choice_and_a_hundred_is_the_cap(real_app, make_member):
    author = make_member()
    board_id, _ = guild(real_app)
    first, second = add_post(author, board_id), add_post(author, board_id)
    client = author.browser()
    tokens = [f"p{first[1]}", f"p{second[1]}"]
    for response, said in ((mass(client, author, tokens, password="not it"), "password"), (mass(client, author, tokens, count=3), "number of items"),
                           (mass(client, author, tokens, action="erase"), "Choose what to do"), (mass(client, author, []), "at least one"),
                           (mass(client, author, [f"p{first[1]}"] * 2 + ["zz!"], count=2), "number of items")):
        assert response.status_code == 400 and said in response.get_data(as_text=True), said
    assert flags(real_app, "submissions", [first[0], second[0]]) == {first[0]: False, second[0]: False}
    many = [f"p{n:x}" for n in range(1, 102)]
    over = mass(client, author, many)
    assert over.status_code == 400 and "up to 100 items" in over.get_data(as_text=True)
    assert flags(real_app, "submissions", [first[0], second[0]]) == {first[0]: False, second[0]: False}, "a hundred and one is refused whole"


def test_a_second_factor_is_asked_where_the_account_has_one(real_app, make_member):
    from ruqqus.helpers import totp as totp_rules  # noqa: F401  (the real check is User.accept_2fa; this only fixes the secret)
    import pyotp
    author = make_member()
    board_id, _ = guild(real_app)
    post_id, base36 = add_post(author, board_id)
    secret = pyotp.random_base32()
    db = author.db_session()
    db.execute(text("UPDATE users SET mfa_secret = :s WHERE id = :u"), {"s": secret, "u": author.id})
    db.commit()
    client = author.browser()
    assert mass(client, author, [f"p{base36}"], action="anonymise").status_code == 400, "the password alone isn't enough"
    answered = mass(client, author, [f"p{base36}"], action="anonymise", twofactor=pyotp.TOTP(secret).now())
    assert answered.status_code == 200 and "1 item changed" in answered.get_data(as_text=True)


def test_a_limited_sign_in_and_an_api_app_can_not_use_the_mass_tools_and_the_rate_is_six_an_hour(real_app, make_member):
    author = make_member()
    board_id, _ = guild(real_app)
    post_id, base36 = add_post(author, board_id)
    refused = mass(limited_browser(author), author, [f"p{base36}"], action="anonymise")
    assert refused.status_code == 403
    assert flags(real_app, "submissions", [post_id]) == {post_id: False}
    app, _ = real_app
    api = app.test_client().post("/history/mass", base_url=BASE, headers={"User-Agent": AGENT, "Authorization": "Bearer nope"},
                                 data={"items": [f"p{base36}"], "action": "anonymise", "password": PASSWORD, "count": "1"})
    assert api.status_code in (301, 302, 401, 403), "an app's token is no sign-in here"
    # the limit is six requests an hour for an account, whatever each answers (a wrong password counts)
    second = make_member()
    client = second.browser()
    answers = [mass(client, second, [f"p{base36}"], action="anonymise", password="wrong").status_code for _ in range(7)]
    assert answers == [400] * 6 + [429], "six an hour an account, wrong passwords counted"


# ---------------------------------------------------------------- History > Anonymous > Deleted

def test_the_deleted_list_is_the_members_own_copies_in_plain_text(real_app, make_member):
    author, other = make_member(), make_member()
    board_id, _ = guild(real_app)
    title = "Deleted <b>bold</b> & title " + secrets.token_hex(3)
    post_id, base36 = add_post(author, board_id, title=title)
    comment_post, _ = add_post(other, board_id)
    comment_id, comment36 = add_comment(author, comment_post, board_id, body="a comment I will delete")
    client = author.browser()
    assert post(client, author, f"/delete_post/{base36}").status_code == 204
    assert post(client, author, f"/delete/comment/{comment36}").status_code == 204
    page = get(client, "/history/anonymous?show=deleted").get_data(as_text=True)
    assert "a comment I will delete" in page and "In the Log as Anonymous" in page and f"/post/{base36}/copy" in page
    assert "<b>bold</b>" not in page and "&lt;b&gt;bold&lt;/b&gt;" in page, "drawn escaped: a deleted item's words are never HTML"
    assert "Live" in page and "Deleted" in page
    assert get(other.browser(), "/history/anonymous?show=deleted").get_data(as_text=True).count("a comment I will delete") == 0
    assert get(limited_browser(author), "/history/anonymous?show=deleted").status_code == 403
    live = get(client, "/history/anonymous").get_data(as_text=True)
    assert "history-deleted" not in live, "the Live list is unchanged"


# ---------------------------------------------------------------- account deletion

def delete_account(client, member, password=PASSWORD):
    """Deletes the account. It is made old first: the site takes down everything a throwaway account (under 12 hours old) wrote as spam, and the
    `User` model stamps "now" whatever a test says."""
    db = member.db_session()
    db.execute(text("UPDATE users SET created_utc = :old WHERE id = :u"), {"old": int(time.time()) - 90 * 86400, "u": member.id})
    db.commit()
    return post(client, member, "/settings/delete_account", password=password, delete_reason="done")


def test_deleting_an_account_makes_everything_it_wrote_anonymous_and_leaves_it_on_the_site(real_app, make_member):
    leaver, other = make_member(), make_member()
    board_id, _ = guild(real_app)
    title = f"Written before leaving {secrets.token_hex(3)}"
    post_id, base36 = add_post(leaver, board_id, title=title, distinguish_level=1)
    own_comment, _ = add_comment(leaver, post_id, board_id, body="my own comment here")
    their_post, their36 = add_post(other, board_id)
    on_theirs, _ = add_comment(leaver, their_post, board_id, body="my comment on their post")
    kept, _ = add_comment(other, post_id, board_id, body="somebody else's words")
    assert delete_account(leaver.browser(), leaver).status_code == 200
    assert flags(real_app, "submissions", [post_id, their_post]) == {post_id: True, their_post: False}
    assert flags(real_app, "comments", [own_comment, on_theirs, kept]) == {own_comment: True, on_theirs: True, kept: False}
    assert one(real_app, "SELECT distinguish_level FROM submissions WHERE id = :i", i=post_id)["distinguish_level"] == 0
    assert one(real_app, "SELECT deleted_utc FROM submissions WHERE id = :i", i=post_id)["deleted_utc"] == 0, "nothing is removed: it stays on the site"
    job = one(real_app, "SELECT done_utc, tries FROM account_anonymise_jobs WHERE user_id = :u", u=leaver.id)
    assert job["done_utc"] > 0 and job["tries"] == 1, "an ordinary account is finished before the deletion answers"
    visitor = get(app_client(real_app), f"/post/{base36}")
    page = visitor.get_data(as_text=True)
    assert title in page and "my own comment here" in page, "still readable"
    assert leaver.username not in page, "and not by name: Anonymous, not '[deleted user]'"
    assert "Anonymous" in page
    # an account that wrote more than one batch is finished by the job each cycle: anything left is found and done, never twice
    from ruqqus.helpers import account_anonymise
    _, db_session = real_app
    late, _ = add_post(leaver, board_id)                                  # a row the first batch did not reach
    db = db_session()
    done = account_anonymise.run_one(db, leaver.id, int(time.time()))
    assert done[0] >= 1 and done[2] is True
    assert flags(real_app, "submissions", [late]) == {late: True}
    assert account_anonymise.run_one(db_session(), leaver.id, int(time.time()))[:2] == (0, 0)


def test_a_wrong_password_deletes_nothing_and_queues_nothing(real_app, make_member):
    member = make_member()
    board_id, _ = guild(real_app)
    post_id, _ = add_post(member, board_id)
    delete_account(member.browser(), member, password="not it")
    assert flags(real_app, "submissions", [post_id]) == {post_id: False}
    assert rows(real_app, "SELECT 1 FROM account_anonymise_jobs WHERE user_id = :u", u=member.id) == []


def test_a_parents_deletion_anonymises_what_its_children_wrote_too(real_app, make_member):
    parent = make_member()
    board_id, _ = guild(real_app)
    child = make_member()
    db = parent.db_session()
    db.execute(text("INSERT INTO account_links (child_id, parent_id, created_utc) VALUES (:c, :p, :n)"), {"c": child.id, "p": parent.id, "n": int(time.time())})
    db.execute(text("UPDATE users SET age_declared = 2, age_lock = 2, filter_level = 2 WHERE id = :c"), {"c": child.id})
    db.commit()
    kid_post, _ = add_post(child, board_id)
    assert delete_account(parent.browser(), parent).status_code == 200
    assert flags(real_app, "submissions", [kid_post]) == {kid_post: True}
    assert rows(real_app, "SELECT 1 FROM account_anonymise_jobs WHERE user_id = :u AND done_utc > 0", u=child.id)
