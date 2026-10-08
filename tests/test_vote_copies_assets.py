"""One vote per person across the copies of a post crosses the vote route, the helper, the page loaders, two
templates, the vote script, both stylesheets and a report script. These checks keep the layers agreeing and keep
the promises: taking a vote back is never refused, a refused vote writes nothing, the author keeps their
automatic upvote on every copy, and no new place can create a hand vote without going through the rule."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


VOTES = read("ruqqus", "routes", "votes.py")
HELPER = read("ruqqus", "helpers", "vote_copies.py")
GET = read("ruqqus", "helpers", "get.py")
POSTS = read("ruqqus", "routes", "posts.py")
SCRIPT = read("ruqqus", "assets", "js", "all_js.js")
REPORT = read("scripts", "report_cross_copy_votes.py")


def route():
    return VOTES.split("def api_vote_post(")[1].split("def api_vote_comment(")[0]


# --- the route --------------------------------------------------------------------------------------------

def test_the_route_asks_after_the_post_state_refusals_and_before_anything_is_written():
    body = route()
    asked = body.index("vote_copies.refusal(g.db, v, post, int(time.time()))")
    assert body.index("That post is archived and can no longer be voted on.") < asked
    assert asked < body.index("existing = g.db.query(Vote)") < body.index("g.db.add(vote)")
    assert asked < body.index("existing.change_to(x)")
    assert body.count("vote_copies.refusal(") == 1


def test_taking_a_vote_back_is_never_checked_and_a_refusal_is_a_403_json():
    body = route()
    assert "    if x != 0:\n        refused = vote_copies.refusal(" in body
    assert "            return jsonify(refused), 403" in body
    assert "from ruqqus.helpers import vote_copies" in VOTES


# --- the helper --------------------------------------------------------------------------------------------

def test_the_helper_only_reads_apart_from_the_lock():
    for word in ("INSERT", "UPDATE", "DELETE", "commit(", "flush(", ".add("):
        assert word not in HELPER, word


def test_the_lock_is_per_person_and_family_and_comes_before_the_look():
    assert 'SELECT pg_advisory_xact_lock(:user, :family)' in HELPER
    assert 'dialect.name == "postgresql"' in HELPER
    refusal = HELPER.split("def refusal(")[1].split("def attach(")[0]
    assert refusal.index("exempt(viewer.id, post.author_id)") < refusal.index("lock(db, viewer.id, primary_post_id(post))") < refusal.index("other_vote(db, viewer.id, post)")


def test_a_member_is_live_when_it_is_not_removed_deleted_or_in_a_banned_guild():
    assert "COALESCE(s.is_banned, :no) = :no AND COALESCE(s.deleted_utc, 0) = 0" in HELPER
    assert "COALESCE(b.is_banned, :no) = :no" in HELPER
    assert HELPER.count("{_LIVE}") == 2                       # the refusal's query and the page's
    assert "v.vote_type <> 0" in HELPER and HELPER.count("v.vote_type <> 0") == 2
    assert "COALESCE(NULLIF(s.repost_id, 0), s.id)" in HELPER


def test_a_post_the_viewer_voted_on_is_never_locked():
    attach = HELPER.split("def attach(")[1]
    assert 'not getattr(post, "_voted", 0)' in attach
    assert "row.id != post.id" in attach
    assert "viewer is None or not posts" in attach


# --- the page loaders ---------------------------------------------------------------------------------------

def test_both_post_loaders_attach_where_the_viewer_voted_after_setting_their_own_vote():
    assert GET.count("vote_copies.attach(") == 2
    single = GET.split("def get_post(")[1].split("def get_posts(")[0]
    assert single.index("x._voted = items[1] or 0") < single.index("vote_copies.attach(nSession, [x], v, int(time.time()))")
    page = GET.split("def get_posts(")[1].split("def get_post_with_comments(")[0]
    assert page.index("output[i]._voted = posts[i][1] or 0") < page.index("vote_copies.attach(g.db, output, v, int(time.time()))")
    assert "def voted_elsewhere(self):" in read("ruqqus", "classes", "submission.py")


# --- the page ---------------------------------------------------------------------------------------------------

def test_a_locked_arrow_is_not_bound_to_the_vote_handlers_and_says_why_when_clicked():
    for name in ("submission_listing.html", "submission.html"):
        html = read("ruqqus", "templates", name)
        assert html.count("{% set locked = p.voted_elsewhere %}") == 1, name
        assert html.count('onclick="voteLocked(this)"') == 2 and html.count('data-locked-message="{{ locked.message }}"') == 2, name
        assert html.count('aria-disabled="true" title="{{ locked.message }}"') == 2, name
        # the vote classes and data attributes are only there when the arrow is live
        assert "{{ 'vote-locked' if locked else 'upvote-button' }}" in html and "{{ 'vote-locked' if locked else 'downvote-button' }}" in html, name
        assert html.count('{% else %}data-id-up="{{ p.base36id }}" data-content-type="post"{% endif %}') == 1, name
        assert html.count('{% else %}data-id-down="{{ p.base36id }}" data-content-type="post"{% endif %}') == 1, name


def test_a_refused_vote_puts_the_arrow_and_the_score_back():
    assert "function post_toast(url, callback, onError) {" in SCRIPT
    assert "if (onError) onError(data)" in SCRIPT
    assert "function snapshotVote(type, id) {" in SCRIPT and "function voteLocked(el) {" in SCRIPT
    assert SCRIPT.count("var restore = snapshotVote(type, id);") == 2
    assert SCRIPT.count('post_toast("/api/vote/" + type + "/" + id + "/" + voteDirection, undefined, restore);') == 2
    restore = SCRIPT.split("return function restore() {")[1].split("\n  };")[0]
    for put_back in ("s.up.classList.toggle('active', s.upActive)", "s.down.classList.toggle('active', s.downActive)",
                     "s.score.textContent = s.text", "s.score.className = s.cls"):
        assert put_back in restore, put_back


def test_the_script_is_fetched_afresh():
    for name in ("default.html", "submit.html", "sign_up.html"):
        assert "all_js.js?v=2.39.1" in read("ruqqus", "templates", name), name


def test_both_stylesheets_dim_the_locked_arrow_even_on_hover():
    for sheet in ("main.scss", "main_dark.scss"):
        css = read("ruqqus", "assets", "style", sheet)
        rule = css.split(".arrow-up.vote-locked::before,")[1].split("}")[0]
        for selector in (".arrow-down.vote-locked::before", ".arrow-up.vote-locked:hover::before", ".arrow-down.vote-locked:hover::before"):
            assert selector in rule, (sheet, selector)
        assert "cursor: not-allowed" in rule and "opacity: 0.35" in rule, sheet


# --- who can still vote across copies, and what no new code may skip -------------------------------------------------

def test_the_author_keeps_the_automatic_upvote_on_every_forward_copy():
    assert "auto_upvote=True, repost_id=0" in POSTS                               # the default stays on
    forward = POSTS.split("def create_forward_post(")[1].split("def create_forward_post_from_comment(")[0]
    assert "auto_upvote" not in forward
    assert "def exempt(viewer_id, author_id):" in HELPER and "return viewer_id == author_id" in HELPER


def test_no_new_place_creates_a_hand_vote_without_going_through_the_rule():
    # a tripwire: these are all the places that create a post vote. Two are the author's automatic upvote (a new
    # post, and each forward copy); the third is the vote route, which asks first. A new one needs a decision.
    found = {}
    for path in sorted((ROOT / "ruqqus").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        count = len(re.findall(r"(?<![A-Za-z])Vote\(user_id", source))
        if count:
            found[path.relative_to(ROOT).as_posix()] = count
    assert found == {"ruqqus/routes/posts.py": 2, "ruqqus/routes/votes.py": 1}, found
    assert "Vote(user_id=author_id, vote_type=1, submission_id=new_post.id)" in POSTS      # automatic, by the author
    assert "vote = Vote(user_id=v.id," in POSTS                                          # the author's upvote on a new post


# --- the report ------------------------------------------------------------------------------------------------------------

def test_the_report_only_reads_and_counts_people_with_more_than_one_active_vote_in_a_family():
    for word in ("INSERT", "UPDATE", "DELETE", ".commit(", ".add("):
        assert word not in REPORT, word
    assert "HAVING COUNT(*) > 1" in REPORT and "v.vote_type <> 0" in REPORT
    assert "COALESCE(NULLIF(s.repost_id, 0), s.id)" in REPORT
    assert "BOOL_OR(v.user_id = s.author_id)" in REPORT
    assert "db.close()" in REPORT
