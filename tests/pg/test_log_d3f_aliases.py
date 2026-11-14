"""Actions on an alias on the real app and Postgres (the Log's slice D3f, decision 24; the rules and SQL are tested without Postgres in tests/test_alias_actions.py).

The promises: a guildmaster exiles or chat-mutes "Anonymous #N" and is never told whose it is; every outcome answers the same; the guild's lists, its mod log and the
public Log draw the number and never an account, and the account's own log and the `to=` filter can't find it; a name-based action never confirms a guess; the
exiled account is told, can appeal, and a different guildmaster's reversal lifts it; and it is rate limited per guildmaster."""
import html
import re
import secrets
import time

from sqlalchemy import text

from pg.test_log_d3c import add_comment, add_post, address, comment_on, get, guild, one, post, rows
from pg.test_log_d3d import admin_of

PASSWORD = "ci-Password-1"


def guildmaster(member, board_id, **perms):
    from ruqqus.classes import ModRelationship
    db = member.db_session()
    db.execute(text("UPDATE users SET creation_ip = :ip WHERE id = :u"), {"ip": address(), "u": member.id})
    grants = {"perm_full": True, "perm_content": True, "perm_access": True, "perm_config": True, **perms}
    db.add(ModRelationship(user_id=member.id, board_id=board_id, accepted=True, invite_rescinded=False, **grants))
    db.commit()
    return member


def anonymous_comment(real_app, member, board_id, host=None):
    """An anonymous comment by `member` in the guild, on a post by `host` (default: `member`), made through the route so its author is given a number:
    (comment id, the comment's fullname, the post's id)."""
    post_id, post36 = add_post(host or member, board_id, title=f"Thread {secrets.token_hex(3)}")
    made = comment_on(member.browser(), member, post36, anonymous="true")
    assert made.status_code == 200, made.get_data(as_text=True)[:200]
    found = one(real_app, "SELECT id FROM comments WHERE author_id = :a ORDER BY id DESC LIMIT 1", a=member.id)["id"]
    from ruqqus.helpers.base36 import base36encode
    return found, f"t3_{base36encode(found)}", post_id


def exile_alias(mod, board_name, thing, **extra):
    client = mod.browser()
    return post(client, mod, f"/mod/exile_alias/{board_name}", thing=thing, **extra)


def json_of(real_app, model, row_id):
    """What the guild's API lists answer for one ban row (`/api/v1/<guild>/mod/exiled`, `/chatbans`: they return each row's `.json`)."""
    app, db_session = real_app
    with app.test_request_context("/"):
        return db_session().query(model).filter_by(id=row_id).one().json


def ban_of(real_app, board_id, member):
    found = rows(real_app, "SELECT id, is_active, alias_post_id, alias_n FROM bans WHERE board_id = :b AND user_id = :u", b=board_id, u=member.id)
    return found[0] if found else None


def test_a_guildmaster_exiles_an_alias_and_every_list_draws_the_number_never_the_account(real_app, make_member):
    mod, author, stranger = (make_member() for _ in range(3))
    board_id, board_name = guild(real_app)
    guildmaster(mod, board_id)
    comment_id, thing, post_id = anonymous_comment(real_app, author, board_id)
    answer = exile_alias(mod, board_name, thing, reason="Harassing other members", rule="")
    assert answer.status_code == 200 and answer.get_json() == {"message": "Done."}
    ban = ban_of(real_app, board_id, author)
    assert ban["is_active"] and ban["alias_n"] == 1 and ban["alias_post_id"] == post_id, "an ordinary exile for the account, marked with the thread and the number"
    from ruqqus.classes import BanRelationship, ChatBan
    data = json_of(real_app, BanRelationship, ban["id"])
    assert data["alias"] == "Anonymous #1" and "user" not in data and "user_id" not in data and author.username not in str(data), "the list's JSON names nobody"
    # the exile list: the number and its thread, never the username
    page = get(mod.browser(), f"/+{board_name}/mod/exiled").get_data(as_text=True)
    assert "Anonymous #1" in page and author.username not in page and f"/mod/unexile_alias/{board_name}/{ban['id']}" in page
    # the guild's own mod log, and the public Log
    modlog = get(mod.browser(), f"/+{board_name}/mod/log").get_data(as_text=True)
    assert "exiled Anonymous #1" in modlog and author.username not in modlog
    app, _ = real_app
    public = get(app.test_client(), "/log?type=sanction").get_data(as_text=True)
    assert "exiled Anonymous #1" in public and author.username not in public
    entry = one(real_app, "SELECT id, target_user_id, alias_n, subject_user_id, actor_role, reason FROM log_entries WHERE action = 'exile_alias' AND board_id = :b", b=board_id)
    assert entry["target_user_id"] is None and entry["alias_n"] == 1 and entry["subject_user_id"] == author.id and entry["reason"] == "Harassing other members"
    # the account's own log and the by-account filter can't find it
    assert "Anonymous #1" not in get(app.test_client(), f"/@{author.username}/log").get_data(as_text=True)
    to_filter = get(app.test_client(), f"/log?type=sanction&to={author.username}").get_data(as_text=True)
    assert "exiled Anonymous #1" not in to_filter
    # the exiled account was told, with the way to appeal; they can no longer comment there
    notice = rows(real_app, "SELECT a.body FROM notifications n JOIN comments_aux a ON a.id = n.comment_id WHERE n.user_id = :u ORDER BY n.id DESC", u=author.id)
    assert any("exiled from +" + board_name in n["body"] for n in notice) and any("Appeal" in n["body"] or "appeal" in n["body"] for n in notice)
    staff = admin_of(make_member)
    assert author.username in get(staff.browser(), "/admin/log").get_data(as_text=True), "the staff see the account behind it"
    from ruqqus.helpers import data_export
    _, db_session = real_app
    exported = data_export.build(db_session(), author.id)["log_entries_about_you"]
    assert [(e["id"], e["alias_n"]) for e in exported] == [(entry["id"], 1)], "their own download lists it, with the number"
    assert get(stranger.browser(), f"/+{board_name}/mod/exiled").status_code in (403, 404), "only guildmasters see the list"


def test_every_outcome_answers_the_same_and_nothing_changes_where_nothing_could(real_app, make_member):
    mod, other_mod, author, admin_author = (make_member() for _ in range(4))
    board_id, board_name = guild(real_app)
    other_board_id, _ = guild(real_app)
    guildmaster(mod, board_id)
    guildmaster(other_mod, board_id)
    admin = admin_of(lambda: admin_author)                       # an admin's own anonymous comment
    _, mine, _ = anonymous_comment(real_app, mod, board_id)          # the guildmaster's own
    _, theirs, _ = anonymous_comment(real_app, other_mod, board_id)  # another guildmaster's
    _, admins, _ = anonymous_comment(real_app, admin, board_id)
    _, elsewhere, _ = anonymous_comment(real_app, author, other_board_id)   # an anonymous comment made in another guild
    named_post, named36 = add_post(author, board_id, title="Named")
    named_comment = add_comment(author, named_post, board_id, body="not anonymous")[0]
    from ruqqus.helpers.base36 import base36encode
    answers = [exile_alias(mod, board_name, t) for t in (mine, theirs, admins, elsewhere, f"t3_{base36encode(named_comment)}", "t3_zzzz", "garbage", "")]
    assert {a.status_code for a in answers} == {200} and {a.get_data(as_text=True) for a in answers} == {answers[0].get_data(as_text=True)}, "the same answer for each"
    assert all(ban_of(real_app, board_id, m) is None for m in (mod, other_mod, admin, author)), "and nothing happened to anyone"
    # one that does work answers identically
    _, ok, _ = anonymous_comment(real_app, author, board_id)
    good = exile_alias(mod, board_name, ok)
    assert good.status_code == 200 and good.get_data(as_text=True) == answers[0].get_data(as_text=True)
    assert ban_of(real_app, board_id, author)["is_active"]
    again = exile_alias(mod, board_name, ok)
    assert again.get_data(as_text=True) == good.get_data(as_text=True), "already exiled answers the same too"
    assert len(rows(real_app, "SELECT 1 FROM log_entries WHERE action = 'exile_alias' AND board_id = :b", b=board_id)) == 1, "once"


def test_a_name_based_action_never_confirms_a_guess_and_an_alias_exile_is_lifted_from_its_own_list(real_app, make_member):
    mod, author, clean = (make_member() for _ in range(3))
    board_id, board_name = guild(real_app)
    guildmaster(mod, board_id)
    add_post(clean, board_id)                                    # an ordinary member who has taken part in the guild, as the one exiled by alias has
    _, thing, _ = anonymous_comment(real_app, author, board_id)
    exile_alias(mod, board_name, thing)
    client = mod.browser()
    # lifting by name answers the same for the account exiled by alias as for one who was never exiled, and lifts nothing
    guess, nobody = (post(client, mod, f"/mod/unexile/{board_name}/{m.username}") for m in (author, clean))
    assert (guess.status_code, guess.get_data(as_text=True)) == (nobody.status_code, nobody.get_data(as_text=True)) and guess.status_code == 409
    assert ban_of(real_app, board_id, author)["is_active"], "still exiled"
    # exiling by name answers the same as for any member who has taken part (never "already exiled") and changes nothing about the alias exile.
    # What a guildmaster who suspects an account can still see is that no new named row appears for it: the residual risk, said in helpers/alias_actions.py
    again = post(client, mod, f"/mod/exile/{board_name}", username=author.username)
    fresh = post(client, mod, f"/mod/exile/{board_name}", username=clean.username)
    assert (again.status_code, again.get_data(as_text=True)) == (fresh.status_code, fresh.get_data(as_text=True)) == (204, "")
    assert len(rows(real_app, "SELECT 1 FROM bans WHERE board_id = :b AND user_id = :u", b=board_id, u=author.id)) == 1
    assert ban_of(real_app, board_id, clean)["is_active"] and ban_of(real_app, board_id, clean)["alias_n"] is None
    assert ban_of(real_app, board_id, author)["alias_n"] == 1, "it stays an alias exile"
    assert len(rows(real_app, "SELECT 1 FROM log_entries WHERE action = 'exile_user' AND board_id = :b AND target_user_id = :u", b=board_id, u=author.id)) == 0
    # the list's own route lifts it, and the Log has it as an alias again
    ban = ban_of(real_app, board_id, author)
    assert post(client, mod, f"/mod/unexile_alias/{board_name}/{ban['id']}").status_code == 204
    assert ban_of(real_app, board_id, author)["is_active"] is False
    assert one(real_app, "SELECT target_user_id, alias_n FROM log_entries WHERE action = 'unexile_alias' AND board_id = :b", b=board_id) == {"target_user_id": None, "alias_n": 1}
    assert post(client, mod, f"/mod/unexile_alias/{board_name}/999999").status_code == 404
    assert post(client, mod, f"/mod/unexile_alias/{board_name}/{ban['id']}").status_code == 404, "an exile that is not active has nothing to lift"
    named = ban_of(real_app, board_id, clean)
    assert named["alias_n"] is None and post(client, mod, f"/mod/unexile_alias/{board_name}/{named['id']}").status_code == 404, "an exile by name has its own route"


def test_the_exiled_account_can_appeal_and_a_different_guildmaster_can_reverse_it(real_app, make_member):
    mod, second_mod, author = (make_member() for _ in range(3))
    board_id, board_name = guild(real_app)
    guildmaster(mod, board_id)
    guildmaster(second_mod, board_id)
    _, thing, _ = anonymous_comment(real_app, author, board_id)
    exile_alias(mod, board_name, thing, reason="A reason")
    entry = one(real_app, "SELECT id FROM log_entries WHERE action = 'exile_alias' AND board_id = :b", b=board_id)["id"]
    page = get(author.browser(), f"/log/{entry}/appeal")
    assert page.status_code == 200, "the account behind it can open the appeal"
    assert get(mod.browser(), f"/log/{entry}/appeal").status_code in (403, 404), "nobody else can"
    sent = post(author.browser(), author, f"/log/{entry}/appeal", reason="That was not me being rude")
    assert sent.status_code == 303 and sent.headers["Location"].endswith("?sent=1")
    queue = get(second_mod.browser(), f"/+{board_name}/mod/appeals").get_data(as_text=True)
    assert "That was not me being rude" in queue and author.username not in queue, "the deciders are never told whose it is"
    appeal = one(real_app, "SELECT id FROM log_appeals WHERE entry_id = :e", e=entry)["id"]
    decided = post(second_mod.browser(), second_mod, f"/+{board_name}/mod/appeals/{appeal}", outcome="reversed", note="Sorry about that")
    assert decided.status_code == 303 and "Decided" in decided.headers["Location"], decided.headers.get("Location")
    assert ban_of(real_app, board_id, author)["is_active"] is False
    undone = one(real_app, "SELECT target_user_id, alias_n, subject_user_id, linked_entry_id FROM log_entries WHERE action = 'unexile_alias' AND board_id = :b", b=board_id)
    assert undone == {"target_user_id": None, "alias_n": 1, "subject_user_id": author.id, "linked_entry_id": entry}


def test_a_chat_mute_by_alias_is_listed_by_its_number_and_lifted_only_from_that_list(real_app, make_member):
    from ruqqus.helpers.base36 import base36encode
    mod, author, clean = (make_member() for _ in range(3))
    board_id, board_name = guild(real_app)
    guildmaster(mod, board_id)
    _, thing, post_id = anonymous_comment(real_app, author, board_id)
    client = mod.browser()
    assert post(client, mod, f"/mod/chatban_alias/{board_name}", thing=thing).status_code == 200
    mute = rows(real_app, "SELECT id, alias_n, alias_post_id FROM chatbans WHERE board_id = :b AND user_id = :u", b=board_id, u=author.id)
    assert mute == [{"id": mute[0]["id"], "alias_n": 1, "alias_post_id": post_id}]
    from ruqqus.classes import ChatBan
    data = json_of(real_app, ChatBan, mute[0]["id"])
    assert data["alias"] == "Anonymous #1" and "user" not in data and "user_id" not in data and author.username not in str(data), "the list's JSON names nobody"
    for path in (f"/+{board_name}/mod/chatbans", f"/+{board_name}/mod/channels"):
        page = get(client, path).get_data(as_text=True)
        assert "Anonymous #1" in page and author.username not in page, path
    log = get(client, f"/+{board_name}/mod/log").get_data(as_text=True)
    assert "chatbanned Anonymous #1" in log and author.username not in log
    assert one(real_app, "SELECT target_user_id, alias_n FROM log_entries WHERE action = 'chatban_alias'") == {"target_user_id": None, "alias_n": 1}
    # by name, the account muted by alias answers exactly as one never muted does: nothing is confirmed, nothing is lifted
    guess, nobody = (post(client, mod, f"/+{board_name}/mod/channels/unmute", username=m.username) for m in (author, clean))
    assert guess.status_code == nobody.status_code == 404 and "They aren't muted here." in html.unescape(guess.get_data(as_text=True))
    unmute_alias_guess, nobody2 = (post(client, mod, f"/mod/unchatban/{base36encode(board_id)}", username=m.username) for m in (author, clean))
    assert unmute_alias_guess.status_code == nobody2.status_code == 409
    by_name = post(client, mod, f"/+{board_name}/mod/channels/mute", username=author.username)
    assert by_name.status_code == 200 and "is already muted" not in html.unescape(by_name.get_data(as_text=True)), "never 'already muted'"
    assert len(rows(real_app, "SELECT 1 FROM chatbans WHERE board_id = :b AND user_id = :u", b=board_id, u=author.id)) == 1, "one mute, still by alias"
    assert rows(real_app, "SELECT alias_n FROM chatbans WHERE board_id = :b AND user_id = :u", b=board_id, u=author.id) == [{"alias_n": 1}]
    assert post(client, mod, f"/mod/unchatban_alias/{board_name}/{mute[0]['id']}").status_code == 204
    assert rows(real_app, "SELECT 1 FROM chatbans WHERE board_id = :b AND user_id = :u", b=board_id, u=author.id) == []
    assert post(client, mod, f"/mod/unchatban_alias/{board_name}/{mute[0]['id']}").status_code == 404


def test_alias_actions_are_limited_per_guildmaster_and_the_menu_names_only_the_number(real_app, make_member):
    mod, author, member, host = (make_member() for _ in range(4))
    board_id, board_name = guild(real_app)
    guildmaster(mod, board_id)
    comment_id, thing, post_id = anonymous_comment(real_app, author, board_id, host=host)
    client = mod.browser()
    statuses = [post(client, mod, f"/mod/exile_alias/{board_name}", thing="t3_zzzz").status_code for _ in range(21)]
    assert statuses == [200] * 20 + [429], "twenty an hour a guildmaster, whatever each did"
    from ruqqus.helpers.base36 import base36encode
    thread = get(mod.browser(), f"/post/{base36encode(post_id)}").get_data(as_text=True)
    assert "Exile Anonymous #1" in thread and author.username not in thread, "the menu offers the number"
    assert "Exile Anonymous" not in get(member.browser(), f"/post/{base36encode(post_id)}").get_data(as_text=True), "and only to a guildmaster"
