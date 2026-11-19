"""The word-filter re-scan and the Log's kept copies (the second review's C29): `action_log.rescan_snapshot_severities`, what a scrub does to a
copy's rating, and how `word_filter_store.rescan` and `scripts/rescan_word_filter.py` reach it.

The promise: a `delete` copy is public and never expires, and a reader's word filter hides or shows its words by the rating it was kept with, so
after the admins change the list every copy that still holds words is rated again, the way its item is rated (a post by its escaped title, its
text and its poll's options, a comment by its text); a scrubbed copy holds no words, carries no rating and is never rated again, even by a re-scan
that read it before the purge; a stopped run is simply run again."""
import html
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import action_log as log
from ruqqus.helpers.wordfilter import WordFilter

import log_tables

ROOT = Path(__file__).resolve().parent.parent
NOW = 1_793_000_000
DAY = 86400

# a list of the admins' own: "grape" is extreme, "melon" is profanity
FILTER = WordFilter([{"word": "grape", "severity": 2}, {"word": "melon", "severity": 1}])


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for statement in log_tables.migration_statements(read("scripts", "migrations", "2026-12-09_action_log.sql")):
            conn.execute(text(statement))
        log_tables.apply(conn)
    with Session(engine) as session:
        yield session


class Item:
    """A post or a comment as the Log keeps it (`action_log.snapshot_of` reads these attributes)."""
    author_id, board_id, url, body, is_sensitive = 30, 1, None, None, False

    def __init__(self, id, title=None, body_html=None, severity=0):
        self.id, self.title, self.body_html, self.word_severity = id, title, body_html, severity


def keep(db, item, kind=log.DELETE, comment=False, public=True):
    return log.keep_snapshot(db, log.snapshot_of(item, kind=kind, comment=comment, public=public), now=NOW)


def rating(db, snapshot_id):
    return db.execute(text("SELECT word_severity FROM log_snapshots WHERE id = :i"), {"i": snapshot_id}).scalar()


def rescan(db, **kw):
    return log.rescan_snapshot_severities(db, FILTER.severity, FILTER.severity_html, **kw)


# --- the re-rating -------------------------------------------------------------------------------------------------------------------

def test_a_stale_rating_follows_the_list_both_ways_and_a_second_run_changes_nothing(db):
    was_clean = keep(db, Item(1, "A title", "<p>a grape in the text</p>", severity=0))      # the word was added to the list since
    was_rude = keep(db, Item(2, "A title", "<p>nothing to see</p>", severity=2))           # the word was taken off it
    unchanged = keep(db, Item(3, "A melon", "<p>fine</p>", severity=1))
    assert rescan(db) == 2
    assert (rating(db, was_clean), rating(db, was_rude), rating(db, unchanged)) == (2, 0, 1)
    assert rescan(db) == 0, "run again, nothing changes"


def test_every_kind_of_copy_that_still_holds_words_is_rated_not_only_the_public_delete_copy(db):
    ids = [keep(db, Item(10 + n, "t", "<p>grape</p>"), kind=kind, public=False)
           for n, kind in enumerate((log.HIDE, log.AUTHOR_HIDE, log.GRACE, log.EVIDENCE))]
    assert rescan(db) == 4
    assert [rating(db, i) for i in ids] == [2, 2, 2, 2]


def test_a_batch_of_one_with_a_commit_each_walks_every_copy_by_its_cursor_and_never_repeats_or_skips(db):
    ids = [keep(db, Item(20 + n, "t", "<p>melon</p>")) for n in range(5)]
    seen = []
    assert rescan(db, batch=1, commit_each=True, log=seen.append) == 5
    assert [rating(db, i) for i in ids] == [1] * 5
    assert len(seen) == 5 and seen[-1] == "Log copies: 5 checked", "one line a batch, counting what was read so far"
    assert rescan(db, batch=1, commit_each=True) == 0


def test_a_comment_copy_is_rated_by_its_text_only_and_a_post_copy_by_its_title_text_and_poll(db):
    comment = keep(db, Item(30, "a grape in the title field", "<p>kind words</p>"), comment=True)
    post_title = keep(db, Item(31, "a melon in the title", "<p>kind words</p>"))
    post_text = keep(db, Item(32, "a title", "<p>a grape here</p>"))
    post_poll = keep(db, Item(33, "a title", "<p>kind words</p>"))
    clean = keep(db, Item(34, "a title", "<p>kind words</p>", severity=2))
    assert rescan(db, poll_text=lambda _db, ids: {33: "grape or pear"} if 33 in ids else {}) == 4
    assert rating(db, comment) == 0, "a comment has no title: only its text is read"
    assert (rating(db, post_title), rating(db, post_text), rating(db, post_poll), rating(db, clean)) == (1, 2, 2, 0)


def test_a_comment_is_never_asked_for_a_polls_text_and_the_ids_asked_are_the_posts_of_the_page(db):
    keep(db, Item(40, None, "<p>text</p>"), comment=True)
    keep(db, Item(41, "t", "<p>text</p>"))
    asked = []

    def poll_text(_db, ids):
        asked.append(list(ids))
        return {}
    rescan(db, poll_text=poll_text)
    assert asked == [[41]]


def test_a_title_is_stored_escaped_and_is_read_as_it_was_typed(db):
    """`&amp;` is one character the author typed: it must not split or join words, so the title is unescaped before it is read."""
    word = WordFilter([{"word": "grape", "severity": 2}])
    escaped = keep(db, Item(50, "fish &amp; grape", "<p>x</p>"))
    assert log.rescan_snapshot_severities(db, word.severity, word.severity_html) == 1
    assert rating(db, escaped) == 2 == word.severity(html.unescape("fish &amp; grape"))


def test_what_the_item_would_be_rated_is_what_the_copy_is_rated(db):
    """The same function as `word_filter_store.post_severity`: max of the unescaped title, the text and the poll's options."""
    title, body, extra = "melon &amp; pear", "<p>a <b>gr</b>ape</p>", "plum or fig"
    copy = keep(db, Item(60, title, body))
    rescan(db, poll_text=lambda _db, ids: {60: extra})
    assert rating(db, copy) == max(FILTER.severity(html.unescape(title)), FILTER.severity_html(body), FILTER.severity(extra))


def test_a_copy_with_no_words_at_all_is_rated_clean(db):
    copy = keep(db, Item(70, None, None, severity=2))
    assert rescan(db) == 1 and rating(db, copy) == 0


# --- a scrubbed copy ---------------------------------------------------------------------------------------------------------------

def test_a_scrub_zeroes_the_rating_with_the_words_and_a_rescan_never_gives_it_back(db):
    copy = keep(db, Item(80, "melon", "<p>grape</p>", severity=2))
    assert log.scrub_for(db, post_ids=[80]) == 1
    row = db.execute(text("SELECT title, body_html, word_severity, scrubbed_utc FROM log_snapshots WHERE id = :i"), {"i": copy}).first()
    assert row[0] is None and row[1] is None and row[2] == 0 and row[3] > 0
    assert log.rescan_snapshot_severities(db, lambda words: 2, lambda markup: 2) == 0, "even a list that rates everything"
    assert rating(db, copy) == 0


def test_a_comment_copy_is_zeroed_by_its_own_purge_too(db):
    copy = keep(db, Item(81, None, "<p>grape</p>", severity=2), comment=True)
    assert log.scrub_for(db, comment_ids=[81]) == 1 and rating(db, copy) == 0


def test_the_cycles_scrub_on_a_date_zeroes_the_rating_of_a_private_copy(db):
    private = keep(db, Item(82, "melon", "<p>grape</p>", severity=2), kind=log.GRACE, public=False)
    public = keep(db, Item(83, "melon", "<p>grape</p>", severity=2))
    assert log.scrub_due(db, NOW + 400 * DAY) == 1
    assert rating(db, private) == 0, "emptied: nothing left to rate"
    assert rating(db, public) == 2, "a delete copy never expires, and keeps its rating"


def test_a_purge_that_lands_after_the_re_scan_read_the_copy_is_not_given_a_rating_again(db):
    """The re-scan reads a batch, an admin's purge empties a copy of it, then the re-scan writes: the write is guarded by `scrubbed_utc = 0`."""
    copy = keep(db, Item(84, "t", "<p>kind words</p>", severity=0))

    def purge_meanwhile(_db, ids):
        log.scrub_for(db, post_ids=[84])
        return {84: "grape"}               # the rating the re-scan was about to write is 2
    assert rescan(db, poll_text=purge_meanwhile) == 0, "not counted: nothing was written"
    assert rating(db, copy) == 0
    assert db.execute(text("SELECT scrubbed_utc FROM log_snapshots WHERE id = :i"), {"i": copy}).scalar() > 0


def test_a_scrubbed_copy_among_live_ones_is_skipped_and_the_rest_are_still_rated(db):
    gone = keep(db, Item(90, "t", "<p>x</p>"))
    kept = keep(db, Item(91, "t", "<p>grape</p>"))
    log.scrub_for(db, post_ids=[90])
    assert rescan(db, batch=1) == 1
    assert (rating(db, gone), rating(db, kept)) == (0, 2)


# --- the wiring ----------------------------------------------------------------------------------------------------------------------

def test_the_update_is_guarded_by_the_scrub_and_the_rating_it_would_write():
    source = read("ruqqus", "helpers", "action_log.py")
    assert "UPDATE log_snapshots SET word_severity = :s WHERE id = :i AND scrubbed_utc = 0 AND word_severity <> :s" in source
    assert '"WHERE id > :last AND scrubbed_utc = 0 ORDER BY id LIMIT :n"' in source, "scrubbed copies are never read, and the cursor is the id"


def test_both_statements_that_empty_a_copy_zero_its_rating():
    source = read("ruqqus", "helpers", "action_log.py")
    assert "body_html = NULL, word_severity = 0, scrubbed_utc = :now" in source.split("SCRUB_DUE = (")[1].split(")")[0]
    assert source.count("body_html = NULL, word_severity = 0, scrubbed_utc = :now") == 2, "SCRUB_DUE and scrub_for"


def test_the_site_wide_rescan_reaches_the_copies_after_its_final_commit_and_commits_as_it_goes():
    source = read("ruqqus", "helpers", "word_filter_store.py")
    body = source.split("def rescan(")[1]
    last_commit = body.rfind("db.commit()")
    call = body.index("action_log.rescan_snapshot_severities(db, f.severity, f.severity_html")
    assert call > last_commit, "a database that can't do this step can't undo the re-rating before it"
    assert "commit_each=True" in body[call:call + 200] and "poll_text=_poll_texts" in body[call:call + 200]
    assert "return changed" in body[call:]


def test_the_script_still_calls_rescan_with_a_log_function_so_the_batches_are_printed():
    script = read("scripts", "rescan_word_filter.py")
    assert "rescan(db, log=print)" in script and "rating(s) changed" in script


def test_the_poll_text_is_read_for_the_primary_post_of_a_forward_copy():
    source = read("ruqqus", "helpers", "word_filter_store.py")
    assert "def _poll_texts(db, post_ids):" in source
    assert "(repost_id or i)" in source and "poll_store.option_texts(db, set(primary.values()))" in source
