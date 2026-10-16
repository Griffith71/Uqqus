"""Group chats across the schema, the Matrix room, the routes, the webhook, the badges, the chat page and its bundle,
the share sheet and both stylesheets. The promises: only the bot can change who is in a group (so every change goes
through a route and its rules); every change is a POST with the form key; a group you are not in is a 404; a block is
never revealed (no automatic removal, the blocker's own client hides the messages); names go in as text."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


ROUTES = read("ruqqus", "routes", "chat_groups.py")
CHAT_ROUTES = read("ruqqus", "routes", "chat.py")
MATRIX = read("ruqqus", "helpers", "matrix_client.py")
SRC = ROOT / "ruqqus" / "assets" / "chat_src" / "src"
INDEX, UI, GROUPS, API = (read("ruqqus", "assets", "chat_src", "src", n) for n in ("index.js", "ui.js", "groups.js", "api.js"))


def test_schema_and_migration_agree():
    migration = read("scripts", "migrations", "2026-10-22_chat_groups.sql")
    schema = read("schema.sql")
    for needle in ("CHECK (role IN ('owner', 'admin', 'member'))", "CHECK (status IN ('invited', 'joined'))",
                   "REFERENCES public.chat_groups(id) ON DELETE CASCADE", "CONSTRAINT chat_groups_room_key UNIQUE (matrix_room_id)",
                   "chat_group_members_user_idx"):
        assert needle in migration and needle in schema, needle


def test_only_the_bot_can_change_who_is_in_a_group():
    levels = MATRIX.split("def group_power_levels", 1)[1].split("\ndef ", 1)[0]
    for key in ('"state_default": 100', '"invite": 100', '"kick": 100', '"ban": 100', '"users_default": 0',
                '"events_default": 0', '"m.room.power_levels": 100', '"m.room.encryption": 100',
                '"m.room.history_visibility": 100', '"m.room.join_rules": 100'):
        assert key in levels, key
    # the bot creates the room, so from room version 12 it must not be listed (Synapse refuses it)
    assert '"users":' not in levels
    room = MATRIX.split("def create_group_room", 1)[1].split("\ndef ", 1)[0]
    assert '"m.megolm.v1.aes-sha2"' in room and '"history_visibility": "invited"' in room
    assert '"guest_access": "forbidden"' in room and '"visibility": "private"' in room


def test_every_change_is_a_post_with_the_form_key_and_a_group_you_are_not_in_is_a_404():
    routes = re.findall(r'@app\.route\("([^"]+)", methods=\["(\w+)"\]\)\n((?:@[^\n]+\n)+)def (\w+)', ROUTES)
    assert len(routes) >= 11
    for path, method, decorators, name in routes:
        assert "@auth_required" in decorators or "@is_not_banned" in decorators, name
        if method == "POST":
            assert "@validate_formkey" in decorators, name
    # adding people, making and renaming a group need an account in good standing
    for name in ("chat_group_create", "chat_group_add", "chat_group_rename"):
        assert re.search(rf"@is_not_banned\n@validate_formkey\ndef {name}\(", ROUTES), name
    membership = ROUTES.split("def _membership", 1)[1].split("\ndef ", 1)[0]
    assert "abort(404)" in membership
    for name in ("chat_group_info", "chat_group_add", "chat_group_remove", "chat_group_leave", "chat_group_accept",
                 "chat_group_decline", "chat_group_rename", "chat_group_role", "chat_group_mark_read"):
        body = ROUTES.split(f"def {name}(", 1)[1].split("\n@app.route", 1)[0]
        assert "_membership(gid, v" in body, name


def test_nobody_is_added_unless_every_name_can_be_and_bots_never_chat():
    create = ROUTES.split("def chat_group_create(", 1)[1].split("\n@app.route", 1)[0]
    assert "if v.is_bot:" in create and "_refusals(v, group_rules.OWNER" in create
    assert create.index("_refusals(") < create.index("create_group_room(")       # checked before anything is made
    assert "GROUPS_PER_DAY" in create and "_daily_adds_left" in create
    people = ROUTES.split("def chat_people(", 1)[1].split("\ndef ", 1)[0]
    assert "User.is_bot == False" in people and "filter_users(" in people and "- blocked" in people


def test_a_block_never_removes_anyone_and_the_blocker_hides_the_messages():
    assert "chat_group" not in read("ruqqus", "helpers", "chat_permissions.py")
    group_json = ROUTES.split("def group_json(", 1)[1]
    assert '"blocked": u.id in mine' in group_json and "_blocked_by_me(v)" in group_json
    assert "A message from someone you blocked" in UI and "author.blocked" in INDEX


def test_the_webhook_counts_group_messages_and_the_badges_count_groups():
    hook = CHAT_ROUTES.split("def matrix_appservice_transaction", 1)[1].split("\n@app.route", 1)[0]
    assert "chat_group_store.note_message(g.db, room_id, sender_user_id" in hook
    user = read("ruqqus", "classes", "user.py")
    assert "chat_group_store.unread_total(g.db, self.id)" in user
    assert "chat_group_store.unread_groups(g.db, self.id)" in user
    assert "chat_group_store.invitations(g.db, self.id)" in user
    listing = CHAT_ROUTES.split("def chat_conversations(", 1)[1].split("\n@app.route", 1)[0]
    assert "chat_group_store.groups_for(" in listing and '"kind": "pair"' in listing


def test_the_share_sheet_offers_the_groups_you_are_in():
    targets = CHAT_ROUTES.split("def chat_share_targets(", 1)[1].split("\n@app.route", 1)[0]
    assert 'groups_for(g.db, v.id, "joined"' in targets and '"kind": "group"' in targets
    share = read("ruqqus", "assets", "js", "share_chat.js")
    assert "target.kind === 'group'" in share


def test_the_page_and_the_bundle():
    chat = read("ruqqus", "templates", "chat.html")
    assert '{% include "partials/chat_group_sheets.html" %}' in chat and 'id="chat-new-group"' in chat
    assert "maxGroupMembers: {{ chat_group_max }}" in chat
    sheets = read("ruqqus", "templates", "partials", "chat_group_sheets.html")
    for element_id in re.findall(r'byId\("([\w-]+)"\)', GROUPS):
        assert f'id="{element_id}"' in sheets + chat, element_id
    assert "innerHTML" not in GROUPS                    # names go in as text
    for name in ("groupInfo", "createGroup", "addToGroup", "removeFromGroup", "setGroupRole", "renameGroup",
                 "leaveGroup", "acceptGroup", "declineGroup", "markGroupRead", "searchPeople"):
        assert f"export const {name}" in API or f"export async function {name}" in API, name
    assert 'import { initGroups } from "./groups.js";' in INDEX
    assert "escapeHtml(convo.label)" in UI and "escapeHtml(group.label)" in UI


def test_both_stylesheets_draw_the_group_parts():
    for name in ("main.scss", "main_dark.scss"):
        css = read("ruqqus", "assets", "style", name)
        for selector in (".chat-group-avatar", ".chat-sender", ".chat-people-list", ".chat-person + .chat-person", ".chat-chip"):
            assert selector in css, (name, selector)


def test_a_message_that_cant_be_decrypted_never_shows_the_libraries_text():
    text_of = INDEX.split("function textOf(", 1)[1].split("\n}", 1)[0]
    assert text_of.index("isDecryptionFailure()") < text_of.index("getContent().body")
