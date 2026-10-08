"""Polls cross the model, the schema, the vote route, the composers and their drafts, submit_post, forwards,
edits, the word filter, get_posts, the post JSON, two templates and the page shell. These checks keep the layers
agreeing and keep the promises: one poll per primary post (so every forward and repost votes the same one), a
vote is final and needs a login and the form key, the options count for the word filter everywhere a post is
rated, and nobody is ever shown who voted."""
import re
from pathlib import Path

from ruqqus.helpers import polls as rules

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


ROUTES = read("ruqqus", "routes", "polls.py")
STORE = read("ruqqus", "helpers", "poll_store.py")
POSTS = read("ruqqus", "routes", "posts.py")
GET = read("ruqqus", "helpers", "get.py")
FILTER = read("ruqqus", "helpers", "word_filter_store.py")
SCRIPT = read("ruqqus", "assets", "js", "poll.js")
COMPOSER_SCRIPT = read("ruqqus", "assets", "js", "poll_composer.js")
BLOCK = read("ruqqus", "templates", "partials", "poll.html")
OPTIONS = read("ruqqus", "templates", "partials", "post_options.html")


def submit_source():
    return POSTS.split("def submit_post(v):")[1].split("def composer_panel(")[0]


# --- storage ------------------------------------------------------------------------------------------

def columns_of(source, cls):
    body = source.split(f"class {cls}(")[1].split("\nclass ")[0]
    return set(re.findall(r"^    (\w+) = Column\(", body, re.M))


def test_the_models_the_schema_and_the_migration_have_the_same_columns():
    model = read("ruqqus", "classes", "poll.py")
    expected = {"Poll": {"id", "post_id", "closes_utc", "created_utc"},
                "PollOption": {"id", "poll_id", "ordinal", "label"},
                "PollVote": {"id", "poll_id", "option_id", "user_id", "created_utc"}}
    tables = {"Poll": "polls", "PollOption": "poll_options", "PollVote": "poll_votes"}
    for cls, columns in expected.items():
        assert columns_of(model, cls) == columns, cls
    for sql, header in ((read("schema.sql"), "CREATE TABLE public."), (read("scripts", "migrations", "2026-10-09_polls.sql"), "CREATE TABLE IF NOT EXISTS ")):
        for cls, table in tables.items():
            block = re.search(rf"{header}{table} \((.*?)\n\);", sql, re.S).group(1)
            names = {line.split()[0] for line in (x.strip() for x in block.splitlines()) if line and not line.startswith("CONSTRAINT")}
            assert names == expected[cls], (header, table)
    assert "from .poll import *" in read("ruqqus", "classes", "__init__.py")
    assert "from .polls import *" in read("ruqqus", "routes", "__init__.py")


def test_one_poll_per_post_one_vote_per_member_and_a_deleted_poll_takes_its_votes():
    for sql in (read("schema.sql"), read("scripts", "migrations", "2026-10-09_polls.sql")):
        assert "CONSTRAINT polls_post_key UNIQUE (post_id)" in sql
        assert "CONSTRAINT poll_votes_one_per_member UNIQUE (poll_id, user_id)" in sql
        assert "CONSTRAINT poll_options_ordinal_key UNIQUE (poll_id, ordinal)" in sql
        assert sql.count("ON DELETE CASCADE") == 3
    model = read("ruqqus", "classes", "poll.py")
    assert 'UniqueConstraint("post_id", name="polls_post_key")' in model
    assert 'UniqueConstraint("poll_id", "user_id", name="poll_votes_one_per_member")' in model
    assert rules.MAX_OPTIONS == 4 and rules.OPTION_CHARS == 40
    assert "label character varying(40) NOT NULL" in read("schema.sql")


# --- one poll for every copy of a post ---------------------------------------------------------------------

def test_a_poll_is_looked_up_through_the_primary_post_everywhere():
    assert "from ruqqus.helpers.coauthors import primary_post_id" in STORE and "from ruqqus.helpers.coauthors import primary_post_id" in ROUTES
    # the page lookup, the single lookup and the vote all resolve the post first
    assert STORE.count("primary_post_id(post)") >= 3
    assert "poll = self.polls.get(primary_post_id(post))" in STORE
    assert "g.db.query(Poll).filter_by(post_id=primary_id).first()" in ROUTES
    # a poll is made on the post just written, never on a copy
    assert "poll_store.create(g.db, new_post, poll_options, poll_hours)" in POSTS
    assert POSTS.count("poll_store.create(") == 1


def test_the_poll_is_made_with_the_post_before_any_forward_copies_it():
    body = submit_source()
    assert body.index("poll_store.create(") < body.index("create_forward_post(")


# --- voting -------------------------------------------------------------------------------------------------------

def route():
    return ROUTES.split("def poll_vote(")[1].split("\n\n\n")[0], ROUTES.split("def poll_vote(")[0].rsplit("@app.route(", 1)[1]


def test_a_vote_needs_a_login_not_a_ban_and_the_form_key():
    body, head = route()
    assert 'methods=["POST"]' in head.splitlines()[0] and "@is_not_banned" in head and "@validate_formkey" in head
    assert head.index("@is_not_banned") < head.index("@validate_formkey")


def test_a_vote_is_counted_only_on_a_post_the_caller_may_see():
    body, _ = route()
    for rule in ("_is_post_id(pid)", "post.is_banned or post.deleted_utc or post.board.is_banned",
                 "post_hidden(post, v)", "post.board.can_view(v)"):
        assert rule in body, rule
    assert body.index("abort(404)") < body.index("g.db.add(PollVote(")
    # a poll whose own post has been removed is gone, even when a copy is still up
    assert "gone = primary is None or bool(primary.is_banned or primary.deleted_utc)" in body


def test_a_vote_is_final_and_a_race_is_a_conflict_not_a_second_vote():
    body, _ = route()
    assert "rules.vote_refusal(gone, rules.is_closed(poll.closes_utc, now), voted, option is not None)" in body
    assert "filter_by(id=int(raw), poll_id=poll.id)" in body          # the option must be this poll's own
    assert "raw.isascii() and raw.isdigit()" in body
    assert "except IntegrityError:" in body and "g.db.rollback()" in body
    assert "g.db.delete(" not in body and ".update(" not in body       # a vote is never changed or taken back


def test_without_script_the_form_posts_and_goes_back_to_the_post():
    body, _ = route()
    assert 'if request.values.get("back"):' in body and "return _back(post)" in body
    assert "return redirect(post.permalink)" in ROUTES


def test_the_answer_is_the_poll_as_it_is_now_drawn():
    body, _ = route()
    assert "post.poll_data = poll_store.load_one(g.db, post, v)" in body
    assert 'render_template("partials/poll_fragment.html", p=post, v=v)' in body


# --- what is shown --------------------------------------------------------------------------------------------------

def test_counts_are_only_in_what_a_page_is_given_when_the_viewer_may_see_them():
    view = STORE.split("def view(")[1].split("\n\n\ndef ")[0]
    assert "results = rules.results_visible(closed, voted, is_author)" in view
    assert '"total": sum(totals) if results else None' in view
    assert '"votes": totals[i] if results else None' in view and '"percent": percents[i] if results else None' in view
    # the viewer's own vote is the only identity anywhere in it
    assert "user" not in view.replace("viewer", "").replace("user_id", "")
    assert "user_id" not in re.sub(r"PollVote\.user_id", "", STORE.split("def view(")[1].split("def attach(")[0])


def test_a_page_of_posts_gets_its_polls_in_a_few_queries():
    attach = STORE.split("def attach(")[1].split("def load_one(")[0]
    assert attach.count("_Found(") == 1                              # one lookup for the whole page
    assert "post.poll_data = found.view(post, viewer, now)" in attach
    assert "poll_store.attach(g.db, posts, v)" in GET
    assert GET.index("posts = sorted(output") < GET.index("poll_store.attach(g.db, posts, v)")
    assert "poll_data" in ROUTES.split("def _poll_of(")[1].split("app.jinja_env")[0]


def test_the_post_json_carries_the_poll_with_the_same_visibility():
    assert "poll = poll_store.json_of(self)" in read("ruqqus", "classes", "submission.py")
    body = STORE.split("def json_of(")[1]
    assert '"total": data["total"]' in body and 'o["votes"]' in body
    assert "has_app_context()" in body                               # reading .json in a script must not need a request


def test_the_jinja_helpers_are_registered():
    registered = ROUTES.split("app.jinja_env.globals.update(")[1].split(")\n")[0]
    for name in ("poll_of=_poll_of", "poll_durations=rules.DURATIONS", "poll_default_hours=rules.DEFAULT_HOURS",
                 "poll_max_options=rules.MAX_OPTIONS", "poll_min_options=rules.MIN_OPTIONS"):
        assert name in registered, name


# --- the word filter -----------------------------------------------------------------------------------------------------

def test_the_options_count_wherever_a_post_is_rated():
    # a new post (and the same post after a body picture is added), a forward copy, an edit and a re-scan
    assert POSTS.count("extra=poll_rules.option_text(poll_options)") == 2
    assert "severity_extra=poll_store.option_text(g.db, primary.id)" in POSTS
    assert "apply_post_severity(new_post, title, body_html, extra=severity_extra)" in POSTS
    assert "post_severity(title, body_html, extra=poll_store.option_text(g.db, primary.id))" in POSTS
    assert "extras = poll_store.option_texts(db, [row.repost_id or row.id for row in rows])" in FILTER
    assert 'extra=extras.get(row.repost_id or row.id, "")' in FILTER
    assert "f.severity(extra) if extra else 0" in FILTER
    # every call that rates a post says what its extra is (none left to forget)
    for call in re.findall(r"apply_post_severity\([^)]*\)", POSTS):
        assert "extra=" in call, call


def test_the_options_of_a_copy_are_those_of_the_post_it_points_at():
    forward = POSTS.split("def create_forward_post(")[1].split("def create_forward_post_from_comment(")[0]
    assert "poll_store.option_text(g.db, primary.id)" in forward
    comment_forward = POSTS.split("def create_forward_post_from_comment(")[1].split("@app.route")[0]
    assert "severity_extra" not in comment_forward                    # a forwarded comment is a new post with no poll


# --- making a poll --------------------------------------------------------------------------------------------------------

def test_bad_options_stop_the_post_before_it_is_made():
    body = submit_source()
    check = body.index('poll_rules.clean_options(request.form.getlist("poll_option"))')
    assert check < body.index("    new_post = Submission(")
    block = body[check:body.index("    new_post = Submission(")]
    assert 'poll_rules.clean_hours(request.form.get("poll_hours")) if poll_options else None' in block
    assert '"api": lambda: ({"error": poll_error}, 400)' in block and "submit.html" in block


def test_a_draft_keeps_the_poll_and_a_scheduled_post_must_be_whole():
    drafts = read("ruqqus", "helpers", "post_drafts.py")
    assert 'poll_rules.clean_options(form.getlist("poll_option"), complete=False)' in drafts
    assert '"poll_options": poll_options' in drafts and '"poll_hours": poll_hours' in drafts
    assert 'data["poll_option"] = list(options["poll_options"])' in drafts and 'data["poll_hours"] = str(' in drafts
    assert "def require_poll(fields):" in drafts and "complete=True" in drafts
    route_source = read("ruqqus", "routes", "post_drafts.py")
    assert route_source.index("pd.require_poll(fields)") < route_source.index("pd.schedule_time(raw_when, now)")
    js = read("ruqqus", "assets", "js", "post_drafts.js")
    assert 'input[name="poll_option"]' in js and 'select[name="poll_hours"]' in js and "!i.disabled" in js


def test_the_poll_panel_is_in_both_composers_and_closed_panels_send_nothing():
    assert "{% macro poll_field(options, hours, prefix) %}" in OPTIONS
    assert 'post_options.poll_field(draft.option("poll_options", [])' in read("ruqqus", "templates", "submit.html")
    inline = read("ruqqus", "templates", "partials", "inline_composer.html")
    assert "coauthors_field, poll_field %}" in inline.split("\n")[16] or "poll_field" in inline.split("{% from")[1].split("\n")[0]
    assert 'poll_field([], poll_default_hours, "ic-poll")' in inline
    # a closed panel's fields are disabled, so they are not in the form that is posted
    macro = OPTIONS.split("{% macro poll_field(")[1].split("{% endmacro %}")[0]
    assert macro.count("{% if not options %} disabled{% endif %}") == 2
    assert 'name="poll_option"' in macro and 'name="poll_hours"' in macro and 'maxlength="40"' in macro
    assert "{% for h, label in poll_durations %}" in macro and "range(poll_max_options)" in macro
    assert "field.disabled = true" in COMPOSER_SCRIPT and "'reset'" in COMPOSER_SCRIPT and "MAX_ROWS = 4" in COMPOSER_SCRIPT
    assert "poll_composer.js" in read("ruqqus", "templates", "default.html")
    # Create a post is a page of its own (not the shared shell), so it loads the script itself
    assert '<script src="/assets/js/poll_composer.js?v=1"></script>' in read("ruqqus", "templates", "submit.html")


# --- the templates and the script -------------------------------------------------------------------------------------------

def test_the_poll_is_drawn_under_the_body_and_note_of_a_card_and_of_a_post():
    for name in ("submission_listing.html", "submission.html"):
        html = read("ruqqus", "templates", name)
        assert '{% from "partials/poll.html" import poll_block %}' in html, name
        assert html.count("{{ poll_block(p, v) }}") == 1, name
        assert html.index("{{ community_note(p) }}") < html.index("{{ poll_block(p, v) }}"), name
    assert read("ruqqus", "templates", "partials", "poll_fragment.html").strip() == \
        '{% from "partials/poll.html" import poll_block %}{{ poll_block(p, v) }}'


def test_the_block_escapes_labels_names_nobody_and_works_without_script():
    assert "| safe" not in BLOCK
    assert BLOCK.count("{{ o.label }}") == 3                          # bars, buttons, the visitor's links
    assert "o.user" not in BLOCK and "voter" not in BLOCK.replace("Log in to vote", "")
    assert '<form method="post" action="/api/poll/{{ p.base36id }}/vote"' in BLOCK
    assert 'name="back" value="1"' in BLOCK and 'name="formkey" value="{{ v.formkey }}"' in BLOCK
    assert "{% if poll.results %}" in BLOCK and "{% elif poll.can_vote %}" in BLOCK
    assert 'data-poll-url="/api/poll/{{ p.base36id }}/vote"' in BLOCK
    # a visitor is sent to log in, to this post
    assert "/login?redirect={{ p.permalink | urlencode }}" in BLOCK


def test_the_script_only_votes_and_never_trusts_markup_beyond_the_servers_fragment():
    assert "innerHTML" not in SCRIPT and "DOMParser" in SCRIPT
    assert "event.stopPropagation()" in SCRIPT                        # voting is not opening the post
    assert '.poll .poll-option[type="submit"]' in SCRIPT
    assert "poll.getAttribute('data-poll-url')" in SCRIPT
    shell = read("ruqqus", "templates", "default.html")
    assert '<script src="/assets/js/poll.js?v=1"></script>' in shell
    assert "{% if v %}<script src=\"/assets/js/poll_composer.js?v=1\"></script>{% endif %}" in shell


def test_both_stylesheets_style_the_poll_and_the_panel():
    for sheet in ("main.scss", "main_dark.scss"):
        css = read("ruqqus", "assets", "style", sheet)
        for selector in (".poll {", ".poll-option {", ".poll-result {", ".poll-bar {", ".poll-result.mine .poll-bar {",
                         ".poll-meta {", ".poll-composer .poll-panel {", "@keyframes poll-grow {"):
            assert selector in css, (sheet, selector)
        # above the stretched link of a card, or a vote would open the post
        assert re.search(r"\.poll \{[^}]*position: relative;[^}]*z-index: 2;", css), sheet
        assert "prefers-reduced-motion" in css.split("@keyframes poll-grow")[1].split("// A continuous feed")[0], sheet
