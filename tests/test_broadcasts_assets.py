"""Broadcast channels across the Matrix room, the routes, the webhook, the chat page and its bundle, the profile, the
job and the admin queue. The promises: only the owner and helpers can write, by the room's own power levels as well
as the routes; a public channel's messages go through the site (word filter, daily count, bans) with their rating, a
channel for circles never does (the site can't read it); a channel you may not see is a 404 and a block reads as
"isn't available"; people who leave a channel's circles are taken out; nobody is told who reported."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


ROUTES = read("ruqqus", "routes", "broadcasts.py")
MATRIX = read("ruqqus", "helpers", "matrix_client.py")
INDEX = read("ruqqus", "assets", "chat_src", "src", "index.js")
UI = read("ruqqus", "assets", "chat_src", "src", "ui.js")


def test_the_room_lets_only_writers_write():
    levels = MATRIX.split("def channel_power_levels", 1)[1].split("\ndef ", 1)[0]
    assert 'levels["events_default"] = 50' in levels and '"m.reaction": 0' in levels and '"m.room.redaction": 0' in levels
    assert "group_power_levels()" in levels                                   # the bot alone changes the room
    room = MATRIX.split("def create_channel_room", 1)[1].split("\ndef ", 1)[0]
    assert "if encrypted:" in room and '"history_visibility": "shared"' in room
    writers = MATRIX.split("def set_writers", 1)[1].split("\ndef ", 1)[0]
    assert "v != 50" in writers                                              # the creator and anyone above 50 stay


def test_routes_need_an_account_and_the_form_key():
    routes = re.findall(r'@app\.route\("([^"]+)", methods=\["(\w+)"\]\)\n((?:@[^\n]+\n)+)def (\w+)', ROUTES)
    assert len(routes) == 12
    for path, method, decorators, name in routes:
        assert "@auth_required" in decorators or "@is_not_banned" in decorators or "@admin_level_required(3)" in decorators, name
        if method == "POST":
            assert "@validate_formkey" in decorators, name
    assert "from .broadcasts import *" in read("ruqqus", "routes", "__init__.py")


def test_a_channel_you_may_not_see_is_a_404_and_a_block_is_not_named():
    channel = ROUTES.split("def _channel(", 1)[1].split("\ndef ", 1)[0]
    assert "abort(404)" in channel and "_may_see(channel, v)" in channel and "_blocked(channel, v)" in channel
    may_see = ROUTES.split("def _may_see(", 1)[1].split("\ndef ", 1)[0]
    assert "circle_guard.may_see(g.db, channel_rules.CIRCLES, channel[1], v, circle_ids=channel_store.circle_ids" in may_see
    join = ROUTES.split("def broadcast_join(", 1)[1].split("\n@app.route", 1)[0]
    assert "channel_rules.join_refusal(" in join and "matrix.put_in_room(" in join
    page = ROUTES.split("def profile_channels(", 1)[1].split("\n@app.route", 1)[0]
    assert "v.any_block_exists(owner)" in page and "user_hidden(owner, v)" in page and "_may_see(row, v)" in page


def test_a_public_channels_messages_go_through_the_site():
    message = ROUTES.split("def broadcast_message(", 1)[1].split("\n@app.route", 1)[0]
    assert "channel[4] != channel_rules.PUBLIC" in message                       # a channel for circles: never here
    assert "channel_rules.post_refusal(me[0], bool(v.is_suspended)" in message
    assert "get_filter(g.db).severity(body)" in message and "channel_rules.SEVERITY_KEY: severity" in message
    assert "matrix.send_as(" in message and "channel_store.record_post(" in message
    assert 'SEVERITY_KEY = "org.uqqus.severity"' in read("ruqqus", "helpers", "broadcasts.py")
    assert 'event.getContent()["org.uqqus.severity"]' in INDEX and "filtered: !!(channel && channel.public" in INDEX
    assert "postToChannel(boot.formkey, state.active.channel.channel_id, text)" in INDEX


def test_unread_and_the_chat_list():
    hook = read("ruqqus", "routes", "chat.py").split("def matrix_appservice_transaction", 1)[1].split("\n@app.route", 1)[0]
    assert "broadcast_store.by_room(g.db, room_id)" in hook and "UserMute.target_id == channel[1]" in hook
    assert "broadcast_store.unread_total(g.db, self.id)" in read("ruqqus", "classes", "user.py")
    chat = read("ruqqus", "templates", "chat.html")
    assert 'data-chat-tab="channel"' in chat and 'id="chat-channels-badge"' in chat and "filterLevel:" in chat
    assert 'listConversations("channel")' in INDEX


def test_members_read_and_react_but_never_get_a_message_box():
    assert 'form.classList.replace("d-flex", "d-none");' in UI                 # d-flex beats the hidden attribute
    assert "Only the channel's owner and helpers write here." in UI
    assert "reportable: !!(channel && channel.public && !mine && !deleted)" in INDEX


def test_the_profile_menu_the_job_and_the_admin_queue():
    header = read("ruqqus", "templates", "partials", "profile_header.html")
    assert header.count("/channels\"><i class=\"fas fa-tower-broadcast fa-fw\"></i>") == 2
    conf = read("supervisord.conf")
    job = conf.split("[program:ruqqusbroadcasts]")[1].split("[program:")[0]
    assert "scripts/sync_broadcasts.py" in job and 'environment=PYTHONPATH="/opt/ruqqus/service"' in job
    script = read("scripts", "sync_broadcasts.py")
    assert "circle_guard.may_see(" in script and "member.any_block_exists(owner)" in script and "matrix.kick_as_bot(" in script
    queue = read("ruqqus", "templates", "admin", "broadcast_reports.html")
    assert "reporter" not in queue and "Nobody is shown who reported" in queue
    assert "* [Reported channel messages](/admin/broadcast_reports)" in read("ruqqus", "templates", "admin", "admin_home.html")
    act = ROUTES.split("def admin_broadcast_report_act(", 1)[1]
    assert "matrix.redact_as_bot(" in act and 'action not in ("remove", "dismiss")' in act


def test_the_schema():
    migration = read("scripts", "migrations", "2026-10-25_broadcasts.sql")
    schema = read("schema.sql")
    for needle in ("CONSTRAINT broadcasts_audience_check CHECK (audience IN (0, 4))",
                   "CONSTRAINT broadcast_members_role_check CHECK (role IN ('owner', 'helper', 'member'))",
                   "CONSTRAINT broadcast_reports_once UNIQUE (post_id, reporter_id)"):
        assert needle in migration and needle in schema, needle
