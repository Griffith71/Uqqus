"""Linking an account from the composer: the routes, the popup's closing page and the upload script
must agree, and keep the properties that make it safe (state, same origin, the member's own nonce,
no credential in a page)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "ruqqus"
ROUTES = (ROOT / "routes" / "media.py").read_text(encoding="utf-8")
POPUP = (ROOT / "templates" / "media_connected.html").read_text(encoding="utf-8")
SCRIPT = (ROOT / "assets" / "js" / "media_upload.js").read_text(encoding="utf-8")
SETTINGS = (ROOT / "templates" / "settings_media.html").read_text(encoding="utf-8")


def func(src, name):
    start = src.index(f"def {name}(")
    nxt = re.search(r"\n@app\.|\ndef ", src[start + 10:])
    return src[start:start + 10 + nxt.start()] if nxt else src[start:]


def head(name):
    return ROUTES[:ROUTES.index(f"def {name}(")].rsplit("\n\n\n", 1)[1]


# --- the routes ------------------------------------------------------------------------------

def test_only_the_services_this_site_has_get_connect_routes_and_they_need_a_login():
    for name in ("settings_media_service_connect", "settings_media_service_callback"):
        h = head(name)
        assert "<any(microsoft, dailymotion):name>" in h and "@auth_required" in h, name
    callback = func(ROUTES, "settings_media_service_callback")
    assert "service.read_state(request.args.get(\"state\"), v.id" in callback          # the member's own request
    assert "service.has_scope(granted[\"scopes\"], service.required)" in callback       # refused a scope: nothing linked
    assert "encrypt_secret(granted[\"refresh_token\"])" in callback                     # never stored plain


def test_a_popup_is_only_recognised_with_a_plain_short_nonce_and_only_once():
    remember = func(ROUTES, "_remember_popup")
    assert 'request.args.get("popup") == "1"' in remember
    assert "nonce.isascii() and nonce.isalnum() and 8 <= len(nonce) <= 40" in remember
    finish = func(ROUTES, "_finish")
    assert 'session.pop("media_connect", None)' in finish                               # used up
    assert "POPUP_SECONDS" in finish and 'render_template("media_connected.html"' in finish
    # both the Google connection and the others end the same way
    assert func(ROUTES, "settings_media_google_callback").count("_finish(") >= 1
    assert "_remember_popup()" in func(ROUTES, "settings_media_google_connect")
    assert "_remember_popup()" in func(ROUTES, "settings_media_service_connect")


def test_the_closing_page_tells_only_this_origin_and_draws_no_markup_from_the_message():
    assert "{{ payload | tojson }}" in POPUP
    assert "window.opener.postMessage(message, window.location.origin)" in POPUP
    assert "new BroadcastChannel('ruqqus-media-connect')" in POPUP
    assert "localStorage.setItem('ruqqus-media-connect'" in POPUP
    assert "| safe" not in POPUP and "innerHTML" not in POPUP
    assert "refresh_token" not in POPUP and "refresh_token" not in SETTINGS


def test_the_status_answer_says_what_can_be_linked_and_where_files_go():
    status = func(ROUTES, "media_status")
    for key in ('"services"', '"using"', '"kinds"', '"offered"', '"video_visibility_by"'):
        assert key in status, key
    assert '"method": "post" if name == "dev" else "popup"' in status


def test_choosing_where_a_kind_of_file_goes_needs_a_login_and_the_form_key():
    for name in ("media_default", "settings_media_default"):
        h = head(name)
        assert "@auth_required" in h and "@validate_formkey" in h, name
    choose = func(ROUTES, "_set_default")
    assert "rules.provider_for(service, kind)" in choose and "a.is_active" in choose


def test_an_upload_can_name_the_account_and_says_where_it_went():
    begin = func(ROUTES, "media_upload_begin")
    assert '_account_for(v, kind, request.form.get("account") or None)' in begin
    assert '"to": {"service": account.provider, "label": provider.label}' in begin


def test_only_a_provider_that_needs_the_browser_s_answer_gets_it_and_it_is_capped():
    complete = func(ROUTES, "media_upload_complete")
    assert "if provider.wants_answer:" in complete
    assert '(request.form.get("answer") or "")[:2048]' in complete


def test_a_refresh_token_handed_out_again_is_kept():
    assert "_keep_rotated(account)" in func(ROUTES, "media_file")
    assert "account._rotated = False" in func(ROUTES, "media_upload_begin")


# --- the upload script -----------------------------------------------------------------------

def test_the_file_is_picked_before_anything_else_so_the_browser_allows_its_dialog():
    pick = SCRIPT[SCRIPT.index("function pickInto("):SCRIPT.index("function insertAtCaret(")]
    assert pick.index("choose(kind)") < pick.index("ensureAccount(kind, node)")
    video = SCRIPT[SCRIPT.index("function pickVideo("):SCRIPT.index("// 2. image buttons in comment forms")]
    assert video.index("choose('video')") < video.index("ensureAccount('video'")


def test_the_popup_s_answer_counts_only_from_this_origin_with_our_own_nonce():
    connect = SCRIPT[SCRIPT.index("function connect("):SCRIPT.index("function chooser(")]
    assert "event.origin === window.location.origin" in connect
    assert "message.n !== nonce" in connect and "message.type !== 'ruqqus-media-connected'" in connect
    assert "new BroadcastChannel('ruqqus-media-connect')" in connect
    assert "event.key !== 'ruqqus-media-connect'" in connect
    assert "window.crypto.getRandomValues(bytes)" in SCRIPT
    # the address of the popup: the service's connect route, with the composer's flag and nonce
    assert "'popup=1&n=' + nonce" in connect


def test_each_way_of_sending_bytes_keeps_the_session_off_a_provider_s_address():
    assert "xhr.withCredentials = upload.url.charAt(0) === '/'" in SCRIPT
    chunks = SCRIPT[SCRIPT.index("function sendChunks("):SCRIPT.index("function sendForm(")]
    assert "xhr.setRequestHeader('Content-Range', 'bytes ' + start + '-' + (end - 1) + '/' + size)" in chunks
    form = SCRIPT[SCRIPT.index("function sendForm("):SCRIPT.index("function send(")]
    assert "form.append(upload.form_field, file, file.name)" in form
    assert "/^https:\\/\\//.test(answer)" in form                 # only an https address is passed back


def test_the_script_never_builds_markup_from_text():
    assert "innerHTML" not in SCRIPT


def test_a_full_lost_or_unpermitted_account_is_fixed_in_place_and_tried_again_once():
    run = SCRIPT[SCRIPT.index("function run("):SCRIPT.index("function pickInto(")]
    assert "error.need === 'storage'" in run and "error.need === 'video'" in run and "error.code === 507" in run
    assert "!retried ? run(file, kind, node, fields, true)" in run


def test_the_settings_page_offers_each_service_and_the_choice_of_where_files_go():
    for address in ("/settings/media/microsoft/connect", "/settings/media/dailymotion/connect",
                    'action="/settings/media/default"', 'action="/settings/media/dailymotion/video"'):
        assert address in SETTINGS, address
    assert "{% if \"microsoft\" in offered %}" in SETTINGS and "{% if \"dailymotion\" in offered %}" in SETTINGS


def test_every_new_provider_is_configured_from_the_environment_example():
    example = (ROOT.parent / ".env.example").read_text(encoding="utf-8")
    for key in ("MICROSOFT_CLIENT_ID", "MICROSOFT_CLIENT_SECRET", "MICROSOFT_TENANT",
                "DAILYMOTION_API_KEY", "DAILYMOTION_API_SECRET"):
        assert key in example, key
