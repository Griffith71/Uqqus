"""Co-authored posts (ruqqus/helpers/coauthors.py): the pure rules, without a database."""
from types import SimpleNamespace as NS

import pytest

from ruqqus.helpers import coauthors as co


def user(id=2, name="bob", **kw):
    base = dict(id=id, username=name, is_deleted=False, is_banned=0)
    base.update(kw)
    return NS(**base)


def why(candidate="default", **kw):
    args = dict(anonymous=False, primary=True, author_id=1, candidate=user() if candidate == "default" else candidate,
                count=0, blocked=False, already=False)
    args.update(kw)
    return co.refusal("bob", **args)


# --- which post ----------------------------------------------------------------------------

def test_a_post_is_its_own_target_and_a_forwarded_copy_points_at_the_original():
    assert co.primary_post_id(NS(id=7, repost_id=0)) == 7
    assert co.primary_post_id(NS(id=7, repost_id=None)) == 7
    assert co.primary_post_id(NS(id=9, repost_id=7)) == 7


# --- reading the field -----------------------------------------------------------------------

def test_names_are_read_from_commas_spaces_and_at_signs_each_once():
    assert co.parse_names("@ann, bob  carol;@Ann\nbob") == ["ann", "bob", "carol"]
    assert co.parse_names("") == [] and co.parse_names(None) == [] and co.parse_names("  ,, @ ") == []


@pytest.mark.parametrize("text", ["ann bob!", "a-b", "x" * 26, "ann/bob", "<script>", "ann@bob"])
def test_something_that_is_not_a_username_is_refused(text):
    with pytest.raises(co.CoauthorError) as err:
        co.parse_names(text)
    assert "is not a username" in err.value.message


def test_too_many_names_or_too_long_a_field_is_refused():
    assert len(co.parse_names(" ".join(f"user{i}" for i in range(co.MAX_COAUTHORS)))) == co.MAX_COAUTHORS
    with pytest.raises(co.CoauthorError) as err:
        co.parse_names(" ".join(f"user{i}" for i in range(co.MAX_COAUTHORS + 1)))
    assert "at most" in err.value.message
    with pytest.raises(co.CoauthorError) as err:
        co.parse_names("a" * (co.FIELD_CHARS + 1))
    assert "too long" in err.value.message


def test_the_limits_are_the_ones_the_form_and_the_column_have():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    assert f'maxlength="{co.FIELD_CHARS}"' in (root / "ruqqus" / "templates" / "partials" / "post_options.html").read_text(encoding="utf-8")
    assert co.MAX_COAUTHORS == 5


# --- who may be invited -----------------------------------------------------------------------

def test_an_ordinary_account_may_be_invited():
    assert why() is None


def test_never_on_an_anonymous_post_and_only_on_a_post_on_the_authors_profile():
    assert why(anonymous=True) == "A co-authored post can't be anonymous."
    assert why(primary=False) == "Only a post on your profile can have co-authors."
    # the post comes first: nothing about the person is said about a post that cannot have co-authors
    assert why(anonymous=True, candidate=None) == "A co-authored post can't be anonymous."


def test_a_name_that_does_not_exist_yourself_and_accounts_that_cannot_be_invited():
    assert why(candidate=None) == "There is no account named @bob."
    assert why(candidate=user(id=1)) == "You are already the author."
    assert why(candidate=user(is_deleted=True)) == "@bob can't be invited."
    assert why(candidate=user(is_banned=1)) == "@bob can't be invited."


def test_a_block_either_way_says_nothing_about_who_blocked_whom():
    assert why(blocked=True) == why(candidate=user(is_banned=1)) == "@bob can't be invited."


def test_once_each_and_at_most_five():
    assert why(already=True) == "@bob was already invited."
    assert why(count=co.MAX_COAUTHORS - 1) is None
    assert why(count=co.MAX_COAUTHORS) == "A post can have 5 co-authors at most."


def test_the_message_names_the_account_as_it_is_spelt():
    assert co.refusal("BOB", anonymous=False, primary=True, author_id=1, candidate=user(name="Bob"), count=0, blocked=True, already=False) == "@Bob can't be invited."
