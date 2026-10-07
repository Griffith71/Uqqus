"""Sending a post to a chat crosses the share menus, the sheet and its script, the chat bundle, three
routes and the stylesheets. Chats are end-to-end encrypted, so the message is sent by the browser's chat
client; these checks keep that path closed to everything but this site's own pages, and keep the card in
the thread from ever showing an author or a post the reader may not see."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


ROUTES = read("ruqqus", "routes", "chat.py")
SCRIPT = read("ruqqus", "assets", "js", "share_chat.js")
MODAL = read("ruqqus", "templates", "partials", "share_chat_modal.html")
INDEX = read("ruqqus", "assets", "chat_src", "src", "index.js")
UI = read("ruqqus", "assets", "chat_src", "src", "ui.js")
SHELL = read("ruqqus", "templates", "default.html")


def route(name):
    return ROUTES.split(f"def {name}(")[1].split("\n@app.route(")[0]


# --- the share menus and the sheet ---------------------------------------------------------

def test_every_share_menu_of_a_post_offers_send_in_chat_for_a_member():
    item = ('{% if v %}<a class="dropdown-item share-chat-item" href="javascript:void(0);" role="button" '
            'data-share-url="{{ p.permalink | full_link }}"><i class="fas fa-paper-plane fa-fw"></i>Send in chat</a>{% endif %}')
    for name, menus in (("submission_listing.html", 2), ("submission.html", 2)):
        html = read("ruqqus", "templates", name)
        assert html.count(item) == menus == html.count("Copy link</a>"), name
        # always right after Copy link, in the same menu
        assert len(re.findall(r"Copy link</a>\n\s*" + re.escape(item), html)) == menus, name


def test_the_sheet_and_its_script_are_on_every_page_but_not_in_the_panel_frames():
    assert "{% if v and not request.args.get('embed') %}{% include \"partials/share_chat_modal.html\" %}{% endif %}" in SHELL
    line = next(x for x in SHELL.splitlines() if "/assets/js/share_chat.js" in x)
    assert "not request.args.get('embed')" in line
    assert "body.embedded .share-chat-item" in read("ruqqus", "assets", "style", "main.scss")


def test_every_element_the_script_uses_is_in_the_sheet():
    ids = set(re.findall(r"getElementById\('([\w-]+)'\)", SCRIPT))
    assert {"shareChatModal", "share-chat-list", "share-chat-search", "share-chat-note", "share-chat-status"} <= ids
    assert not [i for i in ids if f'id="{i}"' not in MODAL]


def test_names_and_messages_only_ever_go_in_as_text():
    assert "innerHTML" not in SCRIPT
    assert "name.textContent = '@' + target.username" in SCRIPT
    assert "/^(https?:\\/\\/|\\/)/.test(target.profile_url)" in SCRIPT         # an avatar address is checked (http: local dev)


def test_the_message_is_the_note_and_the_posts_own_link():
    assert "return (written ? written + '\\n' : '') + current.url;" in SCRIPT
    assert 'maxlength="500"' in MODAL


# --- the path into the encrypted chat -----------------------------------------------------

def test_the_script_only_talks_to_its_own_chat_frame_on_its_own_origin():
    assert "window.location.origin" in SCRIPT and SCRIPT.count("postMessage(") == 2 and "'*'" not in SCRIPT
    assert "event.origin !== window.location.origin || !chat || event.source !== chat.frame.contentWindow" in SCRIPT
    assert "type: 'ruqqus-chat-send'" in SCRIPT and "type: 'ruqqus-chat-ping'" in SCRIPT


def test_the_chat_page_only_answers_its_own_parent_and_only_into_a_chat_the_member_can_write_in():
    handler = INDEX.split('window.addEventListener("message"')[1].split("async function main()")[0]
    assert "window.parent === window || event.origin !== window.location.origin || event.source !== window.parent" in handler
    assert '"*"' not in INDEX
    send = INDEX.split("async function sendShared(")[1].split("window.addEventListener")[0]
    assert "state.conversations.inbox.find(" in send and "state.conversations.request" not in send
    assert "text.length > 2000" in send and "!isReady" in send
    assert "state.client.sendTextMessage(roomId, text)" in send
    assert "isReady = true;\n  tellParent({ type: \"ruqqus-chat-ready\" });" in INDEX


def test_there_is_one_chat_client_per_page_the_panels_frame_is_shared():
    panels = read("ruqqus", "assets", "js", "side_panels.js")
    assert "window.RuqqusPanels = { chatFrame: function () { return frameFor('chat'); } };" in panels
    assert "window.RuqqusPanels && window.RuqqusPanels.chatFrame" in SCRIPT


# --- the routes -----------------------------------------------------------------------------

def test_starting_a_chat_can_answer_json_and_keeps_its_checks():
    start = route("chat_start")
    assert start.count("return _started(") == 3 and "return redirect(" not in start
    assert start.index("if is_blocked(v, target):\n        abort(403)") < start.index("_started(")
    assert 'if request.values.get("json"):' in ROUTES.split("def _started(")[1].split("@app.route")[0]
    assert "@auth_required" in ROUTES.split("def chat_start(")[0].rsplit("@app.route(", 1)[1] and "@validate_formkey" in ROUTES.split("def chat_start(")[0].rsplit("@app.route(", 1)[1]


def test_the_list_of_people_leaves_out_blocks_requests_and_filtered_names():
    targets = route("chat_share_targets")
    for rule in ("UserBlock.user_id == v.id, UserBlock.target_id == v.id", "other.id in blocked", "convo.tab_for(v.id) != \"inbox\"",
                 "user_hidden(other, v)", "filter_users(people, v)", "User.is_deleted == False", "User.is_banned == 0",
                 "i not in seen and i not in blocked"):
        assert rule in targets, rule
    head = ROUTES.split("def chat_share_targets(")[0].rsplit("@app.route(", 1)[1]
    assert 'methods=["GET"]' in head and "@auth_required" in head


def test_the_card_is_made_for_the_reader_and_never_names_an_author():
    preview = route("chat_post_preview")
    for rule in ("post_hidden(post, v)", "post.board.can_view(v)", "post.is_banned or post.board.is_banned", "post.deleted_utc",
                 "POST_ID.match(pid)", "post.has_thumb and not post.is_sensitive"):
        assert rule in preview, rule
    answered = re.search(r"return jsonify\(\{\s*\"ok\": True,(.*?)\}\)", preview, re.S).group(1)
    assert set(re.findall(r'"(\w+)":', answered)) == {"title", "guild", "url", "thumb", "comments"}
    assert "@auth_required" in ROUTES.split("def chat_post_preview(")[0].rsplit("@app.route(", 1)[1]


# --- the card -------------------------------------------------------------------------------

def test_the_thread_draws_a_card_only_for_this_sites_own_post_links_and_only_as_text():
    assert '"https?://" + window.location.host.replace(' in UI and "(?:/\\\\+\\\\w+)?/post/([0-9a-z]{1,10})" in UI
    card = UI.split("function fillPostCard(")[1].split("function postCard(")[0]
    assert "innerHTML" not in card and card.count("textContent") >= 4
    assert 'data.url.startsWith("/")' in card and 'data.thumb.startsWith("https://")' in card
    assert "if (shared) column.appendChild(postCard(shared[1]));" in UI
    assert "shared ? item.body.replace(shared[0], \"\").trim() || \"Shared a post\" : item.body" in UI


def test_both_stylesheets_style_the_sheet_and_the_card():
    for sheet in ("main.scss", "main_dark.scss"):
        css = read("ruqqus", "assets", "style", sheet)
        for cls in (".share-chat-list", ".share-chat-row", ".chat-post-card", ".chat-post-thumb", ".chat-post-title"):
            assert cls in css, (sheet, cls)


def test_get_post_with_a_viewer_honours_graceful_for_a_missing_post():
    # it used to raise TypeError (items[0] on None), which a card for a deleted link tripped over
    source = read("ruqqus", "helpers", "get.py").split("def get_post(")[1].split("\ndef ")[0]
    assert "        if not items:\n            return None" in source
