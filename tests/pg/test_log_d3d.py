"""The ban reveal on the real app and Postgres (the Log's slice D3d; the rules and the SQL are tested without Postgres in tests/test_log_evidence.py).

The promises: an admin banning an account cites its items from a picker that reaches anonymous items and a deleted account's; a list that can't be cited bans
nobody; the Log names the author for a cited item (its line in the Deleted tab, and the ban's own line) only once a DIFFERENT admin approves, never for a
declined one, and keeps the name when the ban is reversed; the staff page lists what is waiting; a deleted account is named by its username."""
import re
import secrets
import time

from sqlalchemy import text

from pg.test_log_d3c import add_comment, add_post, address, get, guild, rows, one, post

BASE = "http://localhost"
AGENT = "ci-log-d3d"


def admin_of(make_member, level=3):
    admin = make_member()
    db = admin.db_session()
    db.execute(text("UPDATE users SET admin_level = :l WHERE id = :u"), {"l": level, "u": admin.id})
    db.commit()
    return admin


def ban(client, admin, user, evidence=(), reason="Posting spam that made the guild unusable", days=""):
    return client.post(f"/api/ban_user/{user.id}", base_url=BASE, headers={"User-Agent": AGENT},
                       data={"formkey": admin.formkey(client), "reason": reason, "rule": "evading", "days": days, "evidence": list(evidence)},
                       environ_base={"REMOTE_ADDR": address()})


def decide(client, admin, entry_id, decision="approve"):
    return post(client, admin, f"/admin/log_evidence/{entry_id}", decision=decision)


def ban_entry(real_app, user):
    return one(real_app, "SELECT id FROM log_entries WHERE action IN ('ban_user', 'suspend_user') AND target_user_id = :u ORDER BY id DESC LIMIT 1", u=user.id)["id"]


def anonymous_post_deleted(member, board_id, real_app, title=None):
    """An anonymous public post by `member`, deleted after the grace window, so it is a line in the Deleted tab."""
    title = title or f"Cited post {secrets.token_hex(3)}"
    post_id, base36 = add_post(member, board_id, title=title, is_anonymous=True)
    assert post(member.browser(), member, f"/delete_post/{base36}").status_code == 204
    return post_id, base36, title


def deleted_tab(real_app, client=None):
    app, _ = real_app
    return get(client or app.test_client(), "/log?type=deletion").get_data(as_text=True)


def section(page, title):
    """The Log line (one card) that holds this title or marker, as text."""
    cards = re.split(r'(?=<div class="card mb-2 p-3 log-entry")', page)
    hit = next(card for card in cards if title in card)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", hit.split('class="card mb-2 p-3 log-entry"', 1)[-1].split('<div class="card mb-2 p-3 log-entry"')[0]))


def test_the_picker_lists_their_items_counts_the_private_ones_and_reaches_a_deleted_account(real_app, make_member):
    first, second = admin_of(make_member), admin_of(make_member)
    target = make_member()
    board_id, _ = guild(real_app)
    private_id, _ = guild(real_app, private=True)
    public_post, public36 = add_post(target, board_id, title="Public one", is_anonymous=True)
    add_post(target, private_id, title="In a private guild")
    comment_id, comment36 = add_comment(target, public_post, board_id, body="a public comment", is_anonymous=True)
    other_post, _ = add_post(second, board_id, title="Somebody else's")
    client = first.browser()
    body = get(client, f"/admin/evidence_items/{target.id}").get_json()
    tokens = {r["token"]: r for r in body["rows"]}
    assert f"p{public36}" in tokens and f"c{comment36}" in tokens and tokens[f"p{public36}"]["anonymous"] and tokens[f"p{public36}"]["citable"]
    assert body["private"] == 1 and "In a private guild" not in str(body), "private items are only counted, never listed"
    assert not any(r["title"] == "Somebody else's" for r in body["rows"]) and body["max"] == 50
    # a deleted account is reachable too (decision 18)
    db = target.db_session()
    db.execute(text("UPDATE users SET is_deleted = true WHERE id = :u"), {"u": target.id})
    db.commit()
    assert f"p{public36}" in {r["token"] for r in get(client, f"/admin/evidence_items/{target.id}").get_json()["rows"]}
    # not for a member, and not for an account the admin couldn't ban
    assert get(make_member().browser(), f"/admin/evidence_items/{target.id}").status_code in (302, 401, 403, 404)
    assert get(first.browser(), f"/admin/evidence_items/{second.id}").status_code == 404, "an equal admin: told nothing"


def test_a_list_that_cannot_be_cited_bans_nobody(real_app, make_member):
    admin = admin_of(make_member)
    target, other = make_member(), make_member()
    board_id, _ = guild(real_app)
    private_id, _ = guild(real_app, private=True)
    theirs, theirs36 = add_post(target, board_id, is_anonymous=True)
    private, private36 = add_post(target, private_id)
    someone_elses, someone36 = add_post(other, board_id)
    client = admin.browser()
    for evidence, said in (([f"p{someone36}"], "own posts"), ([f"p{private36}"], "private place"), ([f"p{theirs36}", f"p{someone36}"], "own posts")):
        refused = ban(client, admin, target, evidence)
        assert refused.status_code == 400 and said in refused.get_data(as_text=True), evidence
        assert one(real_app, "SELECT is_banned FROM users WHERE id = :u", u=target.id)["is_banned"] == 0, "nobody was banned"
    assert rows(real_app, "SELECT 1 FROM log_evidence WHERE target_user_id = :u", u=target.id) == []
    over = ban(client, admin, target, [f"p{n:x}" for n in range(1, 52)])
    assert over.status_code == 400 and "up to 50" in over.get_data(as_text=True)


def test_the_log_names_the_author_only_after_a_different_admin_approves_and_not_once_the_ban_is_reversed(real_app, make_member):
    first, second = admin_of(make_member), admin_of(make_member)
    target = make_member()
    board_id, _ = guild(real_app)
    post_id, base36, title = anonymous_post_deleted(target, board_id, real_app)
    app, _ = real_app
    assert target.username not in section(deleted_tab(real_app), title), "Anonymous before anything"

    banned = ban(first.browser(), first, target, [f"p{base36}"])
    assert banned.status_code in (200, 302)
    assert one(real_app, "SELECT is_banned FROM users WHERE id = :u", u=target.id)["is_banned"], "the ban took effect at once"
    entry = ban_entry(real_app, target)
    pending = one(real_app, "SELECT status, proposed_by FROM log_evidence WHERE entry_id = :e", e=entry)
    assert pending["status"] == "pending" and pending["proposed_by"] == first.id
    assert target.username not in section(deleted_tab(real_app), title), "until approved, the Log shows the ban alone"
    assert "Evidence:" not in section(get(app.test_client(), "/log?type=sanction").get_data(as_text=True), f'id="log-{entry}"')

    # the staff page lists it; the banning admin can't approve their own proposal, a different admin can
    page = get(first.browser(), "/admin/log_evidence").get_data(as_text=True)
    assert f'id="evidence-{entry}"' in page and "Waiting for a different admin" in page and "Approve: name the author" not in section_of(page, entry)
    assert decide(first.browser(), first, entry).status_code == 403
    assert one(real_app, "SELECT status FROM log_evidence WHERE entry_id = :e", e=entry)["status"] == "pending"
    assert decide(second.browser(), second, entry).status_code == 302
    approved = one(real_app, "SELECT status, decided_by, sole_admin FROM log_evidence WHERE entry_id = :e", e=entry)
    assert (approved["status"], approved["decided_by"], approved["sole_admin"]) == ("approved", second.id, False)
    assert rows(real_app, "SELECT 1 FROM log_entries WHERE action = 'admin_evidence' AND audience = 2 AND actor_id = :a", a=second.id), "a staff-only entry says who decided"

    # now the lines about the cited item name the author, and the ban's line lists it
    line = section(deleted_tab(real_app), title)
    assert f"@{target.username}" in line, "the Deleted tab's line about the cited item names who wrote it"
    sanctions = get(app.test_client(), "/log?type=sanction").get_data(as_text=True)
    ban_card = section(sanctions, f'id="log-{entry}"')
    assert "Evidence:" in ban_card and f"post #{base36}" in ban_card
    assert f"/post/{base36}/log" in sanctions

    # reversing the ban does not take the name back (the owner, 2026-11-17: once exposed, it can't be unexposed; a reversed ban is transparency about the
    # moderation): the reversal is its own entry beside the ban, and the cited item still names its author
    unbanned = post(first.browser(), first, f"/api/unban_user/{target.id}", reason="a mistake")
    assert unbanned.status_code in (200, 302)
    assert one(real_app, "SELECT status FROM log_evidence WHERE entry_id = :e", e=entry)["status"] == "approved"
    assert f"@{target.username}" in section(deleted_tab(real_app), title), "still named after the reversal"
    assert "Evidence:" in section(get(app.test_client(), "/log?type=sanction").get_data(as_text=True), f'id="log-{entry}"')


def section_of(page, entry_id):
    start = page.index(f'id="evidence-{entry_id}"')
    return page[start:start + 1500]


def test_a_declined_reveal_never_shows_and_an_item_not_cited_stays_anonymous(real_app, make_member):
    first, second = admin_of(make_member), admin_of(make_member)
    target = make_member()
    board_id, _ = guild(real_app)
    cited_id, cited36, cited_title = anonymous_post_deleted(target, board_id, real_app)
    _, _, other_title = anonymous_post_deleted(target, board_id, real_app)
    ban(first.browser(), first, target, [f"p{cited36}"])
    entry = ban_entry(real_app, target)
    assert decide(second.browser(), second, entry, "decline").status_code == 302
    assert one(real_app, "SELECT status FROM log_evidence WHERE entry_id = :e", e=entry)["status"] == "declined"
    page = deleted_tab(real_app)
    assert f"@{target.username}" not in section(page, cited_title)
    assert decide(second.browser(), second, entry).status_code == 403, "nothing is waiting any more"
    # approving a second ban's evidence of another item names that one only
    again = make_member()
    c_id, c36, c_title = anonymous_post_deleted(again, board_id, real_app)
    _, _, d_title = anonymous_post_deleted(again, board_id, real_app)
    ban(first.browser(), first, again, [f"p{c36}"])
    decide(second.browser(), second, ban_entry(real_app, again))
    page = deleted_tab(real_app)
    assert f"@{again.username}" in section(page, c_title) and f"@{again.username}" not in section(page, d_title), "only what was cited"


def test_a_deleted_account_can_be_banned_with_evidence_and_is_named_by_its_username(real_app, make_member):
    first, second = admin_of(make_member), admin_of(make_member)
    target = make_member()
    board_id, _ = guild(real_app)
    post_id, base36 = add_post(target, board_id, title=f"Before leaving {secrets.token_hex(3)}", is_anonymous=True)
    db = target.db_session()
    db.execute(text("UPDATE users SET created_utc = :old WHERE id = :u"), {"old": int(time.time()) - 90 * 86400, "u": target.id})
    db.commit()
    assert post(target.browser(), target, "/settings/delete_account", password="ci-Password-1", delete_reason="leaving").status_code == 200
    assert one(real_app, "SELECT is_deleted FROM users WHERE id = :u", u=target.id)["is_deleted"] is True
    assert ban(first.browser(), first, target, [f"p{base36}"]).status_code in (200, 302), "a deleted account can still be banned, with evidence"
    entry = ban_entry(real_app, target)
    app, _ = real_app

    def ban_line(page):
        return section(page, f'id="log-{entry}"')

    before = get(app.test_client(), "/log?type=sanction").get_data(as_text=True)
    assert "[deleted user]" in ban_line(before) and target.username not in ban_line(before)
    decide(second.browser(), second, entry)
    after = get(app.test_client(), "/log?type=sanction").get_data(as_text=True)
    assert f"@{target.username}" in ban_line(after) and f"post #{base36}" in ban_line(after), "an approved reveal names a deleted account by its username"
    # the account's own items stay anonymous (the deletion's job made them so) and citing them did not undo it
    assert one(real_app, "SELECT is_anonymous FROM submissions WHERE id = :i", i=post_id)["is_anonymous"] is True


def test_a_timed_suspension_can_cite_too_and_the_item_is_not_listed_as_theirs_anywhere_else(real_app, make_member):
    first, second = admin_of(make_member), admin_of(make_member)
    target = make_member()
    board_id, board_name = guild(real_app)
    post_id, base36, title = anonymous_post_deleted(target, board_id, real_app)
    assert ban(first.browser(), first, target, [f"p{base36}"], days="3").status_code in (200, 302)
    entry = ban_entry(real_app, target)
    assert one(real_app, "SELECT action FROM log_entries WHERE id = :e", e=entry)["action"] == "suspend_user"
    decide(second.browser(), second, entry)
    app, _ = real_app
    assert f"@{target.username}" in section(deleted_tab(real_app), title)
    # nothing else changes: the post is still anonymous on its row and on the profile's own listing
    assert one(real_app, "SELECT is_anonymous FROM submissions WHERE id = :i", i=post_id)["is_anonymous"] is True
    profile = get(app.test_client(), f"/@{target.username}")
    assert title not in profile.get_data(as_text=True)
    # the ban form of a live account's profile carries the picker (a deleted account's: see the test above); a suspended one's page offers the unban instead
    live = make_member()
    assert 'id="ban-evidence"' in get(second.browser(), f"/@{live.username}").get_data(as_text=True)
