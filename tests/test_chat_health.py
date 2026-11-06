"""The guard that keeps the site's database and Synapse's together (helpers/chat_health.py, chat plan phase 1)."""
import re
from pathlib import Path

from ruqqus.helpers import chat_health as ch

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


# --- the mark -------------------------------------------------------------------------------------------------------

def test_the_mark_only_ever_rises():
    mark = ch.raised_mark({}, 42, "postgres", 100)
    assert mark == {"high": 42, "sites": {"postgres": {"high": 42, "seen_utc": 100}}}
    assert ch.raised_mark(mark, 7, "postgres", 200) == mark          # a lower id changes nothing
    higher = ch.raised_mark(mark, 50, "postgres", 300)
    assert ch.high_of(higher) == 50 and higher["sites"]["postgres"] == {"high": 50, "seen_utc": 300}


def test_each_site_database_is_counted_apart():
    mark = ch.raised_mark({}, 100005, "postgres", 1)
    mark = ch.raised_mark(mark, 90703, "ui_preview", 2)
    assert ch.high_of(mark) == 100005                                  # the overall mark is the highest of all
    assert ch.high_of(mark["sites"]["ui_preview"]) == 90703


def test_a_broken_mark_reads_as_none():
    assert ch.high_of({"high": "x"}) == 0 and ch.high_of(None) == 0 and ch.high_of({"high": -5}) == 0


def test_the_site_is_named_after_its_database():
    assert ch.site_key("postgresql://postgres@postgres:5432/ui_preview") == "ui_preview"
    assert ch.site_key("postgresql://u:p@h/postgres?sslmode=require") == "postgres"
    assert ch.site_key("") == "site"


def test_new_ids_are_raised_only_when_they_had_a_chat_account():
    assert ch.must_raise(42, 99902) and ch.must_raise(99902, 99902)
    assert not ch.must_raise(99903, 99902) and not ch.must_raise(1, 0)


# --- the check ------------------------------------------------------------------------------------------------------

def _check(mark, exists, *, known=(2, 20), sample=((10, "@ruqqus_10:x"), (20, "@ruqqus_20:x")), refused=(),
           raise_ids=None):
    written = []
    report = ch.check(None, read_mark=lambda: mark, write_mark=written.append, account_exists=exists, site="postgres",
                      now=1000, refused=refused, sample=list(sample), known=known, raise_ids=raise_ids)
    return report, written


def test_a_first_run_records_the_accounts_the_site_already_has():
    report, written = _check({}, lambda m: True)
    assert report["ok"] and written and ch.high_of(written[0]) == 20
    assert report["mark"] == 20 and any("ids up to 20" in n for n in report["notes"])


def test_chat_accounts_missing_on_the_server_are_a_split_and_nothing_is_written():
    report, written = _check({}, lambda m: m.endswith("10:x") is False)
    assert not report["ok"] and report["missing"] == [10]
    assert "restore the two as a pair" in report["problems"][0]
    assert written == []                                              # a split is never papered over


def test_new_ids_are_moved_past_the_mark():
    seen = []
    report, _ = _check({"high": 99902, "sites": {"postgres": {"high": 99902}}}, lambda m: True,
                       raise_ids=lambda high: seen.append(high) or True)
    assert seen == [99902] and any("start after id 99902" in n for n in report["notes"])


def test_another_site_database_on_the_server_is_said():
    report, _ = _check({"high": 90, "sites": {"postgres": {"high": 20}, "ui_preview": {"high": 90}}}, lambda m: True)
    assert report["ok"] and any("ui_preview" in n for n in report["notes"])


def test_members_refused_chat_are_a_problem_until_put_right():
    report, _ = _check({"high": 20, "sites": {"postgres": {"high": 20}}}, lambda m: True, refused=[42, 3])
    assert not report["ok"] and report["refused"] == [3, 42] and "42" in report["problems"][-1]


# --- a refused member's flag clears when its cause is gone (the review of 2026-11-06, C1) --------------------------------

def _settle(refused, have=(), live=(), exists=(), down=()):
    asked = []

    def account_exists(mxid):
        asked.append(mxid)
        if mxid in down:
            raise ConnectionError
        return mxid in exists
    kept, cleared = ch.settle_refused(set(refused), set(have), set(live), account_exists, lambda i: f"@u{i}")
    return kept, cleared, asked


def test_a_refused_flag_stays_while_the_chat_server_still_has_the_account():
    assert _settle({5}, live={5}, exists={"@u5"}) == ({5}, set(), ["@u5"])


def test_a_refused_flag_clears_when_the_cause_is_gone():
    assert _settle({1}, have={1}, live={1})[:2] == (set(), {1})                  # the member has a chat record here now
    assert _settle({2}, live=set())[:2] == (set(), {2})                          # deleted, or an id the restored database never had
    assert _settle({3}, live={3}, exists=set())[:2] == (set(), {3})              # the stray account is gone from the chat server


def test_a_refused_flag_stays_when_the_chat_server_cant_be_asked():
    assert _settle({4}, live={4}, down={"@u4"})[:2] == ({4}, set())


def test_only_a_few_refused_ids_are_asked_of_the_chat_server_in_one_run():
    calls = []
    ids = set(range(100, 160))
    kept, cleared = ch.settle_refused(ids, set(), ids, lambda m: calls.append(m) or True, lambda i: f"@u{i}", limit=5)
    assert len(calls) == 5 and kept == ids and not cleared                        # the rest wait for the next run, still flagged


def test_the_refused_set_expires_if_nobody_is_refused_for_a_while():
    class Redis:
        def __init__(self):
            self.ttl = None

        def sadd(self, key, value):
            return 1

        def expire(self, key, seconds):
            self.ttl = (key, seconds)
    redis = Redis()
    assert ch.note_refused(redis, 9) is True
    assert redis.ttl == (ch.REFUSED_KEY, ch.REFUSED_SECONDS) and ch.REFUSED_SECONDS == 30 * 86400


def test_the_first_runs_write_to_the_mark_goes_through_the_locked_read_modify_write():
    """C2: check() read the mark, then asked Synapse about up to 20 accounts, then wrote its stale copy over a first chat's newer one."""
    run = read("ruqqus/helpers/chat_health.py").split("def run(", 1)[1].split("\ndef ", 1)[0]
    assert "write_mark=raise_under_lock" in run and "write_mark=matrix.write_id_mark" not in run
    assert "matrix.note_chat_id(" in run.split("def raise_under_lock", 1)[1].split("report = check(", 1)[0]
    client = read("ruqqus/helpers/matrix_client.py")
    assert 'r.lock("chat:id_mark_lock", timeout=30, blocking_timeout=5)' in client            # outlasts a 5 s read plus a 5 s write


def test_a_first_chat_is_made_one_request_at_a_time_per_member():
    """C3: two requests for a new member at once both found no record and both registered; the second was refused as an orphan."""
    src = read("ruqqus/routes/chat.py")
    ensure = src.split("def _ensure_provisioned", 1)[1].split("\ndef ", 1)[0]
    lock = ensure.index("pg_advisory_xact_lock(:k, :u)")
    first, again = ensure.index("filter_by(user_id=v.id).first()"), ensure.rindex("filter_by(user_id=v.id).first()")
    assert first < lock < again < ensure.index("matrix.provision_user(")
    assert "PROVISION_LOCK = 7004" in src


def test_a_chat_server_that_cant_be_asked_is_reported_not_raised():
    def down():
        raise ConnectionError
    report = ch.check(None, read_mark=down, write_mark=None, account_exists=None, site="s", now=1, sample=[], known=(0, 0))
    assert not report["ok"] and "could not be asked" in report["problems"][0]


# --- the wiring -----------------------------------------------------------------------------------------------------

def test_the_mark_is_written_before_any_account_is_made():
    src = read("ruqqus/helpers/matrix_client.py")
    body = src.split("def provision_user", 1)[1]
    assert body.index("note_chat_id(user_id)") < body.index("/register")


def test_a_refused_member_reads_why_instead_of_a_bare_503():
    src = read("ruqqus/routes/chat.py")
    ensure = src.split("def _ensure_provisioned", 1)[1].split("\ndef ", 1)[0]
    assert "_refuse_chat(v)" in ensure and "abort(503)" not in ensure
    refuse = src.split("def _refuse_chat", 1)[1].split("\ndef ", 1)[0]
    assert "chat_health.run(" in refuse and "note_refused" in refuse and "message.html" in refuse
    assert 'tr("Chat isn\'t available for your account right now.' in refuse


def test_every_place_runs_the_check():
    assert "chat_health.run(" in read("scripts/recomputes.py")
    main = read("ruqqus/__main__.py")
    assert "def _check_chat_ids" in main and 'environ.get("CHECK_CHAT_IDS", "1")' in main
    assert 'setdefault("CHECK_CHAT_IDS", "0")' in read("tests/pg/conftest.py")
    for script in ("scripts/verify_backup.sh", "scripts/restore.sh"):
        assert "chat_pair_check.sh" in read(script)
    pair = read("scripts/chat_pair_check.sh")
    assert "org.uqqus.chat_ids" in pair and "NOT IN (SELECT name FROM users)" in pair


def test_giving_an_account_back_is_for_owners_and_logged():
    src = read("ruqqus/routes/chat_health_pages.py")
    give = src.split("def admin_chat_give_back", 1)[0].rsplit("@app.route", 1)[1]
    assert "admin_level_required(4)" in give and "validate_formkey" in give
    assert "record_quietly" in src and "account_exists(mxid)" in src
    assert re.search(r'from \.chat_health_pages import \*', read("ruqqus/routes/__init__.py"))
