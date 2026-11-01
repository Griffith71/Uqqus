"""The public Log on the real app and Postgres (slice B of helpers/action_log.py; the rules and the SQL are tested without Postgres in
tests/test_action_log.py and tests/test_log_view.py). The promises, end to end:

- /log shows who (by role), when, where, which rule and the reason of a hide or a removal, and never a title or text; it is the same
  page for a visitor and for an admin, names no admin and no private guild, and /log/deleted only redirects to it;
- what an author deletes is not published: the text is a private copy for the author and the admins, the history row holds none, and
  the history of a post a guild hid shows the reason but not the words;
- a guild's guildmasters, the item's author and the admins open a preserved copy; everyone else gets a 404;
- an edit carries a summary, and an admin can hide one version of an item's history, which is logged;
- a private guild's log is a 404 for non-members and its entries never reach the public page;
- an admin must give a reason to remove a post or a comment."""
import re
import secrets
import time

from sqlalchemy import text

BASE = "http://localhost"
HEADERS = {"User-Agent": "ci-log-public"}


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
    board = Board(name="ci_logp_" + secrets.token_hex(3), description="a guild for the log test",
                  description_html="<p>a guild for the log test</p>", creator_id=1, subcat_id=0, is_private=private,
                  created_utc=int(time.time()), color="23a9f2")
    db.add(board)
    db.commit()
    return board.id, board.name


def guildmaster(make_member, board_id):
    from ruqqus.classes import ModRelationship
    gm = make_member()
    db = gm.db_session()
    # its own address: the guild limit counts the guilds led from one network, and every test account shares 127.0.0.1
    db.execute(text("UPDATE users SET creation_ip = :ip WHERE id = :u"), {"ip": address(), "u": gm.id})
    db.add(ModRelationship(user_id=gm.id, board_id=board_id, accepted=True, invite_rescinded=False, perm_full=True, perm_content=True,
                           perm_access=True, perm_config=True))
    db.commit()
    return gm


def add_post(member, board_id, title, body, age=3600, anonymous=False, public=True):
    from ruqqus.classes import Submission, SubmissionAux
    from ruqqus.helpers.base36 import base36encode
    db = member.db_session()
    db.rollback()
    with member.app.test_request_context("/"):
        row = Submission(author_id=member.id, board_id=board_id, original_board_id=board_id, created_utc=int(time.time()) - age,
                         post_public=public, is_anonymous=anonymous)
    db.add(row)
    db.flush()
    db.add(SubmissionAux(id=row.id, title=title, body=re.sub("<[^>]+>", "", body), body_html=body))
    db.commit()
    return row.id, base36encode(row.id)


def entries(real_app, **where):
    _, db_session = real_app
    db = db_session()
    db.expire_all()
    clause = " AND ".join(f"{k} = :{k}" for k in where) or "true"
    return [dict(r._mapping) for r in db.execute(text(f"SELECT * FROM log_entries WHERE {clause} ORDER BY id"), where)]


def snapshots(real_app, **where):
    _, db_session = real_app
    db = db_session()
    db.expire_all()
    clause = " AND ".join(f"{k} = :{k}" for k in where) or "true"
    return [dict(r._mapping) for r in db.execute(text(f"SELECT * FROM log_snapshots WHERE {clause} ORDER BY id"), where)]


def hide(real_app, make_member, title="SECRET-TITLE hidden words", body="<p>SECRET-BODY</p>"):
    """A post by a member in a new public guild, hidden by that guild's guildmaster with a reason and a rule."""
    board_id, name = guild(real_app)
    gm, author = guildmaster(make_member, board_id), make_member()
    post_id, b36 = add_post(author, board_id, title, body)
    here = gm.browser()
    answer = post(here, gm, f"/mod/kick/{name}/{b36}", reason="Advertising", rule="good_faith")
    assert answer.status_code == 200, answer.get_data(as_text=True)[:300]
    return board_id, name, gm, author, post_id, b36


# --- the public page ---------------------------------------------------------------------------------------------------------------

def test_a_hide_is_public_with_who_where_the_rule_and_the_reason_and_never_the_words(make_member, real_app):
    board_id, name, gm, author, post_id, b36 = hide(real_app, make_member)
    (row,) = entries(real_app, board_id=board_id, target_submission_id=post_id)
    assert (row["action"], row["rule_key"], row["reason"], row["actor_role"], row["audience"]) == (
        "hide_post_from_guild", "good_faith", "Advertising", "guildmaster", 0)
    page = get(real_app[0].test_client(), f"/log?guild={name}")
    html = page.get_data(as_text=True)
    assert page.status_code == 200
    assert f"@{gm.username}" in html and name in html and "Advertising" in html and "Free expression and good faith" in html
    assert f"#{b36}" in html and "SECRET-TITLE" not in html and "SECRET-BODY" not in html          # the post is named by id only
    assert f"@{author.username}" not in html


def test_the_public_page_is_the_same_for_an_admin_and_names_no_admin(make_member, real_app):
    admin, member = admin_of(make_member), make_member()
    board_id, name = guild(real_app)
    post_id, b36 = add_post(member, board_id, "ADMIN-TITLE", "<p>words</p>")
    here = admin.browser()
    assert post(here, admin, f"/api/ban_post/{b36}", reason="Spam links", rule="evasion").status_code in (200, 302)
    (row,) = entries(real_app, board_id=board_id, target_submission_id=post_id)
    assert (row["actor_role"], row["actor_id"], row["reason"], row["rule_key"]) == ("admin", admin.id, "Spam links", "evasion")
    seen = {}
    for who, client in (("visitor", real_app[0].test_client()), ("admin", admin.browser())):
        html = get(client, f"/log?guild={name}").get_data(as_text=True)
        listing = "".join(re.findall(r'<div class="card mb-2 p-3 log-entry".*?</div>\s*</div>\s*</div>', html, re.S))   # the lines, not the navbar
        assert "Spam links" in listing and "ADMIN-TITLE" not in listing and f"@{admin.username}" not in listing, who
        seen[who] = listing
    assert seen["visitor"].count("log-entry") == seen["admin"].count("log-entry") == 1
    by = get(real_app[0].test_client(), f"/log?guild={name}&by={admin.username}").get_data(as_text=True)
    assert 'class="card mb-2 p-3 log-entry"' not in by                                                                  # filtering by an admin finds nothing


def test_a_private_guilds_decisions_are_never_on_the_public_page_and_its_log_is_a_404_for_outsiders(make_member, real_app):
    board_id, name = guild(real_app, private=True)
    gm, outsider = guildmaster(make_member, board_id), make_member()
    from ruqqus.classes import ContributorRelationship
    member = make_member()
    db = member.db_session()
    db.add(ContributorRelationship(user_id=member.id, board_id=board_id, is_active=True))
    db.commit()
    post_id, b36 = add_post(member, board_id, "PRIVATE-TITLE", "<p>words</p>")
    assert post(gm.browser(), gm, f"/mod/kick/{name}/{b36}", reason="Private reason").status_code == 200
    (row,) = entries(real_app, board_id=board_id)
    assert row["audience"] == 1
    for client in (real_app[0].test_client(), outsider.browser()):
        assert "Private reason" not in get(client, f"/log?guild={name}").get_data(as_text=True)
        assert "Private reason" not in get(client, "/log").get_data(as_text=True)
        assert get(client, f"/+{name}/mod/log").status_code == 404
        assert get(client, f"/log/{row['id']}/copy").status_code == 404
    insider = get(member.browser(), f"/+{name}/mod/log")
    assert insider.status_code == 200 and "Private reason" in insider.get_data(as_text=True)


def test_the_old_deletion_log_address_only_redirects(real_app):
    answer = get(real_app[0].test_client(), "/log/deleted")
    assert answer.status_code == 301 and answer.headers["Location"].endswith("/log")


# --- the guild's own log ------------------------------------------------------------------------------------------------------------

def test_a_public_guilds_log_names_a_hidden_post_by_id_to_the_public_and_by_title_to_its_guildmasters(make_member, real_app):
    board_id, name, gm, author, post_id, b36 = hide(real_app, make_member, title="GM-ONLY-TITLE")
    public = get(real_app[0].test_client(), f"/+{name}/mod/log").get_data(as_text=True)
    assert "GM-ONLY-TITLE" not in public and f"#{b36}" in public and "Advertising" in public
    insider = get(gm.browser(), f"/+{name}/mod/log").get_data(as_text=True)
    assert "GM-ONLY-TITLE" in insider
    assert "Free expression and good faith" in public                                              # the rule is on the entry


# --- what an author deletes ----------------------------------------------------------------------------------------------------------------

def test_an_authors_delete_is_a_private_copy_for_the_author_and_the_admins_and_is_nowhere_public(make_member, real_app):
    board_id, name = guild(real_app)
    author, other, admin = make_member(), make_member(), admin_of(make_member)
    post_id, b36 = add_post(author, board_id, "DELETED-TITLE", "<p>DELETED-WORDS</p>")
    here = author.browser()
    assert post(here, author, f"/delete_post/{b36}").status_code in (200, 204, 302)
    (snap,) = snapshots(real_app, target_submission_id=post_id)
    assert (snap["author_id"], snap["title"], snap["scrubbed_utc"]) == (author.id, "DELETED-TITLE", 0)
    assert snap["expires_utc"] - snap["created_utc"] == 90 * 86400
    _, db_session = real_app
    db = db_session()
    db.expire_all()
    row = db.execute(text("SELECT previous_title, previous_body, previous_body_html FROM content_edit_history "
                          "WHERE target_submission_id = :p AND action = 'delete'"), {"p": post_id}).first()
    assert tuple(row) == (None, None, None)                                                         # the public history row holds no text
    assert entries(real_app, target_submission_id=post_id) == []                                    # a self-delete is not a decision: not in the Log
    for client in (real_app[0].test_client(), other.browser()):
        for path in ("/log", f"/post/{b36}/history", f"/post/{b36}/copy"):
            body = get(client, path)
            assert "DELETED-WORDS" not in body.get_data(as_text=True) and "DELETED-TITLE" not in body.get_data(as_text=True), path
        assert get(client, f"/post/{b36}/copy").status_code == 404
    for member in (author, admin):
        copy = get(member.browser(), f"/post/{b36}/copy")
        assert copy.status_code == 200 and "DELETED-WORDS" in copy.get_data(as_text=True)
        assert "no-store" in copy.headers["Cache-Control"]


def test_a_comment_delete_is_private_in_the_same_way(make_member, real_app):
    from ruqqus.classes import Comment, CommentAux
    board_id, name = guild(real_app)
    author = make_member()
    post_id, b36 = add_post(author, board_id, "A post", "<p>words</p>")
    db = author.db_session()
    with author.app.test_request_context("/"):
        row = Comment(author_id=author.id, parent_submission=post_id, level=1, original_board_id=board_id, created_utc=int(time.time()) - 3600)
    db.add(row)
    db.flush()
    comment_id = row.id
    db.add(CommentAux(id=comment_id, body="COMMENT-WORDS", body_html="<p>COMMENT-WORDS</p>"))
    db.commit()
    from ruqqus.helpers.base36 import base36encode
    c36 = base36encode(comment_id)
    assert post(author.browser(), author, f"/delete/comment/{c36}").status_code in (200, 204, 302)
    (snap,) = snapshots(real_app, target_comment_id=comment_id)
    assert snap["body_html"] == "<p>COMMENT-WORDS</p>" and snap["author_id"] == author.id
    assert "COMMENT-WORDS" not in get(real_app[0].test_client(), f"/comment/{c36}/history").get_data(as_text=True)
    assert get(real_app[0].test_client(), f"/comment/{c36}/copy").status_code == 404
    assert "COMMENT-WORDS" in get(author.browser(), f"/comment/{c36}/copy").get_data(as_text=True)


# --- preserved copies of a hide --------------------------------------------------------------------------------------------------------------

def test_a_guilds_guildmaster_the_author_and_an_admin_open_the_copy_of_a_hide_and_nobody_else(make_member, real_app):
    board_id, name, gm, author, post_id, b36 = hide(real_app, make_member, title="COPY-TITLE", body="<p>COPY-WORDS</p>")
    (row,) = entries(real_app, board_id=board_id, target_submission_id=post_id)
    other_gm = guildmaster(make_member, guild(real_app)[0])
    stranger, admin = make_member(), admin_of(make_member)
    for member in (gm, author, admin):
        copy = get(member.browser(), f"/log/{row['id']}/copy")
        assert copy.status_code == 200, member.username
        assert "COPY-TITLE" in copy.get_data(as_text=True) and "COPY-WORDS" in copy.get_data(as_text=True)
    for client in (real_app[0].test_client(), stranger.browser(), other_gm.browser()):
        assert get(client, f"/log/{row['id']}/copy").status_code == 404
    log = get(gm.browser(), f"/log?guild={name}").get_data(as_text=True)
    assert f"/log/{row['id']}/copy" in log                                                            # the link only where it opens
    assert f"/log/{row['id']}/copy" not in get(stranger.browser(), f"/log?guild={name}").get_data(as_text=True)
    history = get(gm.browser(), f"/post/{b36}/history")
    assert "COPY-WORDS" not in history.get_data(as_text=True) and f"/post/{b36}/copy" in history.get_data(as_text=True)


def test_a_scrubbed_copy_is_gone_for_everyone(make_member, real_app):
    board_id, name, gm, author, post_id, b36 = hide(real_app, make_member, body="<p>GONE-WORDS</p>")
    from ruqqus.helpers import retention
    _, db_session = real_app
    db = db_session()
    db.execute(text("UPDATE log_snapshots SET expires_utc = 1 WHERE target_submission_id = :p"), {"p": post_id})
    db.commit()
    assert retention.prune(db, int(time.time()))["private copies past their date"] >= 1
    (snap,) = snapshots(real_app, target_submission_id=post_id)
    assert snap["body_html"] is None and snap["scrubbed_utc"] > 0
    assert get(author.browser(), f"/post/{b36}/copy").status_code == 404
    assert entries(real_app, board_id=board_id)                                                       # the entry itself stays


# --- edits ------------------------------------------------------------------------------------------------------------------------------------

def test_an_edit_carries_a_summary_and_an_admin_can_hide_one_version_with_a_logged_reason(make_member, real_app):
    board_id, name = guild(real_app)
    author, admin = make_member(), admin_of(make_member)
    post_id, b36 = add_post(author, board_id, "First title", "<p>FIRST-VERSION</p>")
    # a profile post is edited through the same route; here the guild board is enough for the history page
    here = author.browser()
    edit = post(here, author, f"/edit_post/{b36}", title="Second title", body="SECOND-VERSION", edit_summary="Fixed the wording")
    assert edit.status_code in (200, 302), edit.get_data(as_text=True)[:200]
    page = get(real_app[0].test_client(), f"/post/{b36}/history").get_data(as_text=True)
    assert "Fixed the wording" in page and "First title" in page
    _, db_session = real_app
    db = db_session()
    db.expire_all()
    revision = db.execute(text("SELECT id, summary FROM content_edit_history WHERE target_submission_id = :p AND action = 'edit'"),
                          {"p": post_id}).first()
    assert revision[1] == "Fixed the wording"
    refused = post(admin.browser(), admin, f"/admin/history/{revision[0]}/hide", reason="  ")
    assert refused.status_code == 400
    ok = post(admin.browser(), admin, f"/admin/history/{revision[0]}/hide", reason="Personal details", rule="illegal")
    assert ok.status_code == 303 and ok.headers["Location"].endswith(f"/post/{b36}/history")
    db.expire_all()
    assert tuple(db.execute(text("SELECT previous_title, previous_body_html, hidden_by FROM content_edit_history WHERE id = :i"),
                            {"i": revision[0]}).first()) == (None, None, admin.id)
    page = get(real_app[0].test_client(), f"/post/{b36}/history").get_data(as_text=True)
    assert "First title" not in page and "hidden by a site administrator" in page
    (hidden,) = entries(real_app, action="hide_revision", target_submission_id=post_id)
    assert (hidden["reason"], hidden["rule_key"], hidden["actor_role"], hidden["audience"]) == ("Personal details", "illegal", "admin", 0)
    assert get(author.browser(), "/admin/log").status_code in (401, 403, 404, 302)
    assert post(author.browser(), author, f"/admin/history/{revision[0]}/hide", reason="x").status_code in (401, 403, 404, 302)


# --- reasons ----------------------------------------------------------------------------------------------------------------------------------

def test_an_admin_removal_without_a_reason_is_refused_and_changes_nothing(make_member, real_app):
    admin, author = admin_of(make_member), make_member()
    board_id, name = guild(real_app)
    post_id, b36 = add_post(author, board_id, "Keep me", "<p>words</p>")
    refused = post(admin.browser(), admin, f"/api/ban_post/{b36}", reason="  ", rule="evasion")
    assert refused.status_code == 400
    _, db_session = real_app
    db = db_session()
    db.expire_all()
    assert db.execute(text("SELECT is_banned FROM submissions WHERE id = :p"), {"p": post_id}).scalar() is False
    assert entries(real_app, target_submission_id=post_id) == []


# --- the log of one post, one comment or one account ---------------------------------------------------------------------------------------------

def lines(html):
    """The lines of a Log page and nothing else (an admin's own navbar names them)."""
    return "".join(re.findall(r'<div class="card mb-2 p-3 log-entry".*?</div>\s*</div>\s*</div>', html, re.S))


def test_a_posts_log_and_its_authors_log_list_what_was_decided_with_no_title_even_for_a_post_never_on_their_profile(make_member, real_app):
    board_id, name, gm, author, post_id, b36 = hide(real_app, make_member, title="NEVER-IN-THE-LOG", body="<p>NEVER-WORDS</p>")
    admin = admin_of(make_member)
    assert post(admin.browser(), admin, f"/api/ban_post/{b36}", reason="Copyright", rule="illegal").status_code in (200, 302)
    visitor = real_app[0].test_client()
    for path in (f"/post/{b36}/log", f"/@{author.username}/log"):
        page = get(visitor, path)
        assert page.status_code == 200, path
        html = page.get_data(as_text=True)
        found = lines(html)
        assert found.count("log-entry") == 2 and "Advertising" in found and "Copyright" in found, path     # the guildmaster's hide and the admin's removal
        assert f"#{b36}" in found and f"@{gm.username}" in found and f"@{admin.username}" not in found, path
        assert "NEVER-IN-THE-LOG" not in html and "NEVER-WORDS" not in html, path
    # the post was made in a guild, not on the profile: the author's own log still lists it
    assert get(visitor, f"/@{author.username}/log").get_data(as_text=True).count("log-entry") >= 2


def test_a_profiles_log_names_no_anonymous_item_to_the_public_but_the_admins_see_it(make_member, real_app):
    board_id, name = guild(real_app)
    gm, author, admin = guildmaster(make_member, board_id), make_member(), admin_of(make_member)
    post_id, b36 = add_post(author, board_id, "Anonymous", "<p>words</p>", anonymous=True)
    assert post(gm.browser(), gm, f"/mod/kick/{name}/{b36}", reason="Anonymous but off topic").status_code == 200
    public = get(real_app[0].test_client(), f"/@{author.username}/log").get_data(as_text=True)
    assert "Anonymous but off topic" not in public                                       # listing it under its author would say who wrote it
    seen_by_admin = get(admin.browser(), f"/@{author.username}/log").get_data(as_text=True)
    assert "Anonymous but off topic" in lines(seen_by_admin)
    # the post's own log is by id and says nothing of the author
    own = get(real_app[0].test_client(), f"/post/{b36}/log").get_data(as_text=True)
    assert "Anonymous but off topic" in own and author.username not in lines(own)


def test_an_authors_own_delete_is_a_date_for_the_author_and_the_admins_and_nobody_else(make_member, real_app):
    board_id, name = guild(real_app)
    author, other, admin = make_member(), make_member(), admin_of(make_member)
    post_id, b36 = add_post(author, board_id, "DELETED-AND-PRIVATE", "<p>DELETED-WORDS</p>")
    assert post(author.browser(), author, f"/delete_post/{b36}").status_code in (200, 204, 302)
    for who, client, should in (("author", author.browser(), True), ("admin", admin.browser(), True),
                                ("member", other.browser(), False), ("visitor", real_app[0].test_client(), False)):
        for path in (f"/@{author.username}/log", f"/post/{b36}/log"):
            page = get(client, path)
            html = page.get_data(as_text=True)
            assert "DELETED-AND-PRIVATE" not in html and "DELETED-WORDS" not in html, (who, path)
            if page.status_code == 200:
                assert ("deleted post" in lines(html)) is should, (who, path)
    assert get(author.browser(), f"/@{author.username}/log").status_code == 200


def test_a_private_guilds_decisions_are_not_in_anyones_log_of_a_post_or_an_account(make_member, real_app):
    board_id, name = guild(real_app, private=True)
    gm, member = guildmaster(make_member, board_id), make_member()
    from ruqqus.classes import ContributorRelationship
    db = member.db_session()
    db.add(ContributorRelationship(user_id=member.id, board_id=board_id, is_active=True))
    db.commit()
    post_id, b36 = add_post(member, board_id, "PRIVATE-TITLE", "<p>words</p>", public=False)      # made while the guild was private
    assert post(gm.browser(), gm, f"/mod/kick/{name}/{b36}", reason="Only for the guild").status_code == 200
    for client in (real_app[0].test_client(), member.browser(), gm.browser()):
        assert "Only for the guild" not in get(client, f"/@{member.username}/log").get_data(as_text=True)
    assert get(real_app[0].test_client(), f"/post/{b36}/log").status_code in (403, 404)


def test_a_comments_log_lists_its_hide_by_id_and_never_its_words(make_member, real_app):
    from ruqqus.classes import Comment, CommentAux
    from ruqqus.helpers.base36 import base36encode
    board_id, name = guild(real_app)
    gm, author = guildmaster(make_member, board_id), make_member()
    post_id, b36 = add_post(author, board_id, "A post", "<p>words</p>")
    db = author.db_session()
    with author.app.test_request_context("/"):
        row = Comment(author_id=author.id, parent_submission=post_id, level=1, original_board_id=board_id, created_utc=int(time.time()) - 3600)
    db.add(row)
    db.flush()
    comment_id = row.id
    db.add(CommentAux(id=comment_id, body="NEVER-COMMENT-WORDS", body_html="<p>NEVER-COMMENT-WORDS</p>"))
    db.commit()
    c36 = base36encode(comment_id)
    assert post(gm.browser(), gm, f"/mod/hide_comment/{name}/{c36}", reason="Insulting").status_code == 200
    page = get(real_app[0].test_client(), f"/comment/{c36}/log")
    html = page.get_data(as_text=True)
    assert page.status_code == 200 and "Insulting" in lines(html) and "NEVER-COMMENT-WORDS" not in html
    assert "Insulting" in lines(get(real_app[0].test_client(), f"/@{author.username}/log").get_data(as_text=True))


def test_an_unknown_account_or_post_has_no_log(make_member, real_app):
    visitor = real_app[0].test_client()
    assert get(visitor, "/@no_such_account_here/log").status_code == 404
    assert get(visitor, "/post/zzzzzzzz/log").status_code == 404
    assert get(visitor, "/comment/zzzzzzzz/log").status_code == 404
