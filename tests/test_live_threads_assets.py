"""Live threads and livestreams across the routes, the panel, its script, the cards and the stylesheets. The promises:
updates are read through the post's own visibility and written only by its author and co-authors; only an update's
id travels on the live channel; an anonymous post's updates never name who wrote them; the stream card links out and
never adds a player frame; status look-ups never make a page wait."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


ROUTES = read("ruqqus", "routes", "live_threads.py")
PANEL = read("ruqqus", "templates", "partials", "live_panel.html")
SCRIPT = read("ruqqus", "assets", "js", "live_thread.js")
STATUS = read("ruqqus", "helpers", "livestream_status.py")


def test_routes():
    routes = re.findall(r'@app\.route\("([^"]+)", methods=\["(\w+)"\]\)\n((?:@[^\n]+\n)+)def (\w+)', ROUTES)
    assert {name for *_, name in routes} == {"live_toggle", "live_updates", "live_update_one", "live_update_add",
                                             "live_update_strike", "live_update_delete", "live_summary", "live_watch"}
    for path, method, decorators, name in routes:
        assert any(d in decorators for d in ("@auth_required", "@is_not_banned", "@auth_desired")), name
        # every change takes the form key; the watch heartbeat changes nothing anyone can read about anyone
        if method == "POST" and name != "live_watch":
            assert "@validate_formkey" in decorators, name
    for name in ("live_toggle", "live_updates", "live_update_add", "live_summary", "live_watch"):
        body = ROUTES.split(f"def {name}(", 1)[1].split("\n@app.route", 1)[0]
        assert "_readable(pid, v)" in body, name
    for name in ("live_update_one", "_changeable"):
        body = ROUTES.split(f"def {name}(", 1)[1].split("\n@app.route", 1)[0]
        assert "_readable(base36encode(row[1]), v)" in body, name
    assert "readable_post(pid, v)" in ROUTES.split("def _readable(", 1)[1].split("\ndef ", 1)[0]
    add = ROUTES.split("def live_update_add(", 1)[1].split("\n@app.route", 1)[0]
    assert "live_rules.update_refusal(live_rules.may_run(v.id, primary.author_id, _runners(primary))" in add
    assert "_render(body, primary), _severity(body)" in add
    render = ROUTES.split("def _render(", 1)[1].split("\n\n\n", 1)[0]
    assert "sanitize(body_md, linkgen=True)" in render
    assert "get_filter(g.db).severity(body)" in ROUTES.split("def _severity(", 1)[1].split("\n\n\n", 1)[0]
    assert '"live.update", {"id": uid})' in add                                   # only the id travels
    # crossing out and deleting: the update's writer or the post's author, and only the id travels
    changeable = ROUTES.split("def _changeable(", 1)[1].split("\n@app.route", 1)[0]
    assert "live_rules.may_change_update(v.id, row[2], primary.author_id)" in changeable
    for name in ("live_update_strike", "live_update_delete"):
        body = ROUTES.split(f"def {name}(", 1)[1].split("\n@app.route", 1)[0]
        assert "_changeable(uid, v)" in body and '"live.changed", {"id": uid})' in body, name
    summary = ROUTES.split("def live_summary(", 1)[1].split("\n@app.route", 1)[0]
    assert "live_rules.may_run(v.id, primary.author_id, _runners(primary))" in summary and "_render(body, primary)" in summary
    assert "from .live_threads import *" in read("ruqqus", "routes", "__init__.py")


def test_who_is_watching_is_a_count_kept_in_redis_only():
    watch = ROUTES.split("def live_watch(", 1)[1].split("\n@app.route", 1)[0]
    assert 'key = f"live:watch:{primary.id}"' in watch and "r.zcard(key)" in watch
    assert "r.expire(key, 2 * live_rules.WATCH_SECONDS)" in watch
    assert "g.db" not in watch.split("thread = thread_json(")[0]                 # nothing written to the database
    assert '"watching": watching' in watch and "username" not in watch            # a count, never who
    assert 'hashlib.sha256(f"{salt}:{address}".encode())' in watch               # a visitor's address is never kept as is
    assert "tab" not in watch.split('"""', 2)[2]                                # once per address, not per tab (L-06)


def test_a_live_thread_starts_from_both_composers_for_a_chosen_length():
    posts = read("ruqqus", "routes", "posts.py")
    submit = posts.split("def submit_post(", 1)[1]
    assert 'live_rules.clean_hours(request.form.get("live_hours"))' in submit
    assert submit.index('flag(request.form, "live")') < submit.index("new_post = Submission(")
    assert submit.index("new_post = Submission(") < submit.index("start_thread(new_post, v, int(time.time()), live_hours)")
    assert posts.index("from ruqqus.classes import *") < posts.index("from ruqqus.helpers import live_threads as live_rules")
    assert 'live_field("post-live"' in read("ruqqus", "templates", "submit.html")
    assert 'live_field("ic-live")' in read("ruqqus", "templates", "partials", "inline_composer.html")
    drafts = read("ruqqus", "assets", "js", "post_drafts.js")
    assert "data.append('live', 'true');" in drafts and "data.append('live_hours', liveHours.value);" in drafts


def test_go_live_is_in_every_post_menu():
    items = read("ruqqus", "templates", "partials", "post_menu_items.html")
    assert "{% macro live_item(p, v) %}" in items and "{% macro live_button(p, v) %}" in items
    assert items.count("may_run_live(p, v)") == 2 and items.count('href="{{ p.permalink }}?live=start#live-panel"') == 2
    for name in ("submission_listing.html", "submission.html"):
        page = read("ruqqus", "templates", name)
        assert "{{ post_menu.live_item(p, v) }}" in page and "{{ post_menu.live_button(p, v) }}" in page, name


def test_updates_never_name_the_author_of_an_anonymous_post():
    as_json = ROUTES.split("def _update_json(", 1)[1].split("\n\n\n", 1)[0]
    assert "hidden_name = post.is_anonymous or author is None" in as_json
    assert "text_hidden(row[5], v, owner_id=row[2])" in as_json


def test_followers_are_told_only_if_they_may_still_read_it():
    toggle = ROUTES.split("def live_toggle(", 1)[1].split("\n@app.route", 1)[0]
    assert "post_follows.followers(g.db, primary.id, v.id, anonymous=bool(primary.is_anonymous))" in toggle
    assert "_followers_who_may_read(primary, ids)" in toggle


def test_the_panel_the_cards_and_the_script():
    page = read("ruqqus", "templates", "submission.html")
    assert "{{ live_panel(p, v) }}" in page and page.count("{{ live_badge(p) }}") == 2 and "{{ stream_block(p) }}" in page
    listing = read("ruqqus", "templates", "submission_listing.html")
    assert "{{ live_badge(p) }}" in listing and "{{ stream_block(p) }}" in listing
    assert "live_view.attach(g.db, posts)" in read("ruqqus", "helpers", "get.py")
    assert 'target="_blank" rel="nofollow noopener"' in PANEL and "<iframe" not in PANEL   # a link, never a frame
    # the only stored HTML drawn: an update and the summary, both sanitized when they were written
    assert "{{ u.body_html | safe }}" in PANEL and "{{ t.summary_html | safe }}" in PANEL and PANEL.count("| safe") == 2
    assert SCRIPT.count("innerHTML") == 2 and "body.innerHTML = u.body_html || '';" in SCRIPT
    assert "summaryBody.innerHTML = thread.summary_html;" in SCRIPT
    for text in ("This live thread has ended", "{{ t.runners }}", "data-live-until", 'id="live-new-updates"', "Corrected",
                 'id="live-extend"', 'id="live-end"', 'id="live-summary-edit"'):
        assert text in PANEL, text
    for kind in ("live.update", "live.ended", "live.changed", "live.summary", "comment.new"):
        assert f"window.UqqusLive.on('{kind}'" in SCRIPT
    default = read("ruqqus", "templates", "default.html")
    assert default.index("js/live_events.js") < default.index("js/live_thread.js")    # it listens on the live client


def test_status_lookups_never_make_a_page_wait():
    cached = STATUS.split("def cached(", 1)[1].split("\ndef ", 1)[0]
    assert "gevent.spawn(_look_up, ref)" in cached and 'nx=True' in cached
    assert "https://www.googleapis.com/youtube/v3/videos" in STATUS and "https://api.twitch.tv/helix/streams" in STATUS
    assert "TIMEOUT = 4" in STATUS


def test_stylesheets():
    for name in ("main.scss", "main_dark.scss"):
        css = read("ruqqus", "assets", "style", name)
        for selector in (".live-badge {", ".live-panel {", ".live-update {", ".stream-card {", "@keyframes live-update-in",
                         ".live-summary {", ".live-update.is-struck .live-update-body {", ".live-new-pill {", ".live-controls {"):
            assert selector in css, (name, selector)


def test_the_schema():
    migration = read("scripts", "migrations", "2026-10-26_live_threads.sql")
    schema = read("schema.sql")
    for needle in ("CREATE TABLE IF NOT EXISTS public.live_threads", "REFERENCES public.live_threads(post_id) ON DELETE CASCADE"):
        assert needle in migration, needle
    assert "CREATE TABLE public.live_updates (" in schema and "CREATE INDEX live_updates_post_idx" in schema
    reddit = read("scripts", "migrations", "2026-10-28_live_threads_reddit.sql")
    for column in ("ends_utc", "summary", "summary_html", "summary_severity", "summary_edited_utc", "struck_utc"):
        assert f"ADD COLUMN IF NOT EXISTS {column} " in reddit and f"    {column} " in schema, column


def test_the_go_live_items_render():
    """Rendered for real: "Go live" for who may run it, "Manage live thread" while live, nothing for anyone else."""
    from types import SimpleNamespace as NS
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(str(ROOT / "ruqqus" / "templates")))
    state = {"may": True, "live": None}
    env.globals.update(may_run_live=lambda p, v: state["may"], live_of=lambda p: state["live"],
                       author_of=lambda item, viewer: item.author)
    module = env.get_template("partials/post_menu_items.html").make_module()
    post = NS(base36id="7k", permalink="/post/7k/x", is_banned=False, deleted_utc=0)
    viewer = NS(id=2)
    assert "Go live" in str(module.live_item(post, viewer)) and "/post/7k/x?live=start#live-panel" in str(module.live_button(post, viewer))
    state["live"] = {"live": True}
    assert "Manage live thread" in str(module.live_item(post, viewer))
    state["may"] = False
    assert "live" not in str(module.live_item(post, viewer)).lower()
