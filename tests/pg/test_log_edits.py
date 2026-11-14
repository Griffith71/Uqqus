"""What a member edits, in the Log, on the real app and Postgres (slice D3b; the rules are tested without Postgres in tests/test_log_edits.py):
an edit inside the five-minute window is part of drafting and leaves nothing; after it one entry, with the editor named unless the post is
anonymous, the note as its reason and the Edits tab folding a run of edits to one item; an account's edits are limited to 30 an hour."""
import re
import secrets
import time

from sqlalchemy import text

BASE = "http://localhost"
AGENT = "ci-log-edits"


def address():
    return f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"


def get(client, path, country="US"):
    headers = {"User-Agent": AGENT, "Accept-Language": "en", "CF-IPCountry": country}
    return client.get(path, base_url=BASE, headers=headers, environ_base={"REMOTE_ADDR": address()})


def post(client, member, path, **fields):
    return client.post(path, base_url=BASE, headers={"User-Agent": AGENT}, data={"formkey": member.formkey(client), **fields},
                       environ_base={"REMOTE_ADDR": address()})


def guild(real_app, private=False):
    from ruqqus.classes import Board
    _, db_session = real_app
    db = db_session()
    board = Board(name="ci_edit_" + secrets.token_hex(3), description="a guild for the edit tests", description_html="<p>a guild</p>", creator_id=1,
                  subcat_id=0, is_private=private, created_utc=int(time.time()), color="23a9f2")
    db.add(board)
    db.commit()
    return board.id, board.name


def add_post(member, board_id, age=3600, **flags):
    from ruqqus.classes import Submission, SubmissionAux
    from ruqqus.helpers.base36 import base36encode
    db = member.db_session()
    db.rollback()
    with member.app.test_request_context("/"):
        row = Submission(author_id=member.id, board_id=board_id, original_board_id=board_id, created_utc=int(time.time()) - age, post_public=True, **flags)
    db.add(row)
    db.flush()
    db.add(SubmissionAux(id=row.id, title=f"Edit me {secrets.token_hex(3)}", body="before", body_html="<p>before</p>"))
    db.commit()
    return row.id, base36encode(row.id)


def add_comment(member, post_id, board_id, age=3600):
    from ruqqus.classes import Comment, CommentAux
    from ruqqus.helpers.base36 import base36encode
    db = member.db_session()
    db.rollback()
    with member.app.test_request_context("/"):
        row = Comment(author_id=member.id, parent_submission=post_id, level=1, original_board_id=board_id, created_utc=int(time.time()) - age)
    db.add(row)
    db.flush()
    db.add(CommentAux(id=row.id, body="before", body_html="<p>before</p>"))
    db.commit()
    return row.id, base36encode(row.id)


def rows(real_app, sql, **params):
    _, db_session = real_app
    db = db_session()
    db.rollback()
    db.expire_all()
    return [dict(r._mapping) for r in db.execute(text(sql), params)]


def edits(real_app, **where):
    clause = " AND ".join(f"{k} = :{k}" for k in where) or "true"
    return rows(real_app, f"SELECT * FROM log_entries WHERE log_type = 'edit' AND {clause} ORDER BY id", **where)


def edit_tab(real_app, query="", country="US"):
    app, _ = real_app
    return get(app.test_client(), f"/log?type=edit{query}", country).get_data(as_text=True)


def test_an_edit_inside_five_minutes_leaves_nothing_and_one_after_it_is_one_named_entry_with_the_note(real_app, make_member):
    author = make_member()
    board_id, _ = guild(real_app)
    young_id, young = add_post(author, board_id, age=200)          # past the old 90 seconds, inside the five minutes
    client = author.browser()
    assert post(client, author, f"/edit_post/{young}", title="A new title", body="drafting still").status_code in (200, 204, 302, 303)
    assert edits(real_app, target_submission_id=young_id) == []
    assert rows(real_app, "SELECT count(*) AS n FROM content_edit_history WHERE target_submission_id = :i", i=young_id)[0]["n"] == 0
    old_id, old = add_post(author, board_id, age=3600)
    done = post(client, author, f"/edit_post/{old}", title="Revised title", body="after", edit_summary="Fixed the wrong date")
    assert done.status_code in (200, 204, 302, 303)
    (entry,) = edits(real_app, target_submission_id=old_id)
    assert entry["action"] == "edit_post" and entry["actor_role"] == "author" and entry["reason"] == "Fixed the wrong date" and entry["audience"] == 0
    assert len(rows(real_app, "SELECT id FROM content_edit_history WHERE target_submission_id = :i AND action = 'edit'", i=old_id)) == 1
    page = edit_tab(real_app)
    assert "Fixed the wrong date" in page and author.username in page, "the editor is named on a post that isn't anonymous"
    assert f'href="/post/{old}"' in page or f"/post/{old}" in page


def test_the_edit_of_an_anonymous_post_names_nobody_and_filtering_by_its_author_finds_nothing(real_app, make_member):
    author, other = make_member(), make_member()
    board_id, _ = guild(real_app)
    post_id, b36 = add_post(author, board_id, is_anonymous=True)
    assert post(author.browser(), author, f"/edit_post/{b36}", title="Quietly revised", body="after", edit_summary="A quiet fix").status_code in (200, 204, 302, 303)
    assert len(edits(real_app, target_submission_id=post_id)) == 1
    page = edit_tab(real_app)
    assert "A quiet fix" in page and author.username not in page and "Anonymous" in page
    assert "A quiet fix" not in edit_tab(real_app, f"&by={author.username}"), "a filter by the editor finds only items that aren't anonymous"
    named_id, named = add_post(other, board_id)
    post(other.browser(), other, f"/edit_post/{named}", title="Named revision", body="after", edit_summary="Named note")
    by = edit_tab(real_app, f"&by={other.username}")
    assert "Named note" in by and "A quiet fix" not in by


def test_a_run_of_edits_to_one_item_is_one_line_and_the_filter_picks_posts_or_comments(real_app, make_member):
    author = make_member()
    board_id, _ = guild(real_app)
    post_id, b36 = add_post(author, board_id)
    client = author.browser()
    for n in range(3):
        assert post(client, author, f"/edit_post/{b36}", title=f"Run title {n}", body=f"run {n}", edit_summary=f"run note {n}").status_code in (200, 204, 302, 303)
    assert len(edits(real_app, target_submission_id=post_id)) == 3, "the log is still one row per edit"
    page = edit_tab(real_app, f"&by={author.username}")
    assert page.count('class="card mb-2 p-3 log-entry"') == 1 and "3 edits in a row" in page
    assert all(f"run note {n}" in page for n in range(3)), "the earlier ones are behind the disclosure"
    _, c36 = add_comment(author, post_id, board_id)
    assert post(client, author, f"/edit_comment/{c36}", body="after", edit_summary="comment note").status_code in (200, 204, 302, 303)
    assert "comment note" in edit_tab(real_app, "&what=comment") and "run note 0" not in edit_tab(real_app, "&what=comment")
    assert "run note 0" in edit_tab(real_app, "&what=post") and "comment note" not in edit_tab(real_app, "&what=post")
    assert edits(real_app, action="edit_comment")


def test_a_private_guilds_edit_is_not_on_the_public_tab_and_a_childs_filter_hides_a_rude_note(real_app, make_member):
    author = make_member()
    board_id, _ = guild(real_app, private=True)
    post_id, b36 = add_post(author, board_id)
    post(author.browser(), author, f"/edit_post/{b36}", title="Private revision", body="after", edit_summary="private guild note")
    (entry,) = edits(real_app, target_submission_id=post_id)
    assert entry["audience"] == 1 and "private guild note" not in edit_tab(real_app)
    public_id, public = add_post(author, guild(real_app)[0])
    post(author.browser(), author, f"/edit_post/{public}", title="Another", body="after", edit_summary="this is fucking nonsense")
    assert "this is fucking nonsense" in edit_tab(real_app, country="US")
    assert "this is fucking nonsense" not in edit_tab(real_app, country="GB"), "the Child level hides profanity, in a reason as anywhere"


def test_an_account_may_edit_thirty_times_an_hour_and_not_a_thirty_first(real_app, make_member):
    author = make_member()
    board_id, _ = guild(real_app)
    _, b36 = add_post(author, board_id)
    client = author.browser()
    statuses = [post(client, author, f"/edit_post/{b36}", title=f"Edit number {n}", body=f"body {n}").status_code for n in range(31)]
    assert statuses[:30] == [statuses[0]] * 30 and statuses[0] in (200, 204, 302, 303)
    assert statuses[30] == 429, "past thirty an hour the edit is refused"
    other = make_member()
    _, other36 = add_post(other, board_id)
    assert post(other.browser(), other, f"/edit_post/{other36}", title="Someone else's first", body="x").status_code in (200, 204, 302, 303), "per account, not per site"
