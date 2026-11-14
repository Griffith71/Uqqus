"""Exiles and chat mutes need a reason, an exile needs cited content, and the content is kept as it was (the Log, 2026-11-14), on the real app and Postgres.

The promises: every refusal comes before anything about the account is looked at and changes nothing; an exile that cites nothing usable (an anonymous item,
someone else's, another guild's) is refused with one answer that says nothing about who wrote what; a valid exile keeps a private copy of what it cited
as it was, which the author's later edit doesn't touch, and only the person, the guild's guildmasters and the admins can open; the exile list shows the reason;
an alias exile needs a reason too, answers the same whatever `thing` names, and its copy never names the author."""
import secrets

from sqlalchemy import text

from pg.test_log_d3c import add_comment, add_post, get, guild, one, post, rows
from pg.test_log_d3d import admin_of
from pg.test_log_d3f_aliases import anonymous_comment, ban_of, guildmaster

REASON = "Harassing other members of the guild"


def named_post(member, board_id, title=None):
    post_id, b36 = add_post(member, board_id, title=title or f"Evidence post {secrets.token_hex(3)}")
    return post_id, b36, f"t2_{b36}"


def exile(mod, board_name, **fields):
    return post(mod.browser(), mod, f"/mod/exile/{board_name}", **fields)


def test_an_exile_with_no_reason_is_refused_first_whoever_it_names_and_changes_nothing(real_app, make_member):
    mod, member = make_member(), make_member()
    board_id, board_name = guild(real_app)
    guildmaster(mod, board_id)
    _, _, thing = named_post(member, board_id)
    answers = [exile(mod, board_name, username=name, thing=thing) for name in (member.username, "nobody_" + secrets.token_hex(3), mod.username, "")]
    assert {a.status_code for a in answers} == {400}
    assert {a.get_json()["error"] for a in answers} == {"A reason is required."}, "the same answer for a real account, a missing one, oneself and none"
    assert exile(mod, board_name, username=member.username, thing=thing, reason="   ").status_code == 400, "a blank reason is no reason"
    assert ban_of(real_app, board_id, member) is None
    assert rows(real_app, "SELECT 1 FROM log_snapshots WHERE kind = 'evidence' AND author_id = :a", a=member.id) == []


def test_an_exile_that_cites_nothing_usable_is_refused_with_one_answer_and_changes_nothing(real_app, make_member):
    mod, member, other = (make_member() for _ in range(3))
    board_id, board_name = guild(real_app)
    other_board, _ = guild(real_app)
    guildmaster(mod, board_id)
    own_post, own36, own_thing = named_post(member, board_id)
    _, _, elsewhere = named_post(member, other_board)
    _, _, theirs = named_post(other, board_id)
    anon_id, anon36 = add_post(member, board_id, title="An anonymous post", is_anonymous=True)
    nothing = exile(mod, board_name, username=member.username, reason=REASON)
    candidates = [dict(), {"thing": f"t2_{anon36}"}, {"evidence": f"t2_{anon36}"}, {"evidence": theirs}, {"evidence": elsewhere}, {"evidence": "garbage"}, {"evidence": ""}]
    answers = [exile(mod, board_name, username=member.username, reason=REASON, **extra) for extra in candidates]
    assert {a.status_code for a in answers} == {400}
    # an anonymous item, someone else's and another guild's all read the same as citing nothing: the refusal tells nothing about who wrote what
    assert len({a.get_json()["error"] for a in answers}) == 1 and "Cite at least one" in answers[0].get_json()["error"]
    assert nothing.status_code == 400 and nothing.get_json() == answers[0].get_json()
    assert ban_of(real_app, board_id, member) is None
    assert rows(real_app, "SELECT 1 FROM log_snapshots WHERE kind = 'evidence' AND author_id = :a", a=member.id) == []
    # the same account, citing one usable item beside the unusable ones, is exiled
    ok = exile(mod, board_name, username=member.username, reason=REASON, thing=f"t2_{anon36}", evidence=own_thing)
    assert ok.status_code in (200, 204), ok.get_data(as_text=True)[:200]
    assert ban_of(real_app, board_id, member)["is_active"]


def test_a_valid_exile_keeps_what_it_cited_as_it_was_and_the_right_people_open_it(real_app, make_member):
    mod, member, stranger = (make_member() for _ in range(3))
    board_id, board_name = guild(real_app)
    guildmaster(mod, board_id)
    title = f"The words relied on {secrets.token_hex(3)}"
    post_id, b36, thing = named_post(member, board_id, title=title)
    comment_id, c36 = add_comment(member, post_id, board_id, "<p>a comment relied on</p>")
    done = exile(mod, board_name, username=member.username, reason=REASON, rule="good_faith", thing=thing, evidence=f"t3_{c36}")
    assert done.status_code in (200, 204), done.get_data(as_text=True)[:200]
    entry = one(real_app, "SELECT id, reason, rule_key, target_submission_id, actor_role FROM log_entries WHERE action = 'exile_user' AND target_user_id = :u", u=member.id)
    assert (entry["reason"], entry["rule_key"], entry["target_submission_id"], entry["actor_role"]) == (REASON, "good_faith", post_id, "guildmaster")
    ban = ban_of(real_app, board_id, member)
    assert one(real_app, "SELECT mod_note FROM bans WHERE id = :i", i=ban["id"])["mod_note"] == REASON, "the exile list's Reason column"
    copies = rows(real_app, "SELECT kind, author_id, target_submission_id, target_comment_id, expires_utc, created_utc, public FROM log_snapshots "
                            "WHERE entry_id = :e ORDER BY id", e=entry["id"])
    assert [(c["kind"], c["author_id"], c["target_submission_id"], c["target_comment_id"], c["public"]) for c in copies] == \
        [("evidence", member.id, post_id, None, False), ("evidence", member.id, None, comment_id, False)]
    assert all(c["expires_utc"] - c["created_utc"] == 365 * 86400 for c in copies), "a year"
    # the author changes it afterwards: the copy is what was relied on
    db = member.db_session()
    db.execute(text("UPDATE submissions_aux SET title = 'EDITED AFTER', body = 'edited', body_html = '<p>edited</p>' WHERE id = :p"), {"p": post_id})
    db.commit()
    for who, status in ((member, 200), (mod, 200), (admin_of(make_member), 200), (stranger, 404)):
        page = get(who.browser(), f"/post/{b36}/copy")
        assert page.status_code == status, (who.username, page.status_code)
        if status == 200:
            assert title in page.get_data(as_text=True) and "EDITED AFTER" not in page.get_data(as_text=True)
    assert get(real_app[0].test_client(), f"/post/{b36}/copy").status_code in (401, 404, 302), "a visitor can't"
    assert get(mod.browser(), f"/comment/{c36}/copy").status_code == 200


def test_at_most_five_items_are_kept_and_a_guildmaster_of_another_guild_cannot_open_them(real_app, make_member):
    mod, elsewhere_mod, member = (make_member() for _ in range(3))
    board_id, board_name = guild(real_app)
    other_board, _ = guild(real_app)
    guildmaster(mod, board_id)
    guildmaster(elsewhere_mod, other_board)
    made = [named_post(member, board_id) for _ in range(7)]
    answer = exile(mod, board_name, username=member.username, reason=REASON, thing=made[0][2], **{"evidence": [m[2] for m in made[1:]]})
    assert answer.status_code in (200, 204)
    assert len(rows(real_app, "SELECT 1 FROM log_snapshots WHERE kind = 'evidence' AND author_id = :a", a=member.id)) == 5
    assert get(elsewhere_mod.browser(), f"/post/{made[0][1]}/copy").status_code == 404


def test_an_exile_after_an_alias_exile_was_lifted_is_drawn_by_name_and_the_person_is_told(real_app, make_member):
    mod, author = make_member(), make_member()
    board_id, board_name = guild(real_app)
    guildmaster(mod, board_id)
    _, thing, _ = anonymous_comment(real_app, author, board_id)
    from pg.test_log_d3f_aliases import exile_alias
    assert exile_alias(mod, board_name, thing).status_code == 200
    ban = ban_of(real_app, board_id, author)
    assert ban["alias_n"] == 1
    assert post(mod.browser(), mod, f"/mod/unexile_alias/{board_name}/{ban['id']}").status_code == 204
    _, _, own = named_post(author, board_id)
    before = len(rows(real_app, "SELECT 1 FROM notifications WHERE user_id = :u", u=author.id))
    assert exile(mod, board_name, username=author.username, reason=REASON, thing=own).status_code in (200, 204)
    again = ban_of(real_app, board_id, author)
    assert again["is_active"] and again["alias_n"] is None and again["alias_post_id"] is None, "a name exile is drawn by name, not by the old alias"
    page = get(mod.browser(), f"/+{board_name}/mod/exiled").get_data(as_text=True)
    assert author.username in page and REASON in page, "the list names them and shows the reason"
    told = rows(real_app, "SELECT 1 FROM notifications WHERE user_id = :u", u=author.id)
    assert len(told) > before, "told again: the notice was only sent for a brand-new row before"


def test_an_alias_exile_needs_a_reason_answers_the_same_whatever_it_names_and_keeps_a_copy_that_never_names_the_author(real_app, make_member):
    mod, author = make_member(), make_member()
    board_id, board_name = guild(real_app)
    guildmaster(mod, board_id)
    comment_id, thing, _ = anonymous_comment(real_app, author, board_id)
    refused = [post(mod.browser(), mod, f"/mod/exile_alias/{board_name}", thing=t) for t in (thing, "t3_zzzz", "garbage", "")]
    assert {r.status_code for r in refused} == {400} and len({r.get_data(as_text=True) for r in refused}) == 1, "the same answer whatever `thing` names"
    assert ban_of(real_app, board_id, author) is None
    from pg.test_log_d3f_aliases import exile_alias
    assert exile_alias(mod, board_name, thing, reason=REASON).status_code == 200
    ban = ban_of(real_app, board_id, author)
    assert one(real_app, "SELECT mod_note FROM bans WHERE id = :i", i=ban["id"])["mod_note"] == REASON
    kept = rows(real_app, "SELECT kind, author_id, target_comment_id FROM log_snapshots WHERE kind = 'evidence' AND target_comment_id = :c", c=comment_id)
    assert kept == [{"kind": "evidence", "author_id": author.id, "target_comment_id": comment_id}]
    from ruqqus.helpers.base36 import base36encode
    page = get(mod.browser(), f"/comment/{base36encode(comment_id)}/copy")
    assert page.status_code == 200 and "Hello there" in page.get_data(as_text=True)
    assert author.username not in page.get_data(as_text=True), "the copy of an anonymous item never names its author"
    assert get(author.browser(), f"/comment/{base36encode(comment_id)}/copy").status_code == 200, "the person may open their own"


def test_a_chat_mute_needs_a_reason_by_name_and_by_alias_and_the_reason_is_in_the_log(real_app, make_member):
    mod, member, author = (make_member() for _ in range(3))
    board_id, board_name = guild(real_app)
    guildmaster(mod, board_id)
    named_post(member, board_id)
    client = mod.browser()
    refused = post(client, mod, f"/+{board_name}/mod/channels/mute", username=member.username)
    assert refused.status_code == 400 and "A reason is required." in refused.get_data(as_text=True)
    assert rows(real_app, "SELECT 1 FROM chatbans WHERE board_id = :b", b=board_id) == []
    done = post(client, mod, f"/+{board_name}/mod/channels/mute", username=member.username, reason="Spamming the channels")
    assert done.status_code == 200
    assert one(real_app, "SELECT reason FROM log_entries WHERE action = 'chatban_user' AND board_id = :b", b=board_id)["reason"] == "Spamming the channels"
    _, thing, _ = anonymous_comment(real_app, author, board_id)
    assert post(client, mod, f"/mod/chatban_alias/{board_name}", thing=thing).status_code == 400
    assert rows(real_app, "SELECT 1 FROM chatbans WHERE board_id = :b AND user_id = :u", b=board_id, u=author.id) == []
    assert post(client, mod, f"/mod/chatban_alias/{board_name}", thing=thing, reason="Spamming the channels").status_code == 200
    assert one(real_app, "SELECT reason FROM log_entries WHERE action = 'chatban_alias' AND board_id = :b", b=board_id)["reason"] == "Spamming the channels"


def test_an_admin_takedown_needs_a_reason_before_anything_is_taken_down(real_app, make_member):
    admin, member = admin_of(make_member), make_member()
    board_id, _ = guild(real_app)
    post_id, b36, _ = named_post(member, board_id, title="Words an admin would erase")
    comment_id, c36 = add_comment(member, post_id, board_id, "<p>words</p>")
    client = admin.browser()
    assert post(client, admin, f"/api/purge_post/{b36}").status_code == 400
    assert post(client, admin, f"/api/purge_comment/{c36}").status_code == 400
    assert one(real_app, "SELECT purged_utc FROM submissions WHERE id = :i", i=post_id)["purged_utc"] in (0, None), "nothing was erased"
    db = member.db_session()
    db.execute(text("UPDATE users SET display_name = 'Some Name' WHERE id = :u"), {"u": member.id})
    db.commit()
    assert post(client, admin, "/admin/display_name/clear", username=member.username).status_code == 400
    assert one(real_app, "SELECT display_name FROM users WHERE id = :u", u=member.id)["display_name"] == "Some Name"
    assert post(client, admin, f"/api/purge_comment/{c36}", reason="Taking the words down").status_code == 204
    assert one(real_app, "SELECT reason FROM log_entries WHERE action = 'purge_comment' AND target_comment_id = :c", c=comment_id)["reason"] == "Taking the words down"


def test_the_picker_lists_only_an_accounts_named_live_items_in_this_guild_and_says_the_same_for_nobody(real_app, make_member):
    mod, member, other, stranger = (make_member() for _ in range(4))
    board_id, board_name = guild(real_app)
    other_board, _ = guild(real_app)
    guildmaster(mod, board_id)
    named_id, named36, named_thing = named_post(member, board_id, title="A named post to cite")
    anon_id, _ = add_post(member, board_id, title="An anonymous one", is_anonymous=True)
    gone_id, _ = add_post(member, board_id, title="A deleted one", deleted_utc=5)
    named_comment, c36 = add_comment(member, named_id, board_id, "<p>a named comment to cite</p>")
    add_comment(member, named_id, board_id, "<p>an anonymous comment</p>", is_anonymous=True)
    named_post(member, other_board, title="In another guild")
    named_post(other, board_id, title="Someone else's")
    listed = get(mod.browser(), f"/mod/exile_items/{board_name}?username={member.username}")
    assert listed.status_code == 200 and listed.headers["Cache-Control"] == "private, no-store"
    items = listed.get_json()["items"]
    assert {i["id"] for i in items} == {named_thing, f"t3_{c36}"}, "only named, live items of theirs made in this guild"
    assert {i["kind"] for i in items} == {"post", "comment"} and any("A named post to cite" in i["text"] for i in items)
    assert items == sorted(items, key=lambda i: -i["created_utc"])
    nobody = [get(mod.browser(), f"/mod/exile_items/{board_name}?username={name}").get_json() for name in ("nobody_" + secrets.token_hex(3), "", stranger.username)]
    assert nobody == [{"items": []}] * 3, "an account that doesn't exist and one with nothing to cite read the same"
    assert get(stranger.browser(), f"/mod/exile_items/{board_name}?username={member.username}").status_code in (403, 404), "guildmasters only"
    raw = real_app[0].test_client().get(f"/mod/exile_items/{board_name}?username={member.username}", base_url="http://localhost", headers={"User-Agent": "ci"})
    assert raw.status_code in (302, 401, 403) and b'"items"' not in raw.data, "a visitor is sent to sign in (the shared `get` follows that redirect)"


def test_a_guildmasters_post_page_offers_the_dialog_and_nobody_elses_does_not(real_app, make_member):
    mod, member, host = (make_member() for _ in range(3))
    board_id, board_name = guild(real_app)
    guildmaster(mod, board_id)
    post_id, b36, _ = named_post(host, board_id)
    add_comment(member, post_id, board_id, "<p>words</p>")
    page = get(mod.browser(), f"/post/{b36}").get_data(as_text=True)
    assert 'id="decisionModal"' in page and "decision_dialog.js" in page
    assert page.count("decisionDialog(") >= 2 and "post_toast('/mod/exile" not in page, "an exile is never one tap: it asks why"
    assert "/mod/exile_items/" in page
    plain = get(member.browser(), f"/post/{b36}").get_data(as_text=True)
    assert "decisionDialog(" not in plain and "/mod/exile/" not in plain, "someone who is no guildmaster is offered nothing"


def test_the_log_names_what_an_exile_cited_by_id_and_leads_the_right_people_to_the_copy(real_app, make_member):
    mod, member, stranger = (make_member() for _ in range(3))
    board_id, board_name = guild(real_app)
    guildmaster(mod, board_id)
    post_id, b36, thing = named_post(member, board_id)
    assert exile(mod, board_name, username=member.username, reason=REASON, thing=thing).status_code in (200, 204)
    entry = one(real_app, "SELECT id FROM log_entries WHERE action = 'exile_user' AND target_user_id = :u", u=member.id)["id"]
    visitor = get(real_app[0].test_client(), f"/log/{entry}").get_data(as_text=True)
    assert "Cited:" in visitor and f"/post/{b36}/log" in visitor and f"/post/{b36}/copy" not in visitor, "a visitor is told what, never given the words"
    seen = get(stranger.browser(), f"/log/{entry}").get_data(as_text=True)
    assert f"/post/{b36}/log" in seen and f"/post/{b36}/copy" not in seen
    for who in (member, mod, admin_of(make_member)):
        page = get(who.browser(), f"/log/{entry}").get_data(as_text=True)
        assert f"/post/{b36}/copy" in page, f"{who.username}: the person, the deciding guildmasters and the admins open the copy"


def test_an_alias_exile_cites_an_anonymous_item_and_the_public_log_names_nothing_of_it(real_app, make_member):
    mod, author, stranger = (make_member() for _ in range(3))
    board_id, board_name = guild(real_app)
    guildmaster(mod, board_id)
    comment_id, thing, _ = anonymous_comment(real_app, author, board_id)
    from pg.test_log_d3f_aliases import exile_alias
    from ruqqus.helpers.base36 import base36encode
    assert exile_alias(mod, board_name, thing, reason=REASON).status_code == 200
    entry = one(real_app, "SELECT id FROM log_entries WHERE action = 'exile_alias' AND board_id = :b", b=board_id)["id"]
    c36 = base36encode(comment_id)
    for client in (real_app[0].test_client(), stranger.browser()):
        page = get(client, f"/log/{entry}").get_data(as_text=True)
        assert "Cited:" not in page and c36 not in page and author.username not in page, "an anonymous item is never named beside an exile"
    assert f"/comment/{c36}/copy" in get(mod.browser(), f"/log/{entry}").get_data(as_text=True), "the guildmasters who relied on it can open it"
    own = get(author.browser(), f"/log/{entry}/appeal").get_data(as_text=True)
    assert f"/comment/{c36}/copy" in own, "and the person, on the page they appeal from"


def test_an_exile_that_cited_an_item_is_shown_as_lifted_on_the_appeal_page_once_the_person_is_unexiled(real_app, make_member):
    mod, member = make_member(), make_member()
    board_id, board_name = guild(real_app)
    guildmaster(mod, board_id)
    _, _, thing = named_post(member, board_id)
    assert exile(mod, board_name, username=member.username, reason=REASON, thing=thing).status_code in (200, 204)
    entry = one(real_app, "SELECT id FROM log_entries WHERE action = 'exile_user' AND target_user_id = :u", u=member.id)["id"]
    before = get(member.browser(), f"/log/{entry}/appeal").get_data(as_text=True)
    assert "Send appeal" in before and "already been lifted" not in before
    assert post(mod.browser(), mod, f"/mod/unexile/{board_name}/{member.username}").status_code in (200, 204, 302)
    page = get(member.browser(), f"/log/{entry}/appeal").get_data(as_text=True)
    assert "already been lifted" in page and "Send appeal" not in page, "nothing to appeal: the exile was lifted"
