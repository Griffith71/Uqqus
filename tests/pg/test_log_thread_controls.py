"""The Log's thread of a deleted post, on the real app and Postgres: a placeholder for a comment by an account the reader muted or blocked, Flag on a live
comment, and the Log's own Report under a comment its author deleted (C31 and C75 of the second review of the Log; the rules and the wiring are tested
without Postgres in tests/test_log_thread.py and tests/test_log_thread_assets.py).

The promises: a mute or a block hides only a NAMED comment, so it can never say whose an anonymous one is (and a comment its author deleted, which is
drawn as Anonymous, is shown to the muter and the blocker as it is to anyone); the comments under a placeholder stay; the block is one query for the
page; Flag is the site's usual report, offered to a signed-in reader who is not the author (a limited QR sign-in is a stranger to its own anonymous
comment), and never names the author of an anonymous one; the Log's own Report is under a comment whose words are shown, once per person; a report stays
open to a child account and is refused to a held sign-up; and the page still has no vote, comment, tip or share control, no ad and no link from deleted
words."""
import html
import re
import time

from sqlalchemy import event, text

from tests.pg.test_log_deletes import add_comment, add_post, admin_of, entries, get, guild, post, rows, unique

OLD = 3600


def deleted_post(real_app, author, **flags):
    """A guild, a post of the author's that is then deleted (it stays in the Log as Anonymous, and the thread is the post's own address): (board id,
    post id, its base 36 id). Comments are added before it is deleted."""
    board_id, _ = guild(real_app)
    post_id, b36 = add_post(author, board_id, title=unique(), age=OLD, **flags)
    return board_id, post_id, b36


def take_down(author, b36):
    assert post(author.browser(), author, f"/delete_post/{b36}").status_code == 204


def thread(real_app, client, b36, country="US"):
    page = get(client, f"/log/post/{b36}", country)
    assert page.status_code == 200, page.status_code
    return html.unescape(page.get_data(as_text=True))


def sql(member, statement, **params):
    db = member.db_session()
    db.rollback()
    db.execute(text(statement), params)
    db.commit()


def mute(member, other):
    sql(member, "INSERT INTO usermutes (user_id, target_id, created_utc) VALUES (:a, :b, :n)", a=member.id, b=other.id, n=int(time.time()))


def block(member, other):
    sql(member, "INSERT INTO userblocks (user_id, target_id, created_utc) VALUES (:a, :b, :n)", a=member.id, b=other.id, n=int(time.time()))


BLOCKED_LINE = "A comment by an account you blocked or that blocked you."


# ---------------------------------------------------------------- a mute

def test_a_comment_by_a_muted_account_is_a_placeholder_with_its_comments_kept(real_app, make_member):
    author, muter, muted, other = make_member(), make_member(), make_member(), make_member()
    board_id, post_id, b36 = deleted_post(real_app, author)
    muted_id, muted36 = add_comment(muted, post_id, board_id, body="<p>Words from the muted account</p>")
    add_comment(other, post_id, board_id, body="<p>A comment under the muted one</p>", parent=muted_id)
    add_comment(other, post_id, board_id, body="<p>Plain words by somebody else</p>")
    mute(muter, muted)
    take_down(author, b36)
    app, _ = real_app

    page = thread(real_app, muter.browser(), b36)
    assert f"You muted @{muted.username}" in page and f"/settings/unmute?username={muted.username}" in page
    assert "Words from the muted account" not in page
    assert "A comment under the muted one" in page, "the comments under a placeholder stay, as in any thread"
    assert "Plain words by somebody else" in page

    for reader in (other.browser(), app.test_client()):                 # somebody who muted nobody, and a visitor
        stranger = thread(real_app, reader, b36)
        assert "Words from the muted account" in stranger and "You muted" not in stranger and "/settings/unmute" not in stranger

    unmuted = post(muter.browser(), muter, f"/settings/unmute?username={muted.username}")
    assert unmuted.status_code == 200
    again = thread(real_app, muter.browser(), b36)
    assert "Words from the muted account" in again and "You muted" not in again


def test_a_mute_or_a_block_never_hides_an_anonymous_comment_or_one_its_author_deleted(real_app, make_member):
    """Hiding only the anonymous ones would tell the reader which they are: the same promise every feed and thread keeps."""
    author, reader, muted, blocked = make_member(), make_member(), make_member(), make_member()
    board_id, post_id, b36 = deleted_post(real_app, author)
    add_comment(muted, post_id, board_id, body="<p>Anonymous words of the muted one</p>", is_anonymous=True)
    add_comment(blocked, post_id, board_id, body="<p>Anonymous words of the blocked one</p>", is_anonymous=True)
    _, muted_deleted = add_comment(muted, post_id, board_id, body="<p>Words the muted one deleted</p>")
    _, blocked_deleted = add_comment(blocked, post_id, board_id, body="<p>Words the blocked one deleted</p>")
    add_comment(muted, post_id, board_id, body="<p>Named words of the muted one</p>")
    add_comment(blocked, post_id, board_id, body="<p>Named words of the blocked one</p>")
    mute(reader, muted)
    block(reader, blocked)
    assert post(muted.browser(), muted, f"/delete/comment/{muted_deleted}").status_code == 204
    assert post(blocked.browser(), blocked, f"/delete/comment/{blocked_deleted}").status_code == 204
    take_down(author, b36)
    app, _ = real_app

    page = thread(real_app, reader.browser(), b36)
    stranger = thread(real_app, app.test_client(), b36)
    for words in ("Anonymous words of the muted one", "Anonymous words of the blocked one", "Words the muted one deleted", "Words the blocked one deleted"):
        assert words in page and words in stranger, f"{words!r}: an anonymous comment is shown to the reader exactly as it is to a stranger"
    for words in ("Named words of the muted one", "Named words of the blocked one"):
        assert words not in page and words in stranger, f"{words!r}: a named one is a placeholder"
    assert page.count(BLOCKED_LINE) == 1 and f"You muted @{muted.username}" in page
    assert blocked.username not in page, "the placeholder for a block names nobody"
    assert page.count(muted.username) == 2, "the mute's own sentence and its Unmute link, nowhere else"


# ---------------------------------------------------------------- a block

def test_a_block_either_way_is_a_nameless_line_and_is_asked_once_for_the_page(real_app, make_member):
    author, reader, blocking, blocked, neutral = make_member(), make_member(), make_member(), make_member(), make_member()
    board_id, post_id, b36 = deleted_post(real_app, author)
    for member, words in ((blocking, "Words of the one the reader blocked"), (blocked, "Words of the one who blocked the reader"),
                          (neutral, "Words of somebody neither")):
        add_comment(member, post_id, board_id, body=f"<p>{words}</p>")
    add_comment(blocking, post_id, board_id, body="<p>Anonymous words of the one the reader blocked</p>", is_anonymous=True)
    block(reader, blocking)
    block(blocked, reader)
    take_down(author, b36)

    page = thread(real_app, reader.browser(), b36)
    assert page.count(BLOCKED_LINE) == 2
    assert "Words of the one the reader blocked" not in page and "Words of the one who blocked the reader" not in page
    assert "Words of somebody neither" in page and "Anonymous words of the one the reader blocked" in page
    assert blocking.username not in page and blocked.username not in page

    # one query on `userblocks` for the page, however many comments it holds: a thread of 12 asks as often as a thread of 1
    def blocks_asked(count):
        _, small_post, small36 = deleted_post(real_app, author)
        for n in range(count):
            add_comment(neutral if n % 2 else blocking, small_post, board_id, body=f"<p>Comment number {n}</p>")
        take_down(author, small36)
        engine = reader.db_session().get_bind()
        asked = []

        def watch(conn, cursor, statement, parameters, context, executemany):
            if "userblocks" in statement:
                asked.append(statement)

        event.listen(engine, "before_cursor_execute", watch)
        try:
            client = reader.browser()
            assert get(client, f"/log/post/{small36}").status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", watch)
        return len(asked)

    assert blocks_asked(1) == blocks_asked(12)


# ---------------------------------------------------------------- Flag

def test_flag_is_offered_on_a_live_comment_to_a_reader_who_is_not_its_author_and_never_names_an_anonymous_one(real_app, make_member):
    author, reader, writer, hidden_one = make_member(), make_member(), make_member(), make_member()
    board_id, post_id, b36 = deleted_post(real_app, author)
    _, named36 = add_comment(writer, post_id, board_id, body="<p>Named words to flag</p>")
    _, anon36 = add_comment(hidden_one, post_id, board_id, body="<p>Anonymous words to flag</p>", is_anonymous=True)
    take_down(author, b36)
    app, _ = real_app

    page = thread(real_app, reader.browser(), b36)
    assert 'id="reportCommentModal"' in page, "the modal the site's every signed-in page carries"
    assert f"report_commentModal('{named36}', \"{writer.username}\")" in page
    assert re.search(rf"report_commentModal\('{anon36}', \"Anonymous( #\d+)?\"\)", page)
    assert hidden_one.username not in page, "an anonymous comment's Flag names nobody"

    own = thread(real_app, writer.browser(), b36)
    assert f"report_commentModal('{named36}'" not in own, "not on your own comment"
    assert f"report_commentModal('{anon36}'" in own

    visitor = thread(real_app, app.test_client(), b36)
    assert "report_commentModal(" not in visitor and 'id="reportCommentModal"' not in visitor, "a visitor has nothing to report with"

    # the anonymous comment's own author is its author: no Flag on it; a limited QR sign-in is a stranger to it and is offered one, as comments.html does
    mine = thread(real_app, hidden_one.browser(), b36)
    assert f"report_commentModal('{anon36}'" not in mine
    limited = hidden_one.browser()
    with limited.session_transaction() as session:
        session["qr_limited"] = True
        session["qr_expires_utc"] = int(time.time()) + 3600
    strange = thread(real_app, limited, b36)
    assert re.search(rf"report_commentModal\('{anon36}', \"Anonymous( #\d+)?\"\)", strange), "offered Flag, under the number and not the name"


def test_flag_reaches_the_sites_own_route_once_for_a_member_and_a_child_account_but_not_a_held_one(real_app, make_member):
    author, reader, child, held = make_member(), make_member(), make_member(), make_member()
    board_id, post_id, b36 = deleted_post(real_app, author)
    comment_id, c36 = add_comment(make_member(), post_id, board_id, body="<p>Words to flag</p>")
    sql(child, "UPDATE users SET age_declared = 2, age_declared_utc = 5, filter_level = 2, age_lock = 1 WHERE id = :u", u=child.id)
    sql(held, "UPDATE users SET quarantined_utc = :now WHERE id = :u", now=int(time.time()), u=held.id)
    take_down(author, b36)

    def flags(member):
        return rows(real_app, "SELECT id FROM commentflags WHERE comment_id = :c AND user_id = :u", c=comment_id, u=member.id)

    client = reader.browser()
    assert post(client, reader, f"/api/flag/comment/{c36}").status_code == 204 and len(flags(reader)) == 1
    assert post(client, reader, f"/api/flag/comment/{c36}").status_code == 409, "once"
    assert post(child.browser(), child, f"/api/flag/comment/{c36}").status_code == 204, "a child account only reads, and may still report"
    refused = post(held.browser(), held, f"/api/flag/comment/{c36}")
    assert refused.status_code == 403 and not flags(held)
    app, _ = real_app
    visitor = app.test_client().post(f"/api/flag/comment/{c36}", base_url="http://localhost", data={"report_type": "admin"})
    assert visitor.status_code in (302, 401, 403), "a visitor reports nothing"


# ---------------------------------------------------------------- the Log's own Report

def test_a_comment_its_author_deleted_can_be_reported_once_and_draws_no_link_from_its_words(real_app, make_member):
    author, writer, reporter, boss = make_member(), make_member(), make_member(), admin_of(make_member)
    board_id, post_id, b36 = deleted_post(real_app, author)
    body = '<p>Words the commenter deleted <a href="https://example.org/deleted-link">a link</a></p>'
    comment_id, c36 = add_comment(writer, post_id, board_id, body=body)
    rude_id, rude36 = add_comment(writer, post_id, board_id, body="<p>this is fucking nonsense</p>", word_severity=1)
    assert post(writer.browser(), writer, f"/delete/comment/{c36}").status_code == 204
    assert post(writer.browser(), writer, f"/delete/comment/{rude36}").status_code == 204
    take_down(author, b36)
    (entry,) = entries(real_app, action="author_delete_comment", target_comment_id=comment_id)
    (rude_entry,) = entries(real_app, action="author_delete_comment", target_comment_id=rude_id)
    app, _ = real_app
    form = f'action="/log/{entry["id"]}/report"'

    visitor = thread(real_app, app.test_client(), b36)
    assert "Words the commenter deleted" in visitor and "/report" not in visitor, "a visitor has no form"
    page = thread(real_app, reporter.browser(), b36)
    assert "Words the commenter deleted" in page and form in page and 'href="https://example.org' not in page
    assert 'href="https://example.org' not in visitor and writer.username not in page and writer.username not in visitor
    assert f'action="/log/{rude_entry["id"]}/report"' in page

    # the Child level (a reader in the UK) is shown a line for profanity: nothing to report there, and no form
    gb = html.unescape(get(reporter.browser(), f"/log/post/{b36}", "GB").get_data(as_text=True))
    assert "this is fucking nonsense" not in gb and f'action="/log/{rude_entry["id"]}/report"' not in gb and form in gb

    client = reporter.browser()
    assert post(client, reporter, f"/log/{entry['id']}/report", reason="Contains my address").status_code == 200
    assert "already" in post(client, reporter, f"/log/{entry['id']}/report", reason="again").get_data(as_text=True).lower()
    queue = get(boss.browser(), "/admin/log_reports").get_data(as_text=True)
    assert f"log-report-{entry['id']}" in queue and "Contains my address" in queue and reporter.username not in queue

    assert post(boss.browser(), boss, f"/admin/log_reports/{entry['id']}", decision="purged").status_code == 303
    after = thread(real_app, reporter.browser(), b36)
    assert "Words the commenter deleted" not in after and form not in after, "once the admins erased the words there is nothing left to report"


def test_a_line_or_a_placeholder_has_no_report_and_the_thread_has_no_vote_comment_tip_share_or_ad_control(real_app, make_member):
    author, writer, reader, muted = make_member(), make_member(), make_member(), make_member()
    board_id, post_id, b36 = deleted_post(real_app, author)
    add_comment(writer, post_id, board_id, body="<p>Ordinary words</p>")
    add_comment(muted, post_id, board_id, body="<p>Words of the muted one</p>")
    mute(reader, muted)
    take_down(author, b36)
    page = thread(real_app, reader.browser(), b36)
    placeholder = page.split(f"You muted @{muted.username}")[1].split("log-thread-comment")[0]
    assert "report_commentModal" not in placeholder and "/report" not in placeholder
    for control in ('class="upvote', 'data-id-up', 'data-target="#tipModal"', 'data-target="#sharePost'):
        assert control not in page, f"no vote, tip or share control: {control}"
    assert "ad-slot" not in page and "adsbygoogle" not in page, "no ad on a record"
