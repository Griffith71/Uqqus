"""Calls across the routes, the call window, its bundle, the ringing toast, the chat thread and the build. The promises:
every join and heartbeat asks the chat's own rules and the relay's allowance again; nothing but the relay is ever used
(no IP address is shown to anyone); a signal counts only from a device the server lists in the call whose key checks
out; a block, leaving a group or being removed ends your part in its calls; a call's live topic is guarded."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


ROUTES = read("ruqqus", "routes", "calls.py")
CALL_JS = read("ruqqus", "assets", "chat_src", "src", "call.js")
MESH = read("ruqqus", "assets", "chat_src", "src", "rtc", "mesh.js")
SIGNAL = read("ruqqus", "assets", "chat_src", "src", "rtc", "signalling.js")
MEDIA = read("ruqqus", "assets", "chat_src", "src", "rtc", "media.js")
PAGE = read("ruqqus", "templates", "call.html")
RING = read("ruqqus", "assets", "js", "call_ring.js")
LIVE = read("ruqqus", "assets", "js", "live_events.js")


def test_routes_need_an_account_and_the_form_key():
    routes = re.findall(r'@app\.route\("([^"]+)", methods=\["(\w+)"\]\)\n((?:@[^\n]+\n)+)def (\w+)', ROUTES)
    assert {name for *_, name in routes} == {"call_window", "call_join", "call_heartbeat", "call_leave", "call_decline", "call_status"}
    for path, method, decorators, name in routes:
        assert "@auth_required" in decorators, name
        if method == "POST":
            assert "@validate_formkey" in decorators, name
    assert "from .calls import *" in read("ruqqus", "routes", "__init__.py")


def test_every_join_and_heartbeat_asks_the_chats_rules_again():
    join = ROUTES.split("def call_join(", 1)[1].split("\n@app.route", 1)[0]
    assert "_room_and_device(v)" in join and "if refusal:" in join
    assert "turn_usage.refusal(relay, spent, allowance, now)" in join          # the relay's month
    assert "call_rules.join_refusal(" in join and "call_rules.video_allowed(" in join
    assert "You're already in this call in another window." in join
    beat = ROUTES.split("def call_heartbeat(", 1)[1].split("\n@app.route", 1)[0]
    assert "chat_room(v, room_id)" in beat and "call_store.leave_user(r, room_id, v.id)" in beat
    assert "MAX_CALL_SECONDS" in beat
    pair = ROUTES.split("def chat_room(", 1)[1].split("\ndef ", 1)[0]
    assert "call_rules.pair_refusal(convo.status, is_blocked(v, other)" in pair
    assert "me[2] != group_rules.JOINED" in pair                               # a group: joined members only


def test_the_live_topic_of_a_call_is_guarded():
    assert 'live_bus.register_topic("call", _guard_call_topic)' in ROUTES
    guard = ROUTES.split("def _guard_call_topic(", 1)[1].split("\n\n\n", 1)[0]
    assert "call_store.room_of(r, call_id)" in guard and "refusal is None" in guard


def test_blocks_leaving_and_removal_end_your_part():
    perms = read("ruqqus", "helpers", "chat_permissions.py")
    assert "end_call_in(convo.matrix_room_id)" in perms
    groups = read("ruqqus", "routes", "chat_groups.py")
    assert groups.count("_out_of_call(") >= 3                                  # defined, removal and leaving


def test_only_the_relay_is_ever_used_and_media_stays_small():
    assert 'iceTransportPolicy: "relay"' in MESH
    assert MESH.count("new RTCPeerConnection(") == 1                           # the one place a connection is made
    assert "RTCPeerConnection" not in CALL_JS
    assert "turnServer()" in CALL_JS and "Calls are unavailable right now" in CALL_JS
    assert "MAX_VIDEO_BITRATE = 500_000" in MEDIA and "frameRate: { ideal: 24, max: 24 }" in MEDIA


def test_signals_count_only_from_listed_devices_whose_keys_check_out():
    assert "ClientEvent.ReceivedToDeviceMessage" in SIGNAL
    listen = SIGNAL.split("export function listen(", 1)[1]
    for check in ("content.call_id !== callId", "content.to_device !== myDevice", "if (!info || info.sender !== message.sender) return;",
                  "info.senderDevice !== content.from_device", "!== info.senderCurve25519KeyBase64"):
        assert check in listen, check
    assert "client.encryptAndSendToDevice(SIGNAL_TYPE" in SIGNAL
    # a signal from a device not on the roster waits and is never acted on before the roster names it
    on_signal = MESH.split("  onSignal(key, content) {", 1)[1].split("\n  }\n", 1)[0]
    assert "if (!peer) {" in on_signal and "this.pending.set(key, list)" in on_signal
    assert "PENDING_MAX" in on_signal
    # the call window is always encrypted
    assert "encrypted: true," in CALL_JS


def test_the_call_window_is_its_own_device_and_one_per_browser():
    assert "ensureSession(boot, CALL_STORAGE_KEY)" in CALL_JS
    assert 'initRustCrypto({ cryptoDatabasePrefix: "uqqus-call" })' in CALL_JS
    assert 'navigator.locks.request("uqqus-call", { ifAvailable: true }' in CALL_JS
    assert 'export const CALL_STORAGE_KEY = "ruqqus_call_session";' in read("ruqqus", "assets", "chat_src", "src", "token.js")
    index = read("ruqqus", "assets", "chat_src", "src", "index.js")
    assert 'navigator.locks\n      .request("uqqus-matrix", { steal: true }' in index      # one chat client per browser
    assert "await holdChatLock();" in index


def test_the_call_page():
    assert PAGE.startswith("<!DOCTYPE html>")
    assert "window.UQQUS_CALL_BOOT = {{ boot | tojson }};" in PAGE
    assert "asset_url('js/call_bundle.js')" in PAGE
    assert "[hidden] { display: none !important; }" in PAGE                    # buttons set display; hidden must win
    for element_id in re.findall(r'byId\("([\w-]+)"\)', CALL_JS):
        assert f'id="{element_id}"' in PAGE, element_id
    assert "innerHTML" not in CALL_JS                                          # names go in as text
    window = ROUTES.split("def call_window(", 1)[1].split("\n@app.route", 1)[0]
    assert 'resp.headers["Cache-Control"] = "no-store"' in window and '"refusal": refusal' in window
    from ruqqus.helpers import device_access
    assert device_access.is_call_page("/call/!abc")


def test_the_build_makes_and_ships_the_call_bundle():
    pkg = read("ruqqus", "assets", "chat_src", "package.json")
    assert "esbuild src/call.js --bundle --format=esm --minify --sourcemap --outfile=../js/call_bundle.js" in pkg
    assert "COPY --from=chat-bundle /opt/build/ruqqus/assets/js/call_bundle.js" in read("Dockerfile")
    assert "ruqqus/assets/js/call_bundle.js\n" in read(".gitignore")


def test_the_ringing_toast_and_the_live_client():
    default = read("ruqqus", "templates", "default.html")
    assert ("{% if v and not request.args.get('embed') %}<script src=\"{{ asset_url('js/live_events.js') }}\"></script>\n"
            "<script src=\"{{ asset_url('js/call_ring.js') }}\"></script>{% endif %}") in default
    for kind in ("call.ring", "call.started", "call.answered", "call.cancelled"):
        assert f"UqqusLive.on('{kind}'" in RING, kind
    assert "innerHTML" not in RING and "innerHTML" not in LIVE
    assert "/^(https?:\\/\\/|\\/)/.test(data.avatar)" in RING
    assert "HIDDEN_CLOSE_MS" in LIVE
    for name in ("main.scss", "main_dark.scss"):
        css = read("ruqqus", "assets", "style", name)
        assert ".call-ring {" in css and ".chat-call-bar {" in css and "@keyframes call-ring-in" in css


def test_the_chat_thread_offers_calls_and_draws_the_call_line_as_a_note():
    ui = read("ruqqus", "assets", "chat_src", "src", "ui.js")
    assert 'data-call="audio"' in ui and 'data-call="video"' in ui and "setCall(status)" in ui
    assert "if (item.notice) {" in ui
    index = read("ruqqus", "assets", "chat_src", "src", "index.js")
    assert 'notice: !deleted && event.getContent().msgtype === "m.notice"' in index
    assert "/api/calls/status?room=" in index
