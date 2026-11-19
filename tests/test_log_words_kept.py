"""What the Log's own tables of a person's words keep, for how long, and what closing an account and the data download do with them (the second review
of the Log, C30). The tables are `log_reports` (a member's report of a Log entry: who and why) and `log_appeals` (an appeal and the answer to it), and
`log_failures` (the Log's note that it could not write an entry).

The promises: a decided report is deleted 90 days after it was decided and a waiting one stays; an appeal's words (the member's and the answer) are
emptied 90 days after it was decided and its row, outcome and who decided stay; closing an account deletes its decided reports, empties its waiting
ones, closes its open appeals and empties every appeal's words, and touches nobody else's; the download holds a member's own reports and appeals and
never who decided or how a report ended; a failure note keeps only the first line of the error; and the privacy page says all of it."""
import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import account_cleanup, action_log, data_export, log_appeals, log_reports, retention

import log_tables

ROOT = Path(__file__).resolve().parent.parent
NOW = 1_793_000_000
DAY = 86400
TABLES = ("log_appeals", "log_reports", "log_failures")


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for statement in log_tables.LATER:
            if any(f"TABLE {name}" in statement or f"ON {name} " in statement for name in TABLES):
                conn.execute(text(statement))
    with Session(engine) as session:
        yield session


def report(db, entry, reporter, reason="because", status="open", decided=0, created=NOW, decided_by=None):
    db.execute(text("INSERT INTO log_reports (entry_id, reporter_id, reason, created_utc, status, decided_by, decided_utc) "
                    "VALUES (:e, :r, :why, :c, :s, :by, :d)"),
               {"e": entry, "r": reporter, "why": reason, "c": created, "s": status, "by": decided_by, "d": decided})


def appeal(db, entry, user, reason="please look again", status="open", decided=0, note="", decided_by=None, board=None):
    db.execute(text("INSERT INTO log_appeals (entry_id, user_id, board_id, reason, created_utc, status, decided_by, decided_utc, decision_note) "
                    "VALUES (:e, :u, :b, :why, :c, :s, :by, :d, :n)"),
               {"e": entry, "u": user, "b": board, "why": reason, "c": NOW - 200 * DAY, "s": status, "by": decided_by, "d": decided, "n": note})


def reports(db):
    return {(e, r): (why, s) for e, r, why, s in db.execute(text("SELECT entry_id, reporter_id, reason, status FROM log_reports"))}


def appeals(db):
    return {(e, u): row for e, u, *row in db.execute(text(
        "SELECT entry_id, user_id, reason, status, decision_note, decided_by, decided_utc FROM log_appeals"))}


# --- a new table of a person's words cannot skip one of the three places ----------------------------------------------------------------

def test_each_table_of_a_persons_words_is_in_retention_cleanup_and_the_download():
    rules = " ".join(sql for _, sql in retention.RULES)
    steps = " ".join(sql for _, sql in account_cleanup.STEPS)
    sections = " ".join(sql for _, sql in data_export.SECTIONS)
    for table in ("log_reports", "log_appeals"):
        assert table in rules, f"{table}: nothing in helpers/retention.py RULES"
        assert table in steps, f"{table}: nothing in helpers/account_cleanup.py STEPS"
        assert table in sections, f"{table}: nothing in helpers/data_export.py SECTIONS"
    assert "log_failures" in rules


def test_step_and_rule_names_are_unique():
    for table in (account_cleanup.STEPS, retention.RULES):
        names = [name for name, _ in table]
        assert len(names) == len(set(names))


# --- retention --------------------------------------------------------------------------------------------------------------------

def test_a_decided_report_is_deleted_after_90_days_and_a_waiting_one_stays(db):
    old, young, edge = NOW - 91 * DAY, NOW - 89 * DAY, NOW - 90 * DAY
    report(db, 1, 5, "old, dismissed", "dismissed", decided=old, decided_by=20)
    report(db, 2, 5, "old, purged", "purged", decided=old, decided_by=20)
    report(db, 3, 5, "old, redacted", "redacted", decided=old, decided_by=21)
    report(db, 4, 5, "young, purged", "purged", decided=young, decided_by=20)
    report(db, 5, 5, "exactly at the cutoff", "dismissed", decided=edge, decided_by=20)
    report(db, 6, 5, "waiting for a year", "open", created=NOW - 400 * DAY)
    report(db, 7, 6, "waiting", "open")
    done = retention.prune(db, NOW)
    assert done["decided Log reports"] == 3
    assert reports(db) == {(4, 5): ("young, purged", "purged"), (5, 5): ("exactly at the cutoff", "dismissed"),
                           (6, 5): ("waiting for a year", "open"), (7, 6): ("waiting", "open")}


def test_a_decided_appeals_words_are_emptied_and_its_row_stays(db):
    appeal(db, 1, 5, "old reason", "upheld", decided=NOW - 91 * DAY, note="old answer", decided_by=20)
    appeal(db, 2, 5, "young reason", "reversed", decided=NOW - 89 * DAY, note="young answer", decided_by=21)
    appeal(db, 3, 5, "open for a year", "open")
    appeal(db, 4, 6, "old, dismissed", "dismissed", decided=NOW - 120 * DAY, note="no", decided_by=22)
    retention.prune(db, NOW)
    kept = appeals(db)
    assert kept[(1, 5)] == ["", "upheld", "", 20, NOW - 91 * DAY], "the words go; the outcome, who decided and when stay"
    assert kept[(4, 6)] == ["", "dismissed", "", 22, NOW - 120 * DAY]
    assert kept[(2, 5)][0] == "young reason" and kept[(2, 5)][2] == "young answer"
    assert kept[(3, 5)][0] == "open for a year" and kept[(3, 5)][1] == "open", "an open appeal keeps its words"
    assert len(kept) == 4, "no row is deleted: an appeal is made once per decision"


def test_an_old_note_of_a_failure_goes_and_a_young_one_stays(db):
    db.execute(text("INSERT INTO log_failures (created_utc, action, detail) VALUES (:old, 'a', 'x'), (:young, 'b', 'y')"),
               {"old": NOW - 91 * DAY, "young": NOW - 89 * DAY})
    assert retention.prune(db, NOW)["old notes of entries the Log could not write"] == 1
    assert db.execute(text("SELECT action FROM log_failures")).scalars().all() == ["b"]


def test_the_three_periods_are_the_90_days_the_privacy_page_promises():
    assert retention.LOG_REPORT_DAYS == retention.APPEAL_TEXT_DAYS == retention.LOG_FAILURE_DAYS == 90
    page = read("ruqqus", "templates", "help", "privacy.html")
    assert page.count("90 days") >= 3
    assert "UPDATE log_appeals SET reason = ''" in read("ruqqus", "helpers", "retention.py"), "tests/test_log_coverage_assets.py pins this text"


# --- closing an account -----------------------------------------------------------------------------------------------------------

MINE = ("end their open appeals", "empty their appeals", "remove their decided Log reports", "empty their waiting Log reports")


def test_closing_an_account_clears_its_reports_and_appeals_and_nobody_elses(db):
    report(db, 1, 5, "decided", "purged", decided=NOW - DAY, decided_by=20)
    report(db, 2, 5, "waiting", "open")
    report(db, 1, 6, "someone else, waiting", "open")
    report(db, 3, 6, "someone else, decided", "dismissed", decided=NOW - DAY, decided_by=20)
    appeal(db, 1, 5, "I am waiting", "open")
    appeal(db, 2, 5, "decided", "upheld", decided=100, note="the decision stands", decided_by=20)
    appeal(db, 3, 6, "someone else, open", "open")
    appeal(db, 4, 6, "someone else, decided", "reversed", decided=100, note="their answer", decided_by=21)
    skipped = account_cleanup.close_account(db, 5, NOW)
    assert not set(MINE) & set(skipped), "every step fits the schema's columns"

    assert reports(db) == {(2, 5): ("", "open"), (1, 6): ("someone else, waiting", "open"), (3, 6): ("someone else, decided", "dismissed")}, \
        "a decided report goes, a waiting one stays without its words, nobody else's is touched"
    kept = appeals(db)
    assert kept[(1, 5)] == ["", "dismissed", "", None, NOW], "an open appeal is closed by nobody and its words go: it can't sit in the deciders' queue"
    assert kept[(2, 5)] == ["", "upheld", "", 20, 100], "a decided appeal keeps its outcome and who decided; the words go"
    assert kept[(3, 6)][:2] == ["someone else, open", "open"]
    assert kept[(4, 6)][:3] == ["someone else, decided", "reversed", "their answer"]


def test_closing_an_account_twice_changes_nothing_more(db):
    appeal(db, 1, 5, "I am waiting", "open")
    report(db, 1, 5, "waiting", "open")
    account_cleanup.close_account(db, 5, NOW)
    before = (appeals(db), reports(db))
    account_cleanup.close_account(db, 5, NOW + 9 * DAY)
    assert (appeals(db), reports(db)) == before, "the time it was closed is the first time"


def test_a_closed_accounts_open_appeal_is_out_of_the_deciders_queue(db):
    # the queue joins the entry; one row of it is enough to show the appeal is no longer asked about
    db.execute(text("CREATE TABLE log_entries (id INTEGER PRIMARY KEY, action VARCHAR(40), actor_id INTEGER, actor_role VARCHAR(12), "
                    "target_submission_id INTEGER, target_comment_id INTEGER, target_user_id INTEGER, board_id INTEGER, reason TEXT, "
                    "rule_key VARCHAR(20), audience INTEGER)"))
    db.execute(text("INSERT INTO log_entries (id, action, actor_id, actor_role, board_id, audience) VALUES (1, 'exile_user', 10, 'guildmaster', 3, 0)"))
    appeal(db, 1, 5, "please", "open", board=3)
    assert [a["user_id"] for a in log_appeals.open_queue(db, 3)] == [5]
    account_cleanup.close_account(db, 5, NOW)
    assert log_appeals.open_queue(db, 3) == []


# --- the download -----------------------------------------------------------------------------------------------------------------

def test_the_download_holds_a_members_own_reports_and_appeals_and_never_who_decided(db):
    report(db, 1, 5, "mine, waiting", "open", created=NOW - 9)
    report(db, 2, 5, "mine, decided", "purged", created=NOW - 8, decided=NOW - 1, decided_by=20)
    report(db, 1, 6, "theirs", "open")
    appeal(db, 1, 5, "mine, open", "open")
    appeal(db, 2, 5, "mine, decided", "reversed", decided=NOW - 5, note="we undid it", decided_by=21, board=3)
    appeal(db, 3, 6, "theirs", "open")
    out = data_export.build(db, 5)

    assert out["log_reports"] == [{"entry_id": 1, "reason": "mine, waiting", "created_utc": NOW - 9, "state": "waiting"},
                                  {"entry_id": 2, "reason": "mine, decided", "created_utc": NOW - 8, "state": "closed"}], \
        "only this account's, with how it ended as waiting or closed and no more"
    for row in out["log_reports"]:
        assert set(row) == {"entry_id", "reason", "created_utc", "state"}
    assert [(r["entry_id"], r["reason"]) for r in out["appeals"]] == [(1, "mine, open"), (2, "mine, decided")]
    for row in out["appeals"]:
        assert set(row) == {"entry_id", "reason", "status", "decision_note", "created_utc", "decided_utc"}, "never who decided, the guild or the undoing entry"
    assert out["appeals"][1]["decision_note"] == "we undid it"
    assert "theirs" not in data_export.as_json(out)


def test_the_reports_statement_never_selects_who_decided_or_the_outcome():
    sql = dict(data_export.SECTIONS)["log_reports"]
    selected = sql.split(" FROM ", 1)[0].lower()
    for column in ("reporter_id", "decided_by", "decided_utc", "status"):
        assert column not in selected.replace("r.status = 'open'", ""), column
    assert "reporter_id = :u" in sql, "the account's own rows only"
    appeals_sql = dict(data_export.SECTIONS)["appeals"].split(" FROM ", 1)[0].lower()
    for column in ("decided_by", "board_id", "reversal_entry_id", "user_id"):
        assert column not in appeals_sql, column


# --- what a failure note keeps -----------------------------------------------------------------------------------------------------

def failures(db):
    return db.execute(text("SELECT action, detail FROM log_failures ORDER BY id")).all()


def test_a_failure_note_keeps_only_the_first_line_of_the_error(db):
    error = RuntimeError('(psycopg2.errors.CheckViolation) new row for relation "log_entries" violates check constraint "x"\n'
                         'DETAIL:  Failing row contains (5, ann@example.com, 10.1.2.3, a reason someone wrote).\n'
                         '[SQL: INSERT INTO log_entries (reason) VALUES (%(reason)s)]\n[parameters: {\'reason\': \'a reason someone wrote\'}]')
    action_log.note_failure(db, "hide_post_from_guild", error)
    ((action, detail),) = failures(db)
    assert action == "hide_post_from_guild"
    assert detail == '(psycopg2.errors.CheckViolation) new row for relation "log_entries" violates check constraint "x"'
    for secret in ("DETAIL", "Failing row", "ann@", "10.1.2.3", "someone wrote", "[SQL", "parameters"):
        assert secret not in detail


def test_an_email_or_an_address_on_the_first_line_is_taken_out(db):
    action_log.note_failure(db, "x", RuntimeError("could not tell ann@example.com from 10.1.2.3: boom [SQL: select 1] [parameters: (1,)]"))
    action_log.note_failure(db, "y", "plain text, no newline")
    action_log.note_failure(db, "z", None)
    detail = [d for _, d in failures(db)]
    assert detail[0].startswith("could not tell [email] from [address]: boom") and "select" not in detail[0] and "10.1.2.3" not in detail[0]
    assert detail[1] == "plain text, no newline"
    assert detail[2] == "None"


def test_a_note_still_says_what_kind_of_failure_it_was(db):
    action_log.note_failure(db, "modaction", RuntimeError("table is locked\nDETAIL: more"))
    assert "table is locked" in failures(db)[0][1], "tests/test_log_paper_trail.py reads the first line the same way"


# --- the privacy page -------------------------------------------------------------------------------------------------------------

def page_lines():
    page = read("ruqqus", "templates", "help", "privacy.html")
    return {key: next(line for line in page.splitlines() if key in line)
            for key in ('"Report this"', "If you appeal a decision", "When the Log cannot write an entry")}


def test_the_privacy_page_says_what_a_report_an_appeal_and_a_failure_note_keep():
    lines = page_lines()
    report_line, appeal_line, failure_line = lines['"Report this"'], lines["If you appeal a decision"], lines["When the Log cannot write an entry"]
    assert f"up to {log_reports.REASON_MAX} characters" in report_line and f"up to {log_appeals.REASON_MAX} characters" in appeal_line
    assert "90 days after the administrators decide it" in report_line and "one still waiting is kept" in report_line
    assert "delete your account" in report_line and "decided reports are deleted" in report_line and "stays without your words" in report_line
    assert "nobody is shown who reported" in report_line
    assert "emptied 90 days after it is decided" in appeal_line and "can be appealed only once" in appeal_line
    assert "delete your account" in appeal_line and "closed without an answer" in appeal_line
    assert "data download" in appeal_line
    assert "90 days" in failure_line and "first line of the error" in failure_line and "never what anyone wrote" in failure_line


def test_the_new_privacy_lines_use_no_banned_word():
    for line in page_lines().values():
        assert not re.search(r"\b(reply|replies|replied|promote|promoted|yank|crosspost)\b", line, re.I)
