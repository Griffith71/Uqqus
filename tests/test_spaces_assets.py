"""Spaces across the post route, the routes, the page and its script, the card in feeds, the composers, the live
discussion and the build. The promises: a Space is checked in submit_post before the post exists and made right after
it (never a copy of submit_post's logic); every Space route reads the Space through its post's own visibility; every
change is a POST with the form key; a listener has no microphone; nothing is played from anyone the server does not
list on stage; the discussion is the post's comments drawn for each reader; the replay goes to the host's own storage."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


ROUTES = read("ruqqus", "routes", "spaces.py")
SPACE_JS = read("ruqqus", "assets", "chat_src", "src", "space.js")
PAGE = read("ruqqus", "templates", "space.html")
CARD = read("ruqqus", "templates", "partials", "space_card.html")
POSTS = read("ruqqus", "routes", "posts.py")
LIVE = read("ruqqus", "routes", "live_comments.py")


def test_a_space_is_checked_before_the_post_and_made_after_it():
    submit = POSTS.split("def submit_post(", 1)[1]
    check = submit.index("space_rules.make_refusal(")
    made = submit.index("new_post = Submission(")
    create = submit.index("space_store.create(g.db, new_post.id")
    assert check < made < create
    assert 'flag(request.form, "space")' in submit and 'space_rules.parse_start(request.form.get("space_start")' in submit
    assert 'anonymous=flag(request.form, "anonymous")' in submit and "circle_guild=circle_board is not None" in submit
    assert "from ruqqus.helpers import spaces as space_rules, space_store" in POSTS


def test_every_route_reads_the_space_through_its_post():
    routes = re.findall(r'@app\.route\("([^"]+)", methods=\["(\w+)"\]\)\n((?:@[^\n]+\n)+)def (\w+)', ROUTES)
    assert len(routes) >= 18
    for path, method, decorators, name in routes:
        assert "@auth_required" in decorators or "@is_not_banned" in decorators or "@admin_level_required(3)" in decorators, name
        if method == "POST":
            assert "@validate_formkey" in decorators, name
        if path.startswith(("/space/", "/api/spaces/")):
            body = ROUTES.split(f"def {name}(", 1)[1].split("\n@app.route", 1)[0]
            assert "load_space(sid, v)" in body, name
    post_for = ROUTES.split("def _post_for(", 1)[1].split("\ndef ", 1)[0]
    for check in ("get_post(", "graceful=True", "post.in_private_guild_for(v)", "post_hidden(post, v)", "post.deleted_utc", "post.is_banned"):
        assert check in post_for, check
    join = ROUTES.split("def space_join(", 1)[1].split("\n@app.route", 1)[0]
    assert "_blocked_with_hosts(space, v, roles)" in join and "space_store.removed(g.db, space.id, v.id)" in join
    assert "turn_usage.refusal(relay, spent, allowance, now)" in join
    beat = ROUTES.split("def space_heartbeat(", 1)[1].split("\n@app.route", 1)[0]
    assert "_blocked_with_hosts(" in beat and "space_store.removed(" in beat
    assert 'live_bus.register_topic("space", _guard_space_topic)' in ROUTES


def test_the_host_and_co_hosts_run_it_and_admins_can_end_it():
    end = ROUTES.split("def space_end(", 1)[1].split("\n@app.route", 1)[0]
    assert "v.admin_level >= 3" in end and "v.id != space.host_id and not admin" in end
    for name, rule in (("space_invite", "invite_refusal"), ("space_cohost", "cohost_refusal"), ("space_remove", "remove_refusal")):
        body = ROUTES.split(f"def {name}(", 1)[1].split("\n@app.route", 1)[0]
        assert f"space_rules.{rule}(" in body, name
    start = ROUTES.split("def space_start(", 1)[1].split("\n@app.route", 1)[0]
    assert "v.id != space.host_id" in start and "matrix.create_space_room()" in start
    # the reminder notice names neither the Space nor its host
    assert 'send_notification(user, f"A Space you asked to be reminded of is live: [join it]({space.post.permalink})")' in start


def test_the_page_and_its_script():
    assert PAGE.startswith("<!DOCTYPE html>") and "window.UQQUS_SPACE_BOOT = {{ boot | tojson }};" in PAGE
    assert "Everyone in this Space can see who is here, listeners too." in PAGE
    assert "This Space is recorded" in PAGE and "[hidden] { display: none !important; }" in PAGE
    for script in ("js/live_events.js", "js/media_upload.js", "js/space_bundle.js"):
        assert f"asset_url('{script}')" in PAGE, script
    for element_id in re.findall(r'byId\("([\w-]+)"\)', SPACE_JS):
        assert f'id="{element_id}"' in PAGE, element_id
    from ruqqus.helpers import device_access
    assert device_access.is_call_page("/space/12")
    # a listener has no microphone; nothing is played from anyone off stage
    assert "media: null, encrypted: true," in SPACE_JS
    assert "sends: () => onStage()," in SPACE_JS and "hears: (p) => STAGE.has(p.role)," in SPACE_JS
    assert "audio.muted = !(p && STAGE.has(p.role));" in SPACE_JS
    assert 'navigator.locks.request("uqqus-call", { ifAvailable: true }' in SPACE_JS
    assert "if (state.data.room_id) await inRoom(state.data.room_id);" in SPACE_JS
    # the only HTML the page draws is a comment's stored, sanitized HTML
    assert SPACE_JS.count("innerHTML") == 1 and "body.innerHTML = c.body_html;" in SPACE_JS


def test_the_replay_goes_to_the_hosts_own_storage():
    assert 'window.MediaUpload.upload(file, "audio"' in SPACE_JS and 'await api("replay", { media: String(asset.id) });' in SPACE_JS
    replay = ROUTES.split("def space_replay(", 1)[1].split("\n@app.route", 1)[0]
    assert "v.id != space.host_id or not space.record" in replay
    assert 'media_attach.own_asset(g.db, v.id, request.values.get("media"), kinds=("audio",))' in replay
    assert "media_attach.sync(" in replay and "media_safety.scan_later(attached)" in replay
    assert '"webm": (AUDIO, "audio/webm"),' in read("ruqqus", "helpers", "media", "rules.py")


def test_the_card_in_feeds_and_on_the_post_page():
    for name in ("submission_listing.html", "submission.html"):
        page = read("ruqqus", "templates", name)
        assert '{% from "partials/space_card.html" import space_block %}' in page and "{{ space_block(p, v) }}" in page
    assert "space_of(p, v)" in CARD and "data-space-remind" in CARD and 'target="_blank" rel="noopener"' in CARD
    assert "space_view.attach(g.db, posts, v)" in read("ruqqus", "helpers", "get.py")
    view = read("ruqqus", "helpers", "space_view.py")
    assert "p.repost_id or p.id" in view                                    # a guild copy shows its primary's Space
    assert "window.formatSpaceTimes" in read("ruqqus", "assets", "js", "all_js.js")
    for name in ("main.scss", "main_dark.scss"):
        css = read("ruqqus", "assets", "style", name)
        assert ".space-card {" in css and "z-index: 2;" in css.split(".space-card {", 1)[1].split("}", 1)[0]


def test_both_composers_offer_a_space():
    options = read("ruqqus", "templates", "partials", "post_options.html")
    assert "{% macro space_field(prefix) %}" in options
    for name in ('name="space"', 'name="space_start"', 'name="space_record"', "data-space-when"):
        assert name in options, name
    assert 'post_options.space_field("post-space")' in read("ruqqus", "templates", "submit.html")
    assert 'space_field("ic-space")' in read("ruqqus", "templates", "partials", "inline_composer.html")
    assert "asset_url('js/space_composer.js')" in read("ruqqus", "templates", "default.html")


def test_the_discussion_is_the_posts_comments_drawn_for_each_reader():
    assert "announce_comment(c)" in read("ruqqus", "routes", "comments.py")
    announce = LIVE.split("def announce_comment(", 1)[1].split("\ndef ", 1)[0]
    assert '"comment.new",\n                     {"id": comment.base36id})' in announce        # only the id travels
    listing = LIVE.split("def live_comments(", 1)[1]
    for check in ("readable_post(pid, v)", "filter_comments(q, v)", "hide_blocked(q, v, Comment)", "hide_muted(q, v, Comment)",
                  "Comment.deleted_utc == 0", "Comment.is_banned == False"):
        assert check in listing, check
    assert "anonymity.author_of(c, v)" in LIVE and "anonymity.identity_hidden(c, v)" in LIVE
    assert 'live_bus.register_topic("post", _guard_post_topic)' in LIVE


def test_the_build_and_the_admin_queue():
    pkg = read("ruqqus", "assets", "chat_src", "package.json")
    assert "esbuild src/space.js --bundle --format=esm --minify --sourcemap --outfile=../js/space_bundle.js" in pkg
    assert "COPY --from=chat-bundle /opt/build/ruqqus/assets/js/space_bundle.js" in read("Dockerfile")
    assert "ruqqus/assets/js/space_bundle.js\n" in read(".gitignore")
    assert "* [Reported Spaces](/admin/space_reports)" in read("ruqqus", "templates", "admin", "admin_home.html")
    queue = read("ruqqus", "templates", "admin", "space_reports.html")
    assert "reporter" not in queue and "Nobody is shown who reported" in queue
