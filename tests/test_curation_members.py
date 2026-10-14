"""Add to curation (ruqqus/helpers/curation_members.py): the rules, and the real SQL on SQLite."""
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import curation_members as cm


def account(**kw):
    return NS(**{"is_deleted": False, "is_suspended": False, **kw})


def guild(**kw):
    return NS(**{"is_banned": False, "is_private": False, "is_circle": False, **kw})


def test_the_author_of_an_anonymous_post_is_never_added_whoever_asks():
    assert cm.author_refusal(True, account()) == "The author of an anonymous post can't be added."


@pytest.mark.parametrize("who", [None, account(is_deleted=True), account(is_suspended=True)])
def test_a_gone_or_suspended_account_is_not_added(who):
    assert cm.author_refusal(False, who) == "That account can't be added."


def test_a_normal_author_is_added():
    assert cm.author_refusal(False, account()) is None


def test_a_post_on_a_profile_has_no_guild_to_add():
    assert cm.guild_refusal(True, guild(), False) is not None


def test_a_banned_guild_is_never_added():
    assert cm.guild_refusal(False, guild(is_banned=True), True) == "That guild can't be added."


@pytest.mark.parametrize("kind", ["is_private", "is_circle"])
def test_a_private_or_circle_guild_only_goes_in_a_private_curation(kind):
    board = guild(**{kind: True})
    assert cm.guild_refusal(False, board, False) == "A private guild can only go in a private curation."
    assert cm.guild_refusal(False, board, True) is None


def test_a_public_guild_goes_anywhere():
    assert cm.guild_refusal(False, guild(), False) is None and cm.guild_refusal(False, guild(), True) is None


def test_what_the_sheet_offers():
    profile_post = NS(is_anonymous=False, is_profile_post=True)
    guild_post = NS(is_anonymous=False, is_profile_post=False)
    anonymous_guild_post = NS(is_anonymous=True, is_profile_post=False)
    anonymous_profile_post = NS(is_anonymous=True, is_profile_post=True)
    assert cm.offered(profile_post, guild(), account()) == {"author": True, "guild": False}
    assert cm.offered(guild_post, guild(), account()) == {"author": True, "guild": True}
    assert cm.offered(anonymous_guild_post, guild(), account()) == {"author": False, "guild": True}
    assert cm.offered(anonymous_profile_post, guild(), account()) == {"author": False, "guild": False}
    assert cm.offered(guild_post, guild(is_banned=True), account(is_deleted=True)) == {"author": False, "guild": False}


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE curation_users (id integer PRIMARY KEY AUTOINCREMENT, curation_id integer, target_user_id integer, created_utc integer DEFAULT 0, UNIQUE (curation_id, target_user_id))"))
        conn.execute(text("CREATE TABLE curation_guilds (id integer PRIMARY KEY AUTOINCREMENT, curation_id integer, board_id integer, created_utc integer DEFAULT 0, UNIQUE (curation_id, board_id))"))
    session = Session(engine)
    yield session
    session.close()


def test_adding_an_account_twice_is_a_no_op_the_second_time(db):
    assert cm.add_user(db, 1, 7, now=100) is True
    assert cm.add_user(db, 1, 7, now=200) is False
    assert cm.has_user(db, 1, 7) and not cm.has_user(db, 2, 7)
    assert db.execute(text("SELECT created_utc FROM curation_users")).scalar() == 100


def test_adding_a_guild_twice_is_a_no_op_the_second_time(db):
    assert cm.add_guild(db, 1, 3) is True
    assert cm.add_guild(db, 1, 3) is False
    assert cm.has_guild(db, 1, 3) and not cm.has_guild(db, 1, 4)
