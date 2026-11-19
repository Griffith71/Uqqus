"""The small fixes after the review of the automatic quarantine (2026-11-18), on the real app and Postgres; the rules are tested on SQLite in tests/test_auto_quarantine.py
and tests/test_log_appeals.py.

- the PUBLIC lines (`automatic_quarantine`, `automatic_quarantine_lifted`) are dated to the hour, so they can't be paired by the second with the removals the same request
  made; the staff's own entries keep the exact time;
- the number beside Appeals on /admin is the queue the admin it is drawn for may read: a legal-level appeal (a link about minors) is in a level 4 admin's number and not a
  level 3 admin's, and the number and the page's rows are the same set;
- a held guildmaster still READS what their guild hid (a held account reads the site) but has none of a guildmaster's powers, and the content permission is still asked."""
import re

import pytest
from sqlalchemy import text

from pg.test_log_trail_routes import BASE, HEADERS, add_comment, add_post, address, admin_of, entries, get, guild, guildmaster, post, rows
from pg.test_quarantine_queue import appeal, hold_of, publish_lines, release, text_of


@pytest.fixture(autouse=True)
def no_stale_notices(real_app):
    """A worker (and so a test's thread) may reuse one session, and its waiting notices live on it: what an earlier test queued is not this one's to send."""
    real_app[1]().info.pop("auto_quarantine_notices", None)
    yield
    real_app[1]().info.pop("auto_quarantine_notices", None)


# --- the public lines are dated to the hour ----------------------------------------------------------------------------------------------------------------

def test_the_public_lines_are_dated_to_the_hour_and_the_staffs_entries_keep_the_exact_time(real_app, make_member):
    member, boss = make_member(), admin_of(make_member)
    entry = hold_of(real_app, member, "spam", detail="six similar posts in a minute")
    (staff,) = entries(real_app, id=entry)
    publish_lines(real_app)
    (public,) = entries(real_app, action="automatic_quarantine", linked_entry_id=entry)
    assert public["created_utc"] % 3600 == 0
    assert public["created_utc"] == staff["created_utc"] - staff["created_utc"] % 3600 and 0 <= staff["created_utc"] - public["created_utc"] < 3600
    assert public["target_user_id"] is None and public["target_submission_id"] is None and public["audience"] == 0

    lifted = release(boss.browser(), boss, member, entry)
    assert lifted.status_code == 303 and "Lifted" in lifted.headers["Location"]
    (lift,) = entries(real_app, action="unquarantine_user", linked_entry_id=entry)
    publish_lines(real_app)
    (public_lift,) = entries(real_app, action="automatic_quarantine_lifted", linked_entry_id=lift["id"])
    assert public_lift["created_utc"] % 3600 == 0 and public_lift["created_utc"] == lift["created_utc"] - lift["created_utc"] % 3600
    assert lift["audience"] >= 2 and public_lift["audience"] == 0 and public_lift["target_user_id"] is None
    # a visitor reads both lines, and nobody's name. Each is read at its own address: dated to the hour, a public line sorts below every entry written since the hour began
    # (the shared database holds many), so it need not be on the first page of the sanction tab
    visitor = real_app[0].test_client()
    held_page, lifted_page = text_of(get(visitor, f"/log/{public['id']}")), text_of(get(visitor, f"/log/{public_lift['id']}"))
    assert "put an account in quarantine" in held_page and "Automatic spam check" in held_page
    assert "lifted a quarantine an automatic check had placed" in lifted_page
    assert member.username not in held_page + lifted_page and boss.username not in held_page + lifted_page
    assert get(visitor, f"/log/{staff['id']}").status_code == 404 and get(visitor, f"/log/{lift['id']}").status_code == 404, "the staff's entries are not the public's"


def test_a_fresh_hold_has_no_public_line_until_the_cycle_writes_it_and_its_id_is_not_next_to_the_staffs(real_app, make_member):
    """The public line used to be written beside the staff entry (and the removal the same check made), so the ids in the Log's links paired them. Now the cycle writes it
    an hour or more later, with other entries between."""
    member, other = make_member(), make_member()
    entry = hold_of(real_app, member, "spam", detail="six similar posts in a minute")
    visitor = real_app[0].test_client()
    assert not entries(real_app, action="automatic_quarantine", linked_entry_id=entry)
    assert get(visitor, f"/log/{entry}").status_code == 404                                     # the staff entry is not public either
    hold_of(real_app, other, "spam", detail="an unrelated hold written in between")             # an unrelated entry between the hold and its line
    publish_lines(real_app)
    (line,) = entries(real_app, action="automatic_quarantine", linked_entry_id=entry)
    assert line["id"] > entry + 1 and line["audience"] == 0 and line["target_user_id"] is None
    page = text_of(get(visitor, f"/log/{line['id']}"))
    assert "put an account in quarantine" in page and "Automatic spam check" in page and member.username not in page
    publish_lines(real_app)
    assert len(entries(real_app, action="automatic_quarantine", linked_entry_id=entry)) == 1                            # written once


# --- the count beside Appeals on /admin --------------------------------------------------------------------------------------------------------------------

def appeals_on_admin_page(browser):
    page = text_of(get(browser, "/admin"))
    found = re.search(r'href="/admin/appeals">[^<]*</a>\s*\((\d+) waiting\)', page)
    assert found, "the admin page has no Appeals line"
    return int(found.group(1))


def rows_on_appeals_page(browser):
    page = text_of(get(browser, "/admin/appeals"))
    return page, len(re.findall(r'id="appeal-\d+"', page))


def test_the_number_beside_appeals_is_the_queue_the_admin_may_read_and_a_legal_appeal_is_level_4s(real_app, make_member):
    member, plain, level3, level4 = make_member(), make_member(), admin_of(make_member, 3), admin_of(make_member, 4)
    before3, before4 = appeals_on_admin_page(level3.browser()), appeals_on_admin_page(level4.browser())

    # an appeal of a quarantine whose entry is staff-level: both admins are told of it
    hold_of(real_app, plain, "spam", detail="six similar posts in a minute")
    appeal(real_app, plain)
    assert appeals_on_admin_page(level3.browser()) == before3 + 1 and appeals_on_admin_page(level4.browser()) == before4 + 1

    # an appeal of one about minors (legal-level): level 4's number only, and its row only on level 4's page
    hold_of(real_app, member, "link_minors", detail="the domain's own reason")
    filed = appeal(real_app, member)
    assert appeals_on_admin_page(level3.browser()) == before3 + 1, "level 3 is told of nothing that is level 4's to read"
    assert appeals_on_admin_page(level4.browser()) == before4 + 2
    page3, rows3 = rows_on_appeals_page(level3.browser())
    page4, rows4 = rows_on_appeals_page(level4.browser())
    assert f'id="appeal-{filed["id"]}"' not in page3 and member.username not in page3
    assert f'id="appeal-{filed["id"]}"' in page4

    # the number and the page are the same set (the queue lists 100 at most, so only while it is short enough to be drawn whole)
    if before4 + 2 < 100:
        assert rows3 == appeals_on_admin_page(level3.browser()) and rows4 == appeals_on_admin_page(level4.browser())


# --- a held guildmaster still reads, and still has no powers -----------------------------------------------------------------------------------------------

def read_post(client, b36):
    return client.get(f"/post/{b36}", base_url=BASE, headers=HEADERS, environ_base={"REMOTE_ADDR": address()}, follow_redirects=True)


def test_a_held_guildmaster_still_reads_what_their_guild_hid_but_has_none_of_its_powers(real_app, make_member):
    from ruqqus.classes import Board, Comment, User
    from ruqqus.helpers import visibility

    board_id, name = guild(real_app)
    full, held = guildmaster(make_member, board_id), guildmaster(make_member, board_id)
    no_content, no_content_held = (guildmaster(make_member, board_id, perm_full=False, perm_content=False) for _ in range(2))
    author, stranger = make_member(), make_member()
    post_id, b36 = add_post(author, board_id, "HIDDEN-TITLE", "<p>HIDDEN-WORDS</p>")
    other_id, other36 = add_post(author, board_id, "OTHER-TITLE", "<p>OTHER-WORDS</p>")
    hidden = post(full.browser(), full, f"/mod/kick/{name}/{b36}", reason="Advertising", rule="good_faith")
    assert hidden.status_code == 200, hidden.get_data(as_text=True)[:300]
    hold_of(real_app, held)
    hold_of(real_app, no_content_held)

    removed = f"Removed by guildmasters of +{name}"
    # the post's page: the content guildmasters read it, held or not; a guildmaster with no content permission, a stranger and a visitor read the notice
    for reader in (full, held):
        page = text_of(read_post(reader.browser(), b36))
        assert "HIDDEN-WORDS" in page and removed not in page, reader.username
    for client in (no_content.browser(), no_content_held.browser(), stranger.browser(), real_app[0].test_client()):
        page = text_of(read_post(client, b36))
        assert removed in page and "HIDDEN-WORDS" not in page

    # none of a guildmaster's powers: a held guildmaster hides nothing
    refused = post(held.browser(), held, f"/mod/kick/{name}/{other36}", reason="Advertising", rule="good_faith")
    assert refused.status_code == 403
    assert rows(real_app, "SELECT hidden_by_guild FROM submissions WHERE id = :p", p=other_id)[0]["hidden_by_guild"] in (False, None)

    # the same rule where a comment's text and a hidden item's earlier versions are drawn
    comment_id, _c36 = add_comment(author, post_id, board_id)
    db = real_app[1]()
    db.rollback()
    db.execute(text("UPDATE comments SET hidden_by_guild = true WHERE id = :c"), {"c": comment_id})
    db.commit()
    db.expire_all()
    board, comment = db.get(Board, board_id), db.get(Comment, comment_id)
    users = {m.username: db.get(User, m.id) for m in (full, held, no_content, no_content_held, stranger)}
    assert not board.has_mod(users[held.username], "content"), "a held account has no guildmaster's powers"
    assert board.leads(users[held.username]) and board.leads(users[no_content_held.username])
    for reader in (full, held):
        assert visibility.guild_content_reader(board, users[reader.username])
        assert comment._text_hidden_for(users[reader.username]) is False
        assert visibility.history_text_shown(comment, users[reader.username]) is True
    for reader in (no_content, no_content_held, stranger):
        assert not visibility.guild_content_reader(board, users[reader.username])
        assert comment._text_hidden_for(users[reader.username]) is True
        assert visibility.history_text_shown(comment, users[reader.username]) is False
    assert not visibility.guild_content_reader(board, None) and not visibility.guild_content_reader(None, users[held.username])
