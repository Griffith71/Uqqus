"""Who upvoted crosses a helper, two routes, three templates, a script and both stylesheets. These checks keep the
layers agreeing and keep the promises: only the author, only upvotes, only mutual followers, and nothing at all
in the markup of anyone who is not the author."""
import re
from pathlib import Path
from types import SimpleNamespace as NS

from jinja2 import Environment

from ruqqus.helpers import voters

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


HELPER = read("ruqqus", "helpers", "voters.py")
ROUTES = read("ruqqus", "routes", "voters.py")
SCRIPT = read("ruqqus", "assets", "js", "voters.js")
LISTING = read("ruqqus", "templates", "submission_listing.html")
PAGE = read("ruqqus", "templates", "submission.html")
COMMENTS = read("ruqqus", "templates", "comments.html")


# --- the rule in the SQL -----------------------------------------------------------------------------------------

def test_the_sql_needs_a_follow_in_each_direction_and_only_ever_selects_upvotes():
    sql = str(voters._STATEMENTS["post"])
    assert "JOIN follows a ON a.user_id = :author AND a.target_id = v.user_id" in sql
    assert "JOIN follows b ON b.user_id = v.user_id AND b.target_id = :author" in sql
    assert sql.count("vote_type") == 1 and "v.vote_type = 1" in sql
    assert "v.user_id <> :author" in sql and "GROUP BY v.user_id" in sql
    assert "userblocks" in sql and "COALESCE(u.is_banned, 0) = 0" in sql and "COALESCE(u.is_deleted, :no) = :no" in sql
    # fixed text: the only things that change are bound parameters (the two statements differ in table and column)
    assert str(voters._STATEMENTS["comment"]) == sql.replace("votes v", "commentvotes v").replace("v.submission_id", "v.comment_id")


def test_the_accounts_go_through_the_word_filter():
    assert "filter_users(db.query(User).filter(User.id.in_(ids)), viewer)" in HELPER


# --- the routes -------------------------------------------------------------------------------------------------------

def test_the_routes_need_a_login_are_get_only_and_compare_the_authors_id():
    for name in ("post_voters", "comment_voters"):
        head = ROUTES.split(f"def {name}(")[0].rsplit("@app.route(", 1)[1]
        assert 'methods=["GET"]' in head.splitlines()[0] and "@auth_required" in head, name
    answer = ROUTES.split("def _answer(")[1].split("@app.route")[0]
    assert answer.index("abort(404)") < answer.index("item.author_id != v.id") < answer.index("friends_who_upvoted(")
    assert '"Only the author can see who upvoted."' in answer and ", 403" in answer
    assert 'response.headers["Cache-Control"] = "private, no-store"' in answer


def test_nothing_but_name_avatar_and_link_leaves_the_server():
    answer = ROUTES.split("def _answer(")[1].split("@app.route")[0]
    assert '{"username": u.username, "permalink": u.permalink, "avatar": u.profile_url}' in answer
    code = ROUTES.split('"""', 2)[2]                                   # below the docstring, which explains the rule in words
    assert "vote_type" not in code and "downvote" not in code.lower()
    assert "from .voters import *" in read("ruqqus", "routes", "__init__.py")


def test_only_the_admin_vote_tool_loads_the_accounts_behind_a_vote():
    # a tripwire: loading the user of a vote is how a voter list would be made. Today that is the level-4 admin
    # tool (routes/admin.py, unchanged) and nothing else; a new place needs the same decision as this feature
    found = sorted(path.name for path in (ROOT / "ruqqus" / "routes").glob("*.py")
                   if re.search(r"joinedload\((Comment)?Vote\.user\)", path.read_text(encoding="utf-8")))
    assert found == ["admin.py"], found


# --- the markup -------------------------------------------------------------------------------------------------------

def score_span(html, marker):
    return re.search(r"\{% set see_voters = may_see_voters\(" + marker + r", v\) %\}<span.*?</span>", html, re.S).group(0)


def render(snippet, **context):
    env = Environment()
    env.globals["may_see_voters"] = lambda item, viewer: context.pop("see")
    return env.from_string(snippet).render(**context)


def post_context(see):
    return dict(see=see, p=NS(base36id="abc", is_banned=False), v=NS(id=1), voted=0, ups=3, downs=1, score=2)


def test_a_posts_score_is_a_button_only_when_the_viewer_may_see_voters():
    for html in (LISTING, PAGE):
        span = score_span(html, "p")
        on = render(span, **post_context(True))
        assert 'class="score post-score-abc has-voters "' in on
        assert 'data-voters-url="/api/post/abc/voters"' in on and 'role="button"' in on and 'tabindex="0"' in on
        assert "click to see who upvoted" in on
        off = render(span, **post_context(False))
        for part in ("has-voters", "data-voters-url", 'role="button"', "tabindex", "click to see who upvoted", "aria-haspopup"):
            assert part not in off, part
        assert 'class="score post-score-abc "' in off and 'data-original-title="+3 | -1"' in off


def test_the_card_has_the_feed_and_profile_scores_and_both_are_covered():
    assert LISTING.count("{% set see_voters = may_see_voters(p, v) %}") == 2
    assert PAGE.count("{% set see_voters = may_see_voters(p, v) %}") == 1
    # a visitor's score (no vote arrows) is a different line and is not touched
    assert 'post-{{ p.base36id }}-score-none' in LISTING and "see_voters" not in LISTING.split("score-none")[1].split("</span>")[0]


def test_a_comments_score_is_a_button_only_for_its_author_on_desktop_and_phone():
    assert COMMENTS.count("{% set see_voters = may_see_voters(c, v) %}") == 2
    span = score_span(COMMENTS, "c")
    context = dict(c=NS(base36id="xyz"), v=NS(id=1), voted=0, ups=2, downs=0, score=2)
    on = render(span, see=True, **context)
    assert 'data-voters-url="/api/comment/xyz/voters"' in on and "has-voters" in on and on.count("click to see who upvoted") == 1
    off = render(span, see=False, **context)
    assert "has-voters" not in off and "data-voters-url" not in off and "click to see who upvoted" not in off
    assert 'class="score comment-score-xyz "' in off


def test_the_vote_script_still_finds_the_score_it_updates():
    # it looks the score up by the class `post-score-<id>` / `comment-score-<id>` and rewrites its text and colour classes
    for html in (LISTING, PAGE):
        assert "post-score-{{ p.base36id }}" in score_span(html, "p")
    assert "comment-score-{{ c.base36id }}" in score_span(COMMENTS, "c")


# --- the script and the styles -----------------------------------------------------------------------------------------

def test_the_panel_is_built_from_text_and_opened_only_from_the_authors_own_score():
    assert "innerHTML" not in SCRIPT
    assert SCRIPT.count("'.has-voters'") == 2
    assert "anchor.getAttribute('data-voters-url')" in SCRIPT
    assert "make('span', 'voters-name', '@' + person.username)" in SCRIPT
    assert "/^\\//.test(person.permalink)" in SCRIPT and "/^(https?:\\/\\/|\\/)/.test(person.avatar)" in SCRIPT
    for key in ("event.key === 'Escape'", "event.key === 'Enter'", "aria-expanded"):
        assert key in SCRIPT, key
    assert "response.status === 403" in SCRIPT                          # said plainly, not as a broken list
    assert "jQuery(target).closest('[data-toggle=\"tooltip\"]').tooltip('hide')" in SCRIPT      # the score's tooltip must not cover the panel
    assert '<script src="/assets/js/voters.js?v=1"></script>' in read("ruqqus", "templates", "default.html")


def test_both_stylesheets_style_the_score_button_and_the_panel():
    for sheet in ("main.scss", "main_dark.scss"):
        css = read("ruqqus", "assets", "style", sheet)
        for selector in (".score.has-voters {", "#voters-panel {", "#voters-panel[hidden] {", ".voters-row {", ".voters-avatar {",
                         ".voters-note {", ".voters-empty {"):
            assert selector in css, (sheet, selector)
        assert re.search(r"@media \(max-width: 575\.98px\) \{\s*#voters-panel \{\s*position: fixed;", css), sheet
