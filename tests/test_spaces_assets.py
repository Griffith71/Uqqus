"""Spaces across the post route, the routes, the page and its script, the card in feeds, the composers, the live
discussion and the build. The promises: a Space is checked in submit_post before the post exists and made right after
it (never a copy of submit_post's logic); every Space route reads the Space through its post's own visibility; every
change is a POST with the form key; a listener has no microphone; nothing is played or shown from anyone the server
does not list on stage; video only while the server says it is open; the discussion is the Space's own board, drawn for
each reader and pushed by id only; its live updates are run by the host and co-hosts from the Space; the replay goes to
the host's own storage; a Space that was not recorded leaves no post, board or update behind."""
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
        if name == "spaces_home":                     # the Spaces area lists what a visitor may see too
            assert "@auth_desired" in decorators
            continue
        assert "@auth_required" in decorators or "@is_not_banned" in decorators or "@admin_level_required(3)" in decorators, name
        if method == "POST":
            assert "@validate_formkey" in decorators, name
        if path.startswith("/space/post/"):            # from a Space's post to the Space: the post route's checks
            body = ROUTES.split(f"def {name}(", 1)[1].split("\n@app.route", 1)[0]
            assert "get_post(pid, v=v)" in body and "_post_for(row, v) is None" in body, name
            continue
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
    # the only HTML the page draws is an update's and the summary's stored, sanitized HTML; board messages are text
    assert SPACE_JS.count("innerHTML") == 2
    assert 'body.innerHTML = u.body_html || "";' in SPACE_JS and "summary.innerHTML = thread.summary_html;" in SPACE_JS
    board = SPACE_JS.split("function drawMessage(m) {", 1)[1].split("\nfunction ", 1)[0]
    assert "innerHTML" not in board and "body.textContent = m.body;" in board


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


def test_a_space_is_set_up_in_the_spaces_area_not_in_the_composers():
    # the owner's choice: Spaces have their own area, like Chat, and never come from Create a post
    assert "space_field" not in read("ruqqus", "templates", "partials", "post_options.html")
    assert "space_field" not in read("ruqqus", "templates", "submit.html")
    assert "space_field" not in read("ruqqus", "templates", "partials", "inline_composer.html")
    page = read("ruqqus", "templates", "space_new.html")
    for name in ('name="space"', 'name="space_start"', 'name="space_record"', "data-space-when", "data-space-composer"):
        assert name in page, name
    assert "where_profile(" in page and "audience_field(" in page
    script = read("ruqqus", "assets", "js", "space_new.js")
    assert "fetch('/api/vue/submit'" in script and "'/space/post/'" in script and "innerHTML" not in script
    assert "asset_url('js/space_composer.js')" in read("ruqqus", "templates", "default.html")
    for shell in ("default.html", "header.html"):
        assert read("ruqqus", "templates", shell).count('href="/spaces"') == 3, shell     # navbar, account menu, phone menu
    assert 'href="/spaces"' in read("ruqqus", "templates", "sidebar-left.html")


def test_a_space_post_takes_no_comments_and_a_not_recorded_space_leaves_nothing():
    posts = read("ruqqus", "routes", "posts.py")
    assert "comment_permission=cperm.SPACE if space_start is not None else" in posts
    assert "if primary.comment_permission == cperm.SPACE:" in posts                      # its guild copies too
    end = ROUTES.split("def _end(", 1)[1].split("\ndef _people(", 1)[0]
    assert "if not space.record:" in end and "_take_down(space, now)" in end
    take_down = ROUTES.split("def _take_down(", 1)[1].split("\ndef ", 1)[0]
    assert "Submission.repost_id == space.post_id" in take_down and "post.deleted_utc = now" in take_down
    assert "ContentEditHistory" not in take_down                                          # never in the Deletion log
    assert "live_space_for(u, v)" in read("ruqqus", "templates", "partials", "profile_header.html")
    migration = read("scripts", "migrations", "2026-11-10_space_posts.sql")
    assert "SET comment_permission = 5" in migration and "2026-11-10_space_posts.sql" in read("schema.sql")


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


def _route(name):
    return ROUTES.split(f"def {name}(", 1)[1].split("\n@app.route", 1)[0]


def test_the_board_is_drawn_for_each_reader_and_pushed_by_id_only():
    hidden = ROUTES.split("def _hidden_authors(", 1)[1].split("\ndef ", 1)[0]
    for check in ("UserBlock.target_id).filter(UserBlock.user_id == v.id)", "UserBlock.user_id).filter(UserBlock.target_id == v.id)",
                  "UserMute.target_id).filter(UserMute.user_id == v.id)"):
        assert check in hidden, check
    drawn = ROUTES.split("def _messages_json(", 1)[1].split("\ndef ", 1)[0]
    assert "rows = [row for row in rows if row[2] not in hidden]" in drawn and "text_hidden(row[4], v, owner_id=row[2])" in drawn
    assert "q[2] in hidden" in drawn                                    # nor quoted from someone the reader can't see
    add = _route("space_message_add")
    for check in ("space_rules.clean_message(", "space_rules.message_refusal(", "bool(v.is_suspended)",
                  "space_store.quotable(g.db, space.id,", "r.set(key, 1, nx=True, ex=slow)",
                  "space_rules.may_moderate(role, v.admin_level >= 3)", "get_filter(g.db).severity(body)",
                  'live_bus.publish(r, space.topic, "space.message", {"id": mid})'):
        assert check in add, check
    assert "load_space(row[1], v)" in ROUTES.split("def _message_or_404(", 1)[1].split("\ndef ", 1)[0]
    for name in ("space_message_delete", "space_message_remove", "space_message_report"):
        assert "_message_or_404(mid, v)" in _route(name), name
    assert "space_rules.may_delete_message(v.id, row[2])" in _route("space_message_delete")
    assert "space_rules.may_moderate(" in _route("space_message_remove") and "space_rules.may_moderate(" in _route("space_slow")
    assert "row[2] == v.id" in _route("space_message_report")
    for event in ('"space.message_gone", {"id": mid}',):
        assert event in _route("space_message_delete") and event in _route("space_message_remove")


def test_the_live_updates_are_run_from_the_space():
    add = _route("space_update_add")
    assert "_runs_updates(space, v)" in add and "space.status != space_rules.LIVE" in add
    assert '"live.update", {"id": uid})' in add and "live_routes._render(body, space.post)" in add
    assert "_runs_updates(space, v)" in _route("space_summary") and "_runs_updates(space, v)" in _route("space_summary_text")
    start = _route("space_start")
    assert "live_store.start(g.db, space.post_id, v.id, now, now + space_rules.MAX_SECONDS)" in start
    assert "_followers_who_may_read(space.post, ids)" in start              # its followers are told once
    assert "live_store.end(g.db, space.post_id, now)" in ROUTES.split("def _end(", 1)[1].split("\ndef ", 1)[0]
    threads = read("ruqqus", "routes", "live_threads.py")
    for name in ("live_toggle", "live_update_add", "live_summary"):
        body = threads.split(f"def {name}(", 1)[1].split("\n@app.route", 1)[0]
        assert "_space_refusal(primary)" in body, name
    assert "{% if not space_post %}{{ live_panel(p, v) }}{% endif %}" in read("ruqqus", "templates", "submission.html")


def test_video_only_on_stage_and_only_while_open():
    video = _route("space_video")
    assert "space_rules.camera_refusal(" in video and "_relay_video_ok(now)" in video and 'request.values.get("on") == "1"' in video
    assert "call_store.set_video(r, space.room, v.id, device_id, on)" in video
    people = ROUTES.split("def _people(", 1)[1].split("\ndef ", 1)[0]
    assert 'on_video = {e["user_id"] for e in roster if e["video"]} if video_open else set()' in people
    assert '"video": video_open and e["video"] and space_rules.on_stage(role)' in people
    # the browser shows video only from people the roster puts on stage with it on, and turns its camera off when told
    assert 'if (p.video && STAGE.has(p.role) && liveVideo(stream))' in SPACE_JS
    assert "if (state.media && state.media.videoTrack && data.video && !data.video.open) await cameraOff(data.video.note);" in SPACE_JS
    assert 'state.data = await api("video", { on: "1" });' in SPACE_JS.split("async function cameraOn(", 1)[1].split("\n}\n", 1)[0]
    mesh = read("ruqqus", "assets", "chat_src", "src", "rtc", "mesh.js")
    assert "syncTracks() {" in mesh and 'iceTransportPolicy: "relay"' in mesh
    assert 'name="space_video"' in read("ruqqus", "templates", "space_new.html")
    assert 'space_video = flag(request.form, "space_video")' in POSTS and "video=space_video)" in POSTS


def test_a_not_recorded_space_takes_its_board_and_updates_with_it():
    take_down = ROUTES.split("def _take_down(", 1)[1].split("\ndef ", 1)[0]
    assert "space_store.clear_board(g.db, space.id)" in take_down and "live_store.erase(g.db, space.post_id, now)" in take_down
    page = read("ruqqus", "classes", "submission.py").split("def rendered_page(", 1)[1]
    assert page.index("if self.deleted_utc > 0 and cperm.is_space(self):\n            abort(404)") < page.index('template = "submission_deleted.html"')
    migration = read("scripts", "migrations", "2026-11-11_space_board.sql")
    for table in ("space_messages", "space_message_reports"):
        assert f"CREATE TABLE IF NOT EXISTS public.{table} (" in migration and f"CREATE TABLE public.{table} (" in read("schema.sql")
    assert "2026-11-11_space_board.sql" in read("schema.sql")
    queue = read("ruqqus", "templates", "admin", "space_reports.html")
    assert "/admin/space_messages/{{ m.id }}/remove" in queue and "/admin/space_messages/{{ m.id }}/dismiss" in queue
    assert "reporter" not in queue
