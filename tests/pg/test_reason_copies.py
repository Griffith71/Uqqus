"""A redaction reaches every copy of the reason, on the real app and Postgres (the second review of the Log, C48; the rules and every copy are tested without
Postgres in tests/test_reason_copies.py, the helper is ruqqus/helpers/reason_copies.py).

The promises, end to end: when a level-4 admin hides an entry's reason, the pages that printed the same words go quiet too (the guild's own mod log, the
hidden or removed post's page and its history, the banned profile, the exile list), while **what the decision did stands** (the post stays hidden or removed,
the account banned, the exile active, the guild's log still says "the site" and not the admin), a newer decision's words stay, the staff still read the entry's
own reason, the redaction is one new linked staff entry that says what it cleared, a second run finds nothing, and the report queue's Redact does the same."""
import secrets

from sqlalchemy import text

from pg.test_log_d3c import add_comment, add_post, get, guild, one, post, rows
from pg.test_log_d3d import admin_of
from pg.test_log_d3f_aliases import guildmaster
from pg.test_log_evidence import exile, named_post


def token(label):
    return f"PRIVATE-{label}-{secrets.token_hex(4)}"


def shown(client, path):
    """A page's text, which must have been answered (a 404 would show nothing and prove nothing)."""
    answer = get(client, path)
    assert answer.status_code == 200, (path, answer.status_code)
    return answer.get_data(as_text=True)


def hide(real_app, make_member, reason, rule="good_faith"):
    """A post by a member in a new public guild, hidden by the guild's guildmaster with a reason: (board id, name, guildmaster, author, post id, base 36 id, entry id)."""
    board_id, name = guild(real_app)
    gm, author = guildmaster(make_member(), board_id), make_member()
    post_id, b36 = add_post(author, board_id)
    answer = post(gm.browser(), gm, f"/mod/kick/{name}/{b36}", reason=reason, rule=rule)
    assert answer.status_code == 200, answer.get_data(as_text=True)[:300]
    entry = one(real_app, "SELECT id FROM log_entries WHERE action = 'hide_post_from_guild' AND target_submission_id = :p ORDER BY id DESC", p=post_id)["id"]
    return board_id, name, gm, author, post_id, b36, entry


def redact(make_member, entry_id, why="a private detail slipped into it"):
    boss = admin_of(make_member, 4)
    answer = post(boss.browser(), boss, f"/admin/log/{entry_id}/redact", why=why)
    assert answer.status_code == 303, answer.get_data(as_text=True)[:300]
    return boss


def correction(real_app, entry_id):
    return one(real_app, "SELECT staff_note, audience, actor_role FROM log_entries WHERE linked_entry_id = :e AND action = 'redact_entry'", e=entry_id)


def mod_note(real_app, entry_id):
    return one(real_app, "SELECT m._note FROM modactions m JOIN log_entries e ON e.legacy_modaction_id = m.id WHERE e.id = :e", e=entry_id)["_note"]


# --- a guildmaster's hide --------------------------------------------------------------------------------------------------------------------

def test_redacting_a_hide_clears_every_page_that_printed_the_words_and_the_post_stays_hidden(make_member, real_app):
    words = token("HIDE")
    board_id, name, gm, author, post_id, b36, entry = hide(real_app, make_member, words)
    visitor = real_app[0].test_client()
    pages = (f"/+{name}/mod/log", f"/post/{b36}", f"/post/{b36}/history")
    for path in pages:
        assert words in shown(visitor, path), f"before: {path} prints the reason"
    assert one(real_app, "SELECT hidden_reason FROM submissions_aux WHERE id = :p", p=post_id)["hidden_reason"] == words
    assert words in mod_note(real_app, entry)

    before = one(real_app, "SELECT reason, rule_key, staff_note, audience, actor_id, actor_role, created_utc FROM log_entries WHERE id = :e", e=entry)
    boss = redact(make_member, entry)

    for path in pages:
        assert words not in shown(visitor, path), f"after: {path} is quiet"
    state = one(real_app, "SELECT a.hidden_reason, s.hidden_by_guild FROM submissions_aux a JOIN submissions s ON s.id = a.id WHERE a.id = :p", p=post_id)
    assert state["hidden_reason"] == "" and state["hidden_by_guild"] is True, "the words are gone and the post is still hidden"
    assert mod_note(real_app, entry) == ""
    assert one(real_app, "SELECT reason FROM content_edit_history WHERE target_submission_id = :p AND action = 'guild_hide'", p=post_id)["reason"] is None
    guild_log = shown(visitor, f"/+{name}/mod/log")
    assert f"#{b36}" in guild_log, "the line is still there: the decision stands, only its words are hidden"
    # the staff still read the words, and the entry itself was never edited
    assert words in shown(admin_of(make_member, 3).browser(), f"/admin/log?guild={name}")
    after = one(real_app, "SELECT reason, rule_key, staff_note, audience, actor_id, actor_role, created_utc FROM log_entries WHERE id = :e", e=entry)
    assert after == before and after["reason"] == words
    note = correction(real_app, entry)
    assert note == {"staff_note": "a private detail slipped into it (also cleared: the guild log's note, the hidden post's reason, history rows (1))",
                    "audience": 2, "actor_role": "admin"}
    assert one(real_app, "SELECT actor_id FROM log_entries WHERE linked_entry_id = :e AND action = 'redact_entry'", e=entry)["actor_id"] == boss.id


def test_redacting_an_older_hide_leaves_the_newer_hides_words_on_the_pages(make_member, real_app):
    first, second = token("FIRST"), token("SECOND")
    board_id, name, gm, author, post_id, b36, entry1 = hide(real_app, make_member, first)
    assert post(gm.browser(), gm, f"/mod/kick/{name}/{b36}", reason=second, rule="good_faith").status_code == 200
    entry2 = one(real_app, "SELECT id FROM log_entries WHERE action = 'hide_post_from_guild' AND target_submission_id = :p ORDER BY id DESC", p=post_id)["id"]
    assert entry2 != entry1
    redact(make_member, entry1)
    visitor = real_app[0].test_client()
    for path in (f"/+{name}/mod/log", f"/post/{b36}", f"/post/{b36}/history"):
        page = shown(visitor, path)
        assert first not in page and second in page, path
    assert one(real_app, "SELECT hidden_reason FROM submissions_aux WHERE id = :p", p=post_id)["hidden_reason"] == second
    assert mod_note(real_app, entry2) == second
    redact(make_member, entry2)
    for path in (f"/+{name}/mod/log", f"/post/{b36}", f"/post/{b36}/history"):
        page = shown(visitor, path)
        assert first not in page and second not in page, path


def test_redacting_the_hide_of_a_comment_and_a_post_authors_hide_clear_their_lines_and_history(make_member, real_app):
    board_id, name = guild(real_app)
    gm, author, commenter = guildmaster(make_member(), board_id), make_member(), make_member()
    post_id, b36 = add_post(author, board_id)
    guild_words, author_words = token("COMMENT"), token("AUTHOR")
    # a guild's hide of a comment
    comment_id, c36 = add_comment(commenter, post_id, board_id, "A comment the guild hides")
    assert post(gm.browser(), gm, f"/mod/hide_comment/{name}/{c36}", reason=guild_words, rule="good_faith").status_code == 200
    entry = one(real_app, "SELECT id FROM log_entries WHERE action = 'hide_comment_from_guild' AND target_comment_id = :c", c=comment_id)["id"]
    visitor = real_app[0].test_client()
    assert guild_words in shown(visitor, f"/comment/{c36}/history")
    redact(make_member, entry)
    assert guild_words not in shown(visitor, f"/comment/{c36}/history")
    state = one(real_app, "SELECT a.hidden_reason, c.hidden_by_guild FROM comments_aux a JOIN comments c ON c.id = a.id WHERE a.id = :c", c=comment_id)
    assert state["hidden_reason"] == "" and state["hidden_by_guild"] is True
    assert correction(real_app, entry)["staff_note"].endswith("(also cleared: the guild log's note, the hidden comment's reason, history rows (1))")
    # a post's author hiding a comment under their own profile post
    profile_post, profile36 = add_post(author, 2)
    other_id, other36 = add_comment(commenter, profile_post, 2, "A comment its post's author hides")
    assert post(author.browser(), author, f"/author/hide_comment/{other36}", reason=author_words).status_code == 200
    author_entry = one(real_app, "SELECT id FROM log_entries WHERE action = 'author_hide_comment' AND target_comment_id = :c", c=other_id)["id"]
    assert author_words in shown(visitor, f"/comment/{other36}/history")
    redact(make_member, author_entry)
    assert author_words not in shown(visitor, f"/comment/{other36}/history")
    state = one(real_app, "SELECT a.author_hidden_reason, c.author_hidden FROM comments_aux a JOIN comments c ON c.id = a.id WHERE a.id = :c", c=other_id)
    assert state["author_hidden_reason"] == "" and state["author_hidden"] is True
    assert correction(real_app, author_entry)["staff_note"].endswith("(also cleared: the reason a post's author gave for hiding a comment, history rows (1))")


# --- an admin's removal ----------------------------------------------------------------------------------------------------------------------

def test_redacting_an_admins_removal_clears_the_removed_pages_reason_and_the_guild_log_still_says_the_site(make_member, real_app):
    words = token("REMOVE")
    admin, author = admin_of(make_member, 3), make_member()
    board_id, name = guild(real_app)
    post_id, b36 = add_post(author, board_id)
    assert post(admin.browser(), admin, f"/api/ban_post/{b36}", reason=f"**{words}**", rule="evasion").status_code in (200, 302)
    entry = one(real_app, "SELECT id FROM log_entries WHERE action = 'ban_post' AND target_submission_id = :p", p=post_id)["id"]
    visitor = real_app[0].test_client()
    for path in (f"/post/{b36}", f"/+{name}/mod/log"):
        page = shown(visitor, path)
        assert words in page, f"before: {path}"
    assert admin.username not in shown(visitor, f"/+{name}/mod/log"), "the guild's log says the site, not the admin"

    redact(make_member, entry)

    for path in (f"/post/{b36}", f"/+{name}/mod/log"):
        assert words not in shown(visitor, path), f"after: {path}"
    log_page = shown(visitor, f"/+{name}/mod/log")
    assert admin.username not in log_page, "blanking the note must not name the admin: its marker stays"
    assert mod_note(real_app, entry) == "admin action"
    state = one(real_app, "SELECT a.ban_reason, s.is_banned FROM submissions_aux a JOIN submissions s ON s.id = a.id WHERE a.id = :p", p=post_id)
    assert state["ban_reason"] == "" and state["is_banned"] is True, "still removed"
    assert correction(real_app, entry)["staff_note"] == "a private detail slipped into it (also cleared: the removed post's reason)"


# --- an account's ban, a guild's ban ------------------------------------------------------------------------------------------------------------

def test_redacting_an_account_ban_clears_the_banned_profiles_reason_and_the_ban_stands(make_member, real_app):
    words = token("BAN")
    admin, victim = admin_of(make_member, 3), make_member()
    assert post(admin.browser(), admin, f"/api/ban_user/{victim.id}", days="0", reason=words, rule="evasion").status_code in (200, 302)
    entry = one(real_app, "SELECT id FROM log_entries WHERE action = 'ban_user' AND target_user_id = :u", u=victim.id)["id"]
    visitor = real_app[0].test_client()
    assert words in shown(visitor, f"/@{victim.username}")

    redact(make_member, entry)

    assert words not in shown(visitor, f"/@{victim.username}")
    state = one(real_app, "SELECT ban_reason, is_banned, unban_utc FROM users WHERE id = :u", u=victim.id)
    assert state == {"ban_reason": "", "is_banned": admin.id, "unban_utc": 0}, "the words are gone, the ban is not"
    assert correction(real_app, entry)["staff_note"] == "a private detail slipped into it (also cleared: the account's ban reason)"
    assert one(real_app, "SELECT reason FROM log_entries WHERE id = :e", e=entry)["reason"] == words


def test_redacting_a_guild_ban_clears_its_reason_and_the_guild_stays_banned(make_member, real_app):
    from ruqqus.helpers.base36 import base36encode
    words = token("GUILDBAN")
    boss = admin_of(make_member, 4)
    board_id, name = guild(real_app)
    assert post(boss.browser(), boss, f"/api/ban_guild/{base36encode(board_id)}", reason=words, rule="evasion").status_code in (302, 303)
    assert one(real_app, "SELECT ban_reason FROM boards WHERE id = :b", b=board_id)["ban_reason"] == words
    entry = one(real_app, "SELECT id FROM log_entries WHERE action = 'ban_guild' AND board_id = :b", b=board_id)["id"]
    redact(make_member, entry)
    assert one(real_app, "SELECT ban_reason, is_banned FROM boards WHERE id = :b", b=board_id) == {"ban_reason": "", "is_banned": True}
    assert correction(real_app, entry)["staff_note"].endswith("(also cleared: the guild's ban reason)")


# --- the exile list ----------------------------------------------------------------------------------------------------------------------------

def test_redacting_an_exile_clears_the_exile_lists_reason_and_the_exile_stays_active(make_member, real_app):
    words = token("EXILE")
    mod, member = make_member(), make_member()
    board_id, name = guild(real_app)
    guildmaster(mod, board_id)
    _, _, thing = named_post(member, board_id)
    answer = exile(mod, name, username=member.username, reason=words, rule="good_faith", thing=thing)
    assert answer.status_code in (200, 204), answer.get_data(as_text=True)[:200]
    entry = one(real_app, "SELECT id FROM log_entries WHERE action = 'exile_user' AND target_user_id = :u", u=member.id)["id"]
    listing = shown(mod.browser(), f"/+{name}/mod/exiled")
    assert member.username in listing and words in listing

    redact(make_member, entry)

    listing = shown(mod.browser(), f"/+{name}/mod/exiled")
    assert member.username in listing and words not in listing, "still listed, no reason"
    ban = one(real_app, "SELECT is_active, mod_note FROM bans WHERE board_id = :b AND user_id = :u", b=board_id, u=member.id)
    assert ban == {"is_active": True, "mod_note": ""}
    assert correction(real_app, entry)["staff_note"] == "a private detail slipped into it (also cleared: the exile list's reason)"


# --- an author's edit summary ------------------------------------------------------------------------------------------------------------------

def test_redacting_an_edit_clears_the_summary_the_history_page_prints_on_the_post_and_its_copy_and_the_version_stays(make_member, real_app):
    words, old_title = token("EDIT"), "Original title " + secrets.token_hex(3)
    board_id, name = guild(real_app)
    other_board, _ = guild(real_app)
    author = make_member()
    post_id, b36 = add_post(author, board_id, title=old_title)
    copy_id, _ = add_post(author, other_board, title=old_title, repost_id=post_id)
    done = post(author.browser(), author, f"/edit_post/{b36}", title="A revised title", body="after", edit_summary=words)
    assert done.status_code in (200, 204, 302, 303), done.get_data(as_text=True)[:300]
    entry = one(real_app, "SELECT id, reason FROM log_entries WHERE action = 'edit_post' AND target_submission_id = :p", p=post_id)
    assert entry["reason"] == words
    sums = f"SELECT target_submission_id AS t, summary FROM content_edit_history WHERE action = 'edit' AND target_submission_id IN ({post_id}, {copy_id}) ORDER BY t"
    assert [(r["t"], r["summary"]) for r in rows(real_app, sums)] == [(post_id, words), (copy_id, words)], "one row for the post and one for its copy"
    visitor = real_app[0].test_client()
    assert words in shown(visitor, f"/post/{b36}/history") and old_title in shown(visitor, f"/post/{b36}/history")
    assert words in shown(visitor, "/log?type=edit")

    redact(make_member, entry["id"])

    assert [(r["t"], r["summary"]) for r in rows(real_app, sums)] == [(post_id, None), (copy_id, None)]
    page = shown(visitor, f"/post/{b36}/history")
    assert words not in page, "the history page no longer prints the note"
    assert old_title in page, "the version edited away, who edited it and when are still there"
    assert words not in shown(visitor, "/log?type=edit")
    assert one(real_app, "SELECT reason FROM log_entries WHERE id = :e", e=entry["id"])["reason"] == words, "the entry itself is never edited"
    assert correction(real_app, entry["id"])["staff_note"] == "a private detail slipped into it (also cleared: edit summaries (2))"


def test_redacting_a_comments_edit_clears_the_summary_on_its_history_page(make_member, real_app):
    words = token("COMMENT-EDIT")
    board_id, _ = guild(real_app)
    author = make_member()
    post_id, _ = add_post(author, board_id)
    comment_id, c36 = add_comment(author, post_id, board_id, "A comment before the edit")
    done = post(author.browser(), author, f"/edit_comment/{c36}", body="A comment after the edit", edit_summary=words)
    assert done.status_code in (200, 204, 302, 303), done.get_data(as_text=True)[:300]
    entry = one(real_app, "SELECT id FROM log_entries WHERE action = 'edit_comment' AND target_comment_id = :c", c=comment_id)["id"]
    visitor = real_app[0].test_client()
    assert words in shown(visitor, f"/comment/{c36}/history")
    redact(make_member, entry)
    page = shown(visitor, f"/comment/{c36}/history")
    assert words not in page and "A comment before the edit" in page
    assert correction(real_app, entry)["staff_note"] == "a private detail slipped into it (also cleared: edit summaries (1))"


# --- the report queue ----------------------------------------------------------------------------------------------------------------------------

def test_the_report_queues_redact_clears_the_same_copies_and_a_level_3_admin_cannot(make_member, real_app):
    words = token("REPORTED")
    board_id, name, gm, author, post_id, b36, entry = hide(real_app, make_member, words)
    reporter, low, high = make_member(), admin_of(make_member, 3), admin_of(make_member, 4)
    assert post(reporter.browser(), reporter, f"/log/{entry}/report", reason="It names a person").status_code == 200
    assert post(low.browser(), low, f"/admin/log_reports/{entry}", decision="redacted").status_code == 403
    assert one(real_app, "SELECT hidden_reason FROM submissions_aux WHERE id = :p", p=post_id)["hidden_reason"] == words, "level 3 changed nothing"
    assert post(high.browser(), high, f"/admin/log_reports/{entry}", decision="redacted").status_code == 303
    visitor = real_app[0].test_client()
    for path in (f"/+{name}/mod/log", f"/post/{b36}", f"/post/{b36}/history"):
        assert words not in shown(visitor, path), path
    assert mod_note(real_app, entry) == ""
    assert correction(real_app, entry)["staff_note"] == ("Hidden after a report. (also cleared: the guild log's note, the hidden post's reason, history rows (1))")


# --- once, and for the redactions made before ----------------------------------------------------------------------------------------------------

def test_a_second_redaction_is_refused_the_original_is_never_edited_and_a_second_run_finds_nothing(make_member, real_app):
    from ruqqus.helpers import reason_copies
    words = token("ONCE")
    board_id, name, gm, author, post_id, b36, entry = hide(real_app, make_member, words)
    columns = "reason, rule_key, staff_note, audience, actor_id, actor_role, created_utc, target_submission_id, board_id, legacy_modaction_id"
    before = one(real_app, f"SELECT {columns} FROM log_entries WHERE id = :e", e=entry)
    boss = redact(make_member, entry)
    assert post(boss.browser(), boss, f"/admin/log/{entry}/redact", why="again").status_code == 404, "nothing left to hide"
    assert one(real_app, f"SELECT {columns} FROM log_entries WHERE id = :e", e=entry) == before
    assert len(rows(real_app, "SELECT 1 FROM log_entries WHERE linked_entry_id = :e AND action = 'redact_entry'", e=entry)) == 1
    _, db_session = real_app
    db = db_session()
    db.rollback()
    assert reason_copies.scrub(db, entry) == ([], [])
    db.commit()


def test_scrub_redacted_mends_an_entry_redacted_before_the_copies_were_cleared(make_member, real_app):
    from ruqqus.helpers import reason_copies
    words = token("OLD")
    board_id, name, gm, author, post_id, b36, entry = hide(real_app, make_member, words)
    kept_words = token("KEPT")
    other = hide(real_app, make_member, kept_words)
    _, db_session = real_app
    db = db_session()
    db.rollback()
    db.execute(text("UPDATE log_entries SET redacted_utc = :n, redacted_by = 1 WHERE id = :e"), {"n": 1_700_000_000, "e": entry})        # the old way: the entry alone
    db.commit()
    visitor = real_app[0].test_client()
    assert words in shown(visitor, f"/post/{b36}"), "the old redaction left the page printing it"
    read, last, cleared = reason_copies.scrub_redacted(db, after=entry - 1, limit=1)
    db.commit()
    assert (read, last, cleared) == (1, entry, 3)
    assert words not in shown(visitor, f"/post/{b36}") and words not in shown(visitor, f"/+{name}/mod/log")
    assert kept_words in shown(visitor, f"/post/{other[5]}"), "an entry nobody redacted is left alone"
    assert rows(real_app, "SELECT 1 FROM log_entries WHERE linked_entry_id = :e AND action = 'redact_entry'", e=entry) == [], "it writes no entry"
    db.rollback()
    assert reason_copies.scrub_redacted(db, after=entry - 1, limit=1) == (1, entry, 0)
    db.commit()
