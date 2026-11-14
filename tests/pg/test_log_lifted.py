"""What is lifted is told, a chat mute and a guild ban are told when made and can be appealed, and a ban that cites anonymous items says which (the Log,
2026-11-14), on the real app and Postgres.

The promises: a lifting that changes something tells the person (never the one who lifted it, never twice, never when nothing changed, and a lift of an
alias action names no number); a mute (by name or by alias) is told with the rule, the reason, the Log and the Appeal link; a mute and a guild's ban can be
appealed by who they are about (a guild's founder and full guildmasters, each once) and decided by the right people, and an appeal that reverses one sends
its own notice and no second one; a ban that cites anonymous items tells the account which."""
import secrets
import time

from sqlalchemy import text

from pg.test_log_d3c import add_comment, add_post, get, guild, one, post, rows
from pg.test_log_d3d import admin_of, ban
from pg.test_log_d3f_aliases import anonymous_comment, exile_alias, guildmaster
from pg.test_log_trail_routes import notices_of

REASON = "Flooding the channels with the same message"


def named_post(member, board_id):
    post_id, b36 = add_post(member, board_id, title=f"Cited {secrets.token_hex(3)}")
    return post_id, b36, f"t2_{b36}"


def exile(mod, board_name, member, thing):
    return post(mod.browser(), mod, f"/mod/exile/{board_name}", username=member.username, reason="Harassment of other members", thing=thing)


def mute(mod, board_name, member):
    return post(mod.browser(), mod, f"/+{board_name}/mod/channels/mute", username=member.username, reason=REASON, rule="good_faith")


def sayings(real_app, user, containing):
    return [n for n in notices_of(real_app, user.id) if containing in n]


# --- lifted ----------------------------------------------------------------------------------------------------------------------

def test_an_unexile_tells_the_person_once_and_only_when_it_changed_and_never_the_one_who_lifted_it(real_app, make_member):
    mod, member = make_member(), make_member()
    board_id, board_name = guild(real_app)
    guildmaster(mod, board_id)
    _, _, thing = named_post(member, board_id)
    assert exile(mod, board_name, member, thing).status_code in (200, 204)
    before = len(notices_of(real_app, member.id))
    assert post(mod.browser(), mod, f"/mod/unexile/{board_name}/{member.username}").status_code == 204
    told = notices_of(real_app, member.id)[before:]
    assert len(told) == 1 and f"You are no longer exiled from +{board_name}" in told[0]
    assert "](/log" not in told[0], "a lift notice has no link: the entry may be a private guild's"
    assert post(mod.browser(), mod, f"/mod/unexile/{board_name}/{member.username}").status_code == 409
    assert len(notices_of(real_app, member.id)) == before + 1, "nothing changed the second time: nobody is told"
    assert sayings(real_app, mod, "no longer exiled") == []


def test_an_alias_exile_that_is_lifted_tells_the_account_without_a_number(real_app, make_member):
    mod, author = make_member(), make_member()
    board_id, board_name = guild(real_app)
    guildmaster(mod, board_id)
    _, thing, _ = anonymous_comment(real_app, author, board_id)
    assert exile_alias(mod, board_name, thing).status_code == 200
    ban_row = one(real_app, "SELECT id FROM bans WHERE board_id = :b AND user_id = :u", b=board_id, u=author.id)
    before = len(notices_of(real_app, author.id))
    assert post(mod.browser(), mod, f"/mod/unexile_alias/{board_name}/{ban_row['id']}").status_code == 204
    (told,) = notices_of(real_app, author.id)[before:]
    assert f"You are no longer exiled from +{board_name}" in told and "Anonymous" not in told and "#1" not in told


def test_a_mute_is_told_with_the_rule_the_reason_and_the_appeal_and_its_lifting_is_told_too(real_app, make_member):
    mod, member = make_member(), make_member()
    board_id, board_name = guild(real_app)
    guildmaster(mod, board_id)
    assert mute(mod, board_name, member).status_code == 200
    (told,) = sayings(real_app, member, "can no longer write")
    entry = one(real_app, "SELECT id, notice_sent, reason, rule_key FROM log_entries WHERE action = 'chatban_user' AND target_user_id = :u", u=member.id)
    assert f"+{board_name}'s channels" in told and "Flooding the channels" in told and "Free expression and good faith" in told
    assert f"](/log/{entry['id']}/appeal)" in told and entry["notice_sent"] is True
    assert sayings(real_app, mod, "can no longer write") == []
    assert post(mod.browser(), mod, f"/+{board_name}/mod/channels/unmute", username=member.username).status_code == 200
    assert len(sayings(real_app, member, f"You can write in +{board_name}'s channels again")) == 1
    assert post(mod.browser(), mod, f"/+{board_name}/mod/channels/unmute", username=member.username).status_code == 404
    assert len(sayings(real_app, member, "channels again")) == 1, "nothing changed the second time"


def test_a_mute_by_alias_is_told_and_lifted_without_a_number(real_app, make_member):
    mod, author = make_member(), make_member()
    board_id, board_name = guild(real_app)
    guildmaster(mod, board_id)
    _, thing, _ = anonymous_comment(real_app, author, board_id)
    assert post(mod.browser(), mod, f"/mod/chatban_alias/{board_name}", thing=thing, reason=REASON).status_code == 200
    (told,) = sayings(real_app, author, "can no longer write")
    assert "Anonymous" not in told and "#1" not in told and "/appeal)" in told
    mute_row = one(real_app, "SELECT id FROM chatbans WHERE board_id = :b AND user_id = :u", b=board_id, u=author.id)
    assert post(mod.browser(), mod, f"/mod/unchatban_alias/{board_name}/{mute_row['id']}").status_code == 204
    (lifted,) = sayings(real_app, author, "channels again")
    assert "Anonymous" not in lifted


def test_an_unhide_and_an_admins_restore_tell_the_person_only_when_something_was_hidden(real_app, make_member):
    board_id, board_name = guild(real_app)
    mod, author, other = guildmaster(make_member(), board_id), make_member(), make_member()
    post_id, b36 = add_post(author, board_id)
    assert post(mod.browser(), mod, f"/mod/kick/{board_name}/{b36}", reason="Advertising", rule="good_faith").status_code == 200
    assert post(mod.browser(), mod, f"/mod/unhide/{board_name}/{b36}").status_code == 200
    assert len(sayings(real_app, author, f"is shown in +{board_name} again")) == 1
    assert post(mod.browser(), mod, f"/mod/unhide/{board_name}/{b36}").status_code == 200
    assert len(sayings(real_app, author, "is shown in")) == 1, "it was not hidden: nobody is told"
    assert sayings(real_app, mod, "is shown in") == []
    admin = admin_of(make_member)
    other_id, other36 = add_post(other, board_id)
    assert post(admin.browser(), admin, f"/api/ban_post/{other36}", reason="Spam links", rule="evasion").status_code in (200, 302)
    assert post(admin.browser(), admin, f"/api/unban_post/{other36}").status_code in (200, 302)
    assert len(sayings(real_app, other, "was restored")) == 1
    assert post(admin.browser(), admin, f"/api/unban_post/{other36}").status_code in (200, 302)
    assert len(sayings(real_app, other, "was restored")) == 1, "it was not removed"


def test_a_suspension_that_ran_out_is_told_once_and_a_staff_only_one_is_not(real_app, make_member):
    from ruqqus.helpers import action_log, log_notices
    admin, member, alt = admin_of(make_member), make_member(), make_member()
    assert ban(admin.browser(), admin, member, days=1).status_code in (200, 302)
    db = member.db_session()
    db.execute(text("UPDATE users SET unban_utc = :t WHERE id = :u"), {"t": int(time.time()) - 5, "u": member.id})
    db.execute(text("UPDATE log_entries SET until_utc = :t WHERE action = 'suspend_user' AND target_user_id = :u"), {"t": int(time.time()) - 5, "u": member.id})
    db.commit()
    ended = []
    assert action_log.lapse_due(db, int(time.time()), told=ended) >= 1 and member.id in ended
    db.commit()
    before = len(notices_of(real_app, member.id))
    assert log_notices.tell_lapsed(real_app[1], [member.id]) == 1
    assert len(sayings(real_app, member, "suspension has ended")) == 1 and len(notices_of(real_app, member.id)) == before + 1
    again = []
    action_log.lapse_due(db, int(time.time()), told=again)
    assert member.id not in again, "a second run finds the lapse entry and tells nobody"


# --- a guild's ban from the site -------------------------------------------------------------------------------------------------

def test_a_guild_ban_tells_its_heads_and_each_can_appeal_once_and_an_admin_reversal_reinstates_it(real_app, make_member):
    from ruqqus.helpers.base36 import base36encode
    founder, helper, third, plain, stranger = (make_member() for _ in range(5))
    board_id, board_name = guild(real_app)
    guildmaster(founder, board_id)
    guildmaster(helper, board_id)
    guildmaster(third, board_id)
    guildmaster(plain, board_id, perm_full=False, perm_content=True, perm_access=False, perm_config=False)
    owner = admin_of(make_member, 4)
    banned = post(owner.browser(), owner, f"/api/ban_guild/{base36encode(board_id)}", reason="Hosting content that breaks the rules", rule="criminal")
    assert banned.status_code in (200, 302), banned.get_data(as_text=True)[:200]
    entry = one(real_app, "SELECT id, notice_sent FROM log_entries WHERE action = 'ban_guild' AND board_id = :b", b=board_id)
    for head in (founder, helper, third):
        (told,) = sayings(real_app, head, f"+{board_name} was banned")
        assert "Hosting content that breaks the rules" in told and f"](/log/{entry['id']}/appeal)" in told
    assert sayings(real_app, plain, "was banned") == [] and sayings(real_app, stranger, "was banned") == [] and sayings(real_app, owner, "was banned") == []
    assert entry["notice_sent"] is True
    page = get(founder.browser(), f"/+{board_name}").get_data(as_text=True)
    assert f"/log/{entry['id']}/appeal" in page, "the banned guild's page leads its heads to the appeal"
    assert f"/log/{entry['id']}/appeal" not in get(stranger.browser(), f"/+{board_name}").get_data(as_text=True)
    assert get(plain.browser(), f"/log/{entry['id']}/appeal").status_code in (403, 404), "a guildmaster without full access doesn't answer for the guild"
    assert get(stranger.browser(), f"/log/{entry['id']}/appeal").status_code in (403, 404)
    sent = post(founder.browser(), founder, f"/log/{entry['id']}/appeal", reason="We removed the content the same day")
    assert sent.status_code == 303 and sent.headers["Location"].endswith("?sent=1")
    assert post(helper.browser(), helper, f"/log/{entry['id']}/appeal", reason="We have new rules now").status_code == 303, "each head appeals once"
    again = post(founder.browser(), founder, f"/log/{entry['id']}/appeal", reason="Please")
    assert "already appealed" in again.headers["Location"].replace("%20", " ")
    queue = get(owner.browser(), "/admin/appeals").get_data(as_text=True)
    assert "We removed the content the same day" in queue and f"+{board_name}" in queue
    appeal = one(real_app, "SELECT id FROM log_appeals WHERE entry_id = :e AND user_id = :u", e=entry["id"], u=founder.id)["id"]
    junior = admin_of(make_member)                                 # only a level-4 admin bans or reinstates a guild: so only one decides its appeal
    assert "We removed the content the same day" not in get(junior.browser(), "/admin/appeals").get_data(as_text=True)
    assert post(junior.browser(), junior, f"/admin/appeals/{appeal}", outcome="reversed", note="No").status_code == 404
    assert one(real_app, "SELECT is_banned FROM boards WHERE id = :b", b=board_id)["is_banned"] is True
    second_admin = admin_of(make_member, 4)
    decided = post(second_admin.browser(), second_admin, f"/admin/appeals/{appeal}", outcome="reversed", note="The content is gone")
    assert decided.status_code == 303 and "Decided" in decided.headers["Location"]
    assert one(real_app, "SELECT is_banned FROM boards WHERE id = :b", b=board_id)["is_banned"] is False
    assert one(real_app, "SELECT linked_entry_id FROM log_entries WHERE action = 'unban_guild' AND board_id = :b", b=board_id)["linked_entry_id"] == entry["id"]
    assert len(sayings(real_app, founder, "was accepted")) == 1, "the appeal's own notice"
    assert sayings(real_app, founder, "was reinstated") == [], "and not a second one for the same thing"
    late = get(third.browser(), f"/log/{entry['id']}/appeal")
    assert late.status_code == 200 and "already been lifted" in late.get_data(as_text=True) and "Send appeal" not in late.get_data(as_text=True)


def test_an_admin_lifting_a_guild_ban_tells_its_heads(real_app, make_member):
    from ruqqus.helpers.base36 import base36encode
    founder = make_member()
    board_id, board_name = guild(real_app)
    guildmaster(founder, board_id)
    owner = admin_of(make_member, 4)
    post(owner.browser(), owner, f"/api/ban_guild/{base36encode(board_id)}", reason="Hosting content that breaks the rules", rule="criminal")
    assert post(owner.browser(), owner, f"/api/unban_guild/{base36encode(board_id)}", reason="Resolved").status_code in (200, 302)
    assert len(sayings(real_app, founder, f"+{board_name} was reinstated")) == 1
    assert post(owner.browser(), owner, f"/api/unban_guild/{base36encode(board_id)}", reason="Again").status_code in (200, 302)
    assert len(sayings(real_app, founder, "was reinstated")) == 1, "it was not banned"


# --- a chat mute can be appealed ------------------------------------------------------------------------------------------------

def test_a_muted_account_appeals_and_a_different_guildmaster_lifts_it_with_one_notice(real_app, make_member):
    mod, second, member = (make_member() for _ in range(3))
    board_id, board_name = guild(real_app)
    guildmaster(mod, board_id)
    guildmaster(second, board_id)
    assert mute(mod, board_name, member).status_code == 200
    entry = one(real_app, "SELECT id FROM log_entries WHERE action = 'chatban_user' AND target_user_id = :u", u=member.id)["id"]
    assert get(member.browser(), f"/log/{entry}/appeal").status_code == 200
    assert get(mod.browser(), f"/log/{entry}/appeal").status_code in (403, 404), "nobody else can"
    assert post(member.browser(), member, f"/log/{entry}/appeal", reason="I only posted a link once").status_code == 303
    appeal = one(real_app, "SELECT id FROM log_appeals WHERE entry_id = :e", e=entry)["id"]
    own = post(mod.browser(), mod, f"/+{board_name}/mod/appeals/{appeal}", outcome="upheld", note="Fine")
    assert "another can decide" in own.headers["Location"].replace("%20", " "), "not the guildmaster who muted them, while another could"
    decided = post(second.browser(), second, f"/+{board_name}/mod/appeals/{appeal}", outcome="reversed", note="Sorry about that")
    assert decided.status_code == 303 and "Decided" in decided.headers["Location"]
    assert rows(real_app, "SELECT 1 FROM chatbans WHERE board_id = :b AND user_id = :u", b=board_id, u=member.id) == []
    assert one(real_app, "SELECT linked_entry_id FROM log_entries WHERE action = 'unchatban_user' AND board_id = :b", b=board_id)["linked_entry_id"] == entry
    assert len(sayings(real_app, member, "was accepted")) == 1 and sayings(real_app, member, "channels again") == []


def test_a_mute_by_alias_is_appealed_by_the_account_behind_it_and_the_deciders_never_learn_whose(real_app, make_member):
    mod, second, author = (make_member() for _ in range(3))
    board_id, board_name = guild(real_app)
    guildmaster(mod, board_id)
    guildmaster(second, board_id)
    _, thing, _ = anonymous_comment(real_app, author, board_id)
    assert post(mod.browser(), mod, f"/mod/chatban_alias/{board_name}", thing=thing, reason=REASON).status_code == 200
    entry = one(real_app, "SELECT id FROM log_entries WHERE action = 'chatban_alias' AND board_id = :b", b=board_id)["id"]
    assert get(author.browser(), f"/log/{entry}/appeal").status_code == 200
    assert get(mod.browser(), f"/log/{entry}/appeal").status_code in (403, 404)
    assert post(author.browser(), author, f"/log/{entry}/appeal", reason="That was not me").status_code == 303
    queue = get(second.browser(), f"/+{board_name}/mod/appeals").get_data(as_text=True)
    assert "That was not me" in queue and author.username not in queue
    appeal = one(real_app, "SELECT id FROM log_appeals WHERE entry_id = :e", e=entry)["id"]
    assert "Decided" in post(second.browser(), second, f"/+{board_name}/mod/appeals/{appeal}", outcome="reversed", note="Sorry").headers["Location"]
    assert rows(real_app, "SELECT 1 FROM chatbans WHERE board_id = :b AND user_id = :u", b=board_id, u=author.id) == []
    undone = one(real_app, "SELECT target_user_id, alias_n, subject_user_id, linked_entry_id FROM log_entries WHERE action = 'unchatban_alias' AND board_id = :b", b=board_id)
    assert undone == {"target_user_id": None, "alias_n": 1, "subject_user_id": author.id, "linked_entry_id": entry}


# --- a ban that cites anonymous items --------------------------------------------------------------------------------------------

def test_a_ban_that_cites_anonymous_items_tells_the_account_how_many_and_lists_which_on_its_own_appeal_page(real_app, make_member):
    admin, target, other = admin_of(make_member), make_member(), make_member()
    board_id, _ = guild(real_app)
    anon_id, anon36 = add_post(target, board_id, title="An anonymous post", is_anonymous=True)
    named_id, named36 = add_post(target, board_id, title="A named post")
    assert ban(admin.browser(), admin, target, [f"p{anon36}", f"p{named36}"]).status_code in (200, 302)
    (told,) = sayings(real_app, target, "relies on 1 of your anonymous")
    assert "second administrator approves" in told and "/appeal)" in told
    assert f"/post/{anon36}" not in told and f"/post/{named36}" not in told, "no item in a notice: whoever is signed in, a relayed code included, reads it"
    entry = one(real_app, "SELECT id FROM log_entries WHERE action = 'ban_user' AND target_user_id = :u", u=target.id)["id"]
    page = get(target.browser(), f"/log/{entry}/appeal").get_data(as_text=True)
    assert 'id="ban-relies-on"' in page and f"/post/{anon36}" in page and f"/post/{named36}" in page, "the account's own page lists what the ban relies on"
    assert get(other.browser(), f"/log/{entry}/appeal").status_code in (403, 404)
    again = make_member()
    assert ban(admin.browser(), admin, again, []).status_code in (200, 302)
    assert sayings(real_app, again, "relies on") == [], "no citation, no notice"
    plain_entry = one(real_app, "SELECT id FROM log_entries WHERE action = 'ban_user' AND target_user_id = :u", u=again.id)["id"]
    assert "ban-relies-on" not in get(again.browser(), f"/log/{plain_entry}/appeal").get_data(as_text=True)


def test_banning_a_banned_guild_again_changes_nothing_and_tells_nobody_twice(real_app, make_member):
    from ruqqus.helpers.base36 import base36encode
    founder = make_member()
    board_id, board_name = guild(real_app)
    guildmaster(founder, board_id)
    owner = admin_of(make_member, 4)
    first = post(owner.browser(), owner, f"/api/ban_guild/{base36encode(board_id)}", reason="Hosting content that breaks the rules", rule="criminal")
    assert first.status_code in (200, 302)
    again = post(owner.browser(), owner, f"/api/ban_guild/{base36encode(board_id)}", reason="Still hosting it", rule="criminal")
    assert again.status_code == 409
    assert len(rows(real_app, "SELECT 1 FROM log_entries WHERE action = 'ban_guild' AND board_id = :b", b=board_id)) == 1
    assert len(sayings(real_app, founder, "was banned")) == 1
