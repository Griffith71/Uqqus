"""A curation's algorithm crosses the rules (helpers/feed_algorithm.py), the query
(helpers/curation_feed.py), the routes, the schema, the form, its script and the
stylesheets. These checks keep the layers agreeing, and keep the two promises the
feature makes: only the owner changes a curation, and nothing a member types is run."""
import re
from pathlib import Path

from ruqqus.helpers import feed_algorithm as fa

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


ROUTES = read("ruqqus", "routes", "curations.py")
FEED = read("ruqqus", "helpers", "curation_feed.py")
FORM = read("ruqqus", "templates", "curations", "algorithm_form.html")
PAGE = read("ruqqus", "templates", "curations", "detail.html")
SCRIPT = read("ruqqus", "assets", "js", "curation_algorithm.js")


def route(name):
    """The decorators and body of one route function."""
    body = ROUTES.split(f"def {name}(")[0].rsplit("@app.route(", 1)[1]
    return "@app.route(" + body + f"def {name}(" + ROUTES.split(f"def {name}(")[1].split("\n@app.route(")[0]


# --- storage ---------------------------------------------------------------------------

def test_the_model_the_schema_and_the_migration_have_the_column():
    assert 'algorithm = Column(Text, nullable=False, default="{}")' in read("ruqqus", "classes", "curations.py")
    table = re.search(r"CREATE TABLE public\.curations \((.*?)\n\);", read("schema.sql"), re.S).group(1)
    assert "algorithm text DEFAULT '{}' NOT NULL" in table
    assert "ALTER TABLE curations ADD COLUMN IF NOT EXISTS algorithm text DEFAULT '{}' NOT NULL" in read(
        "scripts", "migrations", "2026-10-07_curation_algorithm.sql")


def test_an_algorithm_is_only_ever_stored_through_the_cleaner():
    assert "def algorithm_spec(self):\n        return feed_algorithm.load(self.algorithm)" in read("ruqqus", "classes", "curations.py")
    # every place a curation's algorithm is set: create, save, an admin taking a server off (two ways),
    # fork (not the template's module argument)
    writes = [w for w in re.findall(r"\balgorithm\s*=\s*([^\n]+)", ROUTES) if not w.startswith("feed_algorithm,")]
    assert len(writes) == 5, writes
    assert all(w.startswith("feed_algorithm.dump(") for w in writes), writes
    assert "request.form" not in route("curation_set_algorithm").split("def curation_set_algorithm(")[1]


# --- who may change a curation ---------------------------------------------------------

OWNER_ONLY = ("curation_edit_get", "curation_edit_post", "curation_delete", "curation_add_guild", "curation_remove_guild",
              "curation_add_user", "curation_remove_user", "curation_set_region_filter", "curation_set_language_filter",
              "curation_set_category_filter", "curation_set_algorithm", "curation_preview_algorithm")


def test_only_the_owner_reaches_anything_that_edits_a_curation():
    for name in OWNER_ONLY:
        body = route(name)
        assert "@auth_required" in body, name
        assert "curation = _owned_curation_or_404(slug, v)" in body, name
    owned = ROUTES.split("def _owned_curation_or_404(")[1].split("\n\n\n")[0]
    assert "if curation.owner_id != v.id:\n        abort(403)" in owned


def test_every_change_needs_the_form_key():
    for name in OWNER_ONLY + ("curations_create_post", "curation_follow", "curation_unfollow", "curation_fork"):
        if name == "curation_edit_get":
            continue
        body = route(name)
        assert 'methods=["POST"]' in body and "@validate_formkey" in body, name


def test_a_private_curation_is_closed_to_everyone_but_its_owner():
    page = route("curation_detail")
    assert "if curation.is_private and not is_owner:\n        abort(403)" in page
    for name in ("curation_follow", "curation_fork"):
        assert "if not original or original.is_private:" in route(name) or "if not curation or curation.is_private:" in route(name), name


def test_a_fork_is_a_full_copy():
    fork = route("curation_fork")
    for part in ("region_filter=original.region_filter", "language_filter=original.language_filter",
                 "category_filter=original.category_filter", "algorithm=feed_algorithm.dump(original.algorithm_spec)",
                 "is_private=True", "owner_id=v.id"):
        assert part in fork, part


def test_a_forked_curation_can_still_be_deleted():
    # curations.forked_from_id is a foreign key: deleting the original used to fail while a fork pointed at it
    assert "FOREIGN KEY (forked_from_id) REFERENCES public.curations(id)" in read("schema.sql")
    delete = route("curation_delete")
    forget = 'g.db.query(Curation).filter_by(forked_from_id=curation.id).update({"forked_from_id": None})'
    assert forget in delete and delete.index(forget) < delete.index("g.db.delete(curation)")


# --- the feed --------------------------------------------------------------------------

def test_who_may_see_what_stays_in_the_route_for_every_algorithm():
    viewable = ROUTES.split("def _viewable(")[1].split("\ndef ")[0]
    for rule in ("Submission.is_banned == False", "Submission.deleted_utc == 0", "posts = filter_posts(posts, v)",
                 "Submission.post_public == True", "UserBlock.target_id", "BoardBlock.board_id"):
        assert rule in viewable, rule
    # the curation's wishes come after, and cannot widen any of that
    ids = ROUTES.split("def _curation_ids(")[1].split("\n@cache.memoize")[0]
    assert ids.index("posts = _viewable(posts, v)") < ids.index("posts = curation_feed.apply(posts, spec, now)")
    assert "filter_level=viewer_level(v)" in route("curation_detail")          # the cache key


# --- an outside feed server ------------------------------------------------------------

def test_a_server_only_orders_what_the_viewer_may_see():
    seen = ROUTES.split("def _seen_among(")[1].split("\ndef ")[0]
    assert "_own_filters(_viewable(posts, v), curation)" in seen
    server = ROUTES.split("def _server_ids(")[1].split("\ndef ")[0]
    assert "feed_server.keep_order(ids, _seen_among(curation, ids, v))" in server
    # the ids go nowhere else: the page is drawn from what survived
    assert server.count("return ") == 2 and "return ordered[" in server and "return [], feed_server.OFF" in server
    preview = route("curation_preview_algorithm")
    assert "feed_server.keep_order(named, _seen_among(curation, named, v))" in preview


def test_nothing_about_the_viewer_reaches_the_server():
    server = ROUTES.split("def _server_ids(")[1].split("\ndef ")[0]
    call = re.search(r"feed_server\.ids_for\(([^\n]+)\)", server).group(1)
    assert call == 'curation.id, spec["server"], 25 * page + SERVER_SPARE'       # no viewer, no request
    assert 'feed_server.try_server(spec["server"])' in route("curation_preview_algorithm")
    fetch = read("ruqqus", "helpers", "safe_fetch.py").split("def get_json(")[1]
    assert 'headers={"Accept": "application/json", "User-Agent": USER_AGENT, "Connection": "close"}' in fetch
    assert "flask" not in read("ruqqus", "helpers", "safe_fetch.py") and "flask" not in read("ruqqus", "helpers", "feed_server.py").split('"""', 2)[2]


def test_a_server_address_is_judged_before_it_is_stored_and_fetched_only_through_the_guard():
    posted = ROUTES.split("def _posted_algorithm(")[1].split("\n@app.route(")[0]
    assert 'safe_fetch.check(spec["server"])' in posted and "if not feed_server.enabled():" in posted
    assert posted.index("feed_server.enabled()") < posted.index("safe_fetch.check(")
    for name in ("curation_set_algorithm", "curation_preview_algorithm"):
        assert "spec = _posted_algorithm()" in route(name), name
    # the app's only way out to a member's address
    server = read("ruqqus", "helpers", "feed_server.py")
    assert "fetch = safe_fetch.get_json if fetch is None else fetch" in server
    for banned in ("import requests", "urllib.request", "urlopen", "http.client"):
        assert banned not in server and banned not in ROUTES, banned
    guard = read("ruqqus", "helpers", "safe_fetch.py")
    assert "_Pinned(host, port, address, timeout, context)" in guard          # the checked address, not a second lookup
    assert "allow_redirects" not in guard and "follow" not in guard.split('"""', 2)[2].replace("not followed", "")


def test_an_outside_server_is_said_plainly_and_an_admin_can_take_it_off():
    note = PAGE[PAGE.index("{% if curation.uses_feed_server %}"):PAGE.index("{% else %}", PAGE.index("{% if curation.uses_feed_server %}"))]
    assert "curation-server-note" in note and "<details" not in note            # never collapsed
    assert "curation.algorithm_summary" in note
    assert "{% if v and v.admin_level >= 4 %}" in note and "/remove_feed_server" in note
    remove = route("curation_remove_feed_server")
    assert "@admin_level_required(4)" in remove and "@validate_formkey" in remove and 'methods=["POST"]' in remove
    assert "outside server" in read("ruqqus", "templates", "curations", "browse.html")
    assert "{{ feed_notice }}" in PAGE
    for sheet in ("main.scss", "main_dark.scss"):
        assert ".curation-server-note" in read("ruqqus", "assets", "style", sheet), sheet


def test_the_local_switch_is_only_set_for_local_docker_and_documented_as_such():
    compose, example = read("docker-compose.yml"), read(".env.example")
    assert "FEED_SERVER_ALLOW_LOCAL=${FEED_SERVER_ALLOW_LOCAL:-1}" in compose and "never on a live site" in compose
    assert "FEED_SERVER_ALLOW_LOCAL=\n" in example and "never on a live site" in example
    assert 'os.environ.get("FEED_SERVER_ALLOW_LOCAL") == "1"' in read("ruqqus", "helpers", "safe_fetch.py")


def test_the_help_page_states_the_protocol_the_code_keeps():
    from ruqqus.helpers import feed_server

    page = read("ruqqus", "templates", "help", "feed_servers.html")
    for part in ("limits.LIMIT", "limits.CURSOR_CHARS", "limits.CACHE_SECONDS", "limits.MAX_IDS", "limits.FAILS_BEFORE_PAUSE",
                 '{"posts": ["2k1", "2jz", "2jx"], "cursor": "page-2"}', "nothing about the person looking", "https://"):
        assert part in page, part
    assert feed_server.parse_answer({"posts": ["2k1", "2jz", "2jx"], "cursor": "page-2"})[1] == "page-2"
    assert 'render_template("help/feed_servers.html", v=v, limits=feed_server)' in read("ruqqus", "routes", "static.py")
    assert '<a href="/help/feed_servers">' in FORM


def test_rules_of_ones_own_run_under_a_time_limit_and_a_slow_one_is_a_notice():
    cached = ROUTES.split("def curation_idlist(")[1].split("\ndef _invalidate_curation_feed")[0]
    assert "return curation_feed.limited(g.db, run)" in cached
    assert "except curation_feed.FeedTooSlow as slow:" in route("curation_detail")
    assert "curation_feed.limited(g.db," in route("curation_preview_algorithm")
    limited = FEED.split("def limited(")[1]
    assert "db.begin_nested()" in limited and "SET LOCAL statement_timeout" in limited and "nested.rollback()" in limited
    assert "{{ feed_notice }}" in PAGE


def test_nothing_a_member_types_is_run_as_a_pattern_or_as_sql():
    # words: LIKE text with its wildcards escaped
    mentions = FEED.split("def _mentions(")[1].split("\ndef ")[0]
    assert "fa.like_pattern(word)" in mentions and mentions.count('escape="\\\\"') == 2
    # sites: a checked host name, escaped again
    assert 're.escape(host)' in FEED.split("def _links_to(")[1].split("\ndef ")[0]
    # the only text() is the time limit, and it takes a whole number
    assert FEED.count("text(") == 2 and 'text(f"SET LOCAL statement_timeout = {int(ms)}")' in FEED
    assert ".op(" in FEED and FEED.count(".op(") == 1
    # every list the query reads is one the cleaner builds
    for key in re.findall(r'spec\["(\w+)"\]', FEED):
        assert key in fa.DEFAULT, key


def test_an_anonymous_post_is_nobodys_for_the_variety_limit():
    assert "None if r.is_anonymous else r.author_id" in FEED


def test_the_sql_mix_is_the_documented_mix():
    sql = FEED.split("def mix_score(")[1].split("\ndef ")[0]
    for part in ('mix["votes"] * func.ln(1 + votes)', 'mix["comments"] * func.ln(1 + _comment_count())',
                 '0.2 * mix["members"]', '0.2 * mix["media"]', 'func.power(0.5, age_hours / float(mix["fade"]))'):
        assert part in sql, part
    assert f"limit(fa.MIX_POOL)" in FEED and fa.MIX_POOL == 1000


# --- the form --------------------------------------------------------------------------

def test_the_form_sends_exactly_the_fields_the_server_reads():
    read_by_server = set(re.findall(r'form\.get(?:list)?\("(\w+)"', read("ruqqus", "helpers", "feed_algorithm.py")))
    read_by_server |= {f"mix_{name}" for name in fa.MIX}
    assert {"mode", "server"} <= read_by_server
    in_form = set(re.findall(r'name="(\w+)"', FORM)) | {f"mix_{name}" for name in re.findall(r'name="mix_\{\{ name \}\}"', FORM) and ("votes", "comments")}
    assert in_form - {"formkey"} >= read_by_server, read_by_server - in_form
    assert in_form - {"formkey"} - read_by_server == set()


def choices(marker, var="value"):
    """The values of the `for <var>, label in [...]` list that draws the field at `marker`."""
    before = FORM[:FORM.index(marker)]
    start = before.rindex(f"for {var}, label in [")
    return re.findall(r'\(\s*"?(\w+)"?\s*,', before[start:before.index("] %}", start)])


def test_every_choice_the_form_offers_is_a_name_the_rules_know():
    assert tuple(choices('type="checkbox" name="kinds"')) == fa.KINDS
    assert tuple(choices('type="checkbox" name="hide"')) == fa.HIDE
    assert tuple(choices('{% if spec.rank == value %}')) == fa.RANKS
    assert tuple(choices('{% if spec.age == value %}')) == tuple(fa.AGES)
    assert tuple(int(x) for x in choices('<option value="{{ hours }}"', var="hours")) == fa.FADES
    assert 'value="members"' in FORM and 'value="site"' in FORM and fa.SOURCES == ("members", "site")
    assert set(re.findall(r'name="mode"[^>]*value="(\w+)"', FORM)) == set(fa.MODES)
    assert "range(1, 6)" in FORM and fa.MIX["variety"][1] == 5


def test_every_element_the_script_uses_exists_in_the_form():
    ids = set(re.findall(r"getElementById\('([\w-]+)'\)", SCRIPT))
    assert {"curation-algorithm", "alg-rank", "alg-mix", "alg-summary", "alg-preview", "alg-preview-button", "alg-presets"} <= ids
    missing = sorted(i for i in ids if f'id="{i}"' not in FORM)
    assert not missing, missing
    for name in re.findall(r"form\.elements\.(\w+)", SCRIPT):
        assert f'name="{name}"' in FORM, name
    assert "form.elements['mix_' + name]" in SCRIPT and "var MIX = ['votes', 'comments', 'fade', 'members', 'media', 'variety']" in SCRIPT
    assert set(fa.MIX) == {"votes", "comments", "fade", "members", "media", "variety"}


def test_the_form_works_without_script_and_the_script_only_adds_to_it():
    assert 'action="{{ curation.permalink }}/set_algorithm"' in FORM and '<button type="submit" class="btn btn-primary">Save algorithm</button>' in FORM
    assert 'id="alg-presets" hidden' in FORM and 'id="alg-preview-button" hidden' in FORM
    assert "presets.hidden = false" in SCRIPT and "previewButton.hidden = false" in SCRIPT
    # what the server answers is drawn as text; only the server's own post cards are markup
    assert SCRIPT.count("innerHTML") == 1 and "preview.innerHTML = data.html" in SCRIPT
    assert "item.textContent = line" in SCRIPT and "note.textContent = message" in SCRIPT
    form = read("ruqqus", "templates", "curations", "form.html")
    assert '{% include "curations/algorithm_form.html" %}' in form and "/assets/js/curation_algorithm.js" in form
    assert 'name="preset"' in form                      # creating a curation offers a starting point
    assert 'feed_algorithm.preset(request.form.get("preset", ""))' in route("curations_create_post")


def test_everyone_who_can_see_a_curation_can_read_its_rules():
    assert "How this curation picks posts" in PAGE and "curation.algorithm_summary" in PAGE
    summary = PAGE[PAGE.index("How this curation picks posts") - 200:PAGE.index("How this curation picks posts")]
    assert "is_owner" not in summary
    assert "has_own_algorithm" in read("ruqqus", "templates", "curations", "browse.html")


def test_the_curation_page_can_be_scrolled_like_any_feed():
    assert 'class="posts"' in PAGE and 'aria-label="Page navigation"' in PAGE and re.search(r">\s*Next\s*<", PAGE)


def test_both_stylesheets_style_the_section():
    for sheet in ("main.scss", "main_dark.scss"):
        css = read("ruqqus", "assets", "style", sheet)
        for cls in (".curation-algorithm", ".alg-mix", ".alg-value", ".alg-summary", ".curation-how", ".curation-own-algorithm"):
            assert cls in css, f"{sheet} has no rule for {cls}"
    for cls in ("curation-algorithm", "alg-mix", "alg-value", "alg-summary"):
        assert cls in FORM, cls
