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
    assert {name for *_, name in routes} == {"live_toggle", "live_updates", "live_update_add", "live_update_delete"}
    for path, method, decorators, name in routes:
        assert "@auth_required" in decorators or "@is_not_banned" in decorators, name
        if method == "POST":
            assert "@validate_formkey" in decorators, name
    for name in ("live_toggle", "live_updates", "live_update_add"):
        body = ROUTES.split(f"def {name}(", 1)[1].split("\n@app.route", 1)[0]
        assert "_readable(pid, v)" in body, name
    assert "readable_post(pid, v)" in ROUTES.split("def _readable(", 1)[1].split("\ndef ", 1)[0]
    add = ROUTES.split("def live_update_add(", 1)[1].split("\n@app.route", 1)[0]
    assert "live_rules.update_refusal(live_rules.may_run(v.id, primary.author_id, _runners(primary))" in add
    assert "sanitize(body_md, linkgen=True)" in add and "get_filter(g.db).severity(body)" in add
    assert '"live.update", {"id": uid})' in add                                   # only the id travels
    delete = ROUTES.split("def live_update_delete(", 1)[1]
    assert "row[2] != v.id" in delete
    assert "from .live_threads import *" in read("ruqqus", "routes", "__init__.py")


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
    assert "{{ u.body_html | safe }}" in PANEL and PANEL.count("| safe") == 1
    assert SCRIPT.count("innerHTML") == 1 and "body.innerHTML = u.body_html || '';" in SCRIPT
    for kind in ("live.update", "live.ended", "comment.new"):
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
        for selector in (".live-badge {", ".live-panel {", ".live-update {", ".stream-card {", "@keyframes live-update-in"):
            assert selector in css, (name, selector)


def test_the_schema():
    migration = read("scripts", "migrations", "2026-10-26_live_threads.sql")
    schema = read("schema.sql")
    for needle in ("CREATE TABLE IF NOT EXISTS public.live_threads", "REFERENCES public.live_threads(post_id) ON DELETE CASCADE"):
        assert needle in migration, needle
    assert "CREATE TABLE public.live_updates (" in schema and "CREATE INDEX live_updates_post_idx" in schema
