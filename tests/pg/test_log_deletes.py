"""What a member deletes, and what it leaves in the Log, on the real app and Postgres (the Log's slice D3a; the rules and the SQL are tested without
Postgres in tests/test_deletion.py): a delete inside the five-minute window is an erase with no public trace; after it the words stay as Anonymous
under the reader's word filter and the sensitive rule, with no link; a child account's, a removed item's and a Circle post's are lines or staff-only;
"Remove my name" keeps the post, and is refused where the name is needed; the Report button and the admins' queue; the thread of a deleted post;
and the delete that needs a full sign-in."""
import re
import secrets
import time

from sqlalchemy import text

BASE = "http://localhost"
AGENT = "ci-log-deletes"
WORDS = "A fairly ordinary paragraph about gardens"
TITLE = "Tomatoes and basil"          # a test makes its own with `unique()`: the Log is one table for the whole module


def unique(title=TITLE):
    return f"{title} {secrets.token_hex(3)}"


def address():
    return f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"


def get(client, path, country="US"):
    headers = {"User-Agent": AGENT, "Accept-Language": "en"}
    if country:
        headers["CF-IPCountry"] = country
    return client.get(path, base_url=BASE, headers=headers, environ_base={"REMOTE_ADDR": address()})


def post(client, member, path, **fields):
    return client.post(path, base_url=BASE, headers={"User-Agent": AGENT}, data={"formkey": member.formkey(client), **fields},
                       environ_base={"REMOTE_ADDR": address()})


def admin_of(make_member, level=3):
    admin = make_member()
    db = admin.db_session()
    db.execute(text("UPDATE users SET admin_level = :l WHERE id = :u"), {"l": level, "u": admin.id})
    db.commit()
    return admin


def guild(real_app, private=False, banned=False, circle=False):
    from ruqqus.classes import Board
    _, db_session = real_app
    db = db_session()
    board = Board(name="ci_del_" + secrets.token_hex(3), description="a guild for the delete tests", description_html="<p>a guild</p>", creator_id=1,
                  subcat_id=0, is_private=private, is_circle=circle, is_banned=banned, created_utc=int(time.time()), color="23a9f2")
    db.add(board)
    db.commit()
    return board.id, board.name


def add_post(member, board_id, title=None, body=f"<p>{WORDS} <a href=\"https://example.org/x\">a link</a></p>", age=3600, **flags):
    from ruqqus.classes import Submission, SubmissionAux
    from ruqqus.helpers.base36 import base36encode
    title = title or unique()
    db = member.db_session()
    db.rollback()
    with member.app.test_request_context("/"):
        row = Submission(author_id=member.id, board_id=board_id, original_board_id=board_id, created_utc=int(time.time()) - age,
                         post_public=flags.pop("post_public", True), **flags)
    db.add(row)
    db.flush()
    db.add(SubmissionAux(id=row.id, title=title, body=re.sub("<[^>]+>", "", body), body_html=body))
    db.commit()
    return row.id, base36encode(row.id)


def add_comment(member, post_id, board_id, body="<p>A comment.</p>", age=3600, parent=None, **flags):
    from ruqqus.classes import Comment, CommentAux
    from ruqqus.helpers.base36 import base36encode
    db = member.db_session()
    db.rollback()
    with member.app.test_request_context("/"):
        row = Comment(author_id=member.id, parent_submission=post_id, level=2 if parent else 1, parent_comment_id=parent, original_board_id=board_id,
                      created_utc=int(time.time()) - age, **flags)
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


def ids_of(page):
    return re.findall(r'id="log-(\d+)"', page)


def deleted_tab(real_app, country="US", client=None, what=""):
    app, _ = real_app
    return get(client or app.test_client(), f"/log?type=deletion{what}", country).get_data(as_text=True)


# ---------------------------------------------------------------- a post

def test_a_post_deleted_after_five_minutes_stays_in_the_log_as_anonymous_and_leaves_the_site(real_app, make_member):
    author = make_member()
    board_id, board_name = guild(real_app)
    title = unique()
    post_id, base36 = add_post(author, board_id, title=title, age=3600)
    client = author.browser()
    assert post(client, author, f"/delete_post/{base36}").status_code == 204
    row = rows(real_app, "SELECT deleted_utc, is_anonymous FROM submissions WHERE id = :i", i=post_id)[0]
    assert row["deleted_utc"] > 0 and row["is_anonymous"] is True, "it left the site as Anonymous"
    (entry,) = entries(real_app, action="author_delete_post", target_submission_id=post_id)
    assert entry["log_type"] == "deletion" and entry["actor_role"] == "author" and entry["audience"] == 0 and entry["snapshot_id"]
    copy = rows(real_app, "SELECT kind, public, expires_utc FROM log_snapshots WHERE id = :i", i=entry["snapshot_id"])[0]
    assert (copy["kind"], copy["public"], copy["expires_utc"]) == ("delete", True, 0), "public, and it never expires"
    app, _ = real_app
    page = deleted_tab(real_app)
    assert title in page and WORDS in page and "Anonymous" in page
    assert author.username not in page, "never the name"
    assert "example.org" not in page.split("log-deleted")[1].split("</div>")[0] or 'href="https://example.org' not in page, "a link in deleted text is plain text"
    assert 'href="https://example.org' not in page
    assert title not in get(app.test_client(), "/log?type=deletion&what=comment").get_data(as_text=True), "the posts-or-comments filter"
    assert title in get(app.test_client(), "/log?type=deletion&what=post").get_data(as_text=True)
    # there is no "by" on this tab: asking for one account's deletes lists the same entries as asking for another's (a filter by author would list exactly
    # one person's anonymous deletes)
    mine = ids_of(get(app.test_client(), f"/log?type=deletion&by={author.username}").get_data(as_text=True))
    assert mine and mine == ids_of(get(app.test_client(), "/log?type=deletion&by=somebody_else").get_data(as_text=True)) == ids_of(page)


def test_inside_five_minutes_with_nobody_commenting_it_is_an_erase_with_no_entry_and_a_private_copy(real_app, make_member):
    author = make_member()
    board_id, _ = guild(real_app)
    title = unique()
    post_id, base36 = add_post(author, board_id, title=title, age=30)
    assert post(author.browser(), author, f"/delete_post/{base36}").status_code == 204
    assert entries(real_app, target_submission_id=post_id) == [], "no public trace"
    assert rows(real_app, "SELECT is_anonymous FROM submissions WHERE id = :i", i=post_id)[0]["is_anonymous"] is not True
    copy = rows(real_app, "SELECT kind, public, entry_id FROM log_snapshots WHERE target_submission_id = :i", i=post_id)[0]
    assert (copy["kind"], copy["public"], copy["entry_id"]) == ("grace", False, None)
    assert title not in deleted_tab(real_app)
    app, _ = real_app
    assert get(app.test_client(), f"/log/post/{base36}").status_code == 404, "nothing public to show"


def test_a_young_post_someone_else_has_commented_on_is_published_not_erased(real_app, make_member):
    author, other = make_member(), make_member()
    board_id, _ = guild(real_app)
    post_id, base36 = add_post(author, board_id, age=30)
    add_comment(other, post_id, board_id, age=10)
    assert post(author.browser(), author, f"/delete_post/{base36}").status_code == 204
    assert len(entries(real_app, action="author_delete_post", target_submission_id=post_id)) == 1


def test_twenty_erases_a_day_and_the_next_delete_publishes(real_app, make_member):
    author = make_member()
    board_id, _ = guild(real_app)
    client = author.browser()
    for n in range(20):
        _, b36 = add_post(author, board_id, title=f"Erased {n}", age=10)
        assert post(client, author, f"/delete_post/{b36}").status_code == 204
    post_id, b36 = add_post(author, board_id, title="The twenty-first", age=10)
    assert post(client, author, f"/delete_post/{b36}").status_code == 204
    assert len(entries(real_app, action="author_delete_post", target_submission_id=post_id)) == 1, "past the cap it publishes like a later one"


def test_a_child_accounts_delete_is_a_line_and_a_private_copy_never_the_words(real_app, make_member):
    author = make_member()
    db = author.db_session()
    db.execute(text("UPDATE users SET age_declared = 2, age_declared_utc = 5, filter_level = 2, age_lock = 1 WHERE id = :u"), {"u": author.id})
    db.commit()
    board_id, _ = guild(real_app)
    title = unique()
    post_id, b36 = add_post(author, board_id, title=title, age=3600)
    assert post(author.browser(), author, f"/delete_post/{b36}").status_code == 204
    (entry,) = entries(real_app, target_submission_id=post_id)
    copy = rows(real_app, "SELECT kind, public FROM log_snapshots WHERE id = :i", i=entry["snapshot_id"])[0]
    assert (copy["kind"], copy["public"]) == ("grace", False)
    page = deleted_tab(real_app, country="US")
    assert title not in page and f'id="log-{entry["id"]}"' in page, "the line is there and the words are not"
    assert "What it said isn't shown here." in page or "What it said isn&#39;t shown here." in page


def test_what_a_moderator_had_removed_or_a_banned_guild_holds_gets_a_line_and_no_words(real_app, make_member):
    author = make_member()
    board_id, _ = guild(real_app)
    banned_board, _ = guild(real_app, banned=True)
    removed_id, removed = add_post(author, board_id, title="Removed one", age=3600, is_banned=True)
    inside_id, inside = add_post(author, banned_board, title="In a banned guild", age=3600)
    for b36 in (removed, inside):
        assert post(author.browser(), author, f"/delete_post/{b36}").status_code == 204
    page = deleted_tab(real_app)
    assert "Removed one" not in page and "In a banned guild" not in page
    assert len(entries(real_app, action="author_delete_post", target_submission_id=removed_id)) == 1, "the line is there"
    assert len(entries(real_app, action="author_delete_post", target_submission_id=inside_id)) == 1
    assert rows(real_app, "SELECT count(*) AS n FROM log_snapshots WHERE target_submission_id IN (:a, :b)", a=removed_id, b=inside_id)[0]["n"] == 0, \
        "no copy of the words is made for the Log: a removed item stays on its own row until a purge"


def test_a_circle_post_deleted_is_the_staffs_alone(real_app, make_member):
    author, boss = make_member(), admin_of(make_member)
    board_id, _ = guild(real_app)
    post_id, b36 = add_post(author, board_id, title="For my circle", age=3600, audience=4, post_public=False)
    assert post(author.browser(), author, f"/delete_post/{b36}").status_code == 204
    (entry,) = entries(real_app, target_submission_id=post_id)
    assert entry["audience"] == 2, "a Circle item's entry is staff only"
    assert "For my circle" not in deleted_tab(real_app) and "For my circle" not in deleted_tab(real_app, client=boss.browser())


def test_the_deleted_tab_hides_words_a_childs_filter_hides_and_sensitive_ones_for_a_visitor(real_app, make_member):
    author = make_member()
    board_id, _ = guild(real_app)
    _, rude = add_post(author, board_id, title="Plain title", body="<p>this is fucking nonsense</p>", age=3600, word_severity=1)
    _, edgy = add_post(author, board_id, title="Edgy title", body="<p>graphic but permitted</p>", age=3600, is_sensitive=True)
    for b36 in (rude, edgy):
        post(author.browser(), author, f"/delete_post/{b36}")
    us, gb = deleted_tab(real_app, country="US"), deleted_tab(real_app, country="GB")
    assert "this is fucking nonsense" in us, "an ordinary reader's Standard filter hides only the extreme words"
    assert "this is fucking nonsense" not in gb, "the Child level (a visitor in the UK) hides profanity"
    assert "Edgy title" not in us and "Edgy title" not in gb, "sensitive: a line only for a visitor"
    viewer = make_member()
    db = viewer.db_session()
    db.execute(text("UPDATE users SET all_shows_sensitive = true WHERE id = :u"), {"u": viewer.id})
    db.commit()
    page = deleted_tab(real_app, client=viewer.browser())
    assert "Edgy title" in page and "Sensitive content: show it" in page, "behind the cover for a member who chose it"


# ---------------------------------------------------------------- the thread

def test_the_thread_of_a_deleted_post_shows_it_as_anonymous_with_its_comments_and_the_authors_own_comments_anonymised(real_app, make_member):
    author, other = make_member(), make_member()
    board_id, _ = guild(real_app)
    title = unique()
    post_id, b36 = add_post(author, board_id, title=title, age=3600)
    _, theirs = add_comment(other, post_id, board_id, body="<p>Someone else's comment</p>")
    own_id, _ = add_comment(author, post_id, board_id, body="<p>The author's own comment</p>")
    assert post(author.browser(), author, f"/delete_post/{b36}").status_code == 204
    assert rows(real_app, "SELECT is_anonymous FROM comments WHERE id = :i", i=own_id)[0]["is_anonymous"] is True, "forced, like the post"
    app, _ = real_app
    client = app.test_client()
    moved = get(client, f"/post/{b36}")
    assert moved.status_code == 302 and moved.headers["Location"].endswith(f"/log/post/{b36}"), "the post's own address leads there, in one hop"
    page = get(client, f"/log/post/{b36}").get_data(as_text=True)
    assert title in page and WORDS in page
    assert "Someone else&#39;s comment" in page or "Someone else's comment" in page
    assert "The author&#39;s own comment" in page or "The author's own comment" in page
    assert author.username not in page, "the author's name is nowhere on the page, the comment they wrote under it included"
    assert other.username in page
    for control in ('class="upvote', 'data-id-up', 'comment-form', 'id="submit"', 'tipModal', 'data-target="#sharePost'):
        assert control not in page, f"no vote, comment, tip or share control: {control}"
    assert "ad-slot" not in page and "adsbygoogle" not in page, "no ad on a record"
    comment_page = get(client, f"/comment/{theirs}")
    assert comment_page.status_code in (301, 302) and f"/log/post/{b36}" in comment_page.headers["Location"], "a comment on a deleted post leads there too"


def test_a_comment_its_author_deleted_is_anonymous_in_the_thread_with_its_words(real_app, make_member):
    author, other = make_member(), make_member()
    board_id, _ = guild(real_app)
    post_id, b36 = add_post(author, board_id, age=3600)
    cid, c36 = add_comment(other, post_id, board_id, body="<p>Words the commenter will delete</p>")
    assert post(other.browser(), other, f"/delete/comment/{c36}").status_code == 204
    assert entries(real_app, action="author_delete_comment", target_comment_id=cid)
    assert post(author.browser(), author, f"/delete_post/{b36}").status_code == 204
    app, _ = real_app
    page = get(app.test_client(), f"/log/post/{b36}").get_data(as_text=True)
    assert "Words the commenter will delete" in page and other.username not in page
    assert "Words the commenter will delete" in deleted_tab(real_app, what="&what=comment")


def test_a_live_posts_thread_address_leads_to_the_post_and_a_line_with_no_words_cannot_be_reported(real_app, make_member):
    author, reporter = make_member(), make_member()
    board_id, _ = guild(real_app)
    live_id, live36 = add_post(author, board_id)
    app, _ = real_app
    moved = get(app.test_client(), f"/log/post/{live36}")
    assert moved.status_code == 302 and f"/post/{live36}" in moved.headers["Location"], "the Log's thread is for a deleted post only"
    from ruqqus.helpers import action_log
    _, db_session = real_app
    db = db_session()
    bare = action_log.record(db, "hide_post_from_guild", 1, board_id=board_id, role=action_log.GUILDMASTER)         # no reason: no words on its line
    db.commit()
    assert post(reporter.browser(), reporter, f"/log/{bare}/report", reason="nothing to report").status_code == 404


# ---------------------------------------------------------------- remove my name

def test_remove_my_name_keeps_the_post_anonymous_and_refuses_where_the_name_is_needed(real_app, make_member):
    author = make_member()
    board_id, _ = guild(real_app)
    post_id, b36 = add_post(author, board_id)
    client = author.browser()
    assert post(client, author, f"/delete_post/{b36}", how="unlink").status_code == 204
    row = rows(real_app, "SELECT deleted_utc, is_anonymous FROM submissions WHERE id = :i", i=post_id)[0]
    assert row["is_anonymous"] is True and not row["deleted_utc"], "it stays, as Anonymous"
    assert entries(real_app, target_submission_id=post_id) == [], "anonymising writes no entry: its time would be a signal"
    again = post(client, author, f"/delete_post/{b36}", how="unlink")
    assert again.status_code == 409 and "already anonymous" in again.get_data(as_text=True)
    tipped, tipped36 = add_post(author, board_id, tip_coins=5)
    assert post(client, author, f"/delete_post/{tipped36}", how="unlink").status_code == 409
    circle, circle36 = add_post(author, board_id, audience=4, post_public=False)
    assert post(client, author, f"/delete_post/{circle36}", how="unlink").status_code == 409
    assert not any(r["is_anonymous"] for r in rows(real_app, "SELECT is_anonymous FROM submissions WHERE id IN (:a, :b)", a=tipped, b=circle)), "refused, so unchanged"


def test_remove_my_name_on_a_comment_and_an_apps_delete_scope_cannot_do_it(real_app, make_member):
    author = make_member()
    board_id, _ = guild(real_app)
    post_id, _ = add_post(author, board_id)
    cid, c36 = add_comment(author, post_id, board_id)
    client = author.browser()
    assert post(client, author, f"/delete/comment/{c36}", how="unlink").status_code == 204
    assert rows(real_app, "SELECT is_anonymous, deleted_utc FROM comments WHERE id = :i", i=cid)[0]["is_anonymous"] is True
    other_post, o36 = add_post(author, board_id)
    refused = post(client, author, f"/api/v1/delete_post/{o36}", how="unlink")
    assert refused.status_code in (400, 401), "the API's delete is a delete: taking a name off is the site's own"


# ---------------------------------------------------------------- a full sign-in

def test_a_delete_that_publishes_needs_the_password_but_an_erase_does_not(real_app, make_member):
    author = make_member()
    board_id, _ = guild(real_app)
    post_id, old = add_post(author, board_id, age=3600)
    _, young = add_post(author, board_id, age=10, title="Just made")
    limited = author.browser()
    with limited.session_transaction() as session:
        session["qr_limited"] = True
        session["qr_expires_utc"] = int(time.time()) + 3600
    refused = post(limited, author, f"/delete_post/{old}")
    assert refused.status_code == 403 and refused.get_json()["need_password"] is True
    assert not rows(real_app, "SELECT deleted_utc FROM submissions WHERE id = :i", i=post_id)[0]["deleted_utc"]
    assert post(limited, author, f"/delete_post/{young}").status_code == 204, "inside the window an erase leaves nothing"
    assert post(limited, author, f"/delete_post/{old}", how="unlink").status_code == 403


# ---------------------------------------------------------------- the Report button and the queue

def test_a_member_reports_words_once_the_admins_purge_them_and_nobody_is_told_who(real_app, make_member):
    author, reporter, boss = make_member(), make_member(), admin_of(make_member)
    board_id, _ = guild(real_app)
    title = unique()
    post_id, b36 = add_post(author, board_id, title=title, age=3600)
    post(author.browser(), author, f"/delete_post/{b36}")
    (entry,) = entries(real_app, action="author_delete_post", target_submission_id=post_id)
    app, _ = real_app
    assert "Report this" not in deleted_tab(real_app), "a visitor has no button"
    visitor = app.test_client()
    assert visitor.post(f"/log/{entry['id']}/report", base_url=BASE, data={"reason": "x"}).status_code in (302, 401, 403)
    client = reporter.browser()
    assert "Report this" in deleted_tab(real_app, client=client)
    assert post(client, reporter, f"/log/{entry['id']}/report", reason="Contains my phone number").status_code == 200
    again = post(client, reporter, f"/log/{entry['id']}/report", reason="again")
    assert "already" in again.get_data(as_text=True).lower()
    assert post(client, reporter, f"/log/{entry['id']}/report", reason="").status_code == 400
    assert post(client, reporter, "/log/99999999/report", reason="nothing").status_code == 404
    queue = get(boss.browser(), "/admin/log_reports").get_data(as_text=True)
    assert f"log-report-{entry['id']}" in queue and "Contains my phone number" in queue and reporter.username not in queue
    done = post(boss.browser(), boss, f"/admin/log_reports/{entry['id']}", decision="purged")
    assert done.status_code == 303
    assert rows(real_app, "SELECT purged_utc FROM submissions WHERE id = :i", i=post_id)[0]["purged_utc"] > 0
    assert rows(real_app, "SELECT scrubbed_utc, body FROM log_snapshots WHERE entry_id = :e", e=entry["id"])[0]["scrubbed_utc"] > 0
    assert title not in deleted_tab(real_app)
    assert rows(real_app, "SELECT status FROM log_reports WHERE entry_id = :e", e=entry["id"]) == [{"status": "purged"}]
    assert entries(real_app, action="purge_post", target_submission_id=post_id), "the purge is in the Log as ever"


def test_hiding_a_reason_after_a_report_needs_level_four_and_a_member_cannot_decide(real_app, make_member):
    from ruqqus.helpers import action_log
    reporter, low, high, nobody = make_member(), admin_of(make_member, 3), admin_of(make_member, 4), make_member()
    board_id, board_name = guild(real_app)
    _, db_session = real_app
    db = db_session()
    entry_id = action_log.record(db, "hide_post_from_guild", 1, board_id=board_id, reason="A reason that names a person", role=action_log.GUILDMASTER)
    db.commit()
    assert post(reporter.browser(), reporter, f"/log/{entry_id}/report", reason="It names me").status_code == 200
    assert post(low.browser(), low, f"/admin/log_reports/{entry_id}", decision="redacted").status_code == 403
    assert post(nobody.browser(), nobody, f"/admin/log_reports/{entry_id}", decision="dismissed").status_code in (302, 403), "a member decides nothing"
    assert rows(real_app, "SELECT status FROM log_reports WHERE entry_id = :e", e=entry_id) == [{"status": "open"}]
    assert post(high.browser(), high, f"/admin/log_reports/{entry_id}", decision="redacted").status_code == 303
    assert rows(real_app, "SELECT redacted_utc FROM log_entries WHERE id = :e", e=entry_id)[0]["redacted_utc"] > 0
    assert entries(real_app, action="redact_entry", linked_entry_id=entry_id), "a correction is a new, linked entry"
    app, _ = real_app
    assert "A reason that names a person" not in get(app.test_client(), f"/log?guild={board_name}").get_data(as_text=True)
    assert rows(real_app, "SELECT status FROM log_reports WHERE entry_id = :e", e=entry_id) == [{"status": "redacted"}]


# ---------------------------------------------------------------- where else it shows

def test_a_comment_on_a_deleted_post_names_it_by_the_deleted_tabs_rules_and_an_erased_one_not_at_all(real_app, make_member):
    author, commenter = make_member(), make_member()
    board_id, _ = guild(real_app)
    published, p36 = add_post(author, board_id, title="Published then deleted", age=3600)
    erased, e36 = add_post(author, board_id, title="Erased quickly", age=10)
    add_comment(commenter, published, board_id, body="<p>On the published one</p>")
    post(author.browser(), author, f"/delete_post/{p36}")
    post(author.browser(), author, f"/delete_post/{e36}")
    app, _ = real_app
    profile = get(app.test_client(), f"/@{commenter.username}/comments").get_data(as_text=True)
    assert "Published then deleted" in profile and f"/log/post/{p36}" in profile
    assert "Erased quickly" not in profile
