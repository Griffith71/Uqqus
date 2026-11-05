"""Private posting (ruqqus/helpers/private_posting.py): the rules, and the circle store's SQL on SQLite (tests/circle_tables.py): a circle
holds only mutual followers while the owner has it on, and a follow that ends takes the member out. The routes, with the real app, are in
tests/pg/test_private_posting.py."""
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from circle_tables import add_users, create, follow
from ruqqus.helpers import circle_store as store
from ruqqus.helpers import circles as c
from ruqqus.helpers import private_posting as pp

NOW = 2_000_000_000
OWNER, FRIEND, FAN, STRANGER = 1, 2, 3, 4


def test_it_is_on_only_when_the_account_says_so():
    assert pp.on(NS(private_posting=True)) and not pp.on(NS(private_posting=False)) and not pp.on(None) and not pp.on(NS())
    assert pp.refusal(NS(private_posting=True)) == pp.REFUSAL and pp.refusal(NS(private_posting=False)) is None


def test_a_public_choice_becomes_friends_and_a_circle_choice_is_kept():
    on, off = NS(private_posting=True), NS(private_posting=False)
    assert pp.circles_chosen(on, 0, [], 4) == (4, ["friends"])
    assert pp.circles_chosen(on, 4, ["12"], 4) == (4, ["12"])
    assert pp.circles_chosen(off, 0, [], 4) == (0, [])


def test_a_sub_account_cant_turn_it_off_on_its_own():
    assert pp.may_turn_off(NS(age_lock=1)) and pp.may_turn_off(NS(age_lock=None))
    assert not pp.may_turn_off(NS(age_lock=2))


def test_a_circle_member_must_be_mutual_only_while_it_is_on():
    assert pp.member_refusal(NS(private_posting=True), False) == pp.NOT_MUTUAL
    assert pp.member_refusal(NS(private_posting=True), True) is None
    assert pp.member_refusal(NS(private_posting=False), False) is None


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        create(conn)
        add_users(conn, {OWNER: ("owner", 0), FRIEND: ("friend", 0), FAN: ("fan", 0), STRANGER: ("stranger", 0)})
    session = Session(engine)
    yield session
    session.close()


def turn_on(db, uid=OWNER):
    db.execute(text("UPDATE users SET private_posting = 1 WHERE id = :i"), {"i": uid})


def family(db):
    cid, error = store.create(db, OWNER, c.CUSTOM, "Family", 0, NOW)
    assert error is None
    return cid


def test_mutual_followers_means_both_directions(db):
    follow(db, OWNER, FRIEND)
    assert not store.mutual_followers(db, OWNER, FRIEND)
    follow(db, FRIEND, OWNER)
    assert store.mutual_followers(db, OWNER, FRIEND) and store.mutual_followers(db, FRIEND, OWNER)


def test_with_it_on_only_a_mutual_follower_joins_a_custom_circle(db):
    turn_on(db)
    cid = family(db)
    follow(db, OWNER, FAN)                                    # one way only
    assert store.add_member(db, OWNER, cid, FAN, NOW) == pp.NOT_MUTUAL or "follow you" in (store.add_member(db, OWNER, cid, FAN, NOW) or "")
    assert store.add_member(db, OWNER, cid, STRANGER, NOW)
    follow(db, OWNER, FRIEND)
    follow(db, FRIEND, OWNER)
    assert store.add_member(db, OWNER, cid, FRIEND, NOW) is None


def test_with_it_off_anyone_may_be_added_as_before(db):
    cid = family(db)
    assert store.add_member(db, OWNER, cid, STRANGER, NOW) is None


def test_an_unfollow_takes_the_other_person_out_of_the_circles_of_an_owner_with_it_on(db):
    turn_on(db)
    cid = family(db)
    follow(db, OWNER, FRIEND)
    follow(db, FRIEND, OWNER)
    assert store.add_member(db, OWNER, cid, FRIEND, NOW) is None
    db.execute(text("DELETE FROM follows WHERE user_id = :a AND target_id = :b"), {"a": FRIEND, "b": OWNER})
    assert store.drop_non_mutual(db, FRIEND, OWNER) == 1
    assert store.custom_circles_with(db, OWNER, FRIEND) == set()


def test_it_works_from_either_side_of_the_follow(db):
    """The one who unfollows may be the member, and the one who has it on may be either of the two."""
    turn_on(db, FRIEND)
    cid, error = store.create(db, FRIEND, c.CUSTOM, "Mine", 0, NOW)
    assert error is None
    follow(db, OWNER, FRIEND)
    follow(db, FRIEND, OWNER)
    assert store.add_member(db, FRIEND, cid, OWNER, NOW) is None
    db.execute(text("DELETE FROM follows WHERE user_id = :a AND target_id = :b"), {"a": FRIEND, "b": OWNER})
    assert store.drop_non_mutual(db, OWNER, FRIEND) == 1


def test_an_owner_without_it_keeps_their_circles_when_a_follow_ends(db):
    cid = family(db)
    follow(db, OWNER, FRIEND)
    assert store.add_member(db, OWNER, cid, FRIEND, NOW) is None
    assert store.drop_non_mutual(db, OWNER, FRIEND) == 0
    assert store.custom_circles_with(db, OWNER, FRIEND) == {cid}


def test_a_pair_that_is_still_mutual_loses_nothing(db):
    turn_on(db)
    cid = family(db)
    follow(db, OWNER, FRIEND)
    follow(db, FRIEND, OWNER)
    store.add_member(db, OWNER, cid, FRIEND, NOW)
    assert store.drop_non_mutual(db, OWNER, FRIEND) == 0
