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


def test_an_authors_list_leaves_anonymous_rows_out_for_everyone_but_them_and_admins():
    assert "NOT rows.is_anonymous" in sql(None) and "author_id" not in sql(None)
    other = sql(user(id=5))
    assert "NOT rows.is_anonymous" in other and "rows.author_id = 5" in other     # their own stay
    assert sql(user(id=5, admin=3)) == "true"                                     # admins see all
    assert sql(user(id=5, admin=2)) != "true"
