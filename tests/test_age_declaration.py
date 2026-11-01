"""The age declaration (ruqqus/helpers/age_declaration.py): the rules, who is told the Terms changed (SQL on SQLite), the
database's own rule, and the source: every way an account is made, every way a filter level is written, and where the
question is drawn. The routes themselves run on the real app in tests/pg/test_age_declaration.py."""
import re
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from ruqqus.helpers import age_declaration as age
from ruqqus.helpers.sensitive import shows_sensitive
from ruqqus.helpers.visibility import CHILD as CHILD_FILTER, viewer_level

ROOT = Path(__file__).resolve().parents[1]


def read(*parts):
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def python_sources():
    return [path for path in (ROOT / "ruqqus").rglob("*.py")]


# ---------------------------------------------------------------- the rules

@pytest.mark.parametrize("value, expected", [("1", age.ADULT), ("2", age.CHILD), ("", None), (None, None), ("0", None),
                                             ("3", None), (" 1", None), ("１", None), ("adult", None), (1, None), (["1"], None)])
def test_only_the_two_exact_answers_count(value, expected):
    assert age.parse(value) == expected


def test_a_child_account_is_put_on_the_child_filter_with_sensitive_content_off():
    fields = age.fields_for(age.CHILD, 1000)
    assert fields == {"age_declared": age.CHILD, "age_declared_utc": 1000, "filter_level": CHILD_FILTER,
                      "hide_offensive": True, "all_shows_sensitive": False}


def test_an_adult_answer_touches_nothing_else():
    assert age.fields_for(age.ADULT, 1000) == {"age_declared": age.ADULT, "age_declared_utc": 1000}


def test_clearing_an_answer_says_when_as_nothing():
    assert age.fields_for(None, 1000) == {"age_declared": None, "age_declared_utc": 0}


def test_a_child_account_can_only_be_on_the_child_filter():
    child = NS(age_declared=age.CHILD)
    assert age.locked_filter(child, 0) and age.locked_filter(child, 1)
    assert age.locked_filter(child, CHILD_FILTER) is None
    for other in (NS(age_declared=age.ADULT), NS(age_declared=None), NS()):
        assert age.locked_filter(other, 0) is None


def test_a_child_account_can_only_leave_sensitive_content_off():
    child = NS(age_declared=age.CHILD)
    assert age.locked_sensitive(child, True) and age.locked_sensitive(child, False) is None
    assert age.locked_sensitive(NS(age_declared=age.ADULT), True) is None


def test_the_filter_itself_keeps_sensitive_content_from_the_child_level_whatever_the_flag_says():
    # what slice 3 of the plan asked to be confirmed: the rule was already in helpers/sensitive.py
    assert shows_sensitive(NS(all_shows_sensitive=True, filter_level=CHILD_FILTER)) is False
    assert shows_sensitive(NS(all_shows_sensitive=True, filter_level=1)) is True
    assert viewer_level(NS(filter_level=CHILD_FILTER, age_declared=age.CHILD)) == CHILD_FILTER


def test_who_is_asked():
    assert age.is_asked(NS(age_declared=None, is_bot=False))
    assert not age.is_asked(None)
    assert not age.is_asked(NS(age_declared=None, is_bot=True))
    assert not age.is_asked(NS(age_declared=age.ADULT, is_bot=False))
    assert not age.is_asked(NS(age_declared=age.CHILD, is_bot=False))
    assert not age.is_asked(NS(is_bot=False)), "a stand-in that knows nothing of it is not asked"


# ---------------------------------------------------------------- every way an account is made

def test_every_account_the_site_makes_is_either_asked_or_a_bot():
    makers = {}
    for path in python_sources():
        for match in re.finditer(r"(?<![\w.<])(?<!class )User\(", path.read_text(encoding="utf-8")):
            makers.setdefault(path.name, []).append(match.start())
    assert set(makers) == {"login.py", "bot_accounts.py"}, "a new way to make an account has to decide about the question"
    login = read("ruqqus", "routes", "login.py")
    post = login.split("def sign_up_post(", 1)[1].split("\n@app.route", 1)[0]
    assert 'age_declaration.parse(request.form.get("age_declaration"))' in post
    assert "**age_declaration.fields_for(age_answer, signup_utc)," in post
    # refused before anything is counted against the address or the guard
    assert post.index("if age_answer is None:") < post.index("guard = _signup_guard(")
    bots = read("ruqqus", "routes", "bot_accounts.py").split("account = User(", 1)[1].split(")\n", 1)[0]
    assert "is_bot=True" in bots and "age_declared" not in bots


def test_no_raw_sql_makes_an_account():
    for path in python_sources():
        assert not re.search(r"INSERT\s+INTO\s+(public\.)?users\b", path.read_text(encoding="utf-8"), re.I), path


# ---------------------------------------------------------------- every way out of the Child level

def test_the_filter_level_is_written_in_one_route_and_it_asks_first():
    writers = []
    for path in python_sources():
        source = path.read_text(encoding="utf-8")
        if re.search(r"\.filter_level\s*=[^=]|SET[^\"']*\bfilter_level\b", source):
            writers.append(path.name)
    assert writers == ["settings.py"], writers
    settings = read("ruqqus", "routes", "settings.py")
    route = settings.split("def settings_profile_post(v):", 1)[1].split("\n@app.", 1)[0]
    assert route.index("age_declaration.locked_filter(v, new_level)") < route.index("v.verifyPass(") < route.index("v.filter_level = new_level")
    assert route.count("v.filter_level =") == 1


def test_the_sensitive_choice_is_written_in_one_route_and_it_asks_first():
    writers = []
    for path in python_sources():
        if re.search(r"\.all_shows_sensitive\s*=[^=]|SET[^\"']*\ball_shows_sensitive\b", path.read_text(encoding="utf-8")):
            writers.append(path.name)
    assert writers == ["settings.py"], writers
    route = read("ruqqus", "routes", "settings.py").split("def settings_all_shows_sensitive(v):", 1)[1].split("\n@app.", 1)[0]
    assert route.index("age_declaration.locked_sensitive(") < route.index("v.all_shows_sensitive =")


def test_a_saved_default_holds_no_filter_level():
    filters = read("ruqqus", "helpers", "feed_filters.py")
    assert "filter_level" not in filters and "all_shows_sensitive" not in filters


def test_no_route_takes_a_filter_level_from_the_request_but_the_one_that_asks():
    for path in (ROOT / "ruqqus" / "routes").glob("*.py"):
        found = re.findall(r"""request\.\w+\.get\(["']filter_level["']""", path.read_text(encoding="utf-8"))
        assert len(found) == (1 if path.name == "settings.py" else 0), path


CONSTRAINT = re.search(r"ADD CONSTRAINT users_age_declared_rule CHECK \((.*?)\);", read("schema.sql"), re.S).group(1)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text(f"""CREATE TABLE users (id INTEGER PRIMARY KEY, created_utc INTEGER DEFAULT 0, is_deleted BOOLEAN DEFAULT 0,
            is_bot BOOLEAN DEFAULT 0, filter_level SMALLINT DEFAULT 1, all_shows_sensitive BOOLEAN DEFAULT 0 NOT NULL,
            age_declared SMALLINT, age_declared_utc INTEGER DEFAULT 0 NOT NULL,
            CONSTRAINT users_age_declared_rule CHECK ({CONSTRAINT}))"""))
        conn.execute(text("CREATE TABLE notifications (id INTEGER PRIMARY KEY, user_id INTEGER, comment_id INTEGER)"))
        conn.execute(text("CREATE TABLE comments_aux (id INTEGER PRIMARY KEY, notice_msgid TEXT)"))
    with engine.connect() as conn:
        yield conn


def test_the_database_itself_refuses_a_child_account_off_the_child_filter(db):
    db.execute(text("INSERT INTO users (id, filter_level, age_declared) VALUES (5, 2, 2)"))
    for change in ("filter_level = 1", "filter_level = 0", "all_shows_sensitive = 1"):
        with pytest.raises(IntegrityError):
            db.execute(text(f"UPDATE users SET {change} WHERE id = 5"))
    with pytest.raises(IntegrityError):
        db.execute(text("INSERT INTO users (id, filter_level, age_declared) VALUES (6, 1, 2)"))
    with pytest.raises(IntegrityError):
        db.execute(text("INSERT INTO users (id, filter_level, age_declared) VALUES (7, 2, 3)"))


def test_the_database_leaves_everyone_else_alone(db):
    db.execute(text("INSERT INTO users (id, filter_level, age_declared) VALUES (1, 0, 1), (2, 0, NULL), (3, 2, 1)"))
    db.execute(text("UPDATE users SET filter_level = 1, all_shows_sensitive = 1"))
    db.execute(text("UPDATE users SET age_declared = 1 WHERE id = 2"))
    db.execute(text("INSERT INTO users (id, filter_level, age_declared) VALUES (5, 2, 2)"))
    db.execute(text("UPDATE users SET age_declared = 1 WHERE id = 5"))        # an admin's way out: the filter stays where it is
    db.execute(text("UPDATE users SET filter_level = 1 WHERE id = 5"))        # and then the member can lower it


# ---------------------------------------------------------------- who is told the Terms changed

def test_the_terms_notice_goes_to_people_who_agreed_to_the_old_terms_once(db):
    db.execute(text("""INSERT INTO users (id, created_utc, is_deleted, is_bot, filter_level, age_declared, age_declared_utc) VALUES
        (1, 10, 0, 0, 1, NULL, 0), (2, 10, 0, 0, 1, NULL, 0), (3, 10, 0, 0, 1, 1, 500), (4, 501, 0, 0, 2, 2, 500),
        (5, 500, 0, 0, 1, 1, 500), (6, 10, 1, 0, 1, NULL, 0), (7, 10, 0, 1, 1, NULL, 0)"""))
    # 1 the site's own account, 2 not answered, 3 answered later, 4 and 5 signed up since (User() stamps its own created_utc,
    # so the two can be a second apart), 6 deleted, 7 a bot
    assert age.terms_notice_ids(db) == [2, 3]
    db.execute(text("INSERT INTO comments_aux (id, notice_msgid) VALUES (70, :m), (71, 'another notice')"), {"m": age.TERMS_NOTICE})
    db.execute(text("INSERT INTO notifications (user_id, comment_id) VALUES (2, 70), (3, 71)"))
    assert age.terms_notice_ids(db) == [3]


def test_the_terms_notice_has_the_link_and_the_terms_words():
    assert "[Terms](/help/terms)" in age.TERMS_NOTICE and "at least 18, or at least 13" in age.TERMS_NOTICE


def test_the_one_off_is_by_hand_and_nothing_sends_it_by_itself():
    script = read("scripts", "notify_terms_age.py")
    assert '"--once" not in sys.argv' in script and '"--dry-run" in sys.argv' in script
    assert "notify_many(ids, age_declaration.TERMS_NOTICE)" in script
    assert "TERMS_NOTICE" not in read("scripts", "migrations", "2026-12-10_age_declaration.sql")
    assert "notify" not in read("scripts", "migrations", "2026-12-10_age_declaration.sql").lower()
    for path in python_sources():
        if path.name != "age_declaration.py":
            assert "TERMS_NOTICE" not in path.read_text(encoding="utf-8"), path
    assert "notify_terms_age" not in read("supervisord.conf")


# ---------------------------------------------------------------- the question

def test_the_two_choices_are_written_once_and_asked_in_the_same_words_everywhere():
    question = read("ruqqus", "templates", "partials", "age_question.html")
    assert question.count("I'm 18 or over") == 1
    assert question.count("I'm a parent or guardian setting this up for my child (13 to 17)") == 1
    assert 'name="age_declaration" value="1" required' in question and 'name="age_declaration" value="2" required' in question
    assert " checked" not in question, "nothing is answered for them"
    assert 'import age_choices' in read("ruqqus", "templates", "sign_up.html")
    assert 'import age_form' in read("ruqqus", "templates", "partials", "age_notice.html")
    assert 'import age_form' in read("ruqqus", "templates", "settings_filters.html")
    assert "age_choices(prefix)" in question.split("macro age_form", 1)[1]
    # the page that asks again quotes the same message
    assert "I'm a parent or guardian setting this up for my child (13 to 17)" in read("ruqqus", "templates", "age_confirm.html")


def test_the_notice_never_blocks_and_never_shows_in_a_side_panel():
    shell = read("ruqqus", "templates", "default.html")
    line = next(line for line in shell.splitlines() if "partials/age_notice.html" in line)
    assert "v.age_declared is none" in line and "not v.is_bot" in line and "not request.args.get('embed')" in line
    notice = read("ruqqus", "templates", "partials", "age_notice.html")
    for dialog in ('role="dialog"', "modal", "aria-modal", "position: fixed", "autofocus"):
        assert dialog not in notice
    assert "<script" not in notice
    style = read("ruqqus", "assets", "style", "_components.scss").split(".age-notice {", 1)[1].split("\n}", 1)[0]
    assert "position" not in style and "z-index" not in style
    assert "#" not in style, "tokens only"


def test_the_notice_only_comes_back_to_an_address_that_answers_a_get():
    notice = read("ruqqus", "templates", "partials", "age_notice.html")
    assert """request.full_path.rstrip('?') if request.method in ("GET", "HEAD") else "/settings/content\"""" in notice


def test_the_chat_page_is_the_screen_between_the_bars_so_it_doesnt_carry_the_notice():
    assert "{% set hide_age_notice = true %}" in read("ruqqus", "templates", "chat.html")
    assert "not hide_age_notice" in next(line for line in read("ruqqus", "templates", "default.html").splitlines()
                                         if "partials/age_notice.html" in line)


def test_both_choices_are_a_target_tall():
    question = read("ruqqus", "templates", "partials", "age_question.html")
    assert question.count('class="custom-control custom-radio age-choice"') == 2
    assert "btn-sm" not in question, "the ladder's default, 36px; 28 is under the 32 a control needs on a desktop"
    style = read("ruqqus", "assets", "style", "_components.scss").split(".age-choice .custom-control-label {", 1)[1].split("\n}", 1)[0]
    assert "min-height: var(--hit-min);" in style and "--age-choice-pad: calc((var(--hit-min) - 1.5em) / 2);" in style


def test_a_child_account_isnt_sent_to_a_setting_it_cant_change():
    page = read("ruqqus", "templates", "filtered_words.html")
    child = page.split('{% if state == "child" %}', 1)[1].split('{% elif state == "warning" %}', 1)[0]
    locked, free = child.split("{% else %}", 1)
    assert "v.age_declared == 2" in locked and "Change your word filter" not in locked
    assert f'_("{age.FILTER_LOCKED}")' in locked, "the words Settings uses"
    assert "Change your word filter" in free


def test_only_the_shell_of_ordinary_pages_carries_it():
    assert "age_notice" not in read("ruqqus", "templates", "header.html")
    assert "age_notice" not in read("ruqqus", "templates", "space.html")


def test_the_answer_route():
    pages = read("ruqqus", "routes", "age_declaration_pages.py")
    route = pages.split("def settings_age_declaration(v):", 1)[1].split("\n@app.", 1)[0]
    head = pages.split("def settings_age_declaration(v):", 1)[0]
    assert head.rstrip().endswith('@auth_required\n@not_qr_limited\n@validate_formkey')
    assert "User.age_declared.is_(None)" in route, "once only, in the statement that writes"
    assert route.index('request.form.get("confirm") != "1"') < route.index(".update(")
    assert 'same_site(request.form.get("next")' in route
    assert "not_quarantined" not in pages, "a held account may change its own settings"


def test_the_admin_route():
    pages = read("ruqqus", "routes", "age_declaration_pages.py")
    head, route = pages.split("def admin_age_declaration(v):", 1)
    assert head.rstrip().endswith("@admin_level_required(3)\n@validate_formkey")
    assert "target.is_bot" in route and "v.admin_level <= (target.admin_level or 0)" in route
    assert 'action_log.record_quietly(g.db, "age_declaration"' in route
    assert "notify(target, age_declaration.ADMIN_NOTICES[answer])" in route
    log = read("ruqqus", "helpers", "action_log.py")
    assert '"remove_story", "age_declaration"}' in log, "what a member said about their age is the staff's to read, never the public's"
    assert set(age.ADMIN_NOTICES) == {age.ADULT, age.CHILD, None} == set(age.LABELS)


def test_only_the_member_and_the_admins_are_told_the_answer():
    for name in ("user.py",):
        source = read("ruqqus", "classes", name)
        assert "age_declared" not in source.split("def json_raw", 1)[1].split("\n    @", 1)[0]
    panel = read("ruqqus", "templates", "userpage.html")
    before = panel.split('action="/admin/age_declaration"', 1)[0].rsplit("{% if", 1)[1]
    assert "v.admin_level >=3 and v.admin_level > u.admin_level and not u.is_bot" in before
    for path in (ROOT / "ruqqus" / "templates").rglob("*.html"):
        if "age_declared" in path.read_text(encoding="utf-8"):
            # filtered_words.html: the viewer's own refusal page, which reads only v.age_declared
            assert path.name in ("settings_filters.html", "userpage.html", "default.html", "filtered_words.html"), path


# ---------------------------------------------------------------- what is written down

def test_the_columns_and_the_constraint_are_in_the_migration_and_the_schema():
    migration = read("scripts", "migrations", "2026-12-10_age_declaration.sql")
    schema = read("schema.sql")
    for piece in ("ADD COLUMN IF NOT EXISTS age_declared smallint", "ADD COLUMN IF NOT EXISTS age_declared_utc integer DEFAULT 0 NOT NULL",
                  "IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'users_age_declared_rule')"):
        assert piece in migration
    users = schema.split("CREATE TABLE public.users (", 1)[1].split("\n);", 1)[0]
    assert "    age_declared smallint," in users and "    age_declared_utc integer DEFAULT 0 NOT NULL" in users
    assert "users_age_declared_rule" in schema and "('2026-12-10_age_declaration.sql')" in schema
    model = read("ruqqus", "classes", "user.py")
    assert "age_declared = Column(SmallInteger, default=None)" in model


def test_the_export_and_the_clean_up():
    from ruqqus.helpers import data_export
    sections = dict(data_export.SECTIONS)
    assert "age_declared" in sections["age"] and "age_declared_utc" in sections["age"]
    cleanup = read("ruqqus", "helpers", "account_cleanup.py")
    assert 'UPDATE users SET age_declared = NULL, age_declared_utc = 0 WHERE id = :u' in cleanup
    assert "WHERE target_user_id = :u AND action = 'age_declaration'" in cleanup, "an admin's note says what the answer was"
    assert "ADMIN_NOTICES" in cleanup, "and so does the notice they sent"


def test_the_terms_and_the_privacy_page_say_what_is_true():
    terms = read("ruqqus", "templates", "help", "terms.html")
    assert "at least 18 years old, or at least 13 years old with an account made for you by a parent or guardian" in terms
    assert "if you indicate as such in your user settings" not in terms
    privacy = read("ruqqus", "templates", "help", "privacy.html")
    assert "Whether or not you have indicated that you are 18" not in privacy
    assert "What you told us about your age, and when" in privacy and "we don't check it" in privacy
    assert "keeps only that an admin changed the answer and when, not what it was or why" in privacy
