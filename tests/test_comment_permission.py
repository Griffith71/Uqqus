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
    assert cp.parse("3") == cp.NOBODY
    for bad in ("4", "-1", "", "abc", None, "1.5"):
        assert cp.parse(bad) is None


def test_choices_cover_every_mode_in_order():
    assert [m for m, _ in cp.CHOICES] == list(cp.MODES)
    assert [label for _, label in cp.CHOICES] == ["Everyone", "Accounts you follow", "Premium accounts", "No one"]


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


def test_no_one_limits_everybody_the_author_and_admins_included():
    """"No one" is literal: the author can switch it back, so nobody needs an exemption."""
    p = post(cp.NOBODY)
    assert cp.restriction(p, user(id=p.author_id)) == cp.CLOSED_NOTICE
    assert cp.restriction(p, user(admin=4)) == cp.CLOSED_NOTICE
    assert cp.restriction(p, user(premium=True)) == cp.CLOSED_NOTICE
    assert cp.restriction(p, None) == cp.CLOSED_NOTICE
    assert cp.CLOSED_NOTICE == "Comments are turned off for this post."


def test_the_closed_notice_is_only_for_no_one_and_names_nobody():
    assert cp.closed_notice(post(cp.NOBODY)) == cp.CLOSED_NOTICE
    for mode in (cp.EVERYONE, cp.FOLLOWED, cp.PREMIUM):
        assert cp.closed_notice(post(mode)) is None       # a visitor is still invited to sign in
    assert cp.closed_notice(post(cp.NOBODY, profile=False)) is None
    assert "maker" not in cp.CLOSED_NOTICE


def test_no_one_applies_to_an_anonymous_post_and_says_nothing_about_the_author():
    p = post(cp.NOBODY)
    p.is_anonymous = True
    assert cp.mode_of(p) == cp.NOBODY
    assert cp.restriction(p, user()) == cp.CLOSED_NOTICE
    assert cp.refusal(cp.NOBODY, True) is None


def test_a_forwarded_copy_ignores_the_authors_setting():
    # copies follow their guild's rules, so even a stale value is not read
    for mode in (cp.FOLLOWED, cp.PREMIUM, cp.NOBODY):
        copy = post(mode, profile=False)
        assert cp.mode_of(copy) == cp.EVERYONE
        assert cp.restriction(copy, user()) is None


@pytest.mark.parametrize("stored", [4, -1, 99])
def test_an_unknown_stored_value_limits_nothing(stored):
    assert cp.mode_of(post(stored)) == cp.EVERYONE


def test_the_post_forms_use_the_same_labels_and_values_as_the_helper():
    from pathlib import Path

    macro = (Path(__file__).resolve().parent.parent / "ruqqus" / "templates" / "partials" / "post_options.html").read_text(encoding="utf-8")
    for value, label in cp.CHOICES:
        assert f'({value}, "{label}")' in macro, f"post_options.html is out of step with CHOICES for {label!r}"


def test_accounts_you_follow_never_applies_to_an_anonymous_post():
    """Fixed in the 2026-10-09 audit pass: the notice named the anonymous author, and whether you could comment
    told you whether they follow you."""
    p = post(cp.FOLLOWED)
    p.is_anonymous = True
    assert cp.mode_of(p) == cp.EVERYONE
    assert cp.restriction(p, user()) is None
    assert cp.restriction(p, None) is None


def test_premium_still_applies_to_an_anonymous_post():
    p = post(cp.PREMIUM)
    p.is_anonymous = True
    assert cp.restriction(p, user()) == "Only Premium accounts can comment on this post."


def test_refusal_only_for_followed_on_an_anonymous_post():
    assert cp.refusal(cp.FOLLOWED, True) == cp.ANONYMOUS_REFUSAL
    assert "maker" not in cp.ANONYMOUS_REFUSAL
    for mode in (cp.EVERYONE, cp.PREMIUM, cp.NOBODY):
        assert cp.refusal(mode, True) is None
    for mode in cp.MODES:
        assert cp.refusal(mode, False) is None
