"""Comment search on the real app and a real Postgres: it finds what a viewer may read and nothing else (the rules of
`routes/search.py` `comment_searchlisting`), for the whole site and inside one guild."""
import re
import secrets
import time

import pytest

BASE = "http://localhost"


def addr():
    return f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"


def go(client, path, **kw):
    return client.get(path, base_url=BASE, headers={"User-Agent": "ci-comment-search"}, environ_base={"REMOTE_ADDR": addr()}, **kw)


def search(client, q, guild=None, **params):
    path = f"/+{guild}/search" if guild else "/search"
    answer = go(client, path, query_string={"q": q, "type": "comments", **params})
    assert answer.status_code == 200, (q, answer.status_code)
    return answer.get_data(as_text=True)


def person(make_member):
    member = make_member()
    while member.id == 1:
        member = make_member()
    return member


class Thread:
    """A post by `author` on their profile, forwarded to +general, and the comments made on it."""

    def __init__(self, author, text):
        from ruqqus.classes import Submission
        from ruqqus.helpers.base36 import base36decode
        self.author, self.app = author, author.app
        here = author.browser()
        answer = here.post("/api/vue/submit", base_url=BASE, headers={"User-Agent": "ci"}, environ_base={"REMOTE_ADDR": addr()},
                           data={"title": f"Title {secrets.token_hex(4)}", "body": text, "formkey": author.formkey(here),
                                 "post_to_profile": "1", "forward_guilds": "general"})
        assert answer.status_code == 200, answer.get_data(as_text=True)[:300]
        self.post_b36 = answer.get_json()["id"]
        self.post_id = base36decode(self.post_b36)
        db = author.db_session()
        db.expire_all()
        copy = db.query(Submission).filter(Submission.repost_id == self.post_id, Submission.board_id == 1).first()
        self.copy_id = copy.id if copy else None

    def comment(self, member, text, on_copy=True, **flags):
        from ruqqus.classes import Comment
        from ruqqus.helpers.base36 import base36encode
        here = member.browser()
        target = self.copy_id if (on_copy and self.copy_id) else self.post_id
        answer = here.post("/api/comment", base_url=BASE, headers={"User-Agent": "ci"}, environ_base={"REMOTE_ADDR": addr()},
                           data={"parent_fullname": f"t2_{base36encode(target)}", "body": text, "formkey": member.formkey(here)})
        assert answer.status_code == 200, answer.get_data(as_text=True)[:300]
        db = member.db_session()
        db.expire_all()
        row = db.query(Comment).filter_by(parent_submission=target, author_id=member.id).order_by(Comment.id.desc()).first()
        for key, value in flags.items():
            setattr(row, key, value)
        db.add(row)
        db.commit()
        return base36encode(row.id)


def comment_ids(page):
    """The ids of the comments drawn on a page (not the page's own `comment-error-text` and the like)."""
    return re.findall(r'id="comment-([a-z0-9]+)"', page)


def shows(page, cid):
    return cid in comment_ids(page)


@pytest.fixture
def world(make_member, real_app):
    app, _ = real_app
    author, other = person(make_member), person(make_member)
    word = "needle" + secrets.token_hex(3)
    thread = Thread(author, f"A post about {secrets.token_hex(4)}")
    return app, author, other, word, thread


def test_a_comment_is_found_by_its_words_and_a_post_search_does_not_return_it(world):
    app, author, other, word, thread = world
    cid = thread.comment(other, f"I saw a {word} yesterday")
    visitor = app.test_client()
    assert shows(search(visitor, word), cid)
    assert shows(search(visitor, f'"a {word} yesterday"'), cid), "a phrase"
    assert not shows(search(visitor, f"{word} -yesterday"), cid), "a negated word"
    post_page = go(visitor, "/search", query_string={"q": word}).get_data(as_text=True)
    assert not shows(post_page, cid)


def test_from_in_and_dates_narrow_the_search(world):
    app, author, other, word, thread = world
    cid = thread.comment(other, f"{word} narrow")
    visitor = app.test_client()
    assert shows(search(visitor, f"{word} from:@{other.username}"), cid)
    assert not shows(search(visitor, f"{word} from:@{author.username}"), cid)
    assert shows(search(visitor, f"{word} -from:@{author.username}"), cid)
    assert shows(search(visitor, f"{word} in:+general"), cid)
    assert not shows(search(visitor, f"{word} in:+nosuchguildxyz"), cid)
    today = time.strftime("%Y-%m-%d", time.gmtime())
    assert shows(search(visitor, f"{word} on:{today}"), cid)
    assert not shows(search(visitor, f"{word} before:{today}"), cid)
    assert shows(search(visitor, f"{word} min_votes:1"), cid), "a comment starts with its author's vote"
    assert not shows(search(visitor, f"{word} min_votes:500"), cid)
    assert shows(search(visitor, f"from:@{other.username}"), cid), "from: alone is enough"


def test_is_flags_and_the_operators_a_comment_does_not_have(world):
    app, author, other, word, thread = world
    plain = thread.comment(other, f"{word} plain")
    ai = thread.comment(other, f"{word} made", made_with_ai=True)
    visitor = app.test_client()
    page = search(visitor, f"{word} is:ai")
    assert shows(page, ai) and not shows(page, plain)
    page = search(visitor, f"{word} -is:ai")
    assert shows(page, plain) and not shows(page, ai)
    page = search(visitor, f"{word} has:image lang:en")
    assert shows(page, plain), "dropped operators leave the rest of the search"
    assert "has: is not used when searching comments." in page and "lang: is not used when searching comments." in page


def test_a_search_that_would_read_every_comment_says_so_and_finds_nothing(world):
    app, *_ = world
    page = search(app.test_client(), "ab")
    assert "at least 3 letters" in page and comment_ids(page) == []


def test_removed_deleted_and_anonymous_comments_are_not_found(world):
    app, author, other, word, thread = world
    gone = thread.comment(other, f"{word} deleted", deleted_utc=int(time.time()))
    banned = thread.comment(other, f"{word} banned", is_banned=True)
    anon = thread.comment(other, f"{word} anon", is_anonymous=True)
    kept = thread.comment(other, f"{word} kept")
    visitor = app.test_client()
    page = search(visitor, word)
    assert shows(page, kept) and not shows(page, gone) and not shows(page, banned)
    assert shows(page, anon), "an anonymous comment is found by its words"
    page = search(visitor, f"{word} from:@{other.username}")
    assert shows(page, kept) and not shows(page, anon), "but never through its author"
    page = search(visitor, f"{word} -from:@{other.username}")
    assert shows(page, anon) and not shows(page, kept), "-from leaves out only the non-anonymous ones: it must not tell which were theirs"
    assert shows(search(other.browser(), f"{word} from:@{other.username}"), anon), "its author may find their own"


def test_blocked_and_muted_accounts_are_left_out_for_the_one_who_blocked_or_muted(world, make_member):
    app, author, other, word, thread = world
    cid = thread.comment(other, f"{word} unwelcome")
    viewer = person(make_member)
    here = viewer.browser()
    form = {"formkey": viewer.formkey(here), "username": other.username}
    go(here, "/")                                                          # a page before the block, to be sure it is signed in
    assert shows(search(here, word), cid)
    here.post("/settings/mute", base_url=BASE, data=form, environ_base={"REMOTE_ADDR": addr()})
    assert not shows(search(here, word), cid), "a muted account"
    assert shows(search(here, f"{word} from:@{other.username}"), cid), "unless you go to their comments on purpose"
    here.post("/settings/unmute", base_url=BASE, data=form, environ_base={"REMOTE_ADDR": addr()})
    here.post("/settings/block", base_url=BASE, data=form, environ_base={"REMOTE_ADDR": addr()})
    assert not shows(search(here, word), cid), "a blocked account"
    assert shows(search(app.test_client(), word), cid), "everyone else still finds it"


def test_inside_a_guild_only_that_guilds_comments_are_found(world):
    app, author, other, word, thread = world
    cid = thread.comment(other, f"{word} guild talk")
    visitor = app.test_client()
    assert shows(search(visitor, word, guild="general"), cid)
    page = search(visitor, f"{word} in:+general", guild="general")
    assert "in: is not used when searching one guild." in page
    assert "Comments" in page and "Posts" in page, "the switch is on a guild's search too"


def test_the_switch_and_sorting_and_json(world):
    app, author, other, word, thread = world
    thread.comment(other, f"{word} one")
    thread.comment(other, f"{word} two")
    visitor = app.test_client()
    page = search(visitor, word)
    assert "What to search" in page and "type=comments" in page
    for sort in ("top", "new", "old"):
        assert len(comment_ids(search(visitor, word, sort=sort))) >= 2
    answer = go(visitor, "/api/v1/search", query_string={"q": word, "type": "comments"})
    assert answer.status_code in (200, 401)


def test_comments_under_a_post_that_is_not_public_are_found_only_by_those_who_may_open_it(world, make_member):
    from ruqqus.classes import Submission
    app, author, other, word, thread = world
    cid = thread.comment(other, f"{word} hush", on_copy=False)            # on the author's own profile post
    db = author.db_session()
    post = db.query(Submission).filter_by(id=thread.post_id).first()
    post.post_public = False                                               # as a post for circles is stored
    db.add(post)
    db.commit()
    stranger = person(make_member)
    assert not shows(search(app.test_client(), word), cid), "a visitor"
    assert not shows(search(stranger.browser(), word), cid), "another member"
    assert shows(search(author.browser(), word), cid), "its author"


def test_comments_in_a_private_guild_are_not_found_by_outsiders(world):
    from ruqqus.classes import Board
    app, author, other, word, thread = world
    cid = thread.comment(other, f"{word} members only")
    db = author.db_session()
    board = db.query(Board).filter_by(id=1).first()
    try:
        board.is_private = True
        db.add(board)
        db.commit()
        assert not shows(search(app.test_client(), word), cid)
        assert not shows(search(app.test_client(), word, guild="general"), cid)
    finally:
        board.is_private = False
        db.add(board)
        db.commit()
    assert shows(search(app.test_client(), word), cid)
