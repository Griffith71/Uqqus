"""Who is told the author of an anonymous post or comment
(ruqqus/helpers/anonymity.py)."""
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import Boolean, Column, Integer
from sqlalchemy.orm import declarative_base
from sqlalchemy.dialects import postgresql

from ruqqus.helpers import anonymity as an


def user(id=1, admin=0, username="alice"):
    return NS(id=id, admin_level=admin, username=username)


def item(author=None, anonymous=True, id_=10):
    author = author or user()
    return NS(author_id=author.id, author=author, is_anonymous=anonymous, id=id_)


# --- who sees the identity ------------------------------------------------------

def test_nobody_but_the_author_and_admins_sees_who_wrote_it():
    x = item(user(id=1))
    assert an.identity_hidden(x, None) is True                 # logged out
    assert an.identity_hidden(x, user(id=2)) is True           # another user
    assert an.identity_hidden(x, user(id=2, admin=2)) is True  # below the admin bar
    assert an.identity_hidden(x, user(id=1)) is False          # the author
    assert an.identity_hidden(x, user(id=2, admin=3)) is False # an admin


def test_an_ordinary_item_is_never_hidden():
    x = item(anonymous=False)
    for viewer in (None, user(id=2), user(id=1)):
        assert an.identity_hidden(x, viewer) is False


def test_an_item_without_the_column_counts_as_not_anonymous():
    assert an.identity_hidden(NS(author_id=1, author=user()), None) is False


# --- what is drawn instead ------------------------------------------------------

def test_the_stand_in_author_gives_nothing_away():
    a = an.author_of(item(user(id=1, username="alice")), user(id=2))
    assert a is an.ANONYMOUS
    assert (a.username, a.original_username, str(a)) == ("Anonymous", "Anonymous", "Anonymous")
    assert a.permalink == "javascript:void(0)"           # not a link to any profile
    assert a.profile_url == an.DEFAULT_AVATAR
    assert (a.title, a.id, a.is_deleted, a.is_private, a.admin_level) == (None, None, False, False, 0)
    assert a.anything_a_template_asks_for_later is None  # unknown things read as empty
    assert bool(a) is True
    with pytest.raises(AttributeError):
        a.__wrapped__


def test_the_real_author_is_drawn_for_those_who_may_see_it():
    alice = user(id=1, username="alice")
    assert an.author_of(item(alice), user(id=1)) is alice
    assert an.author_of(item(alice), user(id=9, admin=4)) is alice
    assert an.label_of(item(alice), user(id=2)) == "Anonymous"
    assert an.label_of(item(alice), user(id=1)) == "alice"


# --- the OP mark ----------------------------------------------------------------

def test_op_mark_is_unchanged_when_nothing_is_anonymous():
    alice, bob = user(id=1), user(id=2)
    post = item(alice, anonymous=False)
    assert an.op_badge(item(alice, anonymous=False), post, user(id=3)) is True
    assert an.op_badge(item(bob, anonymous=False), post, user(id=3)) is False


def test_a_comment_with_no_post_has_no_op_mark():
    # system notifications (ban notices, "could not be published") are comments with no post;
    # the notifications page asks about every one of them
    notice = item(user(id=1), anonymous=False)
    for viewer in (user(id=1), user(id=3), user(id=3, admin=3), None):
        assert an.op_badge(notice, None, viewer) is False


def test_an_anonymous_comment_by_the_author_of_a_public_post_has_no_op_mark():
    alice = user(id=1)
    post, comment = item(alice, anonymous=False), item(alice, anonymous=True)
    assert an.op_badge(comment, post, user(id=3)) is False     # would name the commenter
    assert an.op_badge(comment, post, user(id=1)) is True      # the author may see it
    assert an.op_badge(comment, post, user(id=3, admin=3)) is True


def test_on_an_anonymous_post_the_authors_anonymous_comments_are_marked_op():
    alice = user(id=1)
    post, comment = item(alice, anonymous=True), item(alice, anonymous=True)
    assert an.op_badge(comment, post, user(id=3)) is True      # the same unnamed person, nothing learned


# --- commenting on your own anonymous post --------------------------------------

def test_commenting_on_your_own_anonymous_post_is_anonymous():
    alice = user(id=1)
    assert an.must_be_anonymous(item(alice, anonymous=True), alice) is True
    assert an.must_be_anonymous(item(alice, anonymous=False), alice) is False
    assert an.must_be_anonymous(item(alice, anonymous=True), user(id=2)) is False


# --- lists scoped to an author --------------------------------------------------

Base = declarative_base()


class Row(Base):
    __tablename__ = "rows"
    id = Column(Integer, primary_key=True)
    author_id = Column(Integer)
    is_anonymous = Column(Boolean)


def sql(viewer):
    return str(an.hide_anonymous(Row, viewer).compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def test_a_profile_leaves_anonymous_rows_out_for_everyone_but_admins_the_author_included():
    # the owner, 2026-10-27: the author finds their own in History > Anonymous, not on their profile
    def off(viewer):
        return str(an.off_profile(Row, viewer).compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert off(None) == "NOT rows.is_anonymous" and off(user(id=5)) == "NOT rows.is_anonymous"
    assert off(user(id=5, admin=3)) == "true" and off(user(id=5, admin=2)) == "NOT rows.is_anonymous"


def test_an_authors_list_leaves_anonymous_rows_out_for_everyone_but_them_and_admins():
    assert "NOT rows.is_anonymous" in sql(None) and "author_id" not in sql(None)
    other = sql(user(id=5))
    assert "NOT rows.is_anonymous" in other and "rows.author_id = 5" in other     # their own stay
    assert sql(user(id=5, admin=3)) == "true"                                     # admins see all
    assert sql(user(id=5, admin=2)) != "true"


# --- a limited QR sign-in (helpers/qr_login.py) ------------------------------------------------------

def limited(id=1):
    return NS(id=id, admin_level=0, username="alice", qr_limited=True)


def test_a_limited_qr_sign_in_is_a_stranger_to_the_members_anonymous_items():
    me = limited()
    x = item(me)
    assert an.identity_hidden(x, me) is True
    assert an.author_of(x, me) is an.ANONYMOUS
    assert an.is_author(x, me) is False and an.held_from_own(x, me) is True
    assert an.op_badge(x, x, me) is True                       # both anonymous: the same unnamed person, as for a stranger


def test_a_limited_qr_sign_in_still_owns_what_has_the_members_name_on_it():
    me = limited()
    x = item(me, anonymous=False)
    assert an.identity_hidden(x, me) is False and an.is_author(x, me) is True and an.held_from_own(x, me) is False


def test_the_author_with_the_password_and_an_admin_are_as_they_were():
    me = user(id=1)
    x = item(me)
    assert an.is_author(x, me) is True and an.held_from_own(x, me) is False
    assert an.identity_hidden(x, NS(id=2, admin_level=3, qr_limited=False)) is False
    assert an.is_author(x, None) is False and an.held_from_own(x, None) is False


def test_a_list_by_one_author_leaves_a_limited_sign_in_what_it_leaves_a_stranger():
    Base = declarative_base()

    class Row(Base):
        __tablename__ = "rows"
        id = Column(Integer, primary_key=True)
        author_id = Column(Integer)
        is_anonymous = Column(Boolean)

    compiled = lambda viewer: str(an.hide_anonymous(Row, viewer).compile(dialect=postgresql.dialect()))
    assert compiled(limited()) == compiled(None) and compiled(limited()) != compiled(user(id=1))


def test_what_counts_as_your_own_in_a_query_leaves_out_a_limited_sign_ins_anonymous_rows():
    Base = declarative_base()

    class Row(Base):
        __tablename__ = "rows"
        id = Column(Integer, primary_key=True)
        author_id = Column(Integer)
        is_anonymous = Column(Boolean)

    assert "is_anonymous" in str(an.own(Row, limited()).compile(dialect=postgresql.dialect()))
    assert "is_anonymous" not in str(an.own(Row, user(id=1)).compile(dialect=postgresql.dialect()))
