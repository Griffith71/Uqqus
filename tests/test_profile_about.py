"""A profile's About box (ruqqus/helpers/profile_about.py): which guilds are listed with which role,
and which curations are listed (public ones only, whoever looks, and never a name the viewer's filter hides)."""
from types import SimpleNamespace as NS

from ruqqus.helpers.profile_about import CURATIONS_SHOWN, guild_roles, shown_curations


def guild(id, creator_id, private=False):
    return NS(id=id, name=f"g{id}", creator_id=creator_id, is_private=private)


def test_guild_roles_name_the_founder_and_drop_what_the_viewer_may_not_see():
    boards = [guild(1, 7), guild(2, 8), guild(3, 7, private=True)]
    outsider = guild_roles(boards, 7, lambda b: not b.is_private)
    assert [(b.id, role) for b, role in outsider] == [(1, "Founder"), (2, "Guildmaster")]
    member = guild_roles(boards, 7, lambda b: True)
    assert [(b.id, role) for b, role in member] == [(1, "Founder"), (2, "Guildmaster"), (3, "Founder")]


def curation(id, private=False, name=None):
    return NS(id=id, is_private=private, name=name or f"c{id}")


def test_only_public_curations_are_listed_and_a_hidden_name_is_left_out():
    rows = [curation(1), curation(2, private=True), curation(3, name="rude"), curation(4)]
    shown, more = shown_curations(rows, lambda name: name == "rude")
    assert [c.id for c in shown] == [1, 4] and more == 0


def test_curations_are_capped_and_the_rest_counted():
    rows = [curation(i) for i in range(CURATIONS_SHOWN + 3)]
    shown, more = shown_curations(rows, lambda name: False)
    assert len(shown) == CURATIONS_SHOWN and more == 3
