"""The public Log on the real app and Postgres (slice B of helpers/action_log.py; the rules and the SQL are tested without Postgres in
tests/test_action_log.py and tests/test_log_view.py). The promises, end to end:

- /log shows who (by role), when, where, which rule and the reason of a hide or a removal, and never a title or text; it is the same
  page for a visitor and for an admin, names no admin and no private guild, and /log/deleted only redirects to its Deleted tab;
- what an author deletes stays in the Log as Anonymous (slice D3a): the words are a public copy, the history row holds none, and the history
  and the copy page of a deleted item stay its author's and the admins'; the history of a post a guild hid shows the reason but not the words;
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


def names_post(page, ident):
    """Whether a page names a post by its id the way the Log does ('#a1b2c'). Not a substring test: a colour in the page's head such as
    '#ffffff' begins with the same letters as a post whose id is 'ff' (the test failed one run in a few hundred on that)."""
    return re.search(rf"#{re.escape(ident)}(?![0-9A-Za-z])", page) is not None


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
    assert answer.status_code == 301 and answer.headers["Location"].endswith("/log?type=deletion"), "what authors delete is the Log's Deleted tab now"


# --- the guild's own log ------------------------------------------------------------------------------------------------------------

def test_a_public_guilds_log_names_a_hidden_post_by_id_to_the_public_and_by_title_to_its_guildmasters(make_member, real_app):
    board_id, name, gm, author, post_id, b36 = hide(real_app, make_member, title="GM-ONLY-TITLE")
    public = get(real_app[0].test_client(), f"/+{name}/mod/log").get_data(as_text=True)
    assert "GM-ONLY-TITLE" not in public and f"#{b36}" in public and "Advertising" in public
    insider = get(gm.browser(), f"/+{name}/mod/log").get_data(as_text=True)
    assert "GM-ONLY-TITLE" in insider
    assert "Free expression and good faith" in public                                              # the rule is on the entry


# --- what an author deletes ----------------------------------------------------------------------------------------------------------------

def test_an_authors_delete_stays_in_the_log_as_anonymous_while_its_history_and_copy_page_stay_private(make_member, real_app):
    board_id, name = guild(real_app)
    author, other, admin = make_member(), make_member(), admin_of(make_member)
    post_id, b36 = add_post(author, board_id, "DELETED-TITLE", "<p>DELETED-WORDS</p>")
    here = author.browser()
    assert post(here, author, f"/delete_post/{b36}").status_code in (200, 204, 302)
    (snap,) = snapshots(real_app, target_submission_id=post_id)
    assert (snap["kind"], snap["public"], snap["expires_utc"], snap["author_id"], snap["title"], snap["scrubbed_utc"]) == ("delete", True, 0, author.id, "DELETED-TITLE", 0)
    _, db_session = real_app
    db = db_session()
    db.expire_all()
    row = db.execute(text("SELECT previous_title, previous_body, previous_body_html FROM content_edit_history "
                          "WHERE target_submission_id = :p AND action = 'delete'"), {"p": post_id}).first()
    assert tuple(row) == (None, None, None)                                                         # the public history row holds no text
    (entry,) = entries(real_app, target_submission_id=post_id)
    assert entry["log_type"] == "deletion" and entry["actor_role"] == "author" and entry["audience"] == 0
    for client in (real_app[0].test_client(), other.browser()):
        deleted = get(client, "/log?type=deletion").get_data(as_text=True)
        assert "DELETED-WORDS" in deleted and "DELETED-TITLE" in deleted and author.username not in deleted    # the Log keeps the words, as Anonymous
        for path in (f"/post/{b36}/history", f"/post/{b36}/copy"):
            assert get(client, path).status_code == 404, path                                       # the history and the copy page are not public
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
    # the link is offered only to someone who can open it, and a visitor is not even told that a private copy exists (C7 of the review of
    # 2026-11-06: with no viewer snapshot_for answers the newest copy of all)
    for client in (real_app[0].test_client(), stranger.browser(), other_gm.browser()):
        page = get(client, f"/post/{b36}/history")
        assert f"/post/{b36}/copy" not in page.get_data(as_text=True) and "preserved copy" not in page.get_data(as_text=True)


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


def test_an_authors_delete_is_a_line_on_the_posts_log_for_everyone_as_anonymous_and_never_on_the_authors_own_log(make_member, real_app):
    board_id, name = guild(real_app)
    author, other, admin = make_member(), make_member(), admin_of(make_member)
    post_id, b36 = add_post(author, board_id, "DELETED-AND-PUBLIC", "<p>DELETED-WORDS</p>")
    assert post(author.browser(), author, f"/delete_post/{b36}").status_code in (200, 204, 302)
    for who, client in (("author", author.browser()), ("admin", admin.browser()), ("member", other.browser()), ("visitor", real_app[0].test_client())):
        page = get(client, f"/post/{b36}/log")
        html = page.get_data(as_text=True)
        assert page.status_code == 200, who
        assert "deleted post" in lines(html) and "Anonymous" in lines(html) and author.username not in lines(html), who
        assert "DELETED-WORDS" in html, who                                                         # it stays in the Log: that is the point
    for who, client, listed in (("author", author.browser(), False), ("member", other.browser(), False), ("visitor", real_app[0].test_client(), False),
                                ("admin", admin.browser(), True)):
        html = get(client, f"/@{author.username}/log").get_data(as_text=True)
        assert ("deleted post" in lines(html)) is listed, who                                      # an anonymous item is left out of an account's log, an admin's excepted
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


# --- the first slice of the audit (2026-11-05): leaks closed -----------------------------------------------------------------------------------

def add_comment(member, post_id, board_id, body, age=3600, anonymous=False):
    from ruqqus.classes import Comment, CommentAux
    from ruqqus.helpers.base36 import base36encode
    db = member.db_session()
    db.rollback()
    with member.app.test_request_context("/"):
        row = Comment(author_id=member.id, parent_submission=post_id, level=1, original_board_id=board_id,
                      created_utc=int(time.time()) - age, is_anonymous=anonymous)
    db.add(row)
    db.flush()
    db.add(CommentAux(id=row.id, body=re.sub("<[^>]+>", "", body), body_html=body))
    db.commit()
    return row.id, base36encode(row.id)


def followed(client, location):
    return client.get(location, base_url=BASE, headers=HEADERS, environ_base={"REMOTE_ADDR": address()})


def test_a_deleted_posts_address_leads_to_the_logs_thread_and_names_nobody_and_its_history_is_the_authors_and_the_admins(make_member, real_app):
    """H1 and H8: the page of a deleted post is Anonymous, so its address and its history page may not say whose it was."""
    board_id, name = guild(real_app)
    author, other, admin = make_member(), make_member(), admin_of(make_member)
    post_id, b36 = add_post(author, board_id, "GONE-TITLE", "<p>GONE-WORDS</p>")
    assert post(author.browser(), author, f"/delete_post/{b36}").status_code in (200, 204, 302)
    visitor = real_app[0].test_client()
    for client in (visitor, other.browser()):
        moved = get(client, f"/post/{b36}")
        assert moved.status_code == 302 and author.username not in moved.headers["Location"], moved.headers.get("Location")
        assert moved.headers["Location"].endswith(f"/log/post/{b36}")
        page = followed(client, moved.headers["Location"])
        assert page.status_code == 200 and author.username not in page.get_data(as_text=True)
        assert "GONE-TITLE" in page.get_data(as_text=True), "the thread is the deleted post as Anonymous"
        assert get(client, f"/post/{b36}/history").status_code == 404                           # it would say "Deleted by @author"
    for member in (author, admin):
        history = get(member.browser(), f"/post/{b36}/history")
        assert history.status_code == 200 and "Deleted by" in history.get_data(as_text=True), member.username


def test_a_deleted_comments_address_and_history_name_nobody_either(make_member, real_app):
    board_id, name = guild(real_app)
    author, commenter, other = make_member(), make_member(), make_member()
    post_id, b36 = add_post(author, board_id, "A post", "<p>words</p>")
    comment_id, c36 = add_comment(commenter, post_id, board_id, "<p>COMMENT-WORDS</p>")
    assert post(commenter.browser(), commenter, f"/delete/comment/{c36}").status_code in (200, 204, 302)
    assert get(other.browser(), f"/comment/{c36}/history").status_code == 404
    assert get(commenter.browser(), f"/comment/{c36}/history").status_code == 200
    moved = get(other.browser(), f"/@{commenter.username}/comment/{c36}")
    assert moved.status_code in (301, 302) and commenter.username not in moved.headers["Location"], moved.headers.get("Location")


def test_guildmasters_open_the_copy_of_a_hide_but_not_of_what_an_author_deleted_or_a_post_authors_own_hide(make_member, real_app):
    """H2: a private copy carries the guild's board id, which gives no leave to read what an author deleted or hid on their own profile."""
    board_id, name = guild(real_app)
    gm, author, commenter, stranger, admin = guildmaster(make_member, board_id), make_member(), make_member(), make_member(), admin_of(make_member)
    post_id, b36 = add_post(author, board_id, "A post", "<p>words</p>")
    deleted_id, deleted36 = add_comment(commenter, post_id, board_id, "<p>DELETED-BY-ITS-AUTHOR</p>")
    assert post(commenter.browser(), commenter, f"/delete/comment/{deleted36}").status_code in (200, 204, 302)
    (snap,) = snapshots(real_app, target_comment_id=deleted_id)
    assert snap["kind"] == "delete" and snap["board_id"] == board_id
    assert get(gm.browser(), f"/comment/{deleted36}/copy").status_code == 404                  # the guild's guildmaster has no business here
    assert get(stranger.browser(), f"/comment/{deleted36}/copy").status_code == 404
    for member in (commenter, admin):
        assert "DELETED-BY-ITS-AUTHOR" in get(member.browser(), f"/comment/{deleted36}/copy").get_data(as_text=True)
    # a post's author hiding a comment on their own profile post: the comment's author, the hider and the admins
    profile_post, profile36 = add_post(author, 2, "A profile post", "<p>words</p>")
    hidden_id, hidden36 = add_comment(commenter, profile_post, 2, "<p>HIDDEN-BY-THE-POST-AUTHOR</p>")
    assert post(author.browser(), author, f"/author/hide_comment/{hidden36}", reason="Off topic").status_code == 200
    (snap,) = snapshots(real_app, target_comment_id=hidden_id)
    assert (snap["kind"], snap["hider_id"], snap["author_id"]) == ("author_hide", author.id, commenter.id)
    for member in (commenter, author, admin):
        assert "HIDDEN-BY-THE-POST-AUTHOR" in get(member.browser(), f"/comment/{hidden36}/copy").get_data(as_text=True), member.username
    assert get(stranger.browser(), f"/comment/{hidden36}/copy").status_code == 404
    # a guild's hide is still the guildmasters' to read
    hidden_board, hidden_name, hide_gm, hide_author, hide_post, hide36 = hide(real_app, make_member)
    assert "SECRET-BODY" in get(hide_gm.browser(), f"/post/{hide36}/copy").get_data(as_text=True)


def test_the_history_of_a_comment_a_post_author_hid_names_nobody_when_the_post_is_anonymous(make_member, real_app):
    """The account that hid or restored the comment is the POST's author, so whether it is named is the post's rule, not the comment's: a
    named comment under an anonymous post was printing 'Removed by the post's author, @alice' to everyone (C6 of the review of 2026-11-06;
    older than the Log work, it was in the template since 2026-10-28)."""
    author, commenter, stranger, admin = make_member(), make_member(), make_member(), admin_of(make_member)
    post_id, b36 = add_post(author, 2, "An anonymous post", "<p>words</p>", anonymous=True)
    comment_id, c36 = add_comment(commenter, post_id, 2, "<p>NAMED-COMMENT</p>")
    mine = author.browser()
    assert post(mine, author, f"/author/hide_comment/{c36}", reason="Off topic").status_code == 200
    assert post(mine, author, f"/author/unhide_comment/{c36}").status_code == 200
    assert post(mine, author, f"/author/hide_comment/{c36}", reason="Off topic again").status_code == 200
    for client in (real_app[0].test_client(), stranger.browser(), commenter.browser()):
        page = get(client, f"/comment/{c36}/history")
        text_ = page.get_data(as_text=True)
        assert page.status_code == 200 and "Removed by the post" in text_ and "Restored by the post" in text_
        assert author.username not in text_, "the page names the author of an anonymous post"
    # the author themselves, and the admins (who are told who wrote it, as everywhere), still see the name
    for member in (author, admin):
        assert author.username in get(member.browser(), f"/comment/{c36}/history").get_data(as_text=True), member.username
    # a post that is not anonymous is named as before
    open_post, open36 = add_post(author, 2, "A named post", "<p>words</p>")
    open_id, open_c36 = add_comment(commenter, open_post, 2, "<p>OTHER-COMMENT</p>")
    assert post(mine, author, f"/author/hide_comment/{open_c36}", reason="Off topic").status_code == 200
    assert author.username in get(stranger.browser(), f"/comment/{open_c36}/history").get_data(as_text=True)


def test_a_guildmasters_words_cannot_make_their_hide_look_like_the_sites_or_take_it_out_of_the_public_log(make_member, real_app):
    """H3: the role and the audience come from who acted, not from the words of the reason."""
    from ruqqus.classes import ModAction
    board_id, name = guild(real_app)
    gm, author = guildmaster(make_member, board_id), make_member()
    post_id, b36 = add_post(author, board_id, "SPOOF-TITLE", "<p>words</p>")
    reason = "admin action | suspected ban evasion, banned image, CSAM detected, by the site's admins"
    assert post(gm.browser(), gm, f"/mod/kick/{name}/{b36}", reason=reason, rule="good_faith").status_code == 200
    (row,) = entries(real_app, board_id=board_id, target_submission_id=post_id)
    assert (row["actor_role"], row["audience"]) == ("guildmaster", 0), row
    page = get(real_app[0].test_client(), f"/log?guild={name}").get_data(as_text=True)
    assert gm.username in page and "suspected ban evasion" in page                              # still the guildmaster's, still public
    _, db_session = real_app
    db = db_session()
    db.expire_all()
    action = db.query(ModAction).filter_by(board_id=board_id, kind="hide_post_from_guild").first()
    assert action.by_admin is False and action.json["mod"] == gm.username


def test_a_hidden_posts_log_entry_page_title_carries_no_title_for_the_public(make_member, real_app):
    """H4."""
    board_id, name, gm, author, post_id, b36 = hide(real_app, make_member, title="TITLE-IN-THE-HEAD", body="<p>x</p>")
    (row,) = entries(real_app, board_id=board_id, target_submission_id=post_id)
    from ruqqus.helpers.base36 import base36encode
    _, db_session = real_app
    db = db_session()
    mod_id = db.execute(text("SELECT legacy_modaction_id FROM log_entries WHERE id = :i"), {"i": row["id"]}).scalar()
    path = f"/+{name}/mod/log/{base36encode(mod_id)}"
    public = get(real_app[0].test_client(), path)
    assert public.status_code == 200 and "TITLE-IN-THE-HEAD" not in public.get_data(as_text=True)
    assert "TITLE-IN-THE-HEAD" in get(gm.browser(), path).get_data(as_text=True)                # the guildmasters see the title, as before


def test_hiding_a_version_hides_it_on_every_copy_and_earlier_versions_of_a_hidden_post_are_not_shown(make_member, real_app):
    """M1 and M2."""
    from ruqqus.classes import Submission, SubmissionAux
    board_id, name = guild(real_app)
    other_board, _ = guild(real_app)
    author, admin, stranger = make_member(), admin_of(make_member), make_member()
    post_id, b36 = add_post(author, board_id, "OLD-TITLE", "<p>OLD-WORDS</p>")
    db = author.db_session()
    db.rollback()
    with author.app.test_request_context("/"):
        copy = Submission(author_id=author.id, board_id=other_board, original_board_id=board_id, repost_id=post_id,
                          created_utc=int(time.time()) - 3600, post_public=True)
    db.add(copy)
    db.flush()
    db.add(SubmissionAux(id=copy.id, title="OLD-TITLE", body="OLD-WORDS", body_html="<p>OLD-WORDS</p>"))
    db.commit()
    copy_id = copy.id
    assert post(author.browser(), author, f"/edit_post/{b36}", title="NEW-TITLE", body="NEW-WORDS", edit_summary="rewrite").status_code in (200, 302)
    _, db_session = real_app
    db = db_session()
    db.expire_all()
    rows = db.execute(text("SELECT id, target_submission_id, edit_group FROM content_edit_history WHERE action = 'edit' "
                           "AND target_submission_id IN (:a, :b) ORDER BY id"), {"a": post_id, "b": copy_id}).all()
    assert {r[1] for r in rows} == {post_id, copy_id} and len({r[2] for r in rows}) == 1 and rows[0][2]      # one edit, one group
    assert post(admin.browser(), admin, f"/admin/history/{rows[0][0]}/hide", reason="Personal details").status_code == 303
    db.expire_all()
    hidden = db.execute(text("SELECT hidden_utc, previous_title FROM content_edit_history WHERE edit_group = :g"), {"g": rows[0][2]}).all()
    assert len(hidden) == 2 and all(h[0] > 0 and h[1] is None for h in hidden)                  # both copies' rows, not just the one
    # an old version of a post a guild then hid is not shown to strangers
    db.execute(text("UPDATE content_edit_history SET hidden_utc = 0 WHERE edit_group = :g"), {"g": rows[0][2]})
    db.execute(text("UPDATE content_edit_history SET previous_title = 'OLD-TITLE' WHERE edit_group = :g"), {"g": rows[0][2]})
    db.execute(text("UPDATE submissions SET hidden_by_guild = true WHERE id = :p"), {"p": post_id})
    db.commit()
    assert "OLD-TITLE" not in get(stranger.browser(), f"/post/{b36}/history").get_data(as_text=True)
    for member in (author, admin):
        assert "OLD-TITLE" in get(member.browser(), f"/post/{b36}/history").get_data(as_text=True), member.username


def test_an_exile_never_pairs_an_account_with_an_anonymous_item_and_reads_as_the_exile_of_a_person(make_member, real_app):
    """H6: an exile that cites an anonymous post would say who wrote it, in the item's log, the account's log and the filters."""
    board_id, name = guild(real_app)
    gm, author, visible_author = guildmaster(make_member, board_id), make_member(), make_member()
    anon_id, anon36 = add_post(author, board_id, "Anonymous post", "<p>x</p>", anonymous=True)
    _, own_comment36 = add_comment(author, anon_id, board_id, "<p>a named comment: this is how they took part</p>")
    named_id, named36 = add_post(visible_author, board_id, "Named post", "<p>y</p>")
    # an exile needs a reason and a named item of the account's own: the anonymous post named as `thing` is dropped without a word, so the account
    # also cites its named comment (an exile that cited nothing else would be refused, and say nothing about who wrote the anonymous post)
    for username, extra in ((author.username, {"thing": f"t2_{anon36}", "evidence": f"t3_{own_comment36}"}),
                            (visible_author.username, {"thing": f"t2_{named36}"})):
        answer = post(gm.browser(), gm, f"/mod/exile/{name}", username=username, reason="Breaking the guild's rules", **extra)
        assert answer.status_code in (200, 204), answer.get_data(as_text=True)[:200]
    anon_entry, = entries(real_app, action="exile_user", target_user_id=author.id)
    named_entry, = entries(real_app, action="exile_user", target_user_id=visible_author.id)
    assert anon_entry["target_submission_id"] is None                                           # the citation of an anonymous post is dropped
    assert named_entry["target_submission_id"] == named_id                                      # a named post's stays
    visitor = real_app[0].test_client()
    log = get(visitor, f"/log?type=sanction&guild={name}").get_data(as_text=True)
    assert f"@{author.username}" in log and f"@{visible_author.username}" in log               # the line is about the person
    assert not names_post(log, anon36) and not names_post(log, named36)                         # an exile is about the person, not the item
    assert f"@{visible_author.username}" in get(visitor, f"/post/{named36}/log").get_data(as_text=True)
    assert get(visitor, f"/log?type=sanction&to={visible_author.username}").get_data(as_text=True).count("exiled user") == 1
    # the post is made anonymous AFTER the exile cited it: the item's own log and the filters stop pairing the two
    _, db_session = real_app
    db = db_session()
    db.execute(text("UPDATE submissions SET is_anonymous = true WHERE id = :p"), {"p": named_id})
    db.commit()
    assert f"@{visible_author.username}" not in get(visitor, f"/post/{named36}/log").get_data(as_text=True)
    assert get(visitor, f"/log?type=sanction&to={visible_author.username}").get_data(as_text=True).count("exiled user") == 0
    staff = get(admin_of(make_member).browser(), f"/admin/log?type=sanction&to={visible_author.username}").get_data(as_text=True)
    assert "exile_user" in staff                                                                # staff still see it


def test_the_log_pages_are_not_for_search_engines(make_member, real_app):
    member = make_member()
    visitor = real_app[0].test_client()
    for path in ("/log", f"/@{member.username}/log"):
        page = get(visitor, path)
        assert page.status_code == 200 and "noindex" in page.headers.get("X-Robots-Tag", ""), path
    assert 'name="robots" content="noindex"' in get(visitor, "/log").get_data(as_text=True)
    robots = get(visitor, "/robots.txt").get_data(as_text=True)
    for line in ("Disallow: /log", "Disallow: /post/*/log", "Disallow: /comment/*/log", "Disallow: /@*/log"):
        assert line in robots, line


def test_the_sites_own_boards_are_not_named_as_guilds_in_the_log(make_member, real_app):
    from ruqqus.classes import ModAction
    admin, author = admin_of(make_member), make_member()
    post_id, b36 = add_post(author, 2, "A profile post", "<p>words</p>")
    _, db_session = real_app
    db = db_session()
    db.add(ModAction(kind="ban_post", user_id=admin.id, board_id=2, target_submission_id=post_id, note="admin action"))
    db.commit()
    page = get(real_app[0].test_client(), "/log").get_data(as_text=True)
    assert b36 in page and "systemprofile" not in page
