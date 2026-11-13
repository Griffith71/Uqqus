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
    # only the Spaces area makes one, in-process, with a mark on the environ no browser can set (routes/spaces.py)
    assert "if space_wanted and not request.environ.get(space_rules.ENVIRON_KEY):\n        space_error = space_rules.NOT_HERE" in submit
    create = ROUTES.split("def space_create(", 1)[1].split("\n@app.route", 1)[0]
    assert '_post_as(v, "/api/vue/submit", data,\n                              environ={space_rules.ENVIRON_KEY: True, UI_LOCALE_KEY: current_locale()})' in create
    assert 'ENVIRON_KEY = "ruqqus.space_create"' in read("ruqqus", "helpers", "spaces.py")
    assert "environ_base={\"REMOTE_ADDR\": address, UI_LOCALE_KEY: \"en\", **(environ or {})}" in read("ruqqus", "routes", "collab.py")
    assert 'anonymous=flag(request.form, "anonymous")' in submit and "circle_guild=circle_board is not None" in submit
    assert "from ruqqus.helpers import spaces as space_rules, space_store" in POSTS


def test_every_route_reads_the_space_through_its_post():
    routes = re.findall(r'@app\.route\("([^"]+)", methods=\["(\w+)"\]\)\n((?:@[^\n]+\n)+)def (\w+)', ROUTES)
    assert len(routes) >= 18
    for path, method, decorators, name in routes:
        if name in ("spaces_home", "space_home_page"):   # the area and a live thread read like posts, visitors too
            assert "@auth_desired" in decorators
            continue
        assert "@auth_required" in decorators or "@is_not_banned" in decorators or "@admin_level_required(3)" in decorators, name
        if method == "POST":
            assert "@validate_formkey" in decorators, name
        if path.startswith("/space/post/"):            # from a Space's backing row to the Space: its checks
            body = ROUTES.split(f"def {name}(", 1)[1].split("\n@app.route", 1)[0]
            assert "get_post(pid, v=v)" in body and "_space_for_row(post, v)" in body, name
            continue
        if path.startswith(("/space/", "/api/spaces/")):
            body = ROUTES.split(f"def {name}(", 1)[1].split("\n@app.route", 1)[0]
            assert "load_space(sid, v)" in body, name
    # a Space's own address: its backing row's checks, then the redirect to the canonical address (which names the
    # host) only after them; a visitor gets a live thread only, a board or a call asks them to sign in
    home = ROUTES.split("def space_home_page(", 1)[1].split("\n@app.route", 1)[0]
    assert home.index("get_post(pid, v=v)") < home.index("_space_for_row(post, v)") < home.index("canonical_redirect(space.post)")
    assert home.index("if v is None:") < home.index("_space_page(space, v)")
    for_row = ROUTES.split("def _space_for_row(", 1)[1].split("\ndef ", 1)[0]
    assert "_post_for(row, v)" in for_row and "abort(404)" in for_row
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
    assert "if space.kind == space_rules.CALL:" in start            # only a call has a Matrix room
    # the reminder notice names neither the Space nor its host
    # the Space's own page, never its post's address (which names the host: helpers/permalinks.py)
    assert 'REMINDED_LIVE = N_("A Space you asked to be reminded of is live: [join it](/space/%(space)s)")' in ROUTES
    assert "notify(user, REMINDED_LIVE, space=space.id)" in start
    assert "permalink" not in start.split("reminded = set(")[1]


def test_the_page_and_its_script():
    assert PAGE.startswith("<!DOCTYPE html>") and "window.UQQUS_SPACE_BOOT = {{ boot | tojson }};" in PAGE
    assert "Everyone in this Space can see who is here, listeners too." in PAGE
    # a Space is not a post: the page never sends anyone to "the post"; it votes and copies its own address
    assert "Open the post" not in PAGE and "Back to the post" not in PAGE
    assert 'id="space-upvote"' in PAGE and "`/api/vote/post/${boot.postId}/${next}`" in SPACE_JS
    assert "This Space is recorded" in PAGE and "[hidden] { display: none !important; }" in PAGE
    for script in ("js/live_events.js", "js/media_upload.js", "js/space_bundle.js"):
        assert f"asset_url('{script}')" in PAGE, script
    # every element the script asks for is on the page, but the updates panel: only a Space made before live threads
    # were their own kind has updates, and the script asks for them only when the panel is there (UPDATES)
    updates = ("space-updates", "space-update", "space-summary")
    for element_id in re.findall(r'byId\("([\w-]+)"\)', SPACE_JS):
        if element_id.startswith(updates) or element_id == "space-end-board":
            continue
        assert f'id="{element_id}"' in PAGE, element_id
    assert 'const UPDATES = !!byId("space-updates-panel");' in SPACE_JS
    assert 'id="space-end-board"' in PAGE
    from ruqqus.helpers import device_access
    assert device_access.is_call_page("/space/12")
    # a listener has no microphone; nothing is played from anyone off stage
    assert "media: null, encrypted: true," in SPACE_JS
    assert "sends: () => onStage()," in SPACE_JS and "hears: (p) => STAGE.has(p.role)," in SPACE_JS
    assert "audio.muted = !(p && STAGE.has(p.role));" in SPACE_JS
    assert 'navigator.locks.request("uqqus-call", { ifAvailable: true }' in SPACE_JS
    assert "if (!state.stopped && state.data.room_id) await inRoom(state.data.room_id);" in SPACE_JS
    # the only HTML the page draws is an update's and the summary's stored, sanitized HTML; board messages are text
    assert SPACE_JS.count("innerHTML") == 2
    assert 'body.innerHTML = u.body_html || "";' in SPACE_JS and "summary.innerHTML = thread.summary_html;" in SPACE_JS
    board = SPACE_JS.split("function drawMessage(m) {", 1)[1].split("\nfunction ", 1)[0]
    assert "innerHTML" not in board and "body.textContent = m.body;" in board


def test_the_replay_goes_to_the_hosts_own_storage():
    # the sound to their linked storage, or (a public call that allowed cameras) the video to their own channel
    assert 'window.MediaUpload.upload(file, video ? "video" : "audio"' in SPACE_JS
    assert 'await api("replay", video ? { video: String(asset.id) } : { media: String(asset.id) });' in SPACE_JS
    assert "canvas.captureStream(15)" in SPACE_JS and "boot.videoReplay" in SPACE_JS
    replay = ROUTES.split("def space_replay(", 1)[1].split("\n@app.route", 1)[0]
    assert "v.id != space.host_id or not space.record or space.kind != space_rules.CALL" in replay
    assert "space_rules.video_replay(space.kind, space.record, space.video, space.post.audience)" in replay
    own = ROUTES.split("def _own_video_link(", 1)[1].split("\ndef ", 1)[0]
    for check in ("user_id=v.id", "media_rules.VIDEO", "media_rules.HOSTED", "media_rules.video_ref(link)"):
        assert check in own, check
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
    for name in ('name="space_kind"', 'name="space_hours"', 'name="space_start"', 'name="space_record"', "data-space-when",
                 "data-space-composer", 'data-space-for="board live"', 'data-space-for="call"'):
        assert name in page, name
    assert 'name="space"' not in page                     # the route adds it; the browser can't make a Space elsewhere
    assert "where_profile(" in page and "audience_field(" in page
    script = read("ruqqus", "assets", "js", "space_new.js")
    assert "fetch('/api/spaces'" in script and "/api/vue/submit" not in script and "innerHTML" not in script
    assert "asset_url('js/space_composer.js')" in read("ruqqus", "templates", "default.html")
    # not a navbar button (the owner, 2026-10-21): the account menu (partials/account_menu.html, drawn by both shells) and
    # the phone menu in each shell, and the left sidebar's own Spaces section under My Curations (open and folded)
    assert read("ruqqus", "templates", "partials", "account_menu.html").count('href="/spaces"') == 1
    for shell in ("default.html", "header.html"):
        page = read("ruqqus", "templates", shell)
        assert page.count('href="/spaces"') == 1, shell                  # the phone menu
        assert '{% include "partials/account_menu.html" %}' in page, shell
        assert 'href="/spaces" aria-label="Spaces"' not in page, shell
    sidebar = read("ruqqus", "templates", "sidebar-left.html")
    assert sidebar.count('href="/spaces"') == 2 and 'href="/spaces/new"' in sidebar
    assert sidebar.index("My Curations") < sidebar.index('class="sidebar-block sidebar-spaces"')


def test_a_space_post_takes_no_comments_and_a_not_recorded_space_leaves_nothing():
    posts = read("ruqqus", "routes", "posts.py")
    # a live thread takes comments, a board and a call none (helpers/spaces.py comment_mode); copies only show the card
    assert "comment_permission=(space_rules.comment_mode(space_kind) if space_start is not None" in posts
    assert "is_space=space_start is not None," in posts
    assert "if primary.is_space or primary.comment_permission == cperm.SPACE:" in posts
    assert "space_ending.end(g.db, r, space.row, now, by_admin=by_admin)" in ROUTES
    ending = read("ruqqus", "helpers", "space_ending.py")
    assert "if not record:\n        take_down(db, space_id, post_id, now)" in ending
    take_down = ending.split("def take_down(", 1)[1].split("\ndef ", 1)[0]
    assert "WHERE (id = :p OR repost_id = :p) AND deleted_utc = 0" in take_down
    assert "UPDATE comments SET deleted_utc = :now" in take_down
    assert "content_edit_history" not in take_down                                        # never kept as a private copy of a delete
    assert "space_ending.end_due(db, space_redis, now)" in read("scripts", "recomputes.py")
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


def test_a_live_thread_space_is_its_backing_row_s_live_thread():
    add = _route("space_update_add")
    assert "_runs_updates(space, v)" in add and "space.status != space_rules.LIVE" in add
    assert '"live.update", {"id": uid})' in add and "live_routes._render(body, space.post)" in add
    assert "_runs_updates(space, v)" in _route("space_summary") and "_runs_updates(space, v)" in _route("space_summary_text")
    start = _route("space_start")
    # a live thread's thread starts with it, for its own length; a live thread is drawn by its backing row's page
    assert "live_store.start(g.db, space.post_id, v.id, now, row[space_store.ENDS]" in start
    assert "_followers_who_may_read(space.post, ids)" in start              # its followers are told once
    assert "live_store.end(db, post_id, now)" in read("ruqqus", "helpers", "space_ending.py")
    assert "return post_base36id_with_comments(pid=space.post.base36id)" in ROUTES
    threads = read("ruqqus", "routes", "live_threads.py")
    toggle = threads.split("def live_toggle(", 1)[1].split("\n@app.route", 1)[0]
    assert "space_ending.end(g.db, r, row, now)" in toggle and "space_store.set_ends(g.db, primary.id" in toggle
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
    assert 'space_video = flag(request.form, "space_video") and space_kind == space_rules.CALL' in POSTS
    assert "video=space_video," in POSTS


def test_a_not_recorded_space_takes_its_board_and_updates_with_it():
    take_down = read("ruqqus", "helpers", "space_ending.py").split("def take_down(", 1)[1].split("\ndef ", 1)[0]
    assert "space_store.clear_board(db, space_id)" in take_down and "live_store.erase(db, post_id, now)" in take_down
    page = read("ruqqus", "classes", "submission.py").split("def rendered_page(", 1)[1]
    # every taken-down Space shows nothing, a live thread (which takes comments) included
    assert page.index("if self.deleted_utc > 0 and (self.is_space or cperm.is_space(self)):\n            abort(404)") < page.index('template = "submission_deleted.html"')
    migration = read("scripts", "migrations", "2026-11-11_space_board.sql")
    for table in ("space_messages", "space_message_reports"):
        assert f"CREATE TABLE IF NOT EXISTS public.{table} (" in migration and f"CREATE TABLE public.{table} (" in read("schema.sql")
    assert "2026-11-11_space_board.sql" in read("schema.sql")
    queue = read("ruqqus", "templates", "admin", "space_reports.html")
    assert "/admin/space_messages/{{ m.id }}/remove" in queue and "/admin/space_messages/{{ m.id }}/dismiss" in queue
    assert "reporter" not in queue


def test_a_join_that_fails_before_the_call_client_runs_lets_the_call_lock_go():
    # the Join button comes back; pressing it again must not find this page's own uqqus-call lock and say another window holds a call
    join = SPACE_JS[SPACE_JS.index("async function joinSpace() {"):SPACE_JS.index("// in the Space's room before anyone connects")]
    assert "if (!(await holdCallLock())) {" in join
    failed = join[join.index("} catch (e) {"):]
    assert failed.index("if (!state.client) letCallLockGo();") < failed.index('byId("space-join").disabled = false;')
    hold = SPACE_JS[SPACE_JS.index("async function holdCallLock() {"):SPACE_JS.index("async function join() {")]
    assert "if (!navigator.locks || state.callLock) return true;" in hold          # held already: not asked again
    assert "state.callLock = null;" in hold and "release();" in hold
    finish = SPACE_JS[SPACE_JS.index("async function finish(reason) {"):SPACE_JS.index("// --- starting the page")]
    assert finish.index("await recordFinish();") < finish.index("await state.keeper.shutdown()") < finish.index("letCallLockGo();")
    start = SPACE_JS[SPACE_JS.index("async function startMatrix() {"):SPACE_JS.index("function inRoom(roomId) {")]
    assert "await keeper.shutdown();" in start                       # the store key zeroed and its lock let go before the call lock


def test_chat_stopping_without_a_reload_takes_the_page_out_of_its_space():
    """keeper.js stops a page without reloading it (a refused renewal, over the reload budget): a joined Space is left as Leave leaves it
    (finish: the microphone, the peers, the heartbeat, the call client, then the uqqus-call lock), a join on its way stops before it connects."""
    ui = SPACE_JS[SPACE_JS.index("const spaceUi = {"):SPACE_JS.index("class NotStarted")]
    assert "stop: (text) => stopped(text)," in ui and "say(text, true)" not in ui
    stop = SPACE_JS[SPACE_JS.index("function stopped(text) {"):SPACE_JS.index("async function letGo()")]
    assert "state.stopped = true;" in stop and "if (state.joined) return leave(text);" in stop and "return letGo();" in stop
    drop = SPACE_JS[SPACE_JS.index("async function dropClient()"):SPACE_JS.index("async function letGo()")]
    assert drop.index("state.keeper.shutdown()") < drop.index("state.client = null;") and "letCallLockGo" not in drop
    let_go = SPACE_JS[SPACE_JS.index("async function letGo()"):SPACE_JS.index("const spaceUi = {")]
    assert let_go.index("await dropClient();") < let_go.index("letCallLockGo();")
    finish = SPACE_JS[SPACE_JS.index("async function finish("):SPACE_JS.index("// --- starting the page")]
    for step in ("clearInterval(state.heartbeat);", "state.mesh.close()", "state.media.stop()", "state.keeper.shutdown()", "letCallLockGo();"):
        assert step in finish, step
    join = SPACE_JS[SPACE_JS.index("async function joinSpace()"):SPACE_JS.index("state.mesh = new Mesh(")]
    assert join.index("state.stopped = false;") < join.index('state.data = await api("join");')
    assert join.index('state.data = await api("join");') < join.index("if (state.stopped) {") < join.index("state.joined = true;")
    assert "return letGo();" in join[join.index("if (state.stopped) {"):]


def test_a_join_refused_after_chat_stopped_lets_the_stopped_client_and_the_call_lock_go():
    """Chat stopped while the join request was on its way (stopped() only says so while joining) and the request then failed (a parent
    who switched chat off refuses both): the stopped client, its keeper and uqqus-call all go, and a later Join never reuses a stopped
    client."""
    join = SPACE_JS[SPACE_JS.index("async function joinSpace() {"):SPACE_JS.index("let turn = null;")]
    failed = join[join.index("} catch (e) {"):]
    assert "if (state.stopped || (state.keeper && state.keeper.stopped)) await letGo();\n    else if (!state.client) letCallLockGo();" in failed
    before = join[:join.index("  try {")]
    assert before.index("if (!(await holdCallLock())) {") < before.index(
        "if (state.client && (!state.keeper || state.keeper.stopped)) await dropClient();") < before.index("state.stopped = false;")
    assert "state.client = state.client || (await startMatrix());" in join


def test_a_microphone_that_opens_after_the_space_was_left_is_put_down_at_once():
    """Leave (or chat stopping) pressed while the browser's microphone prompt is up: finish() ran with no microphone to stop, so the
    one that opens afterwards is stopped there and then, and the join goes no further (no recording, no watch, no heartbeat)."""
    mic = SPACE_JS[SPACE_JS.index("async function micOn() {"):SPACE_JS.index("function micOff() {")]
    assert mic.index("if (state.media || state.ended || state.stopped) return;") < mic.index("media = await LocalMedia.open(false);")
    after = mic[mic.index("media = await LocalMedia.open(false);"):]
    assert after.index("if (state.ended || state.stopped || state.media) {") < after.index("media.stop();") \
        < after.index("state.media = media;") < after.index("state.mesh.setMedia(state.media);")
    join = SPACE_JS[SPACE_JS.index("async function joinSpace() {"):SPACE_JS.index("// A busy Space sends many roster changes")]
    rest = join[join.index("if (onStage()) await micOn();"):]
    assert rest.index("if (state.ended || state.stopped) return;") < rest.index("recordStart();") \
        < rest.index("window.UqqusLive.watch(") < rest.index("state.heartbeat = setInterval(beat, 10000);")


def test_a_join_with_no_relay_lets_the_call_client_and_lock_go_and_gives_join_back():
    join = SPACE_JS[SPACE_JS.index("async function joinSpace() {"):SPACE_JS.index("state.mesh = new Mesh(")]
    relay = join[join.index("if (!state.stopped && (!turn || !turn.uris || !turn.uris.length)) {"):]
    relay = relay[:relay.index("\n  }\n")]
    assert relay.index('await api("leave").catch(() => {});') < relay.index("await letGo();") \
        < relay.index('say(window.tr("Spaces are unavailable right now. Try again later."), true)')
    let_go = SPACE_JS[SPACE_JS.index("async function letGo()"):SPACE_JS.index("const spaceUi = {")]
    assert 'byId("space-join").disabled = false;' in let_go
