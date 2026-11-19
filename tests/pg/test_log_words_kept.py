"""What the Log keeps of a person's own words, on the real app and Postgres (the second review of the Log, C30; the rules and the SQL are tested without
Postgres in tests/test_log_words_kept.py): a member's report of a Log entry is in their own download and nobody else's, how it ended is never in it, a decided
report is deleted 90 days after the decision and a waiting one stays; closing an account deletes its decided reports, empties its waiting ones, closes its
open appeals (which leave the deciders' queue) and empties every appeal's words and touches nobody else's; and a failure note keeps only the first line of a
real database error."""
import secrets
import time

from sqlalchemy import text

BASE = "http://localhost"
HEADERS = {"User-Agent": "ci-log-words-kept"}
SECRET = "A-SECRET-REASON-THAT-SOMEONE-WROTE"
DAY = 86400


def address():
    return f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"


def post(client, member, path, **fields):
    return client.post(path, base_url=BASE, headers=HEADERS, data={"formkey": member.formkey(client), **fields},
                       environ_base={"REMOTE_ADDR": address()})


def get(client, path):
    return client.get(path, base_url=BASE, headers=HEADERS, environ_base={"REMOTE_ADDR": address()})


def admin_of(make_member, level=3):
    admin = make_member()
    db = admin.db_session()
    db.execute(text("UPDATE users SET admin_level = :l WHERE id = :u"), {"l": level, "u": admin.id})
    db.commit()
    return admin


def rows(real_app, sql, **params):
    _, db_session = real_app
    db = db_session()
    db.rollback()
    db.expire_all()
    return [dict(r._mapping) for r in db.execute(text(sql), params)]


def guild(real_app):
    from ruqqus.classes import Board
    _, db_session = real_app
    db = db_session()
    board = Board(name="ci_kept_" + secrets.token_hex(3), description="a guild for the words-kept test", description_html="<p>a guild</p>",
                  creator_id=1, subcat_id=0, created_utc=int(time.time()), color="23a9f2")
    db.add(board)
    db.commit()
    return board.id, board.name


def an_entry(real_app, board_id, reason):
    """A public guildmaster entry with a reason in it: something a member may report."""
    from ruqqus.helpers import action_log
    _, db_session = real_app
    db = db_session()
    entry_id = action_log.record(db, "hide_post_from_guild", 1, board_id=board_id, reason=reason, role=action_log.GUILDMASTER)
    db.commit()
    return entry_id


def test_a_report_is_in_its_reporters_download_only_and_is_deleted_90_days_after_it_is_decided(real_app, make_member):
    from ruqqus.helpers import data_export, retention
    reporter, other, boss = make_member(), make_member(), admin_of(make_member)
    board_id, _ = guild(real_app)
    entry_id = an_entry(real_app, board_id, "Advertising, " + secrets.token_hex(3))
    assert post(reporter.browser(), reporter, f"/log/{entry_id}/report", reason="Names me " + SECRET).status_code == 200
    assert post(other.browser(), other, f"/log/{entry_id}/report", reason="me too").status_code == 200
    _, db_session = real_app
    db = db_session()

    db.rollback()
    mine = data_export.build(db, reporter.id)["log_reports"]
    assert [(r["entry_id"], r["reason"], r["state"]) for r in mine] == [(entry_id, "Names me " + SECRET, "waiting")]
    assert set(mine[0]) == {"entry_id", "reason", "created_utc", "state"}
    theirs = data_export.build(db, other.id)["log_reports"]
    assert [r["reason"] for r in theirs] == ["me too"], "nobody else's report is in anyone's download"
    assert SECRET not in data_export.as_json(data_export.build(db, other.id))

    assert post(boss.browser(), boss, f"/admin/log_reports/{entry_id}", decision="dismissed").status_code == 303
    db.rollback()
    closed = data_export.build(db, reporter.id)["log_reports"]
    assert [r["state"] for r in closed] == ["closed"], "how it ended is waiting or closed and no more"
    assert "dismissed" not in data_export.as_json(data_export.build(db, reporter.id))

    now = int(time.time())
    db.execute(text("UPDATE log_reports SET decided_utc = :old WHERE entry_id = :e"), {"old": now - 91 * DAY, "e": entry_id})
    db.commit()
    assert retention.prune(db, now)["decided Log reports"] >= 2
    assert rows(real_app, "SELECT reporter_id FROM log_reports WHERE entry_id = :e", e=entry_id) == []


def test_a_waiting_report_is_not_deleted_however_old(real_app, make_member):
    from ruqqus.helpers import retention
    reporter = make_member()
    board_id, _ = guild(real_app)
    entry_id = an_entry(real_app, board_id, "Advertising, " + secrets.token_hex(3))
    assert post(reporter.browser(), reporter, f"/log/{entry_id}/report", reason="Waiting").status_code == 200
    _, db_session = real_app
    db = db_session()
    db.execute(text("UPDATE log_reports SET created_utc = :old WHERE entry_id = :e"), {"old": int(time.time()) - 500 * DAY, "e": entry_id})
    db.commit()
    retention.prune(db, int(time.time()))
    assert [r["reason"] for r in rows(real_app, "SELECT reason FROM log_reports WHERE entry_id = :e", e=entry_id)] == ["Waiting"]


def test_closing_an_account_clears_its_reports_and_appeals_and_the_appeal_leaves_the_deciders_queue(real_app, make_member):
    from ruqqus.classes import ModRelationship, Submission, SubmissionAux
    from ruqqus.helpers import log_appeals, retention
    from ruqqus.helpers.base36 import base36encode
    _, db_session = real_app
    board_id, name = guild(real_app)
    author, other, boss, gm = make_member(), make_member(), admin_of(make_member), make_member()
    db = gm.db_session()
    db.execute(text("UPDATE users SET creation_ip = :ip WHERE id = :u"), {"ip": address(), "u": gm.id})
    db.add(ModRelationship(user_id=gm.id, board_id=board_id, accepted=True, invite_rescinded=False, perm_full=True, perm_content=True,
                           perm_access=True, perm_config=True))
    db.commit()

    # the author's post is hidden by the guild and they appeal
    db = author.db_session()
    db.rollback()
    with author.app.test_request_context("/"):
        row = Submission(author_id=author.id, board_id=board_id, original_board_id=board_id, created_utc=int(time.time()) - 3600, post_public=True)
    db.add(row)
    db.flush()
    db.add(SubmissionAux(id=row.id, title="A post " + secrets.token_hex(3), body="words", body_html="<p>words</p>"))
    db.commit()
    post_id = row.id
    assert post(gm.browser(), gm, f"/mod/kick/{name}/{base36encode(post_id)}", reason="Advertising", rule="good_faith").status_code == 200
    (hidden,) = rows(real_app, "SELECT id FROM log_entries WHERE board_id = :b AND target_submission_id = :p", b=board_id, p=post_id)
    assert post(author.browser(), author, f"/log/{hidden['id']}/appeal", reason="PRIVATE-APPEAL-WORDS").status_code == 303
    assert "PRIVATE-APPEAL-WORDS" in get(gm.browser(), f"/+{name}/mod/appeals").get_data(as_text=True), "the guildmasters can read it while it is open"
    assert [a["user_id"] for a in log_appeals.open_queue(db_session(), board_id)] == [author.id]

    # two reports by the author (one decided, one waiting) and one by somebody else on the decided entry
    decided_entry, waiting_entry = an_entry(real_app, board_id, "Advertising " + secrets.token_hex(3)), an_entry(real_app, board_id, "Spam " + secrets.token_hex(3))
    for entry_id, member, why in ((decided_entry, author, "author, decided"), (waiting_entry, author, "author, waiting"), (decided_entry, other, "other, decided")):
        assert post(member.browser(), member, f"/log/{entry_id}/report", reason=why).status_code == 200
    assert post(boss.browser(), boss, f"/admin/log_reports/{decided_entry}", decision="dismissed").status_code == 303

    before = int(time.time())
    assert post(author.browser(), author, "/settings/delete_account", password=author.password).status_code == 200

    mine = rows(real_app, "SELECT entry_id, reason, status FROM log_reports WHERE reporter_id = :u ORDER BY entry_id", u=author.id)
    assert mine == [{"entry_id": waiting_entry, "reason": "", "status": "open"}], "the decided report is gone and the waiting one has lost their words"
    assert rows(real_app, "SELECT reason, status FROM log_reports WHERE reporter_id = :u", u=other.id) == [{"reason": "other, decided", "status": "dismissed"}]

    (appeal,) = rows(real_app, "SELECT reason, decision_note, status, decided_by, decided_utc FROM log_appeals WHERE entry_id = :e", e=hidden["id"])
    assert (appeal["reason"], appeal["decision_note"], appeal["status"], appeal["decided_by"]) == ("", "", "dismissed", None)
    assert appeal["decided_utc"] >= before
    db = db_session()
    db.rollback()
    assert log_appeals.open_queue(db, board_id) == []
    page = get(gm.browser(), f"/+{name}/mod/appeals").get_data(as_text=True)
    assert "PRIVATE-APPEAL-WORDS" not in page

    # the waiting report can still be decided by the admins, and is deleted once it is old
    assert post(boss.browser(), boss, f"/admin/log_reports/{waiting_entry}", decision="dismissed").status_code == 303
    db.execute(text("UPDATE log_reports SET decided_utc = :old WHERE reporter_id = :u"), {"old": int(time.time()) - 91 * DAY, "u": author.id})
    db.commit()
    retention.prune(db, int(time.time()))
    assert rows(real_app, "SELECT 1 FROM log_reports WHERE reporter_id = :u", u=author.id) == []


def test_a_failure_note_made_from_a_real_database_error_keeps_only_its_first_line(real_app):
    from ruqqus.helpers import action_log
    _, db_session = real_app
    db = db_session()
    try:
        with db.begin_nested():
            db.execute(text("INSERT INTO log_reports (entry_id, reporter_id, reason, status) VALUES (1, 2, :why, 'nonsense')"), {"why": SECRET})
    except Exception as error:
        full = str(error)
        action_log.note_failure(db, "ci_words_kept", error)
    else:
        raise AssertionError("the status check should have refused the row")
    assert SECRET in full and "[SQL:" in full, "the whole error does carry what was written, which is why only a line of it is kept"
    db.commit()
    try:
        (note,) = rows(real_app, "SELECT detail FROM log_failures WHERE action = 'ci_words_kept'")
        assert note["detail"] == full.split("\n", 1)[0] and "log_reports_status" in note["detail"]
        for gone in (SECRET, "DETAIL", "Failing row", "[SQL", "parameters"):
            assert gone not in note["detail"]
    finally:
        db = db_session()
        db.rollback()
        db.execute(text("DELETE FROM log_failures WHERE action = 'ci_words_kept'"))
        db.commit()
