"""Group chats: the rules (ruqqus/helpers/chat_groups.py) and the SQL (helpers/chat_group_store.py on SQLite).

The promises: only the owner and admins add and remove people, an admin never removes the owner or another admin,
only the owner names admins; nobody is added who is a bot, deleted, suspended, in a block with whoever adds them (and
the message never says which), or into a full group; the group is handed on when its owner leaves; unread messages
count only for joined members and never for the sender."""
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import chat_groups as rules
from ruqqus.helpers import chat_group_store as store


class Person:
    def __init__(self, id, username, is_bot=False, is_deleted=False, is_suspended=False):
        self.id, self.username = id, username
        self.is_bot, self.is_deleted, self.is_suspended = is_bot, is_deleted, is_suspended


ANN, BOB, CAT, DAN = Person(1, "ann"), Person(2, "bob"), Person(3, "cat"), Person(4, "dan")


# --- rules ----------------------------------------------------------------------------------------------------

def test_names_are_plain_one_line_text():
    assert rules.clean_name("  Weekend   plans \n") == "Weekend plans"
    assert rules.clean_name("") == ""
    assert rules.clean_name("Café 🎉") == "Café 🎉"
    for bad in ("<b>x</b>", "@everyone", "a" + chr(0x200B) + "b", "x" * 51):
        with pytest.raises(rules.Refused) as e:
            rules.clean_name(bad)
        assert "group name" in str(e.value)


def test_only_owners_and_admins_add_people():
    assert rules.add_refusal(1, rules.MEMBER, BOB, False, False, 2) == "Only the group's owner and admins can add people."
    assert rules.add_refusal(1, rules.OWNER, BOB, False, False, 2) is None
    assert rules.add_refusal(1, rules.ADMIN, BOB, False, False, 2) is None


def test_who_can_never_be_added_and_the_block_is_never_named():
    same = "@{} can't be added to a group."
    assert rules.add_refusal(1, rules.OWNER, Person(5, "botty", is_bot=True), False, False, 2) == same.format("botty")
    assert rules.add_refusal(1, rules.OWNER, Person(6, "gone", is_deleted=True), False, False, 2) == same.format("gone")
    assert rules.add_refusal(1, rules.OWNER, Person(7, "sus", is_suspended=True), False, False, 2) == same.format("sus")
    assert rules.add_refusal(1, rules.OWNER, BOB, True, False, 2) == same.format("bob")       # a block, either way
    assert rules.add_refusal(1, rules.OWNER, None, False, False, 2, typed_name="nobody") == "No account called @nobody."
    assert rules.add_refusal(1, rules.OWNER, ANN, False, False, 2) == "You're already in this group."
    assert rules.add_refusal(1, rules.OWNER, BOB, False, True, 2) == "@bob is already in this group."


def test_a_group_has_at_most_max_members():
    assert rules.add_refusal(1, rules.OWNER, BOB, False, False, rules.MAX_MEMBERS - 1) is None
    assert rules.add_refusal(1, rules.OWNER, BOB, False, False, rules.MAX_MEMBERS) == \
        f"A group can have at most {rules.MAX_MEMBERS} people."


def test_who_can_remove_whom():
    assert rules.remove_refusal(1, rules.OWNER, 2, rules.ADMIN) is None
    assert rules.remove_refusal(1, rules.OWNER, 2, rules.MEMBER) is None
    assert rules.remove_refusal(2, rules.ADMIN, 3, rules.MEMBER) is None
    assert rules.remove_refusal(2, rules.ADMIN, 4, rules.ADMIN) == "Only the owner can remove an admin."
    assert rules.remove_refusal(2, rules.ADMIN, 1, rules.OWNER) == "Only the owner can remove an admin."
    assert rules.remove_refusal(3, rules.MEMBER, 4, rules.MEMBER) == "Only the group's owner and admins can remove people."
    assert rules.remove_refusal(1, rules.OWNER, 1, rules.OWNER) == "Use Leave to leave the group."
    assert rules.remove_refusal(1, rules.OWNER, 9, None) == "They aren't in this group."


def test_only_the_owner_names_admins():
    assert rules.role_refusal(rules.OWNER, 2, rules.MEMBER, rules.ADMIN, 1) is None
    assert rules.role_refusal(rules.OWNER, 2, rules.ADMIN, rules.MEMBER, 1) is None
    assert rules.role_refusal(rules.ADMIN, 3, rules.MEMBER, rules.ADMIN, 2) == "Only the owner can choose the admins."
    assert rules.role_refusal(rules.OWNER, 1, rules.OWNER, rules.MEMBER, 1) == "The owner's role can't be changed."
    assert rules.role_refusal(rules.OWNER, 2, rules.MEMBER, rules.OWNER, 1) == "That isn't a role."
    assert rules.role_refusal(rules.OWNER, 9, None, rules.ADMIN, 1) == "They aren't in this group."


def test_invitations_land_where_a_one_to_one_chat_would():
    assert rules.tab_for_invite(True) == rules.INBOX
    assert rules.tab_for_invite(False) == rules.REQUEST


def test_the_group_is_handed_on_when_its_owner_leaves():
    rest = [(2, rules.MEMBER, rules.JOINED, 50), (3, rules.ADMIN, rules.JOINED, 90), (4, rules.ADMIN, rules.JOINED, 70),
            (5, rules.ADMIN, rules.INVITED, 0)]
    assert rules.next_owner(rest) == 4                         # the earliest admin to join
    assert rules.next_owner([(2, rules.MEMBER, rules.JOINED, 50), (6, rules.MEMBER, rules.JOINED, 20)]) == 6
    assert rules.next_owner([(5, rules.ADMIN, rules.INVITED, 0)]) is None    # an invitation never takes over
    assert rules.next_owner([]) is None


def test_what_a_group_is_called():
    assert rules.label("Weekend", False, ["Ann", "Bob"], 2) == "Weekend"
    assert rules.label("Rude", True, ["Ann", "Bob"], 2) == "Ann, Bob"           # the name hidden by the word filter
    assert rules.label("", False, ["Ann", "Bob", "Cat", "Dan", "Eve"], 5) == "Ann, Bob, Cat and 2 others"
    assert rules.label("", False, ["Ann", "Bob", "Cat", "Dan"], 4) == "Ann, Bob, Cat and 1 other"
    assert rules.label("", False, [], 0) == "Group chat"


# --- the SQL --------------------------------------------------------------------------------------------------

def _tables():
    from pathlib import Path
    sql = (Path(__file__).resolve().parent.parent / "scripts" / "migrations" / "2026-10-22_chat_groups.sql").read_text(encoding="utf-8")
    sql = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
    out = []
    for s in (s.strip() for s in sql.split(";") if "CREATE" in s):
        s = s.replace("public.", "").replace("IF NOT EXISTS ", "").replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")
        out.append(s)
    return out


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for statement in _tables():
            conn.execute(text(statement))
    with Session(engine) as session:
        yield session


def _group(db, now=100):
    gid = store.create(db, "!room:ruqqus.local", "Weekend", 0, ANN.id, now)
    store.add_member(db, gid, BOB.id, rules.MEMBER, rules.JOINED, ANN.id, now + 1)
    store.add_member(db, gid, CAT.id, rules.MEMBER, rules.INVITED, ANN.id, now + 2)
    return gid


def test_the_creator_is_its_owner_and_members_are_listed_owner_first(db):
    gid = _group(db)
    assert store.group(db, gid)[1] == "!room:ruqqus.local"
    rows = store.members(db, gid)
    assert [(m[0], m[1], m[2]) for m in rows] == [(ANN.id, "owner", "joined"), (BOB.id, "member", "joined"),
                                                   (CAT.id, "member", "invited")]
    assert store.size(db, gid) == 3
    assert store.group_by_room(db, "!room:ruqqus.local")[0] == gid
    assert store.group_by_room(db, "!other:ruqqus.local") is None


def test_unread_counts_only_joined_members_and_never_the_sender(db):
    gid = _group(db)
    assert store.note_message(db, "!room:ruqqus.local", BOB.id, 200)
    assert store.member(db, gid, ANN.id)[4] == 1
    assert store.member(db, gid, BOB.id)[4] == 0          # the sender
    assert store.member(db, gid, CAT.id)[4] == 0          # an invitation: not in yet
    assert store.group(db, gid)[6] == 200                 # last activity
    assert not store.note_message(db, "!room:ruqqus.local", CAT.id, 210)    # an invited person can't write
    assert not store.note_message(db, "!room:ruqqus.local", DAN.id, 210)    # nor a stranger
    assert not store.note_message(db, "!nope:ruqqus.local", BOB.id, 210)
    assert store.unread_total(db, ANN.id) == 1 and store.unread_groups(db, ANN.id) == 1
    store.mark_read(db, gid, ANN.id, 300)
    assert store.unread_total(db, ANN.id) == 0 and store.member(db, gid, ANN.id)[5] == 300


def test_invitations_are_accepted_once_and_sit_in_requests(db):
    gid = _group(db)
    assert store.invitations(db, CAT.id) == 1
    assert [r[0] for r in store.groups_for(db, CAT.id, "invited")] == [gid]
    assert store.groups_for(db, CAT.id, "joined") == []
    assert store.accept(db, gid, CAT.id, 400)
    assert not store.accept(db, gid, CAT.id, 401)          # nothing left to accept
    assert store.member(db, gid, CAT.id)[2] == "joined" and store.member(db, gid, CAT.id)[6] == 400
    assert store.invitations(db, CAT.id) == 0


def test_a_closed_group_leaves_every_list(db):
    gid = _group(db)
    store.close(db, gid, 500)
    assert store.group(db, gid) is None and store.group_by_room(db, "!room:ruqqus.local") is None
    assert store.groups_for(db, ANN.id, "joined") == [] and store.invitations(db, CAT.id) == 0
    assert not store.note_message(db, "!room:ruqqus.local", BOB.id, 600)


def test_roles_removal_and_renaming(db):
    gid = _group(db)
    store.set_role(db, gid, BOB.id, rules.ADMIN)
    assert store.member(db, gid, BOB.id)[1] == "admin"
    store.remove_member(db, gid, BOB.id)
    assert store.member(db, gid, BOB.id) is None and store.size(db, gid) == 2
    store.rename(db, gid, "Trip", 1)
    assert store.group(db, gid)[2:4] == ("Trip", 1)


def test_daily_counts_for_the_limits(db):
    _group(db, now=100)
    store.create(db, "!two:ruqqus.local", "", 0, ANN.id, 5000)
    assert store.created_since(db, ANN.id, 0) == 2 and store.created_since(db, ANN.id, 1000) == 1
    assert store.added_since(db, ANN.id, 0) == 2           # Bob and Cat; never herself
    assert store.added_since(db, BOB.id, 0) == 0


def test_the_database_refuses_unknown_roles_and_states(db):
    gid = _group(db)
    with pytest.raises(Exception):
        store.add_member(db, gid, DAN.id, "king", rules.JOINED, ANN.id, 1)
    db.rollback()
    with pytest.raises(Exception):
        store.add_member(db, gid, DAN.id, rules.MEMBER, "lurking", ANN.id, 1)
