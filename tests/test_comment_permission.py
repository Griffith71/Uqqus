"""Who can comment on a post (ruqqus/helpers/comment_permission.py)."""
from types import SimpleNamespace as NS

import pytest

from ruqqus.helpers import comment_permission as cp


def author(id=1, follows=(), name="maker"):
    return NS(id=id, username=name, following_ids=set(follows))


def post(mode=0, profile=True, by=None):
    by = by or author()
    return NS(is_profile_post=profile, comment_permission=mode, author=by, author_id=by.id)


def user(id=50, premium=False, admin=0):
    return NS(id=id, admin_level=admin, has_premium_no_renew=premium)


def test_parse_accepts_only_known_modes():
    assert cp.parse("0") == cp.EVERYONE
    assert cp.parse(" 1 ") == cp.FOLLOWED
    assert cp.parse(2) == cp.PREMIUM
    for bad in ("3", "-1", "", "abc", None, "1.5"):
        assert cp.parse(bad) is None


def test_choices_cover_every_mode_in_order():
    assert [m for m, _ in cp.CHOICES] == list(cp.MODES)
    assert [label for _, label in cp.CHOICES] == ["Everyone", "Accounts you follow", "Premium accounts"]


def test_everyone_is_the_default_and_limits_nothing():
    assert cp.restriction(post(0), user()) is None
    assert cp.restriction(post(0), None) is None        # logged out: the box is hidden for other reasons
    assert cp.restriction(post(None), user()) is None   # a row from before the column existed


def test_followed_mode_lets_in_only_accounts_the_author_follows():
    p = post(cp.FOLLOWED, by=author(follows={50}))
    assert cp.restriction(p, user(id=50)) is None
    msg = cp.restriction(p, user(id=51))
    assert msg == "Only accounts @maker follows can comment on this post."
    # following the AUTHOR is not enough: it is the author's follow list that counts
    assert cp.restriction(post(cp.FOLLOWED, by=author(follows=())), user(id=51)) is not None


def test_premium_mode_lets_in_only_premium_accounts():
    p = post(cp.PREMIUM)
    assert cp.restriction(p, user(premium=True)) is None
    assert cp.restriction(p, user(premium=False)) == "Only Premium accounts can comment on this post."


def test_logged_out_visitors_are_limited_when_the_post_is():
    assert cp.restriction(post(cp.FOLLOWED), None) is not None
    assert cp.restriction(post(cp.PREMIUM), None) is not None


def test_the_author_and_admins_are_never_limited():
    for mode in (cp.FOLLOWED, cp.PREMIUM):
        p = post(mode)
        assert cp.restriction(p, user(id=p.author_id)) is None
        assert cp.restriction(p, user(admin=3)) is None
        assert cp.restriction(p, user(admin=2)) is not None     # below the admin bar


def test_a_forwarded_copy_ignores_the_authors_setting():
    # copies follow their guild's rules, so even a stale value is not read
    for mode in (cp.FOLLOWED, cp.PREMIUM):
        copy = post(mode, profile=False)
        assert cp.mode_of(copy) == cp.EVERYONE
        assert cp.restriction(copy, user()) is None


@pytest.mark.parametrize("stored", [3, -1, 99])
def test_an_unknown_stored_value_limits_nothing(stored):
    assert cp.mode_of(post(stored)) == cp.EVERYONE


def test_the_post_forms_use_the_same_labels_and_values_as_the_helper():
    from pathlib import Path

    macro = (Path(__file__).resolve().parent.parent / "ruqqus" / "templates" / "partials" / "post_options.html").read_text(encoding="utf-8")
    for value, label in cp.CHOICES:
        assert f'({value}, "{label}")' in macro, f"post_options.html is out of step with CHOICES for {label!r}"
