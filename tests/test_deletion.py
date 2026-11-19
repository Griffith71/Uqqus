"""A member's own delete and what it leaves in the Log (the Log plan's decisions 3, 4, 12, 20, 25; slice D3a): helpers/deletion.py (the rules),
helpers/deletion_store.py (the SQL), helpers/anonymise.py (what taking a name off does to the Log's pointers), helpers/log_reports.py (the Report
button) and the public copy a delete keeps (helpers/action_log.py), on SQLite with the tables built from the migrations.

The promises: a delete inside the grace window is an erase (no entry), capped per account and refused once someone else has commented; after it the
words stay as Anonymous, but never for a child account, a removed or hidden item or a banned guild; a reader's word filter, the sensitive rule and
the guild's state decide what of the words a reader is shown; "Remove my name" is refused where the name is needed; anonymising strips the item from
every entry that pairs it with its author; the Report button is once per person and never shows who."""
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import action_log as log, anonymise, deletion, deletion_store, log_reports, log_view

import log_tables

ROOT = Path(__file__).resolve().parent.parent
NOW = 1_793_000_000
DAY = 86400


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


# --- the rules ---------------------------------------------------------------------------------------------------------------------------

def plan(age=60, erased=0, others=False, child=False):
    return deletion.plan(created_utc=NOW - age, now=NOW, erased_today=erased, others_replied=others, child=child)


def test_inside_five_minutes_with_nobody_else_having_commented_a_delete_is_an_erase():
    assert deletion.GRACE_SECONDS == 300 and deletion.GRACE_ERASES_PER_DAY == 20
    assert plan(age=0) == plan(age=299) == deletion.ERASE
    assert plan(age=300) == plan(age=3600) == deletion.PUBLISH, "after it every delete is public"


def test_the_erase_is_refused_once_someone_else_has_commented_and_past_twenty_a_day():
    assert plan(age=10, others=True) == deletion.PUBLISH, "nobody's words are orphaned, and the window is no way to bury an argument"
    assert plan(age=10, erased=19) == deletion.ERASE and plan(age=10, erased=20) == deletion.PUBLISH


def test_a_child_accounts_delete_after_the_window_is_a_line_and_inside_it_an_erase():
    assert plan(age=10, child=True) == deletion.ERASE
    assert plan(age=4000, child=True) == deletion.LINE
    assert plan(age=4000, child=True, others=True) == deletion.LINE


def test_an_items_clock_is_its_own_age_an_item_from_the_future_is_young():
    assert deletion.in_grace(NOW + 50, NOW) and not deletion.in_grace(NOW - 300, NOW)


def test_the_words_are_published_unless_a_child_a_removal_a_guild_hide_or_a_banned_guild_says_no():
    ok = dict(child=False, removed=False, hidden_by_guild=False, board_banned=False)
    assert deletion.text_published(**ok)
    for key in ok:
        assert not deletion.text_published(**{**ok, key: True}), key


def test_a_circle_item_is_the_staffs_alone():
    assert deletion.entry_floor(circle=True) == log.STAFF and deletion.entry_floor(circle=False) == log.PUBLIC


COPY = {"public": True, "scrubbed": False, "word_severity": 0, "is_sensitive": False}


def test_what_a_reader_is_shown_of_the_words():
    show = lambda copy=COPY, banned=False, level=1, sensitive=False: deletion.text_view(copy, board_banned=banned, level=level, shows_sensitive=sensitive)
    assert show() == deletion.TEXT
    assert show(None) == show({**COPY, "public": False}) == show({**COPY, "scrubbed": True}) == deletion.LINE_ONLY
    assert show(banned=True) == deletion.LINE_ONLY, "items in a banned guild never show words"
    assert show({**COPY, "word_severity": 1}, level=1) == deletion.TEXT and show({**COPY, "word_severity": 1}, level=2) == deletion.LINE_ONLY
    assert show({**COPY, "word_severity": 2}, level=1) == deletion.LINE_ONLY, "Standard hides the extreme"
    assert show({**COPY, "is_sensitive": True}) == deletion.LINE_ONLY, "a visitor or a member who didn't choose it gets a line"
    assert show({**COPY, "is_sensitive": True}, sensitive=True) == deletion.COVERED
    assert show({**COPY, "is_sensitive": True, "word_severity": 2}, sensitive=True) == deletion.LINE_ONLY


def test_remove_my_name_is_refused_where_the_name_is_needed_and_says_why():
    ok = dict(circle=False, tipped=False, shared=False, space=False, already=False, deleted=False)
    assert deletion.unlink_refusal(**ok) is None
    expect = {"deleted": deletion.GONE, "already": deletion.ALREADY, "space": deletion.SPACE, "circle": deletion.CIRCLE,
              "tipped": deletion.TIPPED, "shared": deletion.SHARED}
    for key, message in expect.items():
        assert deletion.unlink_refusal(**{**ok, key: True}) == message, key       # the rule still exists; the site no longer asks it of a shared post (slice D3f)
    assert deletion.unlink_refusal(**ok, bot=True) == deletion.BOT
    assert deletion.parse_how("unlink") == deletion.UNLINK and deletion.parse_how("x") == deletion.parse_how(None) == deletion.DELETE


# --- the Log's copy and the SQL ----------------------------------------------------------------------------------------------------

@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for statement in log_tables.migration_statements(read("scripts", "migrations", "2026-12-09_action_log.sql")):
            conn.execute(text(statement))
        log_tables.apply(conn, modactions=True)
        conn.execute(text("CREATE TABLE submissions (id INTEGER PRIMARY KEY, audience INTEGER DEFAULT 0, author_id INTEGER, repost_id INTEGER DEFAULT 0, is_anonymous BOOLEAN DEFAULT 0, "
                          "tip_coins INTEGER DEFAULT 0, language_code VARCHAR(5), distinguish_level INTEGER DEFAULT 0, gm_distinguish INTEGER DEFAULT 0)"))
        conn.execute(text("CREATE TABLE comments (id INTEGER PRIMARY KEY, author_id INTEGER, parent_submission INTEGER, parent_comment_id INTEGER, "
                          "is_anonymous BOOLEAN DEFAULT 0, language_code VARCHAR(5), distinguish_level INTEGER DEFAULT 0, gm_distinguish INTEGER DEFAULT 0)"))
        conn.execute(text("CREATE TABLE comment_forwardrels (id INTEGER PRIMARY KEY, comment_id INTEGER, promoted_submission_id INTEGER, promoted_by_id INTEGER)"))
        conn.execute(text("CREATE TABLE boards (id INTEGER PRIMARY KEY, is_private BOOLEAN DEFAULT 0, is_circle BOOLEAN DEFAULT 0)"))
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, admin_level INTEGER DEFAULT 0)"))
        conn.execute(text("CREATE TABLE award_relationships (id INTEGER PRIMARY KEY, user_id INTEGER, submission_id INTEGER, comment_id INTEGER, coins INTEGER)"))
        conn.execute(text("CREATE TABLE IF NOT EXISTS post_coauthors (id INTEGER PRIMARY KEY, post_id INTEGER, user_id INTEGER, status VARCHAR(10))"))
        conn.execute(text("CREATE TABLE collab_docs (id INTEGER PRIMARY KEY, post_id INTEGER, deleted_utc INTEGER DEFAULT 0)"))
        conn.execute(text("INSERT INTO boards VALUES (1, 0, 0), (2, 1, 0), (3, 0, 1)"))
        conn.execute(text("INSERT INTO users VALUES (1, 0), (10, 0), (20, 3), (30, 0), (31, 0)"))
    with Session(engine) as session:
        yield session


class Item:
    id, author_id, board_id = 100, 30, 1
    title, url, body, body_html = "A &amp; B", "https://example.org/x", "words", "<p>words <a href=\"https://example.org\">a link</a></p>"
    word_severity, is_sensitive = 0, False


def delete_entry(db, *, kind=log.DELETE, public=True, floor=log.PUBLIC, board=1, now=NOW):
    keep = log.snapshot_of(Item, kind=kind, public=public)
    return log.record(db, "author_delete_post", 30, role=log.AUTHOR, board_id=board, post_id=100, floor=floor, snapshot=keep, now=now)


def test_a_delete_copy_never_expires_and_carries_the_ratings_and_a_private_copy_still_does(db):
    entry = delete_entry(db)
    row = db.execute(text("SELECT kind, public, expires_utc, word_severity, is_sensitive FROM log_snapshots WHERE entry_id = :e"), {"e": entry}).first()
    assert tuple(row) == (log.DELETE, 1, 0, 0, 0)
    private = log.keep_snapshot(db, log.snapshot_of(Item, kind=log.GRACE), now=NOW)
    assert db.execute(text("SELECT expires_utc, public FROM log_snapshots WHERE id = :i"), {"i": private}).first() == (NOW + 90 * DAY, 0)
    assert log.scrub_due(db, NOW + 400 * DAY) == 1, "only the private copy is emptied on its date"
    assert db.execute(text("SELECT body FROM log_snapshots WHERE entry_id = :e"), {"e": entry}).scalar() == "words"


def test_the_entry_is_of_the_deletion_type_a_role_of_author_and_never_names_the_item_with_the_account(db):
    entry = delete_entry(db)
    row = db.execute(text("SELECT log_type, actor_role, audience, target_user_id, target_submission_id FROM log_entries WHERE id = :e"), {"e": entry}).first()
    assert tuple(row) == ("deletion", "author", 0, None, 100)
    rows, _ = log.fetch(db, level=0)
    assert rows[0]["actor_id"] is None, "an author is never named below staff"
    assert log.fetch(db, level=0, actor_id=30)[0] == [], "filtering by the author finds nothing"


def test_a_circle_deletes_entry_is_staff_only_and_a_private_guilds_is_the_guilds(db):
    circle = delete_entry(db, floor=deletion.entry_floor(circle=True))
    private = delete_entry(db, board=2)
    assert db.execute(text("SELECT audience FROM log_entries WHERE id = :e"), {"e": circle}).scalar() == log.STAFF
    assert db.execute(text("SELECT audience FROM log_entries WHERE id = :e"), {"e": private}).scalar() == log.GUILD
    assert [r["id"] for r in log.fetch(db, level=0)[0]] == []
    assert {r["id"] for r in log.fetch(db, level=3)[0]} == {circle, private}, "staff read both"
    assert [r["id"] for r in log.fetch(db, level=0, member_boards=[2])[0]] == [private]


def test_the_words_are_read_for_a_page_of_entries_and_an_admins_scrub_empties_them(db):
    entry = delete_entry(db)
    line_only = delete_entry(db, kind=log.GRACE, public=False)
    texts = log.deleted_texts(db, [entry, line_only])
    assert set(texts) == {entry} and texts[entry]["public"] and texts[entry]["title"] == "A &amp; B" and texts[entry]["post_id"] == 100
    log.scrub_for(db, post_ids=[100])
    after = log.deleted_texts(db, [entry])[entry]
    assert after["scrubbed"] and after["title"] is None and after["body_html"] is None
    assert deletion.text_view(after, board_banned=False, level=1, shows_sensitive=False) == deletion.LINE_ONLY


def test_a_deleted_items_links_come_off_and_its_pictures_and_sounds_become_markers():
    html = '<p>see <a href="https://evil.example">this</a> <img src="/media/1/a.png"> <audio src="/media/2/b.mp3"></audio></p>'
    plain = log_view.without_links(log_view.without_media(html))
    assert "<a" not in plain and "href" not in plain and "evil.example" not in plain and "this" in plain
    assert "<img" not in plain and "<audio" not in plain


def test_the_grace_count_is_the_authors_erases_in_the_last_day_and_not_a_childs_delete(db):
    for age in (10, 3600, 20 * 3600, 25 * 3600):
        log.keep_snapshot(db, log.snapshot_of(Item, kind=log.GRACE), now=NOW - age)
    delete_entry(db, kind=log.GRACE, public=False)                         # a child account's delete has an entry: it is not an erase
    log.keep_snapshot(db, {**log.snapshot_of(Item, kind=log.GRACE), "author_id": 31}, now=NOW - 10)
    assert deletion_store.erased_today(db, 30, NOW) == 3 and deletion_store.erased_today(db, 31, NOW) == 1


def test_who_else_has_commented_counts_everyone_but_the_author_deleted_ones_too(db):
    db.execute(text("INSERT INTO submissions (id, author_id) VALUES (100, 30), (101, 30)"))
    db.execute(text("INSERT INTO comments (id, author_id, parent_submission, parent_comment_id) VALUES (1, 30, 100, NULL), (2, 31, 101, NULL), (3, 30, 101, 2), (4, 31, 100, 1)"))
    assert deletion_store.others_replied_to_posts(db, [100], 30) is True
    assert deletion_store.others_replied_to_posts(db, [100, 101], 31) is True
    assert deletion_store.others_replied_to_posts(db, [102], 30) is False
    assert deletion_store.others_replied_to_comment(db, 1, 30) is True, "a reply by someone else"
    assert deletion_store.others_replied_to_comment(db, 2, 31) is True and deletion_store.others_replied_to_comment(db, 3, 30) is False


def test_tipped_and_shared_and_the_entries_a_page_asks_about(db):
    db.execute(text("INSERT INTO submissions (id, author_id, tip_coins) VALUES (100, 30, 0), (101, 30, 5)"))
    assert deletion_store.post_tipped(db, [100]) is False and deletion_store.post_tipped(db, [100, 101]) is True
    db.execute(text("INSERT INTO award_relationships (id, comment_id) VALUES (1, 7)"))
    assert deletion_store.comment_tipped(db, 7) and not deletion_store.comment_tipped(db, 8)
    assert deletion_store.is_shared(db, 100) is False
    db.execute(text("INSERT INTO post_coauthors (post_id, user_id, status) VALUES (100, 31, 'pending')"))
    assert deletion_store.is_shared(db, 100) is False, "an invitation nobody accepted is not a co-author"
    db.execute(text("UPDATE post_coauthors SET status = 'accepted'"))
    db.execute(text("INSERT INTO collab_docs (post_id, deleted_utc) VALUES (101, 0), (102, 5)"))
    assert deletion_store.is_shared(db, 100) and deletion_store.is_shared(db, 101) and not deletion_store.is_shared(db, 102)
    entry = delete_entry(db)
    assert deletion_store.post_entry(db, [101, 100]) == entry and deletion_store.post_entry(db, [5, 6]) is None and deletion_store.post_entry(db, []) is None
    comment_entry = log.record(db, "author_delete_comment", 30, role=log.AUTHOR, board_id=1, comment_id=7, now=NOW)
    assert deletion_store.comment_entries(db, [7, 8]) == {7: comment_entry}


def test_the_languages_of_the_deleted_items_on_a_page(db):
    db.execute(text("INSERT INTO submissions (id, author_id, language_code) VALUES (100, 30, 'de'), (101, 30, NULL)"))
    db.execute(text("INSERT INTO comments (id, author_id, language_code) VALUES (5, 30, 'fr')"))
    assert deletion_store.languages(db, [100, 101], [5]) == ({100: "de"}, {5: "fr"})
    assert deletion_store.languages(db) == ({}, {})


# --- taking a name off ------------------------------------------------------------------------------------------------------------

def test_anonymising_takes_the_item_out_of_what_pairs_it_with_its_author_and_nothing_else(db):
    exile = log.record(db, "exile_user", 10, board_id=1, target_user_id=30, now=NOW)                 # a guildmaster exiling the author
    db.execute(text("UPDATE log_entries SET target_submission_id = 100 WHERE id = :e"), {"e": exile})  # citing the post (H6 would drop it on write)
    herald = log.record(db, "herald_post", 30, board_id=1, post_id=100, now=NOW)                    # the author heralding their own post
    hide = log.record(db, "hide_post_from_guild", 10, board_id=1, post_id=100, now=NOW)             # a decision about the post: no account is named
    other = log.record(db, "exile_user", 10, board_id=1, target_user_id=31, now=NOW)
    db.execute(text("UPDATE log_entries SET target_submission_id = 100 WHERE id = :e"), {"e": other})
    db.execute(text("INSERT INTO modactions (id, user_id, board_id, target_user_id, target_submission_id, kind) VALUES (1, 30, 1, NULL, 100, 'herald_post'), "
                    "(2, 10, 1, 30, 100, 'exile_user'), (3, 10, 1, NULL, 100, 'hide_post_from_guild')"))
    anonymise.strip_pointers(db, author_id=30, post_ids=[100])
    pointer = lambda e: db.execute(text("SELECT target_submission_id FROM log_entries WHERE id = :e"), {"e": e}).scalar()
    assert pointer(exile) is None and pointer(herald) is None, "the exile that cited it and the author's own herald lose the item"
    assert pointer(hide) == 100 and pointer(other) == 100, "a decision about the item and an exile of someone else keep it"
    assert [r[0] for r in db.execute(text("SELECT target_submission_id FROM modactions ORDER BY id"))] == [None, None, 100], "the guild's own log row too"
    anonymise.strip_pointers(db, author_id=30)                                                         # nothing named: nothing to do
    anonymise.strip_pointers(db, author_id=30, comment_ids=[9])


def test_the_authors_own_comments_under_a_deleted_post_become_anonymous_and_nobody_elses(db):
    db.execute(text("INSERT INTO comments (id, author_id, parent_submission, is_anonymous) VALUES (1, 30, 100, 0), (2, 31, 100, 0), (3, 30, 101, 0), (4, 30, 100, 1)"))
    assert anonymise.anonymise_replies(db, [100], 30) == 1
    assert [(r[0], bool(r[1])) for r in db.execute(text("SELECT id, is_anonymous FROM comments ORDER BY id"))] == [(1, True), (2, False), (3, False), (4, True)]
    assert anonymise.anonymise_replies(db, [], 30) == 0


def test_anonymising_replies_ends_a_shield_or_crown_and_takes_the_posts_they_were_forwarded_as(db):
    """The second review, C17 and C18: a comment's admin shield and guildmaster crown name its author's role, and a comment forwarded into a guild
    is a post of its own under the commenter's name."""
    db.execute(text("INSERT INTO submissions (id, author_id, is_anonymous) VALUES (100, 31, 0), (500, 30, 0)"))
    db.execute(text("UPDATE submissions SET distinguish_level = 1, gm_distinguish = 7 WHERE id = 500"))
    db.execute(text("INSERT INTO comments (id, author_id, parent_submission, is_anonymous, distinguish_level, gm_distinguish) VALUES (1, 30, 100, 0, 1, 7)"))
    db.execute(text("INSERT INTO comment_forwardrels (comment_id, promoted_submission_id, promoted_by_id) VALUES (1, 500, 30)"))
    assert anonymise.anonymise_replies(db, [100], 30, clear_cache=False) == 1
    assert tuple(db.execute(text("SELECT is_anonymous, distinguish_level, gm_distinguish FROM comments WHERE id = 1")).first()) == (1, 0, 0)
    assert tuple(db.execute(text("SELECT is_anonymous, distinguish_level, gm_distinguish FROM submissions WHERE id = 500")).first()) == (1, 0, 0)


# --- the Report button -----------------------------------------------------------------------------------------------------------

def test_a_report_is_once_per_person_with_a_reason_of_200_characters_and_twenty_a_day(db):
    assert log_reports.file_report(db, 5, 31, "x" * 500, NOW) == "ok"
    assert db.execute(text("SELECT length(reason) FROM log_reports")).scalar() == log_reports.REASON_MAX == 200
    assert log_reports.file_report(db, 5, 31, "again", NOW) == "again"
    assert log_reports.file_report(db, 5, 30, "another person", NOW) == "ok"
    for entry in range(10, 10 + log_reports.PER_DAY):
        log_reports.file_report(db, entry, 10, "r", NOW)
    assert log_reports.file_report(db, 99, 10, "r", NOW) == "limit"
    assert log_reports.file_report(db, 99, 10, "r", NOW + DAY + 1) == "ok", "a new day"


def test_the_queue_is_most_reported_first_never_says_who_and_one_decision_closes_every_report_of_an_entry(db):
    for reporter in (30, 31, 10):
        log_reports.file_report(db, 5, reporter, f"from {reporter}", NOW)
    log_reports.file_report(db, 6, 30, "once", NOW + 5)
    queue = log_reports.waiting(db)
    assert [q["entry_id"] for q in queue] == [5, 6] and queue[0]["reports"] == 3 and log_reports.waiting_count(db) == 2
    assert all(set(q) == {"entry_id", "reports", "latest_utc", "reasons"} for q in queue), "never a reporter"
    assert log_reports.decide(db, 5, log_reports.PURGED, 20, NOW + 9) == 3
    assert log_reports.decide(db, 5, log_reports.DISMISSED, 21, NOW + 10) == 0, "decided once"
    assert [q["entry_id"] for q in log_reports.waiting(db)] == [6]
    with pytest.raises(ValueError):
        log_reports.decide(db, 6, "open", 20, NOW)


def test_the_migration_schema_and_fixture_agree_on_the_d3a_columns():
    migration, schema = read("scripts", "migrations", "2027-01-01_log_deletes.sql"), read("schema.sql")
    for needle in ("public boolean DEFAULT false NOT NULL", "word_severity smallint DEFAULT 0 NOT NULL", "is_sensitive boolean DEFAULT false NOT NULL"):
        assert needle in migration and needle in schema, needle
    assert "'deletion'" in migration and "'deletion'" in schema and "CREATE TABLE public.log_reports" in schema and "('2027-01-01_log_deletes.sql')" in schema
    assert "pg_get_constraintdef(oid) LIKE '%deletion%'" in migration, "widened only while it lacks the type: running it again after D3b must not narrow it"


def test_every_path_that_publishes_or_takes_a_name_off_asks_for_the_password_first():
    """The owner's decision 22: a limited QR sign-in (a relayed code) can't delete after the grace window, remove a name or publish. Inside the window an
    erase leaves nothing, so it is allowed."""
    posts, comments = read("ruqqus", "routes", "posts.py"), read("ruqqus", "routes", "comments.py")
    for source, publish, unlink in ((posts, "def delete_post_as(", "def unlink_post("), (comments, "def delete_comment(", "def unlink_comment(")):
        gate = source.split(publish)[1][:7000]
        assert "if what != deletion.ERASE and getattr(v, \"qr_limited\", False):" in gate and "qr_limited_answer(v)" in gate, publish
        assert 'getattr(v, "qr_limited", False)' in source.split(unlink)[1].split("\n\n\n")[0], unlink
    for source in (posts, comments):
        route = source.split("deletion.parse_how(request.form.get(\"how\")) == deletion.UNLINK")[1].split("\n\n")[0]
        assert 'request.path.startswith(("/api/v1", "/api/v2"))' in route and "UNLINK_SITE_ONLY" in route      # an app's delete scope deletes: no unlinking by API


def test_the_delete_dialogs_offer_only_what_the_server_will_do_and_open_on_delete():
    script = read("ruqqus", "assets", "js", "all_js.js")
    for fn, option in (("delete_postModal(id, canUnlink)", "deletePostUnlinkOption"), ("delete_commentModal(id, canUnlink)", "deleteCommentUnlinkOption")):
        body = script.split(f"function {fn}")[1].split("\n};")[0]
        assert f'getElementById("{option}").hidden = !canUnlink' in body and "{how: how" in body and '"unlink"' in body, fn
    # the post dialog's tick box ("also remove my name from my comments here (N)"): the count comes from the server, the box shows only under
    # "Remove my name" and only when there is something to change, and what it says is sent as `with_comments`
    post_dialog = script.split("function delete_postModal(id, canUnlink)")[1].split("\n};")[0]
    assert "/own_comment_count" in post_dialog and "with_comments:" in post_dialog and "unlinkRadio.checked && ownComments > 0" in post_dialog
    modal = read("ruqqus", "templates", "delete_post_modal.html")
    assert 'id="deletePostCommentsOption" hidden' in modal and 'id="delete-post-with-comments"' in modal
    for name, call in (("submission_listing.html", "delete_postModal"), ("submission.html", "delete_postModal"), ("comments.html", "delete_commentModal")):
        text_ = read("ruqqus", "templates", name)
        assert text_.count(f"{call}('") == text_.count("may_unlink_"), name


def test_a_comment_whose_forwarded_post_has_a_discussion_counts_as_answered(db):
    """C73: a forwarded comment is a post with its own thread, and the delete takes it too."""
    db.execute(text("INSERT INTO comments (id, author_id, parent_submission) VALUES (1, 30, 100)"))
    db.execute(text("INSERT INTO submissions (id, author_id) VALUES (500, 30)"))
    db.execute(text("INSERT INTO comment_forwardrels (comment_id, promoted_submission_id, promoted_by_id) VALUES (1, 500, 30)"))
    assert deletion_store.others_replied_to_comment(db, 1, 30) is False
    db.execute(text("INSERT INTO comments (id, author_id, parent_submission) VALUES (2, 31, 500)"))          # somebody answers under the forwarded post
    assert deletion_store.others_replied_to_comment(db, 1, 30) is True
