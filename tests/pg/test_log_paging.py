"""What a page of the Log's item, account and thread pages asks the database for, on the real app and Postgres (the second review's C56; the rules are tested
on SQLite in tests/test_log_paging.py).

The promises: page N of the log of a post, a comment or an account shows entries (N-1)*25 to N*25 of the newest-first list and reads them with an offset (the
page used to read the first N*25 and draw the last 25), and its Next link is there while there is more (it never was); the thread of a deleted post draws the
same page whatever its size (the first 25 top-level comments with everything under them, the next page the rest, the heading counting every comment, a
block, a hidden, a removed and a deleted comment as they were) and asks the database for the same number of statements for 200 comments as for 12, where it
read each comment's text on its own."""
import html
import time

from sqlalchemy import event

from tests.pg.test_log_deletes import add_comment, add_post, get, guild, ids_of, post, unique
from tests.pg.test_log_thread_controls import BLOCKED_LINE, block, deleted_post, take_down

OLD = 3600


def record_about(member, board_id, count, post_id=None, comment_id=None, action="hide_post_from_guild"):
    """`count` public entries about one post or comment, written a second apart: their ids, oldest first."""
    from ruqqus.helpers import action_log
    db = member.db_session()
    db.rollback()
    base = int(time.time()) - 100_000
    made = [action_log.record(db, action, member.id, board_id=board_id, post_id=post_id, comment_id=comment_id, role=action_log.GUILDMASTER,
                              reason=f"Reason number {i}", now=base + i) for i in range(count)]
    db.commit()
    return made


def statements_of(run):
    """The statements the real app's engine ran while `run()` did (with their parameters)."""
    from ruqqus.__main__ import _engine
    seen = []

    def note(conn, cursor, statement, parameters, context, executemany):
        seen.append((statement, parameters))

    event.listen(_engine, "before_cursor_execute", note)
    try:
        run()
    finally:
        event.remove(_engine, "before_cursor_execute", note)
    return seen


# ---------------------------------------------------------------- the log of an item or an account

def test_the_log_of_a_post_pages_by_offset_and_has_a_next_link_where_there_is_more(real_app, make_member):
    author, gm = make_member(), make_member()
    board_id, _ = guild(real_app)
    post_id, b36 = add_post(author, board_id, title=unique(), age=OLD)
    made = record_about(gm, board_id, 60, post_id=post_id)
    newest_first = [str(i) for i in reversed(made)]
    visitor = real_app[0].test_client()

    pages = {n: get(visitor, f"/post/{b36}/log?page={n}").get_data(as_text=True) for n in (1, 2, 3)}
    assert [ids_of(pages[n]) for n in (1, 2, 3)] == [newest_first[:25], newest_first[25:50], newest_first[50:]]
    assert 'rel="next"' in pages[1] and 'rel="next"' in pages[2] and 'rel="next"' not in pages[3], "the Next link never rendered before"
    assert 'href="?page=1"' in pages[2] and 'href="?page=2"' in pages[3] and 'href="?page=1"' not in pages[1]
    assert ids_of(get(visitor, f"/post/{b36}/log").get_data(as_text=True)) == newest_first[:25], "no page given is the first"

    # past the last page, and past the 40 pages the log reads: an empty page that is the page 40 shows, never an error
    page_40 = get(visitor, f"/post/{b36}/log?page=40")
    page_41 = get(visitor, f"/post/{b36}/log?page=41")
    assert page_40.status_code == page_41.status_code == 200
    assert ids_of(page_40.get_data(as_text=True)) == ids_of(page_41.get_data(as_text=True)) == []
    assert 'rel="next"' not in page_40.get_data(as_text=True) and 'rel="next"' not in page_41.get_data(as_text=True)


def test_a_page_of_the_log_reads_its_own_twenty_six_entries_and_not_everything_before_it(real_app, make_member):
    author, gm = make_member(), make_member()
    board_id, _ = guild(real_app)
    post_id, b36 = add_post(author, board_id, title=unique(), age=OLD)
    record_about(gm, board_id, 60, post_id=post_id)
    visitor = real_app[0].test_client()
    seen = statements_of(lambda: get(visitor, f"/post/{b36}/log?page=3"))
    reads = [params for statement, params in seen if "FROM log_entries" in statement and "ORDER BY created_utc DESC, id DESC LIMIT" in statement]
    assert reads, "the page reads the Log with the one statement fetch makes"
    assert any(isinstance(p, dict) and p.get("limit") == 26 and p.get("offset") == 50 for p in reads), reads
    assert not any(isinstance(p, dict) and p.get("limit") == 76 for p in reads), "the old read: the first 75 entries to draw the last 10"


def test_a_comments_and_an_accounts_log_have_a_second_page_too(real_app, make_member):
    author, gm, commenter = make_member(), make_member(), make_member()
    board_id, _ = guild(real_app)
    post_id, _ = add_post(author, board_id, title=unique(), age=OLD)
    comment_id, c36 = add_comment(commenter, post_id, board_id)
    on_comment = record_about(gm, board_id, 30, comment_id=comment_id, action="hide_comment_from_guild")
    on_account = record_about(gm, board_id, 30, post_id=post_id)
    visitor = real_app[0].test_client()

    first = get(visitor, f"/comment/{c36}/log").get_data(as_text=True)
    second = get(visitor, f"/comment/{c36}/log?page=2")
    assert second.status_code == 200
    assert ids_of(first) == [str(i) for i in reversed(on_comment)][:25] and ids_of(second.get_data(as_text=True)) == [str(i) for i in reversed(on_comment)][25:]
    assert 'rel="next"' in first and 'rel="next"' not in second.get_data(as_text=True)

    account = get(visitor, f"/@{author.username}/log").get_data(as_text=True)
    account_2 = get(visitor, f"/@{author.username}/log?page=2")
    assert account_2.status_code == 200
    newest_first = [str(i) for i in reversed(on_account)]
    assert ids_of(account) == newest_first[:25] and ids_of(account_2.get_data(as_text=True)) == newest_first[25:]
    assert 'rel="next"' in account and 'rel="next"' not in account_2.get_data(as_text=True)


# ---------------------------------------------------------------- the thread of a deleted post

def special(i):
    """What kind of comment number i is: anonymous (by the ghost), hidden by the guild, removed by the admins, or plain."""
    return "anon" if i % 7 == 3 else "hidden" if i % 11 == 4 else "banned" if i % 13 == 6 else ""


def build_thread(real_app, members, ghost, count, deleted_count):
    """`count` comments under one deleted post in groups of five (a top-level comment and a chain of four under it, to depth 3), written by `members` in
    turn; every 7th anonymous by `ghost`, every 11th hidden by the guild, every 13th removed, and `deleted_count` of the plain ones deleted by their
    authors after the five-minute window. Returns (post b36, {i: (b36, author)}, the indexes deleted)."""
    author = members[0]
    board_id, post_id, b36 = deleted_post(real_app, author)
    comments, ids, levels, parents = {}, [], [], []
    for i in range(count):
        kind = special(i)
        if i % 5 == 0:
            parent, level = None, 0
        elif levels[i - 1] < 3:
            parent, level = ids[i - 1], levels[i - 1] + 1
        else:
            parent, level = parents[i - 1], 3                    # the chain stays at depth 3: the next one is a sibling of the last
        writer = ghost if kind == "anon" else members[i % len(members)]
        flags = {"is_anonymous": True} if kind == "anon" else {"hidden_by_guild": True} if kind == "hidden" else {"is_banned": True} if kind == "banned" else {}
        comment_id, c36 = add_comment(writer, post_id, board_id, body=f"<p>Words of comment cm{i:03d}x</p>", age=OLD + (count - i), parent=parent, **flags)
        ids.append(comment_id)
        levels.append(level)
        parents.append(parent)
        comments[i] = (c36, writer)
    plain = [i for i in range(count) if not special(i)]
    deleted = plain[10:10 + deleted_count] if count >= 50 else plain[:deleted_count]
    for i in deleted:
        c36, writer = comments[i]
        assert post(writer.browser(), writer, f"/delete/comment/{c36}").status_code == 204
    take_down(author, b36)
    return b36, comments, deleted


def thread_page(client, b36, number=1):
    page = get(client, f"/log/post/{b36}?page={number}")
    assert page.status_code == 200, page.status_code
    return html.unescape(page.get_data(as_text=True))


def test_a_big_thread_is_drawn_as_it_always_was_in_two_pages(real_app, make_member):
    alice, bob, carol, reader, ghost = (make_member() for _ in range(5))
    b36, comments, deleted = build_thread(real_app, [alice, bob, carol], ghost, 200, 3)
    block(reader, carol)
    app, _ = real_app
    client, visitor = reader.browser(), app.test_client()

    first, second = thread_page(client, b36, 1), thread_page(client, b36, 2)
    assert first.count('class="log-thread-comment') == 125 and second.count('class="log-thread-comment') == 75, "25 top-level comments with their chains, then the rest"
    assert "200 comments" in first and "200 comments" in second, "the heading counts every comment read, not the page's"
    assert 'rel="next"' in first and 'rel="next"' not in second and 'rel="prev"' in second

    for i, (c36, writer) in comments.items():
        page = first if i // 5 < 25 else second
        token, other = f"cm{i:03d}x", second if page is first else first
        assert token not in other, i
        kind = special(i)
        if i in deleted:
            assert token in page, f"comment {i} was deleted by its author: its words stay, as Anonymous"
        elif kind in ("hidden", "banned"):
            assert token not in page, f"comment {i} is hidden or removed: a line, never its words"
        elif kind == "anon":
            assert token in page and ghost.username not in page
        elif writer.id == carol.id:
            assert token not in page, f"comment {i} is by an account the reader blocked"
        else:
            assert token in page, i
    assert (first + second).count(BLOCKED_LINE) == len([i for i, (_, w) in comments.items() if w.id == carol.id and not special(i)
                                                                                      and i not in deleted])
    assert "A comment hidden by a guildmaster." in first and "A comment removed by the site's admins." in first
    assert carol.username not in first + second, "the placeholder for a block names nobody"

    stranger = thread_page(visitor, b36, 1)
    assert BLOCKED_LINE not in stranger, "a visitor has blocked nobody"
    assert any(f"cm{i:03d}x" in stranger for i, (_, w) in comments.items() if w.id == carol.id and not special(i) and i // 5 < 25 and i not in deleted)


def test_a_page_of_a_big_thread_asks_the_database_what_a_small_one_does(real_app, make_member):
    alice, bob, carol, reader, ghost = (make_member() for _ in range(5))
    small_b36, _, _ = build_thread(real_app, [alice, bob, carol], ghost, 12, 2)
    big_b36, _, _ = build_thread(real_app, [alice, bob, carol], ghost, 200, 3)
    block(reader, carol)
    client = reader.browser()

    def asked(b36, number):
        return statements_of(lambda: thread_page(client, b36, number))

    small = asked(small_b36, 1)
    for number in (1, 2):
        big = asked(big_b36, number)
        assert len(big) <= len(small) + 2, (number, len(big), len(small))
        texts = [s for s, _ in big if "comments_aux" in s]
        assert len(texts) <= 2, "the text of the page's comments rides with them; it is not read for each one"
        assert len([s for s, _ in big if "SELECT id, parent_comment_id FROM comments" in s]) == 1, "one index read for the thread"
        assert len([s for s, _ in big if "userblocks" in s]) == len([s for s, _ in small if "userblocks" in s])


def test_a_thread_past_its_last_page_is_empty_and_a_post_nobody_commented_on_says_so(real_app, make_member):
    alice, bob, carol, ghost = (make_member() for _ in range(4))
    b36, _, _ = build_thread(real_app, [alice, bob, carol], ghost, 8, 1)
    visitor = real_app[0].test_client()
    page = thread_page(visitor, b36, 5)
    assert "8 comments" in page and "log-thread-comment" not in page and 'rel="next"' not in page
    empty = thread_page(visitor, deleted_empty(real_app, alice), 1)
    assert "Nobody commented on it." in empty and "0 comments" in empty


def deleted_empty(real_app, author):
    _, _, b36 = deleted_post(real_app, author)
    take_down(author, b36)
    return b36
