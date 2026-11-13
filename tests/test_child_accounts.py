"""Parent and child accounts, the rules and the SQL (ruqqus/helpers/child_accounts.py, child_store.py, child_activity.py, and the parent's switches in
helpers/age_declaration.py), on in-memory SQLite with the tables as schema.sql has them. The routes run on the real app in tests/pg/test_child_accounts.py."""
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import age_declaration as age
from ruqqus.helpers import child_accounts as rules
from ruqqus.helpers import child_activity as activity
from ruqqus.helpers import child_store as store

NOW = 2_000_000_000
PARENT, KID, KID2, OTHER, STRANGER = 1, 2, 3, 4, 5


def adult(**kw):
    base = dict(age_declared=1, age_lock=None, is_bot=False, is_deleted=False, is_suspended=False, is_quarantined=False)
    base.update(kw)
    return NS(**base)


def sub(controls=None, **kw):
    """A parent's sub-account whose controls are the defaults with these changes (a row that exists)."""
    base = dict(age_declared=2, age_lock=2, child_controls={**rules.DEFAULTS, **(controls or {})})
    base.update(kw)
    return NS(**base)


def sub_without_a_row(**kw):
    """A sub-account whose controls can't be read: `User.child_controls` is {} (no row, or the lookup failed)."""
    return NS(**{**dict(age_declared=2, age_lock=2, child_controls={}), **kw})


# ---------------------------------------------------------------- the rules

def test_chat_spaces_money_and_starting_a_guild_start_off_and_there_is_no_switch_for_posting_or_commenting():
    assert rules.DEFAULTS == {"chat": False, "spaces": False, "money": False, "anonymous_guilds": False}
    assert rules.CONTROLS == ("chat", "spaces", "money", "anonymous_guilds")      # the last is "starting a guild" (its old name): a child account only reads
    assert rules.switches(None) == rules.DEFAULTS and rules.switches({"chat": 1}) ["chat"] is True


@pytest.mark.parametrize("name, expected", [("chat", "chat"), ("posting", None), ("commenting", None), ("private_posting", None), ("", None), (None, None), ("CHAT", None),
                                            ("chat; drop table users", None), (["chat"], None)])
def test_a_control_is_only_ever_one_of_the_fixed_names(name, expected):
    assert rules.clean_control(name) == expected


def test_every_control_has_a_title_and_a_sentence():
    assert set(rules.CONTROL_TEXT) == set(rules.CONTROLS)
    assert all(len(pair) == 2 and all(pair) for pair in rules.CONTROL_TEXT.values())


@pytest.mark.parametrize("year, month", [("2013", "6"), ("2012", "12"), ("2010", "1"), ("2009", "11")])
def test_a_child_of_13_or_more_and_under_18_may_have_an_account_made(year, month):
    assert rules.birth_refusal(year, month, 2026, 11) is None


@pytest.mark.parametrize("year, month, why", [("2014", "1", rules.TOO_YOUNG), ("2013", "11", rules.TOO_YOUNG),      # might still be 12
                                              ("2008", "11", rules.NOT_A_CHILD), ("1990", "5", rules.NOT_A_CHILD),
                                              ("2027", "1", rules.BIRTH_INVALID), ("2026", "12", rules.BIRTH_INVALID), ("1800", "5", rules.BIRTH_INVALID),
                                              ("2010", "0", rules.BIRTH_INVALID), ("2010", "13", rules.BIRTH_INVALID), ("", "5", rules.BIRTH_INVALID),
                                              ("abc", "5", rules.BIRTH_INVALID), ("2010", "", rules.BIRTH_INVALID), (None, None, rules.BIRTH_INVALID),
                                              ("２０１０", "5", rules.BIRTH_INVALID), ("-2010", "5", rules.BIRTH_INVALID), ("20100", "5", rules.BIRTH_INVALID)])
def test_a_child_outside_those_ages_or_an_unreadable_date_is_refused(year, month, why):
    assert rules.birth_refusal(year, month, 2026, 11) == why


def test_in_australia_nobody_under_16_may_have_an_account_even_with_a_parent():
    assert rules.child_min_age("AU") == 16 and rules.child_min_age("GB") == 13 and rules.child_min_age(None) == 13
    assert rules.birth_refusal("2011", "5", 2026, 11, "AU") == rules.TOO_YOUNG            # 15
    assert rules.birth_refusal("2011", "5", 2026, 11, "DE") is None                        # a German teen's parent may consent
    assert rules.birth_refusal("2009", "5", 2026, 11, "AU") is None                        # 17


def test_only_an_adult_in_good_standing_whose_age_is_confirmed_makes_children():
    assert rules.parent_refusal(adult(), False, 0, 0) is None and rules.eligible_parent(adult(), False)
    for bad in (None, adult(is_bot=True), adult(is_deleted=True), adult(is_suspended=True), adult(is_quarantined=True), adult(age_declared=None),
                adult(age_declared=2, age_lock=1), adult(age_declared=2), adult(age_lock=2)):
        assert rules.parent_refusal(bad, False, 0, 0) == rules.NEED_ADULT, bad
    assert rules.parent_refusal(adult(), True, 0, 0) == rules.NEED_CHECK, "in a strict country an unconfirmed adult needs the check first"


def test_at_most_five_children_and_three_a_day():
    assert rules.parent_refusal(adult(), False, 4, 0) is None and rules.parent_refusal(adult(), False, 5, 0) == rules.TOO_MANY
    assert rules.parent_refusal(adult(), False, 2, 2) is None and rules.parent_refusal(adult(), False, 2, 3) == rules.TODAY
    assert (rules.MAX_CHILDREN, rules.PER_DAY) == (5, 3)


# ---------------------------------------------------------------- what a parent's switches do to the refusals

def test_a_sub_account_is_allowed_what_its_parent_switched_on_and_told_whose_decision_it_is_otherwise():
    off, chat = sub(), sub({"chat": True})
    assert age.feature_refusal(off, "chat") == age.PARENT_OFF
    assert age.feature_refusal(chat, "chat") is None and age.feature_refusal(chat, "money") == age.PARENT_OFF
    assert age.feature_refusal(sub({"money": True}), "money") is None
    assert age.feature_refusal(sub({"anonymous_guilds": True}), "guilds") is None
    assert age.feature_refusal(sub({"anonymous_guilds": True}), "anonymous") == age.PARENT_OFF, "there is no such feature any more: a child account posts nothing, anonymously or not"
    assert age.feature_refusal(sub({"chat": True}), None) == age.PARENT_OFF, "a feature with no name is never switched on"


def test_a_standalone_locked_account_has_no_parent_to_switch_anything():
    alone = NS(age_declared=2, age_lock=1, child_controls={"chat": True, "money": True})
    assert age.feature_refusal(alone, "chat") == age.FEATURE_LOCKED and not age.parent_allows(alone, "chat")
    assert age.feature_refusal(adult(), "chat") is None and age.feature_refusal(None, "chat") is None


def test_the_word_filter_and_sensitive_content_are_never_the_parents_to_switch():
    everything = sub({name: True for name in rules.CONTROLS})
    assert age.post_refusal(everything, True, False) == age.FEATURE_LOCKED
    assert age.locked_filter(everything, 0) and age.locked_sensitive(everything, True)


def test_a_locked_account_may_neither_post_anonymously_nor_mark_sensitive_whatever_its_parent_switched():
    everything = sub({name: True for name in rules.CONTROLS})
    assert age.post_refusal(everything, False, True) == age.FEATURE_LOCKED and age.post_refusal(everything, True, False) == age.FEATURE_LOCKED
    assert age.post_refusal(sub(), False, False) is None and age.post_refusal(NS(age_declared=2, age_lock=1), False, True) == age.FEATURE_LOCKED
    assert age.post_refusal(adult(), True, True) is None and age.post_refusal(None, True, True) is None


def test_every_locked_account_is_read_only_whoever_holds_the_lock_and_whatever_its_parent_switched():
    """The owner, 2026-11-05: a child account can't create content at all. One rule, no parent's switch."""
    for locked in (sub(), sub({name: True for name in rules.CONTROLS}), sub_without_a_row(), NS(age_declared=2, age_lock=1, child_controls={}),
                   NS(age_declared=2, age_lock=None)):
        assert age.content_refusal(locked) == age.FEATURE_LOCKED, locked
    assert age.content_refusal(adult()) is None and age.content_refusal(None) is None
    assert age.content_refusal(NS(age_declared=None, age_lock=None)) is None, "an account not asked yet is not a child"
    assert not hasattr(age, "activity_refusal"), "the parent's posting and commenting switches are gone"


def test_a_sub_account_whose_controls_cant_be_read_is_refused_everything_it_fails_closed():
    lost = sub_without_a_row()
    assert age.content_refusal(lost) == age.FEATURE_LOCKED
    for feature in ("chat", "spaces", "money", "guilds", "anonymous", "messaging"):
        assert age.feature_refusal(lost, feature) == age.PARENT_OFF, feature


def test_someone_can_reach_a_sub_account_for_what_its_parent_allowed():
    assert age.target_refusal(sub(), "chat") == age.TARGET_LOCKED and age.target_refusal(sub({"chat": True}), "chat") is None
    assert age.target_refusal(sub({"chat": True}), "money") == age.TARGET_LOCKED
    assert age.target_refusal(NS(age_declared=2, age_lock=1, child_controls={"chat": True}), "chat") == age.TARGET_LOCKED
    assert age.target_refusal(adult(), "chat") is None


def test_a_sub_account_is_made_a_child_account_held_by_its_parent_on_the_child_filter():
    fields = age.sub_account_fields(NOW)
    assert fields["age_declared"] == age.CHILD and fields["age_lock"] == age.PARENT_LOCK == 2
    assert fields["filter_level"] == 2 and fields["all_shows_sensitive"] is False and "private_posting" not in fields


# ---------------------------------------------------------------- the SQL

TABLES = (
    "CREATE TABLE users (id integer PRIMARY KEY, username text, is_deleted boolean DEFAULT 0, is_banned integer DEFAULT 0, unban_utc integer DEFAULT 0, "
    "age_declared integer, age_lock integer, negative_balance_cents integer DEFAULT 0)",
    "CREATE TABLE child_family (id integer PRIMARY KEY AUTOINCREMENT, parent_id integer NOT NULL, child_id integer, made_utc integer NOT NULL DEFAULT 0, "
    "suspended_until_utc integer NOT NULL DEFAULT 0, prior_until_utc integer NOT NULL DEFAULT 0)",
    "CREATE TABLE account_links (child_id integer PRIMARY KEY, parent_id integer NOT NULL, created_utc integer NOT NULL DEFAULT 0)",
    "CREATE TABLE child_controls (child_id integer PRIMARY KEY, chat boolean NOT NULL DEFAULT 0, spaces boolean NOT NULL DEFAULT 0, money boolean NOT NULL DEFAULT 0, "
    "anonymous_guilds boolean NOT NULL DEFAULT 0, "
    "ask_release_utc integer NOT NULL DEFAULT 0, ask_release_country text, updated_utc integer NOT NULL DEFAULT 0)",
    "CREATE TABLE submissions (id integer PRIMARY KEY, author_id integer, repost_id integer, deleted_utc integer DEFAULT 0, is_space boolean DEFAULT 0, "
    "is_anonymous boolean DEFAULT 0, is_banned boolean DEFAULT 0, audience integer DEFAULT 0, created_utc integer DEFAULT 0, post_public boolean DEFAULT 1, "
    "board_id integer DEFAULT 1, hidden_by_guild boolean DEFAULT 0)",
    "CREATE TABLE submissions_aux (id integer, title text)",
    "CREATE TABLE comments (id integer PRIMARY KEY, author_id integer, parent_submission integer, is_banned boolean, deleted_utc integer DEFAULT 0, "
    "is_anonymous boolean DEFAULT 0, created_utc integer DEFAULT 0)",
    "CREATE TABLE comments_aux (id integer, body text)",
    "CREATE TABLE votes (id integer PRIMARY KEY AUTOINCREMENT, user_id integer, submission_id integer, vote_type integer, created_utc integer DEFAULT 0)",
    "CREATE TABLE save_relationship (id integer PRIMARY KEY AUTOINCREMENT, submission_id integer, user_id integer, created_utc integer DEFAULT 0)",
    "CREATE TABLE follows (id integer PRIMARY KEY AUTOINCREMENT, user_id integer, target_id integer, created_utc integer DEFAULT 0)",
    "CREATE TABLE boards (id integer PRIMARY KEY, name text, is_private boolean DEFAULT 0, is_circle boolean DEFAULT 0, is_banned boolean DEFAULT 0)",
    "CREATE TABLE subscriptions (id integer PRIMARY KEY AUTOINCREMENT, user_id integer, board_id integer, created_utc integer DEFAULT 0, is_active boolean)",
    "CREATE TABLE user_reports (id integer PRIMARY KEY AUTOINCREMENT, user_id integer, reporter_id integer, reason text, created_utc integer DEFAULT 0, "
    "resolved_utc integer DEFAULT 0)",
    "CREATE TABLE chat_conversations (id integer PRIMARY KEY AUTOINCREMENT, user_a_id integer, user_b_id integer, last_activity_utc integer DEFAULT 0)",
    "CREATE TABLE chat_groups (id integer PRIMARY KEY, name text, deleted_utc integer DEFAULT 0, last_activity_utc integer DEFAULT 0)",
    "CREATE TABLE chat_group_members (group_id integer, user_id integer, status text)",
)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for statement in TABLES:
            conn.execute(text(statement))
        for uid, name in ((PARENT, "parent"), (KID, "Kid"), (KID2, "kid2"), (OTHER, "other"), (STRANGER, "stranger")):
            conn.execute(text("INSERT INTO users (id, username) VALUES (:i, :n)"), {"i": uid, "n": name})
    session = Session(engine)
    yield session
    session.close()


def test_a_child_is_linked_to_one_parent_with_the_defaults(db):
    assert store.link(db, KID, PARENT, NOW) is True
    assert store.link(db, KID, OTHER, NOW) is False, "a child has one parent"
    assert store.parent_of(db, KID) == PARENT and store.parent_of(db, STRANGER) is None
    assert store.controls_of(db, KID) == {"chat": False, "spaces": False, "money": False, "anonymous_guilds": False}
    assert store.controls_of(db, STRANGER) is None
    assert store.linked(db, KID, PARENT) and store.linked(db, PARENT, KID) and not store.linked(db, KID, OTHER) and not store.linked(db, KID, KID2)


def test_a_parents_children_are_listed_oldest_first_with_their_switches(db):
    store.link(db, KID, PARENT, NOW)
    store.link(db, KID2, PARENT, NOW + 5)
    store.link(db, STRANGER, OTHER, NOW)
    assert store.child_ids(db, PARENT) == [KID, KID2] and store.count_children(db, PARENT) == 2
    store.set_control(db, KID, "chat", True, NOW)
    listed = store.children_of(db, PARENT, NOW)
    assert [c["username"] for c in listed] == ["Kid", "kid2"] and listed[0]["controls"]["chat"] is True and listed[1]["controls"]["chat"] is False
    assert all(c["ask_release_utc"] == 0 and "private_posting" not in c and "ask_private_utc" not in c for c in listed)
    assert store.made_since(db, PARENT, NOW + 1) == 1 and store.made_since(db, PARENT, 0) == 2


def test_a_parent_reaches_only_their_own_child_by_name_ignoring_case(db):
    store.link(db, KID, PARENT, NOW)
    store.link(db, STRANGER, OTHER, NOW)
    assert store.child_of(db, PARENT, "kid", NOW)["id"] == KID and store.child_of(db, PARENT, "KID", NOW)["id"] == KID
    assert store.child_of(db, PARENT, "stranger", NOW) is None, "someone else's child is nobody's business"
    assert store.child_of(db, PARENT, "parent", NOW) is None and store.child_of(db, PARENT, None, NOW) is None and store.child_of(db, PARENT, "", NOW) is None
    db.execute(text("UPDATE users SET is_deleted = 1 WHERE id = :i"), {"i": KID})
    assert store.child_of(db, PARENT, "kid", NOW) is None and store.children_of(db, PARENT, NOW) == []


@pytest.mark.parametrize("name", ["chat", "spaces", "money", "anonymous_guilds"])
def test_each_switch_changes_only_itself(db, name):
    store.link(db, KID, PARENT, NOW)
    before = store.controls_of(db, KID)
    assert store.set_control(db, KID, name, not before[name], NOW + 1)
    after = store.controls_of(db, KID)
    assert after[name] is (not before[name]) and {k: v for k, v in after.items() if k != name} == {k: v for k, v in before.items() if k != name}


@pytest.mark.parametrize("name", ["", None, "private_posting", "posting", "commenting", "chat = 1, money", "child_id", "ask_private_utc", "updated_utc"])
def test_nothing_but_the_four_names_reaches_the_sql(db, name):
    store.link(db, KID, PARENT, NOW)
    assert store.set_control(db, KID, name, True, NOW) is False
    assert store.controls_of(db, KID) == rules.DEFAULTS


def test_a_switch_for_an_account_with_no_controls_changes_nothing(db):
    assert store.set_control(db, STRANGER, "chat", True, NOW) is False


def test_a_child_asks_once_and_the_parent_clears_it(db):
    store.link(db, KID, PARENT, NOW)
    assert store.asked(db, KID, "release") == 0
    assert store.ask(db, KID, "release", NOW + 9) and store.asked(db, KID, "release") == NOW + 9
    assert store.children_of(db, PARENT, NOW)[0]["ask_release_utc"] == NOW + 9
    assert store.clear_ask(db, KID, "release") and store.asked(db, KID, "release") == 0
    assert store.ask(db, KID, "private", NOW) is False and store.asked(db, KID, "private") == 0, "Private posting is gone, and so is asking about it"
    assert store.ask(db, KID, "bogus", NOW) is False and store.asked(db, KID, "bogus") == 0
    assert store.ask(db, STRANGER, "release", NOW) is False, "an account with no controls row has nobody to ask"


def test_releasing_ends_the_link_and_says_whose_it_was(db):
    store.link(db, KID, PARENT, NOW)
    assert store.release(db, KID) == PARENT
    assert store.parent_of(db, KID) is None and store.controls_of(db, KID) is None and store.child_ids(db, PARENT) == []
    assert store.release(db, KID) is None


def test_closing_an_account_forgets_its_own_link_and_its_childrens(db):
    store.link(db, KID, PARENT, NOW)
    store.link(db, KID2, PARENT, NOW)
    store.link(db, STRANGER, OTHER, NOW)
    store.forget_account(db, PARENT)
    assert store.child_ids(db, PARENT) == [] and store.controls_of(db, KID) is None and store.controls_of(db, KID2) is None
    assert store.parent_of(db, STRANGER) == OTHER, "someone else's family is untouched"
    store.forget_account(db, STRANGER)
    assert store.parent_of(db, STRANGER) is None and store.child_ids(db, OTHER) == []


def test_the_limits_count_what_a_parent_made_whatever_became_of_it(db):
    """Stopping, releasing or deleting a child must not free a slot in the limits: they read the ledger, not the live links."""
    for child in (KID, KID2, STRANGER):
        store.link(db, child, PARENT, NOW)
    store.release(db, KID)                       # stopped, or released
    store.forget_account(db, KID2)               # deleted (the ledger row stays, without its id)
    assert store.count_children(db, PARENT) == 1 and store.made_since(db, PARENT, 0) == 3
    assert rules.parent_refusal(adult(), False, 1, 3, 3) == rules.TODAY
    assert rules.parent_refusal(adult(), False, 0, 0, rules.PER_WINDOW) == rules.RECENT
    assert rules.parent_refusal(adult(), False, 0, 0, rules.PER_WINDOW - 1) is None


def test_a_parents_whole_family_is_related_even_after_the_link_ended_and_siblings_too(db):
    store.link(db, KID, PARENT, NOW)
    store.link(db, KID2, PARENT, NOW)
    store.link(db, STRANGER, OTHER, NOW)
    assert store.related(db, PARENT, KID) and store.related(db, KID, PARENT)
    assert store.related(db, KID, KID2) and store.related(db, KID2, KID), "two children of one parent are one family: they sign in on one browser on purpose"
    assert not store.related(db, KID, STRANGER) and not store.related(db, PARENT, OTHER) and not store.related(db, PARENT, STRANGER)
    store.release(db, KID)                       # the link ends, the family doesn't
    assert store.related(db, PARENT, KID) and store.related(db, KID, KID2)
    store.forget_account(db, KID)                # the child is deleted: its id leaves the ledger
    assert not store.related(db, PARENT, KID)


def test_the_tolerant_lookups_answer_a_default_when_the_tables_are_not_there_yet(db):
    db.execute(text("DROP TABLE child_family"))
    db.execute(text("DROP TABLE account_links"))
    assert store.related_safely(db, PARENT, KID) is False and store.child_ids_safely(db, PARENT) == []


def test_a_request_is_opened_once_and_answered_once(db):
    store.link(db, KID, PARENT, NOW)
    assert store.ask(db, KID, "release", NOW + 1, "GB") is True
    assert store.ask(db, KID, "release", NOW + 2, "US") is False, "asking again while it is open changes nothing, so the parent is told once"
    assert store.asked(db, KID, "release") == NOW + 1 and store.asked_country(db, KID) == "GB", "where the CHILD asked from is what is kept"
    assert store.take_ask(db, KID, "release") is True and store.take_ask(db, KID, "release") is False, "two answers at once do their work once"
    assert store.asked(db, KID, "release") == 0 and store.asked_country(db, KID) is None
    assert store.ask(db, KID, "private", NOW, None) is False and store.take_ask(db, KID, "bogus") is False


def test_a_suspension_the_site_gave_a_child_is_remembered_on_the_ledger_and_outlives_the_link(db):
    store.link(db, KID, PARENT, NOW)
    store.link(db, KID2, PARENT, NOW)
    assert store.suspension(db, KID) == (0, 0) and store.suspended_children(db, PARENT) == []
    assert store.set_suspension(db, KID, NOW + 500) and store.suspension(db, KID) == (NOW + 500, 0)
    assert store.set_suspension(db, KID2, NOW + 900, NOW + 300)
    assert store.suspended_children(db, PARENT) == [(KID, NOW + 500, 0), (KID2, NOW + 900, NOW + 300)]
    store.release(db, KID)                       # the link ends while the parent is still suspended: the marker is still found
    assert store.suspended_children(db, PARENT)[0] == (KID, NOW + 500, 0) and store.suspension(db, KID) == (NOW + 500, 0)
    assert store.set_suspension(db, KID, 0, 0) and store.suspended_children(db, PARENT) == [(KID2, NOW + 900, NOW + 300)]
    assert store.set_suspension(db, STRANGER, 5) is False and store.suspension(db, STRANGER) == (0, 0)
    store.forget_account(db, KID2)               # a closed account has nothing left to lift
    assert store.suspended_children(db, PARENT) == []


def test_money_is_locked_for_a_locked_account_unless_a_parent_switched_it_on(db):
    store.link(db, KID, PARENT, NOW)
    db.execute(text("UPDATE users SET age_declared = 2, age_lock = 2 WHERE id = :k"), {"k": KID})
    db.execute(text("UPDATE users SET age_declared = 2, age_lock = 1 WHERE id = :k"), {"k": KID2})
    db.execute(text("UPDATE users SET age_declared = 1 WHERE id = :k"), {"k": OTHER})
    assert store.money_locked(db, KID) and store.money_locked(db, KID2)
    assert not store.money_locked(db, OTHER) and not store.money_locked(db, STRANGER) and not store.money_locked(db, PARENT)
    store.set_control(db, KID, "money", True, NOW)
    assert not store.money_locked(db, KID), "a parent's switch allows it"
    db.execute(text("UPDATE users SET age_lock = 1 WHERE id = :k"), {"k": KID})
    assert store.money_locked(db, KID), "a standalone lock has no parent to allow it, whatever a row left behind says"


def test_a_parent_who_has_a_child_that_owes_money_is_told_so(db):
    store.link(db, KID, PARENT, NOW)
    store.link(db, KID2, PARENT, NOW)
    assert store.owing_children(db, PARENT) == 0
    db.execute(text("UPDATE users SET negative_balance_cents = 300 WHERE id = :k"), {"k": KID2})
    assert store.owing_children(db, PARENT) == 1 and store.owing_children(db, OTHER) == 0
    db.execute(text("UPDATE users SET is_deleted = 1 WHERE id = :k"), {"k": KID2})
    assert store.owing_children(db, PARENT) == 0


def test_the_ban_cascade_may_run_where_there_is_no_advisory_lock(db):
    """The lock is Postgres's: on SQLite a required lock raises and the cascade's own (required=False) is ignored."""
    import sqlalchemy
    with pytest.raises(sqlalchemy.exc.DBAPIError):
        store.lock_parent(db, PARENT)
    db.rollback()
    store.lock_parent(db, PARENT, required=False)
    assert store.count_children_safely(db, PARENT) == 0


def test_a_child_listed_as_suspended_only_while_the_suspension_is_in_force(db):
    store.link(db, KID, PARENT, NOW)
    db.execute(text("UPDATE users SET is_banned = 1, unban_utc = :u WHERE id = :i"), {"u": NOW + 100, "i": KID})
    assert store.children_of(db, PARENT, NOW)[0]["suspended"] is True
    assert store.children_of(db, PARENT, NOW + 200)[0]["suspended"] is False, "a lapsed suspension is not shown as one"
    db.execute(text("UPDATE users SET unban_utc = 0 WHERE id = :i"), {"i": KID})
    assert store.children_of(db, PARENT, NOW + 999)[0]["suspended"] is True, "a permanent one has no end"


# ---------------------------------------------------------------- what a parent sees

def put(db, sql, **params):
    db.execute(text(sql), params)


def test_the_activity_is_the_childs_and_nobody_elses(db):
    put(db, "INSERT INTO boards (id, name) VALUES (1, 'pets')")
    for sid, author, extra in ((10, KID, {}), (11, OTHER, {}), (12, KID, {"d": 5}), (13, KID, {"r": 10}), (14, KID, {"sp": 1}), (15, KID, {"anon": 1, "aud": 4}),
                               (16, KID, {"ban": 1})):
        put(db, "INSERT INTO submissions (id, author_id, repost_id, deleted_utc, is_space, is_anonymous, audience, is_banned, created_utc) "
                "VALUES (:i, :a, :r, :d, :sp, :an, :au, :b, :t)",
            i=sid, a=author, r=extra.get("r", 0), d=extra.get("d", 0), sp=extra.get("sp", 0), an=extra.get("anon", 0), au=extra.get("aud", 0), b=extra.get("ban", 0), t=sid)
        put(db, "INSERT INTO submissions_aux (id, title) VALUES (:i, :t)", i=sid, t=f"Title &amp; {sid}")
    posts = activity.posts(db, KID)
    assert [p["id"] for p in posts] == [16, 15, 10], "only the child's own live originals, newest first (not deleted, a repost or a Space)"
    assert posts[2]["title"] == "Title & 10", "titles are stored as escaped HTML and come out as plain text for the template to escape"
    assert posts[1]["anonymous"] and posts[1]["for_circles"] and posts[0]["removed"] and posts[2]["b36"]
    assert activity.posts(db, STRANGER) == []


def test_votes_and_bookmarks_never_name_a_post_other_people_kept_to_a_circle_or_a_private_guild(db):
    put(db, "INSERT INTO boards (id, name, is_private, is_circle, is_banned) VALUES (1, 'pets', 0, 0, 0), (2, 'secret', 1, 0, 0), (3, 'lounge', 0, 1, 0), (4, 'gone', 0, 0, 1)")
    # (id, public, deleted, banned, board, hidden by the guild)
    for sid, public, deleted, banned, board, hidden in ((40, 1, 0, 0, 1, 0), (41, 0, 0, 0, 1, 0), (42, 1, 7, 0, 1, 0), (43, 1, 0, 1, 1, 0),
                                                        (44, 1, 0, 0, 2, 0), (45, 1, 0, 0, 3, 0), (46, 1, 0, 0, 4, 0), (47, 1, 0, 0, 1, 1)):
        put(db, "INSERT INTO submissions (id, author_id, post_public, deleted_utc, is_banned, board_id, hidden_by_guild) VALUES (:i, :o, :p, :d, :b, :bd, :h)",
            i=sid, o=OTHER, p=public, d=deleted, b=banned, bd=board, h=hidden)
        put(db, "INSERT INTO submissions_aux (id, title) VALUES (:i, :t)", i=sid, t=f"Post {sid}")
        put(db, "INSERT INTO votes (user_id, submission_id, vote_type) VALUES (:k, :i, 1)", k=KID, i=sid)
        put(db, "INSERT INTO save_relationship (submission_id, user_id) VALUES (:i, :k)", i=sid, k=KID)
    assert [v["post_id"] for v in activity.votes(db, KID)] == [40] and [b["post_id"] for b in activity.bookmarks(db, KID)] == [40], \
        "a post another member kept private, in a private or Circle guild, banned, hidden or removed is never named"


def test_a_comment_in_a_private_place_shows_no_words_and_the_rest_do(db):
    put(db, "INSERT INTO boards (id, name, is_private) VALUES (1, 'pets', 0), (2, 'secret', 1)")
    put(db, "INSERT INTO submissions (id, author_id, board_id) VALUES (20, :o, 1), (21, :o, 2)", o=OTHER)
    put(db, "INSERT INTO submissions_aux (id, title) VALUES (20, 'Hello'), (21, 'Hidden')")
    put(db, "INSERT INTO comments (id, author_id, parent_submission, is_banned) VALUES (30, :k, 20, 0), (31, :k, 21, 0)", k=KID)
    put(db, "INSERT INTO comments_aux (id, body) VALUES (30, 'in the open'), (31, 'about the secret')")
    by_id = {c["id"]: c for c in activity.comments(db, KID)}
    assert by_id[30]["text"] == "in the open" and by_id[31]["text"] is None, "the words of a comment would say what a private post is about"


def test_comments_votes_bookmarks_follows_and_guilds(db):
    put(db, "INSERT INTO boards (id, name, is_private, is_circle) VALUES (1, 'pets', 0, 0), (2, 'cars', 0, 0), (3, 'secret', 1, 0), (4, 'lounge', 0, 1)")
    put(db, "INSERT INTO submissions (id, author_id, board_id) VALUES (20, :o, 1)", o=OTHER)
    put(db, "INSERT INTO submissions_aux (id, title) VALUES (20, 'Hello')")
    put(db, "INSERT INTO comments (id, author_id, parent_submission, is_banned) VALUES (30, :k, 20, 0)", k=KID)
    put(db, "INSERT INTO comments (id, author_id, parent_submission, is_banned, deleted_utc) VALUES (31, :k, 20, 0, 9)", k=KID)
    put(db, "INSERT INTO comments (id, author_id, parent_submission, is_banned) VALUES (32, :o, 20, 0)", o=OTHER)
    put(db, "INSERT INTO comments_aux (id, body) VALUES (30, :b)", b="a  long\n comment " * 40)
    put(db, "INSERT INTO comments_aux (id, body) VALUES (31, 'deleted'), (32, 'not theirs')")
    assert [c["id"] for c in activity.comments(db, KID)] == [30]
    text30 = activity.comments(db, KID)[0]["text"]
    assert len(text30) <= activity.EXCERPT + 1 and "\n" not in text30 and "  " not in text30 and text30.endswith("…")
    put(db, "INSERT INTO votes (user_id, submission_id, vote_type) VALUES (:k, 20, 1), (:k, 20, 0), (:o, 20, -1)", k=KID, o=OTHER)
    assert [(v["post_id"], v["up"]) for v in activity.votes(db, KID)] == [(20, True)], "a taken-back vote isn't a vote; nobody else's is shown"
    put(db, "INSERT INTO save_relationship (submission_id, user_id) VALUES (20, :k), (20, :o)", k=KID, o=OTHER)
    assert [b["post_id"] for b in activity.bookmarks(db, KID)] == [20]
    put(db, "INSERT INTO follows (user_id, target_id) VALUES (:k, :o), (:o, :k)", k=KID, o=OTHER)
    assert [f["username"] for f in activity.follows(db, KID)] == ["other"]
    put(db, "INSERT INTO subscriptions (user_id, board_id, is_active) VALUES (:k, 1, 1), (:k, 2, 0), (:o, 2, 1), (:k, 3, 1), (:k, 4, 1)", k=KID, o=OTHER)
    assert [g["name"] for g in activity.guilds(db, KID)] == ["pets"], "only open guilds are named"
    assert activity.private_guilds(db, KID) == 2, "a private or Circle guild is counted and never named"


def test_reports_show_that_they_were_made_and_never_what_the_reporter_wrote_or_who(db):
    put(db, "INSERT INTO user_reports (user_id, reporter_id, reason, resolved_utc) VALUES (:k, :o, 'I am in his class', 0), (:k, :s, 'spam', 9), (:o, :s, 'not theirs', 0)",
        k=KID, o=OTHER, s=STRANGER)
    reports = activity.reports_against(db, KID)
    assert sorted(r["resolved"] for r in reports) == [False, True]
    assert all(set(r) == {"created_utc", "resolved"} for r in reports), "no reporter id and no words anywhere"


def test_who_they_chat_with_is_names_only(db):
    put(db, "INSERT INTO chat_conversations (user_a_id, user_b_id) VALUES (:k, :o), (:s, :k), (:o, :s)", k=KID, o=OTHER, s=STRANGER)
    put(db, "INSERT INTO chat_groups (id, name) VALUES (1, 'Study'), (2, 'Gone'), (3, 'Invited')")
    put(db, "UPDATE chat_groups SET deleted_utc = 5 WHERE id = 2")
    put(db, "INSERT INTO chat_group_members (group_id, user_id, status) VALUES (1, :k, 'joined'), (2, :k, 'joined'), (3, :k, 'invited'), (1, :o, 'joined')", k=KID, o=OTHER)
    people, groups = activity.chat_partners(db, KID)
    assert sorted(people) == ["other", "stranger"] and groups == [("Study", 1)]
    everything = activity.everything(db, KID)
    assert set(everything) == {"posts", "comments", "votes", "bookmarks", "follows", "guilds", "private_guilds", "reports", "chat_people", "chat_groups"}


def test_nothing_in_the_activity_module_reads_a_chat_or_a_table_outside_the_list():
    """The tables it reads are a fixed list, none of them holds what a chat says (the site holds no chat text: chats are end-to-end encrypted), and
    nothing in it names a room, a message or the chat server."""
    import ast
    import re
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / "ruqqus" / "helpers" / "child_activity.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):                        # the code and its SQL, without the docstrings that say what it never does
        if isinstance(node, (ast.Module, ast.FunctionDef)) and node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
            node.body = node.body[1:]
    code = ast.unparse(tree)
    tables = set(re.findall(r"(?:FROM|JOIN)\s+(\w+)", code))
    assert tables == {"submissions", "submissions_aux", "comments", "comments_aux", "votes", "save_relationship", "follows", "users", "subscriptions", "boards",
                      "user_reports", "chat_conversations", "chat_group_members", "chat_groups"}
    for word in ("matrix", "room", "message", "event"):
        assert word not in code.lower(), word
