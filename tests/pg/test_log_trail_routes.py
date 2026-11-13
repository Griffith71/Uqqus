"""The paper trail made complete, on the real app and Postgres (the Log's slice D2; the rules and the SQL are tested without Postgres in
tests/test_log_paper_trail.py, tests/test_log_appeals.py and tests/test_log_notices.py). End to end:

- a hide, a removal and a ban each reach the person they are about, with the rule, the reason, the Log and the Appeal link, and sets `notice_sent`;
- that person (and nobody else) can appeal, once; the guild's guildmasters decide a guild's decision and the site's admins everything else, never
  the one who decided while another could; a reversal runs the undo and writes a NEW entry linked to the first; the person is told;
- an image check's ban goes through `User.ban`: generic words in the Log and on the profile, the match a separate level-4 entry, the alts staff-only;
- what the staff do to run the site is a staff-only entry; a timed suspension that ran out says so once; a mod-log row whose copy failed is copied;
- an entry's reason can be hidden by a level-4 admin, as a new linked entry; deleting an account writes what it ends."""
import re
import secrets
import time

from sqlalchemy import text

BASE = "http://localhost"
HEADERS = {"User-Agent": "ci-log-paper-trail"}


def address():
    return f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"


def post(client, member, path, **fields):
    data = {"formkey": member.formkey(client), **fields}
    return client.post(path, base_url=BASE, headers=HEADERS, data=data, environ_base={"REMOTE_ADDR": address()})


def get(client, path):
    return client.get(path, base_url=BASE, headers=HEADERS, environ_base={"REMOTE_ADDR": address()})


def admin_of(make_member, level=3):
    admin = make_member()
    db = admin.db_session()
    db.execute(text("UPDATE users SET admin_level = :l WHERE id = :u"), {"l": level, "u": admin.id})
    db.commit()
    return admin


def guild(real_app, private=False):
    from ruqqus.classes import Board
    _, db_session = real_app
    db = db_session()
    board = Board(name="ci_trail_" + secrets.token_hex(3), description="a guild for the paper trail test",
                  description_html="<p>a guild for the paper trail test</p>", creator_id=1, subcat_id=0, is_private=private,
                  created_utc=int(time.time()), color="23a9f2")
    db.add(board)
    db.commit()
    return board.id, board.name


def guildmaster(make_member, board_id, **perms):
    from ruqqus.classes import ModRelationship
    gm = make_member()
    db = gm.db_session()
    db.execute(text("UPDATE users SET creation_ip = :ip WHERE id = :u"), {"ip": address(), "u": gm.id})
    grants = {"perm_full": True, "perm_content": True, "perm_access": True, "perm_config": True, **perms}
    db.add(ModRelationship(user_id=gm.id, board_id=board_id, accepted=True, invite_rescinded=False, **grants))
    db.commit()
    return gm


def add_post(member, board_id, title="A post", body="<p>words</p>", age=3600, anonymous=False):
    from ruqqus.classes import Submission, SubmissionAux
    from ruqqus.helpers.base36 import base36encode
    db = member.db_session()
    db.rollback()
    with member.app.test_request_context("/"):
        row = Submission(author_id=member.id, board_id=board_id, original_board_id=board_id, created_utc=int(time.time()) - age,
                         post_public=True, is_anonymous=anonymous)
    db.add(row)
    db.flush()
    db.add(SubmissionAux(id=row.id, title=title, body=re.sub("<[^>]+>", "", body), body_html=body))
    db.commit()
    return row.id, base36encode(row.id)


def add_comment(member, post_id, board_id, body="<p>A comment.</p>", anonymous=False):
    from ruqqus.classes import Comment, CommentAux
    from ruqqus.helpers.base36 import base36encode
    db = member.db_session()
    db.rollback()
    with member.app.test_request_context("/"):
        row = Comment(author_id=member.id, parent_submission=post_id, level=1, original_board_id=board_id,
                      created_utc=int(time.time()) - 3600, is_anonymous=anonymous)
    db.add(row)
    db.flush()
    db.add(CommentAux(id=row.id, body=re.sub("<[^>]+>", "", body), body_html=body))
    db.commit()
    return row.id, base36encode(row.id)


def rows(real_app, sql, **params):
    _, db_session = real_app
    db = db_session()
    db.rollback()
    db.expire_all()
    return [dict(r._mapping) for r in db.execute(text(sql), params)]


def entries(real_app, **where):
    clause = " AND ".join(f"{k} = :{k}" for k in where) or "true"
    return rows(real_app, f"SELECT * FROM log_entries WHERE {clause} ORDER BY id", **where)


def notices_of(real_app, user_id):
    """The text of every notification this account has, oldest first."""
    return [r["body"] for r in rows(real_app, "SELECT a.body FROM notifications n JOIN comments_aux a ON a.id = n.comment_id "
                                              "WHERE n.user_id = :u ORDER BY n.id", u=user_id)]


def hide(real_app, make_member, reason="Advertising", rule="good_faith"):
    board_id, name = guild(real_app)
    gm, author = guildmaster(make_member, board_id), make_member()
    post_id, b36 = add_post(author, board_id)
    answer = post(gm.browser(), gm, f"/mod/kick/{name}/{b36}", reason=reason, rule=rule)
    assert answer.status_code == 200, answer.get_data(as_text=True)[:300]
    (entry,) = entries(real_app, board_id=board_id, target_submission_id=post_id)
    return board_id, name, gm, author, post_id, b36, entry


# --- a decision reaches the person it is about ------------------------------------------------------------------------------------

def test_a_guilds_hide_tells_the_author_the_rule_the_reason_the_log_and_the_appeal_and_marks_the_entry(make_member, real_app):
    board_id, name, gm, author, post_id, b36, entry = hide(real_app, make_member)
    assert entry["notice_sent"] is True and entry["reason"] == "Advertising" and entry["rule_key"] == "good_faith"
    (told,) = notices_of(real_app, author.id)
    assert "was hidden from +" + name + " by a guildmaster" in told
    assert "```\nFree expression and good faith: Advertising\n```" in told                      # the reason sits where nothing is linked
    assert f"](/log/{entry['id']})" in told and f"](/log/{entry['id']}/appeal)" in told
    assert notices_of(real_app, gm.id) == []                                                      # not the one who decided


def test_a_reason_cannot_put_a_link_or_end_the_block_in_a_message_that_arrives_as_the_sites(make_member, real_app):
    board_id, name = guild(real_app)
    gm, author = guildmaster(make_member, board_id), make_member()
    _, b36 = add_post(author, board_id)
    answer = post(gm.browser(), gm, f"/mod/kick/{name}/{b36}", reason="``` [log in here](https://phish.example/login) https://phish.example")
    assert answer.status_code == 200
    (told,) = notices_of(real_app, author.id)
    outside = told.replace(told.split("```")[1], "")
    assert told.count("```") == 2 and "phish.example" not in outside
    page = real_app[0].test_client().get("/", base_url=BASE)
    assert page.status_code in (200, 302)
    html = rows(real_app, "SELECT a.body_html FROM notifications n JOIN comments_aux a ON a.id = n.comment_id WHERE n.user_id = :u", u=author.id)[0]["body_html"]
    assert "<a " not in html.split("<pre>")[1].split("</pre>")[0] if "<pre>" in html else "phish.example</a>" not in html


def test_a_comment_hide_and_an_admins_removal_tell_the_person_too(make_member, real_app):
    board_id, name = guild(real_app)
    gm, author, commenter = guildmaster(make_member, board_id), make_member(), make_member()
    post_id, b36 = add_post(author, board_id)
    comment_id, c36 = add_comment(commenter, post_id, board_id)
    assert post(gm.browser(), gm, f"/mod/hide_comment/{name}/{c36}", reason="Insulting", rule="good_faith").status_code == 200
    (told,) = notices_of(real_app, commenter.id)
    assert "was hidden from +" + name in told and "Insulting" in told
    admin = admin_of(make_member)
    other, other_post = make_member(), None
    other_post, other36 = add_post(other, board_id)
    assert post(admin.browser(), admin, f"/api/ban_post/{other36}", reason="Spam links", rule="evasion").status_code in (200, 302)
    (told,) = notices_of(real_app, other.id)
    assert "was removed" in told and "Spam links" in told and "Evading and manipulating the site" in told
    (entry,) = entries(real_app, target_submission_id=other_post, action="ban_post")
    assert entry["notice_sent"] is True and f"/log/{entry['id']}/appeal" in told


def test_a_post_authors_hide_of_a_comment_is_in_the_log_by_role_never_by_name_and_tells_the_commenter(make_member, real_app):
    from ruqqus.helpers import permalinks  # noqa: F401  (the profile board's id is the site's own)
    author, commenter = make_member(), make_member()
    profile = rows(real_app, "SELECT id FROM boards WHERE name = 'systemprofile'")[0]["id"]
    post_id, b36 = add_post(author, profile, anonymous=True)                           # an anonymous post: the author must not be named
    comment_id, c36 = add_comment(commenter, post_id, profile)
    assert post(author.browser(), author, f"/author/hide_comment/{c36}", reason="Off topic").status_code == 200
    (entry,) = entries(real_app, target_comment_id=comment_id, action="author_hide_comment")
    assert (entry["actor_role"], entry["audience"], entry["reason"], entry["notice_sent"]) == ("author", 0, "Off topic", True)
    public = get(real_app[0].test_client(), "/log").get_data(as_text=True)
    assert f"@{author.username}" not in public and "The post&#39;s author" in public or "The post's author" in public
    assert f"@{author.username}" not in get(real_app[0].test_client(), f"/log?by={author.username}").get_data(as_text=True)
    (told,) = notices_of(real_app, commenter.id)
    assert "hidden by the author of the post" in told
    assert post(author.browser(), author, f"/author/unhide_comment/{c36}").status_code == 200
    assert entries(real_app, target_comment_id=comment_id, action="author_unhide_comment")


# --- the appeal --------------------------------------------------------------------------------------------------------------------

def test_a_guild_appeal_end_to_end_only_the_person_files_it_the_guildmasters_decide_and_a_reversal_is_a_new_linked_entry(make_member, real_app):
    board_id, name, gm, author, post_id, b36, entry = hide(real_app, make_member)
    eid = entry["id"]
    mine, stranger = author.browser(), make_member().browser()
    form = get(mine, f"/log/{eid}/appeal")
    assert form.status_code == 200 and "Why was it wrong?" in form.get_data(as_text=True), (form.status_code, re.sub(r"\s+", " ", re.sub("<[^>]+>", " ", form.get_data(as_text=True)))[-700:])
    assert get(stranger, f"/log/{eid}/appeal").status_code == 404                              # not theirs: the same page as for no entry
    assert get(real_app[0].test_client(), f"/log/{eid}/appeal").status_code in (401, 302)
    assert post(stranger, author, f"/log/{eid}/appeal", reason="not mine").status_code == 404

    sent = post(mine, author, f"/log/{eid}/appeal", reason="I did not advertise")
    assert sent.status_code == 303 and sent.headers["Location"].endswith("?sent=1")
    again = post(mine, author, f"/log/{eid}/appeal", reason="and again")
    assert "already%20appealed" in again.headers["Location"]
    (appeal,) = rows(real_app, "SELECT * FROM log_appeals WHERE entry_id = :e", e=eid)
    assert (appeal["board_id"], appeal["status"], appeal["user_id"]) == (board_id, "open", author.id)

    # the guild's queue shows it (the post by id, never its words); an admin's queue and a stranger's do not
    queue = get(gm.browser(), f"/+{name}/mod/appeals")
    html = queue.get_data(as_text=True)
    assert queue.status_code == 200 and "I did not advertise" in html and f"#{b36}" in html and "Advertising" in html
    assert get(stranger, f"/+{name}/mod/appeals").status_code == 404
    admin = admin_of(make_member)
    assert "I did not advertise" not in get(admin.browser(), "/admin/appeals").get_data(as_text=True)
    assert post(stranger, author, f"/+{name}/mod/appeals/{appeal['id']}", outcome="reversed", note="x").status_code == 404

    done = post(gm.browser(), gm, f"/+{name}/mod/appeals/{appeal['id']}", outcome="reversed", note="You are right, sorry")
    assert done.status_code == 303 and "Decided" in done.headers["Location"]
    assert rows(real_app, "SELECT hidden_by_guild FROM submissions WHERE id = :p", p=post_id)[0]["hidden_by_guild"] is False
    (undo,) = entries(real_app, target_submission_id=post_id, action="unhide_post_from_guild")
    assert undo["linked_entry_id"] == eid and undo["audience"] == 0 and undo["actor_id"] == gm.id        # public, as the original was
    assert entries(real_app, id=eid)[0]["action"] == "hide_post_from_guild"                              # the original is never edited
    settled = rows(real_app, "SELECT * FROM log_appeals WHERE id = :i", i=appeal["id"])[0]
    assert (settled["status"], settled["decided_by"], settled["reversal_entry_id"]) == ("reversed", gm.id, undo["id"])
    told = notices_of(real_app, author.id)[-1]
    assert "was accepted, and the decision was reversed" in told and "You are right, sorry" in told
    redo = post(gm.browser(), gm, f"/+{name}/mod/appeals/{appeal['id']}", outcome="upheld", note="second thoughts")
    assert "already%20decided" in redo.headers["Location"]
    after = get(mine, f"/log/{eid}/appeal")                                                           # their appeal's outcome stays on the page
    assert after.status_code == 200 and "was accepted, and the decision was reversed" in after.get_data(as_text=True)
    assert "Why was it wrong?" not in after.get_data(as_text=True)


def test_a_guildmaster_who_made_the_decision_does_not_decide_its_appeal_while_another_could_and_a_decision_needs_its_note(make_member, real_app):
    board_id, name, gm, author, post_id, b36, entry = hide(real_app, make_member)
    other = guildmaster(make_member, board_id)
    assert post(author.browser(), author, f"/log/{entry['id']}/appeal", reason="no").status_code == 303
    (appeal,) = rows(real_app, "SELECT id FROM log_appeals WHERE entry_id = :e", e=entry["id"])
    mine = post(gm.browser(), gm, f"/+{name}/mod/appeals/{appeal['id']}", outcome="reversed", note="I changed my mind")
    assert "another%20can%20decide" in mine.headers["Location"]
    assert rows(real_app, "SELECT status FROM log_appeals WHERE id = :i", i=appeal["id"])[0]["status"] == "open"
    assert "You made this decision" in get(gm.browser(), f"/+{name}/mod/appeals").get_data(as_text=True)
    bare = post(other.browser(), other, f"/+{name}/mod/appeals/{appeal['id']}", outcome="upheld", note="")
    assert "say%20why" in bare.headers["Location"]
    decided = post(other.browser(), other, f"/+{name}/mod/appeals/{appeal['id']}", outcome="upheld", note="The rule is clear")
    assert "Decided" in decided.headers["Location"]
    assert rows(real_app, "SELECT hidden_by_guild FROM submissions WHERE id = :p", p=post_id)[0]["hidden_by_guild"] is True       # it stands
    assert "The rule is clear" in notices_of(real_app, author.id)[-1]


def test_a_guildmaster_without_the_permission_neither_sees_nor_decides_an_appeal_of_that_kind(make_member, real_app):
    board_id, name, gm, author, post_id, b36, entry = hide(real_app, make_member)
    access_only = guildmaster(make_member, board_id, perm_full=False, perm_content=False, perm_access=True)
    assert post(author.browser(), author, f"/log/{entry['id']}/appeal", reason="no").status_code == 303
    (appeal,) = rows(real_app, "SELECT id FROM log_appeals WHERE entry_id = :e", e=entry["id"])
    assert "no" not in re.findall(r'<blockquote[^>]*>(.*?)</blockquote>', get(access_only.browser(), f"/+{name}/mod/appeals").get_data(as_text=True), re.S)
    assert post(access_only.browser(), access_only, f"/+{name}/mod/appeals/{appeal['id']}", outcome="upheld", note="x").status_code == 404


def test_a_suspended_person_appeals_the_ban_an_admin_gave_and_a_different_admin_decides_it(make_member, real_app):
    banning, deciding = admin_of(make_member), admin_of(make_member)
    victim = make_member()
    assert post(banning.browser(), banning, f"/api/ban_user/{victim.id}", days="0", reason="Posting spam links", message="That was spam.",
                rule="evasion").status_code in (200, 302)
    (entry,) = entries(real_app, target_user_id=victim.id, action="ban_user")
    assert entry["notice_sent"] is True
    told = notices_of(real_app, victim.id)
    assert any("permanently suspended" in t for t in told) and any(f"/log/{entry['id']}/appeal" in t for t in told)      # the way to appeal, beside it
    own = get(victim.browser(), f"/@{victim.username}").get_data(as_text=True)
    assert 'id="own-suspension"' in own and f"/log/{entry['id']}/appeal" in own                                           # on their own profile
    assert f"/log/{entry['id']}/appeal" not in get(make_member().browser(), f"/@{victim.username}").get_data(as_text=True)
    assert f"/log/{entry['id']}/appeal" not in get(real_app[0].test_client(), f"/@{victim.username}").get_data(as_text=True)
    assert get(real_app[0].test_client(), f"/@{victim.username}").status_code in (200, 404)

    assert post(victim.browser(), victim, f"/log/{entry['id']}/appeal", reason="That was not spam").status_code == 303        # a suspended account can
    (appeal,) = rows(real_app, "SELECT id, board_id FROM log_appeals WHERE entry_id = :e", e=entry["id"])
    assert appeal["board_id"] is None
    mine = get(banning.browser(), "/admin/appeals").get_data(as_text=True)
    assert "That was not spam" in mine and "You made this decision" in mine and f'action="/admin/appeals/{appeal["id"]}"' not in mine
    assert "another%20can%20decide" in post(banning.browser(), banning, f"/admin/appeals/{appeal['id']}", outcome="reversed", note="ok").headers["Location"]
    theirs = get(deciding.browser(), "/admin/appeals").get_data(as_text=True)
    assert f'action="/admin/appeals/{appeal["id"]}"' in theirs and "@" + victim.username in theirs

    assert "Decided" in post(deciding.browser(), deciding, f"/admin/appeals/{appeal['id']}", outcome="reversed",
                             note="We looked again").headers["Location"]
    assert rows(real_app, "SELECT is_banned FROM users WHERE id = :u", u=victim.id)[0]["is_banned"] == 0
    (undo,) = entries(real_app, target_user_id=victim.id, action="unban_user")
    assert undo["linked_entry_id"] == entry["id"] and undo["audience"] == 0 and undo["actor_role"] == "admin"
    assert "was accepted" in notices_of(real_app, victim.id)[-1]
    assert get(real_app[0].test_client(), f"/log/{entry['id']}").status_code == 200                                         # one entry, openable by anyone


def test_the_entry_page_follows_the_logs_visibility_and_offers_the_appeal_only_to_the_person(make_member, real_app):
    board_id, name, gm, author, post_id, b36, entry = hide(real_app, make_member)
    page = get(real_app[0].test_client(), f"/log/{entry['id']}")
    assert page.status_code == 200 and "Advertising" in page.get_data(as_text=True) and "Appeal this decision" not in page.get_data(as_text=True)
    assert "Appeal this decision" in get(author.browser(), f"/log/{entry['id']}").get_data(as_text=True)
    assert "Appeal this decision" not in get(make_member().browser(), f"/log/{entry['id']}").get_data(as_text=True)
    staff = admin_of(make_member, 4)
    note = entries(real_app, id=post_staff_entry(real_app, staff, make_member)["id"])[0]
    assert get(real_app[0].test_client(), f"/log/{note['id']}").status_code == 404                                         # staff-only: not found
    assert get(staff.browser(), f"/log/{note['id']}").status_code == 200
    assert get(real_app[0].test_client(), "/log/99999999").status_code == 404


def post_staff_entry(real_app, admin, make_member):
    domain = f"ci-trail-{secrets.token_hex(3)}.test"
    answer = post(admin.browser(), admin, "/admin/ban_domain", domain=domain, reason="1")
    assert answer.status_code in (200, 302), answer.get_data(as_text=True)[:200]
    (entry,) = [e for e in entries(real_app, action="admin_domain") if domain in e["staff_note"]]
    return entry


# --- what the site decided by itself --------------------------------------------------------------------------------------------------

def test_an_image_checks_ban_goes_through_user_ban_in_generic_words_with_the_match_as_a_level_4_entry_and_the_alts_staff_only(make_member, real_app):
    from ruqqus.classes import Alt, User
    from ruqqus.helpers import action_log, scan_bans
    _, db_session = real_app
    uploader, alt, bystander = make_member(), make_member(), make_member()
    db = db_session()
    db.rollback()
    db.add(Alt(user1=uploader.id, user2=alt.id))
    db.commit()
    scan = db_session()
    entry = scan_bans.ban_uploader(scan, scan.get(User, uploader.id), days=0, detail="the CDN answered 451 for an uploaded picture", legal=True)
    scan.commit()
    assert entry
    for account in (uploader, alt):
        row = rows(real_app, "SELECT is_banned, ban_reason, unban_utc FROM users WHERE id = :u", u=account.id)[0]
        assert row["is_banned"] and row["ban_reason"] == "Automatic image check" and row["unban_utc"] == 0
    assert not rows(real_app, "SELECT is_banned FROM users WHERE id = :u", u=bystander.id)[0]["is_banned"]
    public, detail = entries(real_app, id=entry)[0], entries(real_app, linked_entry_id=entry)[0]
    assert (public["actor_role"], public["actor_id"], public["rule_key"], public["reason"], public["audience"], public["action"]) == (
        "system", action_log.SITE_ACCOUNT_ID, "illegal", "Automatic image check", 0, "ban_user")
    assert (detail["action"], detail["audience"], detail["staff_note"]) == ("suppress_content", action_log.LEGAL, "the CDN answered 451 for an uploaded picture")
    (alt_entry,) = entries(real_app, target_user_id=alt.id, action="ban_user")
    assert alt_entry["audience"] == action_log.STAFF and f"alt of @{uploader.username}" in alt_entry["staff_note"]
    html = get(real_app[0].test_client(), "/log?type=sanction").get_data(as_text=True)
    assert "Automatic image check" in html and "451" not in html and f"@{alt.username}" not in html
    profile = get(real_app[0].test_client(), f"/@{uploader.username}").get_data(as_text=True)
    assert "Sexualizing" not in profile and "Automatic image check" in profile


def test_a_timed_image_ban_keeps_its_end_and_a_removal_by_the_check_is_public_with_the_match_for_level_4_only(make_member, real_app):
    from ruqqus.classes import User
    from ruqqus.helpers import action_log, scan_bans
    _, db_session = real_app
    uploader = make_member()
    scan = db_session()
    entry = scan_bans.ban_uploader(scan, scan.get(User, uploader.id), days=3, detail="matched a picture on the site's banned list: Spam picture")
    board_id, _name = guild(real_app)
    post_id, _b36 = add_post(uploader, board_id)
    removal = action_log.record_image_check_removal(scan, post_id=post_id, board_id=board_id, detail="matched a picture on the site's banned list: Spam picture")
    scan.commit()
    ban = entries(real_app, id=entry)[0]
    assert ban["action"] == "suspend_user" and abs(ban["until_utc"] - (int(time.time()) + 3 * 86400)) < 30
    assert entries(real_app, linked_entry_id=entry)[0]["audience"] == action_log.STAFF                  # the site's own list: staff, not level 4
    (removed,) = entries(real_app, id=removal)
    assert (removed["action"], removed["audience"], removed["reason"], removed["actor_role"]) == ("ban_post", 0, "Automatic image check", "system")
    public = get(real_app[0].test_client(), f"/log?guild={_name}").get_data(as_text=True)
    assert "Automatic image check" in public and "Spam picture" not in public


def test_the_spam_guards_removal_reads_as_the_generic_spam_check(make_member, real_app):
    from ruqqus.classes import ModAction
    _, db_session = real_app
    board_id, name = guild(real_app)
    spammer = make_member()
    post_id, _ = add_post(spammer, board_id)
    db = db_session()
    db.add(ModAction(user_id=1, target_submission_id=post_id, kind="ban_post", board_id=board_id, note="spam"))
    db.commit()
    (entry,) = entries(real_app, board_id=board_id, target_submission_id=post_id)
    assert (entry["rule_key"], entry["reason"], entry["actor_role"]) == ("evasion", "Automatic spam check", "system")


# --- what the staff do to run the site -----------------------------------------------------------------------------------------------

def test_what_the_staff_do_to_run_the_site_is_a_staff_only_entry_and_never_on_the_public_log(make_member, real_app):
    level4, level3 = admin_of(make_member, 4), admin_of(make_member)
    word = "".join(secrets.choice("bcdfghjklmnpqrstvwxz") for _ in range(9))        # letters only: the list stores a word as it is matched
    assert post(level4.browser(), level4, "/admin/word_filter/add", word=word, severity="1").status_code in (200, 302)
    _, db_session = real_app
    try:
        (made,) = [e for e in entries(real_app, action="admin_word_filter") if word in e["staff_note"]]
    finally:
        # a table with one row in it stops the built-in word list applying (helpers/word_filter_store.py): give the other tests' database back as it was
        db = db_session()
        db.execute(text("DELETE FROM word_filter_entries WHERE word = :w"), {"w": word})
        db.commit()
        from ruqqus.helpers.word_filter_store import get_filter
        get_filter(db, force=True)
    assert (made["audience"], made["actor_role"], made["actor_id"], made["reason"]) == (2, "admin", level4.id, "")
    assert word not in get(real_app[0].test_client(), "/log?type=guild").get_data(as_text=True)
    assert word in get(level3.browser(), "/admin/log?action=admin_word_filter").get_data(as_text=True)
    entry = post_staff_entry(real_app, level4, make_member)
    assert entry["audience"] == 2
    assert "admin_domain" not in get(real_app[0].test_client(), "/log").get_data(as_text=True)


def test_the_admin_page_counts_appeals_and_says_when_the_paper_trail_has_gaps(make_member, real_app):
    admin = admin_of(make_member)
    home = get(admin.browser(), "/admin").get_data(as_text=True)
    assert "/admin/appeals" in home and "waiting" in home
    _, db_session = real_app
    db = db_session()
    db.execute(text("INSERT INTO log_failures (created_utc, action, detail) VALUES (:now, 'ban_user', 'the table was locked')"), {"now": int(time.time())})
    db.commit()
    try:
        assert "The paper trail has gaps" in get(admin.browser(), "/admin/log").get_data(as_text=True)
        assert "could not be written" in get(admin.browser(), "/admin").get_data(as_text=True)
    finally:
        db.execute(text("DELETE FROM log_failures"))
        db.commit()
    assert "The paper trail has gaps" not in get(admin.browser(), "/admin/log").get_data(as_text=True)


def test_a_mod_log_row_whose_copy_failed_is_copied_by_the_upkeep_and_counted_until_then(make_member, real_app):
    from ruqqus.helpers import action_log
    _, db_session = real_app
    admin = admin_of(make_member)
    board_id, _name = guild(real_app)
    db = db_session()
    old = int(time.time()) - 3600
    made = db.execute(text("INSERT INTO modactions (user_id, board_id, kind, target_submission_id, target_user_id, target_comment_id, _note, created_utc) "
                           "VALUES (1, :b, 'hide_post_from_guild', 0, 0, 0, 'copy me', :t) RETURNING id"), {"b": board_id, "t": old}).first()[0]
    db.commit()
    assert not entries(real_app, legacy_modaction_id=made)
    assert action_log.unmirrored_count(db) >= 1
    assert "still ha" in get(admin.browser(), "/admin/log").get_data(as_text=True)
    copied, failed = action_log.reconcile(db)
    db.commit()
    assert copied >= 1 and failed == 0
    (entry,) = entries(real_app, legacy_modaction_id=made)
    assert (entry["action"], entry["board_id"]) == ("hide_post_from_guild", board_id)
    assert action_log.reconcile(db) == (0, 0)


def test_a_timed_suspension_says_when_it_ran_out_once(make_member, real_app):
    from ruqqus.helpers import action_log
    _, db_session = real_app
    admin, victim = admin_of(make_member), make_member()
    assert post(admin.browser(), admin, f"/api/ban_user/{victim.id}", days="1", reason="Too much shouting", rule="good_faith").status_code in (200, 302)
    (entry,) = entries(real_app, target_user_id=victim.id, action="suspend_user")
    assert abs(entry["until_utc"] - (int(time.time()) + 86400)) < 30
    assert "until" in get(real_app[0].test_client(), "/log?type=sanction").get_data(as_text=True)
    db = db_session()
    past = int(time.time()) - 100
    db.execute(text("UPDATE log_entries SET until_utc = :p WHERE id = :e"), {"p": past, "e": entry["id"]})
    db.execute(text("UPDATE users SET unban_utc = :p WHERE id = :u"), {"p": past, "u": victim.id})
    db.commit()
    assert action_log.lapse_due(db) >= 1
    db.commit()
    (lapse,) = entries(real_app, linked_entry_id=entry["id"])
    assert (lapse["action"], lapse["actor_role"], lapse["audience"], lapse["target_user_id"]) == ("unsuspend_user", "system", 0, victim.id)
    assert action_log.lapse_due(db) == 0
    assert "lifted the suspension" in get(real_app[0].test_client(), "/log?type=sanction").get_data(as_text=True)


def test_a_level_4_admin_can_hide_an_entrys_reason_and_it_is_a_new_linked_entry_not_an_edit(make_member, real_app):
    level4, level3, victim = admin_of(make_member, 4), admin_of(make_member), make_member()
    assert post(level3.browser(), level3, f"/api/ban_user/{victim.id}", days="0", reason="PRIVATE-DETAIL-TEXT", rule="evasion").status_code in (200, 302)
    (entry,) = entries(real_app, target_user_id=victim.id, action="ban_user")
    assert "PRIVATE-DETAIL-TEXT" in get(real_app[0].test_client(), "/log?type=sanction").get_data(as_text=True)
    assert post(level3.browser(), level3, f"/admin/log/{entry['id']}/redact", why="slipped in").status_code == 403           # level 4 only
    assert post(level4.browser(), level4, f"/admin/log/{entry['id']}/redact", why="").status_code == 303
    assert entries(real_app, id=entry["id"])[0]["redacted_utc"] == 0                                                         # a reason is needed
    assert post(level4.browser(), level4, f"/admin/log/{entry['id']}/redact", why="a private detail slipped into it").status_code == 303
    assert "PRIVATE-DETAIL-TEXT" not in get(real_app[0].test_client(), "/log?type=sanction").get_data(as_text=True)
    assert "PRIVATE-DETAIL-TEXT" in get(level3.browser(), f"/admin/log?target={victim.username}").get_data(as_text=True)     # staff still read it
    (note,) = entries(real_app, linked_entry_id=entry["id"], action="redact_entry")
    assert (note["audience"], note["actor_id"], note["staff_note"]) == (2, level4.id, "a private detail slipped into it")
    assert entries(real_app, id=entry["id"])[0]["reason"] == "PRIVATE-DETAIL-TEXT"                                          # never edited


# --- deleting an account ----------------------------------------------------------------------------------------------------------------

def test_deleting_an_account_writes_the_exile_the_seat_and_the_approval_it_ends(make_member, real_app):
    from ruqqus.classes import BanRelationship, ContributorRelationship
    _, db_session = real_app
    leaver = make_member()
    exiled_from, _ = guild(real_app)
    led, _ = guild(real_app)
    approved_in, _ = guild(real_app)
    db = db_session()
    db.execute(text("UPDATE users SET creation_ip = :ip WHERE id = :u"), {"ip": address(), "u": leaver.id})
    db.add(BanRelationship(user_id=leaver.id, board_id=exiled_from, banning_mod_id=1, is_active=True))
    db.add(ContributorRelationship(user_id=leaver.id, board_id=approved_in, is_active=True))
    db.commit()
    from ruqqus.classes import ModRelationship
    db.add(ModRelationship(user_id=leaver.id, board_id=led, accepted=True, invite_rescinded=False, perm_full=True))
    db.commit()
    gone = post(leaver.browser(), leaver, "/settings/delete_account", password=leaver.password)
    assert gone.status_code == 200, gone.get_data(as_text=True)[:300]
    made = {(e["action"], e["board_id"]): e for e in entries(real_app, target_user_id=leaver.id)}
    assert set(made) == {("unexile_user", exiled_from), ("remove_mod", led), ("uncontrib_user", approved_in)}
    assert all(e["actor_role"] == "system" and e["reason"] == "Its account was deleted." and e["audience"] == 0 for e in made.values())


def test_a_guild_that_loses_its_last_guildmaster_with_the_account_is_opened_up_in_the_log(make_member, real_app):
    from ruqqus.classes import ModRelationship
    _, db_session = real_app
    leader = make_member()
    board_id, name = guild(real_app, private=True)
    db = db_session()
    db.execute(text("UPDATE users SET creation_ip = :ip WHERE id = :u"), {"ip": address(), "u": leader.id})
    db.add(ModRelationship(user_id=leader.id, board_id=board_id, accepted=True, invite_rescinded=False, perm_full=True))
    db.commit()
    assert post(leader.browser(), leader, "/settings/delete_account", password=leader.password).status_code == 200
    assert rows(real_app, "SELECT is_private FROM boards WHERE id = :b", b=board_id)[0]["is_private"] is False
    (opened,) = entries(real_app, board_id=board_id, action="guild_made_public")
    assert (opened["actor_role"], opened["audience"], opened["rule_key"]) == ("system", 0, "")
    assert "opened +" + name + " to everyone" in get(real_app[0].test_client(), f"/log?type=guild&guild={name}").get_data(as_text=True)


# --- the smaller things the audit listed ------------------------------------------------------------------------------------------

def test_a_held_sign_up_is_closed_as_banned_only_when_the_admin_could_ban_the_account(make_member, real_app):
    """S6: `decide(..., "banned")` ran before the check that the admin outranks the account, so an account the ban then skipped had its review
    closed as banned all the same."""
    _, db_session = real_app
    admin, peer, lower = admin_of(make_member), admin_of(make_member), make_member()
    db = db_session()
    for member in (peer, lower):
        db.execute(text("UPDATE users SET quarantined_utc = :now WHERE id = :u"), {"now": int(time.time()), "u": member.id})
        db.execute(text("INSERT INTO signup_reviews (user_id, created_utc, score, reasons, network, name_shape, decision) "
                        "VALUES (:u, :now, 60, 'fast', 'n', 's', '')"), {"u": member.id, "now": int(time.time())})
    db.commit()
    answer = post(admin.browser(), admin, "/admin/signups/decide", decision="ban", uid=[peer.id, lower.id])
    assert answer.status_code == 302
    review = {r["user_id"]: r["decision"] for r in rows(real_app, "SELECT user_id, decision FROM signup_reviews WHERE user_id IN (:a, :b)", a=peer.id, b=lower.id)}
    assert review == {peer.id: "", lower.id: "banned"}
    assert rows(real_app, "SELECT is_banned FROM users WHERE id = :u", u=lower.id)[0]["is_banned"] and not rows(real_app, "SELECT is_banned FROM users WHERE id = :u", u=peer.id)[0]["is_banned"]


def test_an_appeals_words_are_in_the_export_and_leave_with_the_account(make_member, real_app):
    from ruqqus.helpers import data_export
    _, db_session = real_app
    board_id, name, gm, author, post_id, b36, entry = hide(real_app, make_member)
    assert post(author.browser(), author, f"/log/{entry['id']}/appeal", reason="PRIVATE-APPEAL-WORDS").status_code == 303
    db = db_session()
    exported = data_export.build(db, author.id)
    assert [(a["entry_id"], a["reason"], a["status"]) for a in exported["appeals"]] == [(entry["id"], "PRIVATE-APPEAL-WORDS", "open")]
    assert post(author.browser(), author, "/settings/delete_account", password=author.password).status_code == 200
    (left,) = rows(real_app, "SELECT reason, decision_note, status FROM log_appeals WHERE entry_id = :e", e=entry["id"])
    assert (left["reason"], left["decision_note"]) == ("", "")


def test_the_appeals_words_are_emptied_after_a_decision_and_the_notes_of_old_failures_go(make_member, real_app):
    from ruqqus.helpers import retention
    _, db_session = real_app
    board_id, name, gm, author, post_id, b36, entry = hide(real_app, make_member)
    db = db_session()
    now = int(time.time())
    db.execute(text("INSERT INTO log_appeals (entry_id, user_id, board_id, reason, created_utc, status, decided_by, decided_utc, decision_note) "
                    "VALUES (:e, :u, :b, 'old words', :t, 'upheld', 1, :d, 'old reply')"),
               {"e": entry["id"], "u": author.id, "b": board_id, "t": now - 200 * 86400, "d": now - 100 * 86400})
    db.execute(text("INSERT INTO log_failures (created_utc, action) VALUES (:old, 'a'), (:new, 'b')"), {"old": now - 100 * 86400, "new": now})
    db.commit()
    done = retention.prune(db, now)
    assert done["words of decided appeals"] >= 1 and done["old notes of entries the Log could not write"] >= 1
    assert rows(real_app, "SELECT reason, decision_note, status FROM log_appeals WHERE entry_id = :e", e=entry["id"])[0] == {
        "reason": "", "decision_note": "", "status": "upheld"}                                                       # the outcome stays, the words go
    assert [r["action"] for r in rows(real_app, "SELECT action FROM log_failures")] == ["b"]
    db.execute(text("DELETE FROM log_failures"))
    db.commit()
