"""Account search (`@term`) finds a handle or a display name, treats what is typed as a name and not a pattern, and keeps
leaving out private, deleted and banned accounts. On the real app, with the trigram indexes in place."""
import secrets

import pytest

BASE = "http://localhost"


def search(client, term):
    answer = client.get("/search", base_url=BASE, query_string={"q": "@" + term}, headers={"User-Agent": "ci-search"},
                        environ_base={"REMOTE_ADDR": f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"})
    assert answer.status_code == 200, answer.status_code
    return answer.get_data(as_text=True)


def appears(page, member):
    return f'/@{member.username}"' in page


def set_fields(member, **fields):
    db = member.db_session()
    user = member.user()
    for key, value in fields.items():
        setattr(user, key, value)
    db.add(user)
    db.commit()


@pytest.fixture
def visitor(real_app):
    app, _ = real_app
    return app.test_client()


def test_a_display_name_finds_the_account_as_a_handle_does(make_member, visitor):
    token = "zorb" + secrets.token_hex(3)
    member = make_member()
    set_fields(member, display_name=f"{token.capitalize()} Quillfeather")
    assert appears(search(visitor, token), member), "a word of the display name"
    assert appears(search(visitor, token.upper()), member), "in any case"
    assert appears(search(visitor, member.username), member), "the handle still works"
    assert appears(search(visitor, member.username[3:]), member), "a part of the handle"


def test_private_deleted_and_hidden_accounts_are_not_found_by_either_name(make_member, visitor):
    token = "hid" + secrets.token_hex(3)
    private, deleted, banned = make_member(), make_member(), make_member()
    for member in (private, deleted, banned):
        set_fields(member, display_name=f"{token} person")
    set_fields(private, is_private=True)
    set_fields(deleted, is_deleted=True)
    set_fields(banned, is_banned=1, unban_utc=0)
    page = search(visitor, token)
    assert not any(appears(page, m) for m in (private, deleted, banned))
    page = search(visitor, private.username)
    assert not appears(page, private)


def test_what_is_typed_is_a_name_not_a_pattern(make_member, visitor):
    plain = make_member(username="zq" + secrets.token_hex(3) + "xy")
    underscored = make_member(username="zq" + plain.username[2:8] + "_y")
    page = search(visitor, plain.username[:-2] + "_y")                        # a literal underscore, not "any letter"
    assert appears(page, underscored) and not appears(page, plain)
    assert not appears(search(visitor, "%"), plain), "a percent sign is not a wildcard"


def test_the_exact_handle_comes_first_then_an_exact_display_name(make_member, visitor):
    word = "ord" + secrets.token_hex(3)
    by_name = make_member()
    by_handle = make_member(username=word)
    partial = make_member()
    set_fields(by_name, display_name=word)
    set_fields(partial, display_name=f"{word} and more")
    page = search(visitor, word)
    places = [page.index(f'/@{m.username}"') for m in (by_handle, by_name, partial)]
    assert places == sorted(places), places


def test_the_trigram_indexes_exist(pg_engine):
    from sqlalchemy import text
    with pg_engine.connect() as conn:
        names = {r[0] for r in conn.execute(text("SELECT indexname FROM pg_indexes WHERE tablename = 'users'"))}
    assert {"users_username_trgm_idx", "users_display_name_trgm_idx"} <= names
