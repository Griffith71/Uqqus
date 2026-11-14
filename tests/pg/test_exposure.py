"""Unfair exposure (the owner, 2026-11-14): a guildmaster who tests a guess about who is behind an alias leaves a staff-only note, once a day, and the answer they
get is the same as ever, on the real app and Postgres.

The promises: each way of testing (exile or mute the suspect by name when they are exiled or muted by alias, and the other way round) is noted; a plain action on
anyone else is not; the note is staff-only (not in the public Log, not told to anyone, not to the account) and the staff page lists it; and the rules page says it
is a bannable offense."""
import secrets

from pg.test_log_d3c import get, guild, one, post, rows
from pg.test_log_d3d import admin_of
from pg.test_log_d3f_aliases import anonymous_comment, exile_alias, guildmaster
from pg.test_log_lifted import named_post
from pg.test_log_trail_routes import notices_of

NOTES = "SELECT id, actor_id, actor_role, audience, board_id, target_user_id, staff_note FROM log_entries WHERE action = 'possible_probing' AND board_id = :b ORDER BY id"
REASON = "Spamming the channels"


def by_name_exile(mod, board_name, member, thing=""):
    return post(mod.browser(), mod, f"/mod/exile/{board_name}", username=member.username, reason="A guess", thing=thing)


def test_exiling_the_suspect_by_name_when_they_are_exiled_by_alias_is_noted_for_the_staff_and_answers_as_ever(real_app, make_member):
    mod, second, author, bystander = (make_member() for _ in range(4))
    board_id, board_name = guild(real_app)
    guildmaster(mod, board_id)
    guildmaster(second, board_id)
    _, thing, _ = anonymous_comment(real_app, author, board_id)
    _, _, own = named_post(author, board_id)                      # the account also wrote under its name here, so a guildmaster has something to cite
    _, _, bystander_own = named_post(bystander, board_id)
    assert exile_alias(second, board_name, thing).status_code == 200
    told = len(notices_of(real_app, author.id))
    # with nothing cited the exile is one answer for the alias account and for any other participant: asking whether a guess is right costs a citation
    nothing = [by_name_exile(mod, board_name, who) for who in (author, bystander)]
    assert {a.status_code for a in nothing} == {400}
    assert len({a.get_data(as_text=True).replace(who.username, "NAME") for a, who in zip(nothing, (author, bystander))}) == 1, "the quiet answer is not cheaper than a refusal"
    assert rows(real_app, NOTES, b=board_id) == [], "and nothing is noted for an exile that was refused"
    answer = by_name_exile(mod, board_name, author, own)
    assert answer.status_code == 204, "citing something real, the answer is the quiet one it always was: nothing is confirmed"
    assert len(notices_of(real_app, author.id)) == told, "the account is told nothing more"
    (entry,) = rows(real_app, NOTES, b=board_id)
    assert (entry["actor_id"], entry["actor_role"], entry["audience"], entry["target_user_id"]) == (mod.id, "guildmaster", 2, author.id)
    assert "exiled by name an account that is exiled by alias" in entry["staff_note"] and f"made by @{second.username}" in entry["staff_note"]
    assert by_name_exile(mod, board_name, author, own).status_code == 204
    assert len(rows(real_app, NOTES, b=board_id)) == 1, "the same test the same day is one line"
    # an ordinary exile of someone else is nobody's business
    assert post(mod.browser(), mod, f"/mod/exile/{board_name}", username=bystander.username, reason="Rude", thing=bystander_own).status_code in (200, 204)
    assert len(rows(real_app, NOTES, b=board_id)) == 1


def test_the_note_is_for_the_staff_alone(real_app, make_member):
    mod, second, author, stranger = (make_member() for _ in range(4))
    board_id, board_name = guild(real_app)
    guildmaster(mod, board_id)
    guildmaster(second, board_id)
    _, thing, _ = anonymous_comment(real_app, author, board_id)
    _, _, own = named_post(author, board_id)
    exile_alias(second, board_name, thing)
    by_name_exile(mod, board_name, author, own)
    visitor = real_app[0].test_client()
    for client in (visitor, stranger.browser(), mod.browser(), author.browser()):
        page = get(client, "/log?type=sanction").get_data(as_text=True)
        assert "possible probing" not in page and "possible_probing" not in page and "tested against a suspect" not in page
    assert get(mod.browser(), f"/+{board_name}/mod/log").status_code in (200, 403)
    assert "tested against a suspect" not in get(mod.browser(), f"/+{board_name}/mod/log").get_data(as_text=True), "not even the guild's own log"
    admin = admin_of(make_member)
    staff = get(admin.browser(), "/admin/log?action=possible_probing").get_data(as_text=True)
    assert "possible_probing" in staff and f"@{mod.username}" in staff and "tested against a suspect" in staff
    home = get(admin.browser(), "/admin").get_data(as_text=True)
    assert "Possible probing of anonymous accounts" in home and "unfair exposure" in home
    assert get(stranger.browser(), "/admin/log?action=possible_probing").status_code in (302, 403, 404)


def test_muting_by_name_a_suspect_muted_by_alias_and_the_reverse_orders_are_noted(real_app, make_member):
    mod, author, other = (make_member() for _ in range(3))
    board_id, board_name = guild(real_app)
    guildmaster(mod, board_id)
    _, thing, _ = anonymous_comment(real_app, author, board_id)
    assert post(mod.browser(), mod, f"/mod/chatban_alias/{board_name}", thing=thing, reason=REASON).status_code == 200
    by_name = post(mod.browser(), mod, f"/+{board_name}/mod/channels/mute", username=author.username, reason=REASON)
    assert by_name.status_code == 200 and "is already muted" not in by_name.get_data(as_text=True)
    (entry,) = rows(real_app, NOTES, b=board_id)
    assert entry["target_user_id"] == author.id and "muted by name an account that is muted by alias" in entry["staff_note"]
    # the other way round: the guildmaster exiled the suspect by name, then acts on the alias
    _, other_thing, _ = anonymous_comment(real_app, other, board_id)
    _, _, own = named_post(other, board_id)
    assert post(mod.browser(), mod, f"/mod/exile/{board_name}", username=other.username, reason="Rude", thing=own).status_code in (200, 204)
    assert exile_alias(mod, board_name, other_thing).status_code == 200
    notes = rows(real_app, NOTES, b=board_id)
    assert len(notes) == 2 and "acted by alias on an account they had exiled by name" in notes[1]["staff_note"] and notes[1]["target_user_id"] == other.id
    # a different guildmaster's earlier exile by name is a coincidence, not this guildmaster's test
    third, fourth = make_member(), make_member()
    guildmaster(third, board_id)
    _, third_thing, _ = anonymous_comment(real_app, fourth, board_id)
    _, _, fourth_own = named_post(fourth, board_id)
    assert post(mod.browser(), mod, f"/mod/exile/{board_name}", username=fourth.username, reason="Rude", thing=fourth_own).status_code in (200, 204)
    assert exile_alias(third, board_name, third_thing).status_code == 200
    assert len(rows(real_app, NOTES, b=board_id)) == 2


def test_the_rules_say_it_is_a_bannable_offense_and_the_admins_ban_with_that_rule(real_app, make_member):
    page = get(real_app[0].test_client(), "/help/rules").get_data(as_text=True)
    assert "unfair exposure" in page and "can lead to a ban" in page and "Do not try to find out who is behind an anonymous post" in page
    card = get(admin_of(make_member).browser(), "/submit").get_data(as_text=True)
    assert "unfair exposure" in card
    admin, guildmaster_account = admin_of(make_member), make_member()
    banned = post(admin.browser(), admin, f"/api/ban_user/{guildmaster_account.id}", days="3", rule="evasion",
                  reason="Unfair exposure: tested a guess about an anonymous account")
    assert banned.status_code in (200, 302)
    entry = one(real_app, "SELECT rule_key, reason FROM log_entries WHERE action = 'suspend_user' AND target_user_id = :u", u=guildmaster_account.id)
    assert entry["rule_key"] == "evasion" and entry["reason"].startswith("Unfair exposure")
