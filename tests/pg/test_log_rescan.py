"""The word-filter re-scan and the Log's kept copies, on the real app and Postgres (the second review's C29; the rules and the SQL are tested without
Postgres in tests/test_log_snapshot_rescan.py).

The promises: a `delete` copy is public and never expires, so when the admins change the word list a re-scan rates it again (a Child-level reader
stops being shown words the new list hides, and a clean copy that an old list had hidden comes back), the poll of a deleted post counts as it counts
for the live post, and a purge from the Report queue leaves a copy with no words and no rating, which a later re-scan leaves alone."""
import re
import secrets
import time

from sqlalchemy import text

BASE = "http://localhost"
AGENT = "ci-log-rescan"
RUDE = f"this is fucking nonsense {secrets.token_hex(4)}"        # one per run: the Log is one table for the whole session


def unique(title="Copy"):
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


def guild(real_app):
    from ruqqus.classes import Board
    _, db_session = real_app
    db = db_session()
    board = Board(name="ci_rescan_" + secrets.token_hex(3), description="a guild for the re-scan tests", description_html="<p>a guild</p>", creator_id=1,
                  subcat_id=0, created_utc=int(time.time()), color="23a9f2")
    db.add(board)
    db.commit()
    return board.id


def add_post(member, board_id, title, body, age=3600, **flags):
    from ruqqus.classes import Submission, SubmissionAux
    from ruqqus.helpers.base36 import base36encode
    db = member.db_session()
    db.rollback()
    with member.app.test_request_context("/"):
        row = Submission(author_id=member.id, board_id=board_id, original_board_id=board_id, created_utc=int(time.time()) - age, post_public=True, **flags)
    db.add(row)
    db.flush()
    db.add(SubmissionAux(id=row.id, title=title, body=re.sub("<[^>]+>", "", body), body_html=body))
    db.commit()
    return row.id, base36encode(row.id)


def rows(real_app, sql, **params):
    _, db_session = real_app
    db = db_session()
    db.rollback()
    db.expire_all()
    return [dict(r._mapping) for r in db.execute(text(sql), params)]


def copy_of(real_app, post_id):
    (row,) = rows(real_app, "SELECT id, title, word_severity, scrubbed_utc, body_html FROM log_snapshots WHERE kind = 'delete' AND target_submission_id = :i", i=post_id)
    return row


def deleted_tab(real_app, country="US"):
    app, _ = real_app
    return get(app.test_client(), "/log?type=deletion", country).get_data(as_text=True)


def rescan(real_app):
    from ruqqus.helpers.word_filter_store import rescan as run
    _, db_session = real_app
    db = db_session()
    try:
        return run(db)
    finally:
        db.close()


def test_a_changed_list_rates_a_public_copy_again_and_the_readers_filter_follows(real_app, make_member):
    author = make_member()
    board_id = guild(real_app)
    rude_title, clean_title = unique("Rude copy"), unique("Clean copy")
    # a word that the list of the day did not have, and a clean text that an older list had rated extreme
    rude_id, rude = add_post(author, board_id, rude_title, f"<p>{RUDE}</p>", word_severity=0)
    clean_id, clean = add_post(author, board_id, clean_title, "<p>Gardens and tomatoes</p>", word_severity=2)
    client = author.browser()
    for b36 in (rude, clean):
        assert post(client, author, f"/delete_post/{b36}").status_code == 204
    assert (copy_of(real_app, rude_id)["word_severity"], copy_of(real_app, clean_id)["word_severity"]) == (0, 2), "the copies keep the rating the posts had"
    before_gb, before_us = deleted_tab(real_app, "GB"), deleted_tab(real_app, "US")
    assert RUDE in before_gb, "the stale rating shows the words to a Child-level reader"
    assert clean_title not in before_us, "and hides a clean copy from a Standard reader"

    assert rescan(real_app) >= 2
    from ruqqus.helpers.word_filter_store import get_filter
    _, db_session = real_app
    db = db_session()
    expected = get_filter(db).severity_html(f"<p>{RUDE}</p>")
    db.close()
    assert expected >= 1
    assert copy_of(real_app, rude_id)["word_severity"] == expected
    assert copy_of(real_app, clean_id)["word_severity"] == 0
    assert RUDE not in deleted_tab(real_app, "GB"), "the Child level no longer sees the words"
    assert clean_title in deleted_tab(real_app, "US") and clean_title in deleted_tab(real_app, "GB")
    rescan(real_app)
    assert copy_of(real_app, rude_id)["word_severity"] == expected, "a second run leaves the copy as it was"


def test_the_options_of_a_deleted_posts_poll_count_for_its_copy_as_they_count_for_the_post(real_app, make_member):
    author = make_member()
    board_id = guild(real_app)
    post_id, b36 = add_post(author, board_id, unique("Poll copy"), "<p>Which one do you like best?</p>", word_severity=0)
    assert post(author.browser(), author, f"/delete_post/{b36}").status_code == 204
    _, db_session = real_app
    db = db_session()
    db.execute(text("INSERT INTO polls (post_id, closes_utc, created_utc) VALUES (:p, :c, :n)"), {"p": post_id, "c": int(time.time()) + 3600, "n": int(time.time())})
    poll_id = db.execute(text("SELECT id FROM polls WHERE post_id = :p"), {"p": post_id}).scalar()
    for ordinal, label in enumerate(("Tomatoes", "fucking nonsense")):
        db.execute(text("INSERT INTO poll_options (poll_id, ordinal, label) VALUES (:p, :o, :l)"), {"p": poll_id, "o": ordinal, "l": label})
    db.commit()
    db.close()
    assert copy_of(real_app, post_id)["word_severity"] == 0
    rescan(real_app)
    assert copy_of(real_app, post_id)["word_severity"] >= 1, "a rude option rates the post, and so the copy of it"


def test_a_purge_from_the_report_queue_leaves_a_copy_with_no_words_and_no_rating_that_a_rescan_leaves_alone(real_app, make_member):
    author, reporter, boss = make_member(), make_member(), admin_of(make_member)
    board_id = guild(real_app)
    title = unique("Purged copy")
    post_id, b36 = add_post(author, board_id, title, f"<p>{RUDE}</p>", word_severity=1)        # shown to a Standard reader, so the Report button is there
    post(author.browser(), author, f"/delete_post/{b36}")
    (entry,) = rows(real_app, "SELECT id FROM log_entries WHERE action = 'author_delete_post' AND target_submission_id = :i", i=post_id)
    assert copy_of(real_app, post_id)["word_severity"] == 1
    assert post(reporter.browser(), reporter, f"/log/{entry['id']}/report", reason="It is rude").status_code == 200
    assert post(boss.browser(), boss, f"/admin/log_reports/{entry['id']}", decision="purged").status_code == 303
    after = copy_of(real_app, post_id)
    assert after["scrubbed_utc"] > 0 and after["title"] is None and after["body_html"] is None
    assert after["word_severity"] == 0, "nothing is left to rate, so nothing is rated"
    rescan(real_app)
    again = copy_of(real_app, post_id)
    assert again["scrubbed_utc"] == after["scrubbed_utc"] and again["word_severity"] == 0 and again["title"] is None
    assert title not in deleted_tab(real_app, "US")
