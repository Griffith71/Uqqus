"""Guild channels wired together (routes/guild_channels.py, helpers/matrix_client.py, routes/chat.py, the chat client, the
guild page, the schema and the docs). The rules and the SQL are in tests/test_guild_channels.py; here the promises are the
places that must stay in step: a channel is read through its guild's visibility and ladder, a message goes only through
the site and is signed, the room is not encrypted, the webhook removes whatever the site did not send, every change is a
POST with the form key and a mod-log entry, and the schema, the migration and the docs agree."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


ROUTES = read("ruqqus", "routes", "guild_channels.py")


def func(source, name):
    return source.split(f"def {name}(", 1)[1].split("\n@app.route", 1)[0]


def test_every_change_is_a_post_with_the_form_key_and_the_right_permission():
    posts = re.findall(r'@app\.route\("([^"]+)", methods=\["POST"\]\)\n((?:@\w[^\n]*\n)+)def (\w+)', ROUTES)
    names = {name for _, _, name in posts}
    assert names == {"guild_channel_join", "guild_channel_message", "guild_channel_mark_read", "guild_channel_report",
                     "guild_channel_remove", "guild_channel_create", "guild_channel_edit", "guild_channel_delete",
                     "guild_channel_order", "guild_channel_mute", "guild_channel_unmute", "guild_channel_report_act",
                     # part 2
                     "guild_channel_pin", "guild_channel_mute_for_me", "guild_channel_accept_rules", "guild_category_create",
                     "guild_category_rename", "guild_category_delete", "guild_category_order",
                     # part 5
                     "guild_automod_save", "guild_feed_add", "guild_feed_remove"}
    for path, decorators, name in posts:
        assert "@validate_formkey" in decorators, name
        assert "@auth_required" in decorators or "@is_not_banned" in decorators, name
    config = {"guild_channel_create", "guild_channel_edit", "guild_channel_delete", "guild_channel_order",
              "guild_category_create", "guild_category_rename", "guild_category_delete", "guild_category_order",
              "guild_automod_save", "guild_feed_add", "guild_feed_remove"}
    content = {"guild_channel_mute", "guild_channel_unmute", "guild_channel_report_act"}
    for path, decorators, name in posts:
        if name in config:
            assert '@is_guildmaster("config")' in decorators, name
        if name in content:
            assert '@is_guildmaster("content")' in decorators, name
    # reading and the pages
    assert '@app.route("/+<guildname>/chat", methods=["GET"])\n@auth_desired' in ROUTES
    assert '@app.route("/api/guilds/<guildname>/channels", methods=["GET"])\n@auth_required' in ROUTES
    assert '@app.route("/api/guilds/channels/<int:cid>/senders", methods=["GET"])\n@auth_required' in ROUTES


def test_every_read_goes_through_the_guild_and_the_ladder():
    load = func(ROUTES, "_load")
    assert "store.one_for(g.db, cid, v.id)" in load and "_read_refusal(row, v, _hidden(board, v, row))" in load
    assert "board.is_banned and v.admin_level < 3" in load and "abort(404)" in load
    refusal = func(ROUTES, "_read_refusal")
    for needle in ("rules.guild_visible(", "hidden=hidden", "bot=bool(v.is_bot)", "exiled=bool(row.exiled) and v.admin_level < 2"):
        assert needle in refusal, needle
    # the routes that act on one channel all start with _load; the listing and the page filter by the same rule
    for name in ("guild_channel_join", "guild_channel_message", "guild_channel_mark_read", "guild_channel_senders",
                 "guild_channel_report", "guild_channel_remove", "guild_channel_pin", "guild_channel_pins",
                 "guild_channel_mute_for_me", "guild_channel_accept_rules", "guild_channel_search"):
        assert "_load(cid, v)" in func(ROUTES, name), name
    assert "_read_refusal(row, v, _hidden(board, v, row))" in func(ROUTES, "guild_channels_list")
    assert "_read_refusal(row, v, _hidden(board, v, row))" in func(ROUTES, "conversations_for")
    assert "_visible_board(guildname, v)" in func(ROUTES, "guild_chat")
    assert "board.can_view(v)" in func(ROUTES, "_visible_board")


def test_a_message_is_checked_rated_signed_and_sent_by_the_site():
    message = func(ROUTES, "guild_channel_message")
    order = ["_load(cid, v)", "rules.clean_message(", "_write_refusal(row, v, None)", "store.burst_count(", "store.posts_since(",
             "store.slow_wait(", "store.new_nonce(r)", "_severity(body)", "rules.site_mark(", "matrix.send_as(",
             "store.record_post(", "store.mark_read(", "g.db.commit()"]
    positions = [message.index(item) for item in order]
    assert positions == sorted(positions), order
    assert "_slow_exempt(row)" in message and "rules.slow_exempt(row.mod)" in func(ROUTES, "_slow_exempt")   # guildmasters are not slowed
    assert "rules.SEVERITY_KEY: severity" in message and "rules.NONCE_KEY: nonce" in message
    assert "matrix.mxid_for(v.id)" in message and "store.slow_release(r, cid, v.id)" in message
    assert "rules.quoted_event_id(" in message                         # a quote is an event id, never free text
    # nothing but the site writes: the room is not encrypted, so the site could read what is said in it
    create = read("ruqqus", "helpers", "matrix_client.py").split("def create_guild_channel_room(", 1)[1].split("\ndef ", 1)[0]
    # unencrypted unless the channel is an encrypted one of a private or Circle guild (rules.encrypted_refusal)
    assert "encrypted: bool = False" in read("ruqqus", "helpers", "matrix_client.py").split("def create_guild_channel_room(")[1]
    assert create.index("if encrypted:") < create.index("m.room.encryption") and '"history_visibility": "shared"' in create
    assert "guild_channel_power_levels(announcement, writer_mxids)" in create
    levels = read("ruqqus", "helpers", "matrix_client.py").split("def guild_channel_power_levels(", 1)[1].split("\ndef ", 1)[0]
    assert "group_power_levels()" in levels and 'levels["events_default"] = 50' in levels
    assert '"m.sticker": 100' in levels


def test_the_webhook_removes_whatever_the_site_did_not_send():
    chat = read("ruqqus", "routes", "chat.py")
    guard = func(chat, "_guard_room_event")
    assert guard.index("guild_channel_store.by_room(g.db, room_id)") < guard.index("broadcast_store.by_room(g.db, room_id)")
    assert "_guard_guild_channel(guild_channel, event)" in guard
    channel = chat.split("def _guard_guild_channel(", 1)[1].split("\n@app.route", 1)[0]
    for needle in ("guild_channel_rules.mark_ok(app.config[\"SECRET_KEY\"], room_id, sender, content)",
                   "guild_channel_store.claim_nonce(r, nonce, event_id)", "guild_channel_rules.reaction_ok(",
                   "guild_channel_store.remove_post(g.db, channel.id, target, now)", "remove(\"Not allowed in a channel\")"):
        assert needle in channel, needle
    # a removed message must not move the channel's last activity: touch comes only after the signature held
    assert channel.index("claim_nonce") < channel.index("guild_channel_store.touch(g.db, room_id, now, _user_id_for_mxid(sender))")
    assert "store.touch(" not in func(ROUTES, "guild_channel_message")                # the webhook is the one count
    # the list in the chat page
    assert 'if status == "guild":' in chat and "conversations_for(v)" in chat


def test_the_mod_log_names_the_writer_and_the_channel_but_never_the_text():
    remove = func(ROUTES, "_remove_message")
    assert '_log(board, v, "channel_message_remove", f"#{row.name}", target_user_id=post[1])' in remove
    assert "body" not in remove.split("_log(")[1].split("\n")[0]
    logs = read("ruqqus", "classes", "mod_logs.py")
    for kind in ("channel_create", "channel_edit", "channel_delete", "channel_message_remove"):
        assert f'"{kind}":{{' in logs, kind
    for kind in ("channel_create", "channel_edit", "channel_delete"):
        assert f'"{kind}"' in ROUTES
    assert '"chatban_user"' in ROUTES and '"unchatban_user"' in ROUTES      # the old kinds, for the guild-wide mute


def test_losing_the_right_to_a_room_takes_you_out_of_it():
    boards = read("ruqqus", "routes", "boards.py")
    for name in ("unsubscribe_board", "mod_ban_bid_user", "mod_unapprove_bid_user"):
        assert "guild_channel_access.remove_from_rooms(g.db, board.id," in func(boards, name), name
    access = read("ruqqus", "helpers", "guild_channel_access.py")
    assert "matrix.kick_as_bot(" in access and "begin_nested()" in access           # best effort, never in the way
    assert "guild_channel_sync.run(db, now, matrix.kick_as_bot, matrix.set_writers, matrix.mxid_for" in read("scripts", "recomputes.py")
    assert "guild_channel_store.rescan_severities(db, f.severity)" in read("ruqqus", "helpers", "word_filter_store.py")


def test_the_pages_and_the_tab():
    tabs = read("ruqqus", "templates", "partials", "guild_tabs.html")
    assert "guild_has_chat(b, v)" in tabs and 'href="{{ b.permalink }}/chat"' in tabs
    assert "app.jinja_env.globals.update(guild_has_chat=has_chat)" in ROUTES
    assert 'href="/+{{ b.name }}/mod/channels"' in read("ruqqus", "templates", "board.html")
    settings = read("ruqqus", "templates", "guild", "guild_settings.html")
    assert settings.count('href="/+{{ b.name }}/mod/channels"') == 2                 # desktop and phone
    page = read("ruqqus", "templates", "guild", "chat.html")
    assert '{% extends "guild/guild_tab_page.html" %}' in page and "/chat?embed=1&amp;single=1&amp;room=" in page
    assert "| safe" not in page                                                      # names, topics and rules are escaped
    manage = read("ruqqus", "templates", "guild", "channels.html")
    assert "| safe" not in manage
    assert manage.count('name="formkey" value="{{ v.formkey }}"') >= 8               # every form carries the key
    chat_page = read("ruqqus", "templates", "chat.html")
    assert 'data-chat-tab="guild"' in chat_page and "chat-single" in chat_page
    for sheet in ("main.scss", "main_dark.scss"):
        css = read("ruqqus", "assets", "style", sheet)
        for selector in (".guild-chat-channel", ".guild-chat-frame", ".guild-chat-dot", "#chat #chat-app.chat-single #chat-list-pane"):
            assert selector in css, (sheet, selector)
        assert css.index(".guild-chat-frame") < css.index("// Create post card")      # guild CSS goes before the marker


def test_the_chat_client_writes_through_the_site_and_stays_quiet_in_a_big_room():
    index = read("ruqqus", "assets", "chat_src", "src", "index.js")
    send = index.split("function onSend(", 1)[1].split("\nasync function onAccept", 1)[0]
    assert send.index("postToGuildChannel(") < send.index("state.locked")           # not encrypted: nothing to unlock
    assert "state.active.gchannel" in send
    assert "if (!gchannel) state.client.sendReadReceipt(" in index                  # no receipts to a whole guild
    assert "state.active.isRequest || state.active.gchannel) return;" in index      # and no typing
    assert "canEdit: mine && !deleted && (!gchannel || gchannel.encrypted)" in index        # an edit would be unsigned (an encrypted room's is its own)
    assert "markGuildChannelRead(boot.formkey" in index and "joinGuildChannel(boot.formkey" in index
    assert "GUILD_LIST_MS" in index                                                 # the dearest list is not asked for every message
    assert "if (boot.single) return;" in index and "single: {{ 'true' if request.args.get('single') else 'false' }}" in \
        read("ruqqus", "templates", "chat.html")                                    # a frame for one channel has no keys to unlock
    assert "Direction.Backward" in index and "scrollback(room, 30)" in index and "onLoad: loadEarlier" in index
    assert "Load earlier messages" in read("ruqqus", "assets", "chat_src", "src", "ui.js")
    assert "events.filter((event) => !event.isRedacted() && !isThreadMessage(event) && !(infoOf(event)" in index          # no wall of "Message deleted"
    for sheet in ("main.scss", "main_dark.scss"):
        assert ".guild-chat-main {\n        flex: none;" in read("ruqqus", "assets", "style", sheet)   # stacked on a phone
    ui = read("ruqqus", "assets", "chat_src", "src", "ui.js")
    assert 'convo.kind === "guild_channel"' in ui and "handlers.onRemove" in ui
    assert "innerHTML = `" in ui and "escapeHtml(guildChannel.label)" in ui and "escapeHtml(convo.guild)" in ui
    helper = read("ruqqus", "assets", "chat_src", "src", "guild_channels.js")
    assert "guildChannelSenders(channelId, ids)" in helper and "innerHTML" not in helper


def test_schema_migration_and_docs_agree():
    migration = read("scripts", "migrations", "2026-11-04_guild_channels.sql")
    schema = read("schema.sql")
    assert "('2026-11-04_guild_channels.sql')" in schema
    block = schema.split("-- Guild channels (migration 2026-11-04_guild_channels.sql", 1)[1].split("-- Which migrations", 1)[0]
    for table in ("guild_channels", "guild_channel_reads", "guild_channel_posts", "guild_channel_reports"):
        create = re.search(rf"CREATE TABLE (?:IF NOT EXISTS )?public\.{table} \((.*?)\n\);", migration, re.S).group(1)
        mirrored = re.search(rf"CREATE TABLE public\.{table} \((.*?)\n\);", block, re.S).group(1)
        assert create.strip() == mirrored.strip(), table
    assert "from .guild_channels import *" in read("ruqqus", "routes", "__init__.py")
    docs = read("CLAUDE.md")
    assert "## Guild channels (legacy app)" in docs
    for needle in ("2026-11-04_guild_channels.sql", "not end-to-end encrypted", "site_mark", "No row per member",
                   "chatbans", "removed message's text"):
        assert needle in docs, needle


def test_part_two_threads_mentions_pins_search_and_the_navbar():
    message = func(ROUTES, "guild_channel_message")
    assert message.index("rules.thread_refusal(") < message.index("matrix.send_as(")
    assert '"rel_type": "m.thread"' in message and "store.record_post(g.db, cid, v.id, event_id, body, severity, now, thread)" in message
    # people are told only after the message is committed (send_notification commits on its own)
    assert message.index("_mentioned(row, board, v, post_id, body, now)") < message.index("g.db.commit()") < message.index("send_notification(target, text_)")
    mentioned = func(ROUTES, "_mentioned")
    for needle in ("v.any_block_exists(target)", "_read_refusal(standing, target,", "UserMute", "rules.is_muted(standing.muted_until, now)",
                   "store.notice_due(r, row.id, v.id, target.id)", "target.is_bot"):
        assert needle in mentioned, needle
    assert "store.clear_mentions(g.db, cid, v.id" in func(ROUTES, "guild_channel_mark_read")
    pin = func(ROUTES, "guild_channel_pin")
    assert "not _can_pin(row)" in pin and "rules.MAX_PINS" in pin and '"channel_pin"' in pin and '"channel_unpin"' in pin
    pins = func(ROUTES, "guild_channel_pins")
    assert "_visible_text(body, severity, v)" in pins and 'who["muted"]' in pins          # their filter and their mutes
    search = func(ROUTES, "guild_channel_search")
    assert "@limiter.limit(" in ROUTES.split("def guild_channel_search(")[0].rsplit("@app.route", 1)[1]
    for needle in ("rules.search_terms(", "text_hidden(severity or 0, v)", 'who["muted"] or who["blocked"]', "rules.KEEP_TEXT_DAYS"):
        assert needle in search, needle
    assert "rules.rules_key(row.rules)" in func(ROUTES, "guild_channel_accept_rules")
    assert "rules.mute_until(" in func(ROUTES, "guild_channel_mute_for_me")
    rules_src = read("ruqqus", "helpers", "guild_channels.py")
    assert "rules_pending and not mod" in rules_src                                     # a guildmaster is never asked
    # the navbar adds channels you have opened that have news, and mentions
    assert "guild_channel_store.unread_total(g.db, self.id, int(time.time()))" in read("ruqqus", "classes", "user.py")
    store_src = read("ruqqus", "helpers", "guild_channel_store.py")
    assert "FROM guild_channel_reads r JOIN guild_channels c" in store_src.split("def unread_total(")[1]
    for kind in ("channel_pin", "channel_unpin", "category_create", "category_edit", "category_delete"):
        assert f'"{kind}":{{' in read("ruqqus", "classes", "mod_logs.py"), kind
    assert "guild_channel_store.rescan_category_severities(db, f.severity)" in read("ruqqus", "helpers", "word_filter_store.py")


def test_part_two_in_the_chat_client_and_pages():
    index = read("ruqqus", "assets", "chat_src", "src", "index.js")
    ui = read("ruqqus", "assets", "chat_src", "src", "ui.js")
    helper = read("ruqqus", "assets", "chat_src", "src", "guild_channels.js")
    assert 'rel_type === "m.thread"' in index and "!isThreadMessage(event)" in index                  # threads are not among the rest
    assert 'client.relations(active.room.roomId, rootId, "m.thread"' in index
    assert 'postToGuildChannel(boot.formkey, active.conversation.channel_id, text, null, rootId)' in index
    assert "state.active.itemOf = itemOf" in index and "threadable: false, threadCount: 0, pinnable: false" in index
    assert "onAcceptRules" in index and "I agree to the rules" in ui and "guildChannel.needs_rules" in ui
    assert "mentionPattern(boot.username)" in index and "chat-mention" in ui
    assert "innerHTML" not in helper                                                                  # panels use textContent
    assert "Searches the last" in helper and "Turn notifications back on" in helper
    assert "if (handlers.onQuote)" in ui and "handlers.onThread" in ui and "handlers.onPin" in ui
    for sheet, background in (("main.scss", "$white"), ("main_dark.scss", "$gray-600")):
        css = read("ruqqus", "assets", "style", sheet)
        assert f"z-index: 5;\n    flex-direction: column;\n    background-color: {background};" in css, sheet
        for selector in (".chat-panel-row", ".chat-mention", ".chat-thread-link", ".guild-chat-badge"):
            assert selector in css, (sheet, selector)
    manage = read("ruqqus", "templates", "guild", "channels.html")
    assert 'name="rules_gate"' in manage and manage.count("category_select(") >= 3 and "/mod/channels/categories/" in manage
    page = read("ruqqus", "templates", "guild", "chat.html")
    assert "for section in sections" in page and "guild-chat-badge" in page


def test_part_two_schema_and_migration_agree():
    migration = read("scripts", "migrations", "2026-11-05_guild_channels_polish.sql")
    schema = read("schema.sql")
    assert "('2026-11-05_guild_channels_polish.sql')" in schema
    block = schema.split("-- Guild channels, part 2 (migration 2026-11-05_guild_channels_polish.sql", 1)[1].split("-- Which migrations", 1)[0]
    for table in ("guild_channel_categories", "guild_channel_pins", "guild_channel_prefs", "guild_channel_rules_ack",
                  "guild_channel_mentions"):
        create = re.search(rf"CREATE TABLE (?:IF NOT EXISTS )?public\.{table} \((.*?)\n\);", migration, re.S).group(1)
        mirrored = re.search(rf"CREATE TABLE public\.{table} \((.*?)\n\);", block, re.S).group(1)
        assert create.strip() == mirrored.strip(), table
    for column in ("thread_event_id character varying(255) DEFAULT '' NOT NULL", "category_id integer REFERENCES public.guild_channel_categories(id) ON DELETE SET NULL",
                   "rules_gate boolean DEFAULT false NOT NULL"):
        assert column in migration and column in block, column
    docs = read("CLAUDE.md")
    for needle in ("2026-11-05_guild_channels_polish.sql", "2026-11-06_guild_channels_unread.sql", "Threads", "Pinned messages", "accept the rules"):
        assert needle in docs, needle


def test_unread_is_a_count_of_messages():
    store_src = read("ruqqus", "helpers", "guild_channel_store.py")
    assert "activity_seq = activity_seq + 1" in store_src and "last_read_seq" in store_src.split("def mark_read(")[1].split("\ndef ")[0]
    migration = read("scripts", "migrations", "2026-11-06_guild_channels_unread.sql")
    schema = read("schema.sql")
    assert "('2026-11-06_guild_channels_unread.sql')" in schema
    for column in ("activity_seq bigint DEFAULT 0 NOT NULL", "last_read_seq bigint DEFAULT 0 NOT NULL"):
        assert column in migration and column in schema, column


def test_part_three_voice_and_stage_channels():
    calls = read("ruqqus", "routes", "calls.py")
    # a voice channel is one more kind of room the call routes know: asked again at every join and heartbeat
    assert "return voice_room(v, room_id)" in func(calls, "chat_room")
    join = func(calls, "call_join")
    order = ["voice = room[\"kind\"] == \"voice\"", "channels.voice_blocked(v, people)", "channels.voice_joins_ok(v, now)",
             "channels.voice_enter(v, room)", "call_store.join(r, room_id, v.id, device_id, kind, video, now)"]
    positions = [join.index(item) for item in order]
    assert positions == sorted(positions), order
    assert "call_rules.AUDIO if voice else" in join                                  # nobody sends video in a voice channel
    assert "current is None and not voice" in join and "call_rules.VOICE_FULL" in join
    assert "    if voice:\n        pass" in join                                      # nobody is rung or told
    assert "isVoice" in func(calls, "call_window") and "room[\"kind\"] != \"pair\"" in func(calls, "call_window")
    heartbeat = func(calls, "call_heartbeat")
    assert "call_store.refresh(r, room_id)" in heartbeat and "voice_max_seconds()" in heartbeat
    leave = func(calls, "call_leave")
    assert "channels.voice_exit(v, room)" in leave and 'None if room["kind"] == "voice" else summary' in leave
    assert "channels.voice_who(room, roster, v)" in func(calls, "call_status")
    voice = read("ruqqus", "routes", "guild_channels.py").split("def voice_room(")[1].split("\ndef voice_blocked(")[0]
    for needle in ("_read_refusal(row, v, _hidden(board, v, row))", "_write_refusal(row, v, None)", "channel.kind != rules.VOICE",
                   "board.is_banned and v.admin_level < 3"):
        assert needle in voice, needle
    assert "UserBlock" in read("ruqqus", "routes", "guild_channels.py").split("def voice_blocked(")[1].split("\ndef ")[0]
    # a voice room is the silent encrypted one calls use; a stage has no room
    new_room = func(read("ruqqus", "routes", "guild_channels.py"), "_new_room")
    assert "matrix.create_space_room()" in new_room and 'f"stage:{secrets.token_hex(12)}"' in new_room
    assert "voice_stale(db, channel.id, roster_of(channel.matrix_room_id)" in read("ruqqus", "helpers", "guild_channel_sync.py")
    assert "roster_of" in read("scripts", "recomputes.py")
    # nothing is typed in a room that has no messages
    for name, guard in (("guild_channel_join", "_messages_only(row)"), ("guild_channel_message", "_plaintext_only(row)"),
                        ("guild_channel_pin", "_plaintext_only(row)")):
        assert guard in func(read("ruqqus", "routes", "guild_channels.py"), name), name
    # the client: a card, the same call window
    index = read("ruqqus", "assets", "chat_src", "src", "index.js")
    assert "renderVoiceCard(pane, conversation, () => startCall(conversation.room_id, \"audio\"))" in index
    assert "openGuildCard(conversation)" in index and "stopCards()" in func_js(index, "closeThread")
    assert "You're the only one here." in read("ruqqus", "assets", "chat_src", "src", "call.js")
    page = read("ruqqus", "templates", "guild", "chat.html")
    assert 'selected.channel_kind == "stage"' in page and "| safe" not in page
    migration = read("scripts", "migrations", "2026-11-07_guild_voice.sql")
    schema = read("schema.sql")
    assert "('2026-11-07_guild_voice.sql')" in schema
    for line in ("CHECK (kind IN ('text', 'announcement', 'voice', 'stage'))", "ADD COLUMN IF NOT EXISTS space_post_id integer"):
        assert line in migration, line
    assert "ADD COLUMN space_post_id integer" in schema
    docs = read("CLAUDE.md")
    for needle in ("2026-11-07_guild_voice.sql", "Voice channels", "Stage channels"):
        assert needle in docs, needle


def func_js(source, name):
    return source.split(f"function {name}(", 1)[1].split("\n}\n", 1)[0]


def test_part_four_roles():
    roles = read("ruqqus", "routes", "guild_roles.py")
    found = re.findall(r'@app\.route\("([^"]+)", methods=\["(\w+)"\]\)\n((?:@\w[^\n]*\n)+)def (\w+)', roles)
    assert {name for *_, name in found} == {"guild_roles_page", "guild_role_create", "guild_role_edit", "guild_role_delete",
                                            "guild_role_order", "guild_role_add", "guild_role_remove"}
    for path, method, decorators, name in found:
        assert '@is_guildmaster("config")' in decorators, name                      # roles are guild configuration
        if method == "POST":
            assert "@validate_formkey" in decorators, name
    add = func(roles, "guild_role_add")
    for needle in ("channel_store.standing_sets(", "rules.member_refusal(", "target.is_bot", "_log(board, v, \"role_assign\""):
        assert needle in add, needle
    assert '"role_unassign"' in func(roles, "guild_role_remove") and '"role_create"' in roles and '"role_delete"' in roles
    logs = read("ruqqus", "classes", "mod_logs.py")
    for kind in ("role_create", "role_edit", "role_delete", "role_assign", "role_unassign"):
        assert f'"{kind}":{{' in logs, kind
    assert "from .guild_roles import *" in read("ruqqus", "routes", "__init__.py")

    # what a role says is read where the channel is read, by the same rule everywhere
    channels = read("ruqqus", "routes", "guild_channels.py")
    assert "role_read=role_rules.resolve(row.role_allow_read, row.role_deny_read)" in func(channels, "_read_refusal")
    assert "role_write=role_rules.resolve(row.role_allow_write, row.role_deny_write)" in func(channels, "_write_refusal")
    assert "role_read=guild_roles.resolve(" in read("ruqqus", "helpers", "guild_channel_store.py")
    assert "role_store.set_overwrites(g.db, board.id, cid, overwrites)" in func(channels, "guild_channel_edit")
    assert "_can_remove(row)" in func(channels, "guild_channel_remove") and "_can_pin(row)" in func(channels, "guild_channel_pin")
    assert "_slow_exempt(row)" in func(channels, "guild_channel_message")
    assert "role_store.top_roles(g.db, board_id" in func(channels, "_sender_map")
    store_src = read("ruqqus", "helpers", "guild_channel_store.py")
    for column in ("role_allow_read", "role_deny_read", "role_allow_write", "role_deny_write", "role_slow_exempt", "role_remove", "role_pin"):
        assert f"AS {column}" in store_src, column
    # leaving the guild or being exiled takes the roles; unapproving doesn't
    boards = read("ruqqus", "routes", "boards.py")
    assert "remove_from_rooms(g.db, board.id, v.id, drop_roles=True)" in func(boards, "unsubscribe_board")
    assert "drop_roles=True" in func(boards, "mod_ban_bid_user") and "drop_roles" not in func(boards, "mod_unapprove_bid_user")
    assert "guild_role_store.rescan_severities(db, f.severity)" in read("ruqqus", "helpers", "word_filter_store.py")

    # the pages and the client
    page = read("ruqqus", "templates", "guild", "roles.html")
    assert "| safe" not in page and page.count('name="formkey" value="{{ v.formkey }}"') >= 6 and "flair-{{ role.colour }}" in page
    assert 'href="/+{{ b.name }}/mod/roles"' in read("ruqqus", "templates", "board.html")
    assert read("ruqqus", "templates", "guild", "guild_settings.html").count('href="/+{{ b.name }}/mod/roles"') == 2
    manage = read("ruqqus", "templates", "guild", "channels.html")
    assert 'name="ow_read_{{ role.id }}"' in manage and 'name="ow_write_{{ role.id }}"' in manage
    index = read("ruqqus", "assets", "chat_src", "src", "index.js")
    ui = read("ruqqus", "assets", "chat_src", "src", "ui.js")
    assert "gchannel.can_remove" in index and "gchannel.can_pin" in index and "senderRole:" in index
    assert "/^[a-z]{1,12}$/.test(item.senderRole.colour" in ui and "flair-${item.senderRole.colour}" in ui

    migration = read("scripts", "migrations", "2026-11-08_guild_roles.sql")
    schema = read("schema.sql")
    assert "('2026-11-08_guild_roles.sql')" in schema
    block = schema.split("-- Guild roles (migration 2026-11-08_guild_roles.sql", 1)[1].split("-- Which migrations", 1)[0]
    for table in ("guild_roles", "guild_role_members", "guild_channel_overwrites"):
        create = re.search(rf"CREATE TABLE (?:IF NOT EXISTS )?public\.{table} \((.*?)\n\);", migration, re.S).group(1)
        mirrored = re.search(rf"CREATE TABLE public\.{table} \((.*?)\n\);", block, re.S).group(1)
        assert create.strip() == mirrored.strip(), table
    docs = read("CLAUDE.md")
    for needle in ("2026-11-08_guild_roles.sql", "allow beats deny", "## Guild channels"):
        assert needle in docs, needle


def test_part_five_automod_feeds_and_encrypted_channels():
    channels = read("ruqqus", "routes", "guild_channels.py")
    message = func(channels, "guild_channel_message")
    # the guild's own rules come after the write right and before anything is counted or sent; guildmasters are exempt
    assert message.index("_write_refusal(row, v, None)") < message.index("automod.refusal(settings,") < message.index("store.burst_count(")
    assert "not row.mod" in message.split("automod.refusal(")[0].rsplit("settings = ", 1)[1]
    assert '"automod_settings"' in func(channels, "guild_automod_save")
    feed = func(channels, "guild_feed_add")
    for needle in ("row.kind != rules.ANNOUNCEMENT", "bot.is_bot", "status = 'approved'", "rules.MAX_FEEDS", "store.latest_bot_post("):
        assert needle in feed, needle                                           # only new posts, only a running bot
    # an encrypted channel: only where the site can't read it, and it says what it lacks
    assert "rules.encrypted_refusal(kind, encrypted, bool(board.is_private or board.is_circle)" in func(channels, "guild_channel_create")
    assert "rules.encrypted_refusal(row.kind, bool(row.encrypted), True" in func(channels, "guild_channel_edit")
    for name in ("guild_channel_message", "guild_channel_pin", "guild_channel_report", "guild_channel_search"):
        assert "_plaintext_only(row)" in func(channels, name), name
    assert "rules.full_refusal(store.size(g.db, cid), bool(row.encrypted))" in func(channels, "guild_channel_join")
    assert "store.members_among(g.db, cid, ids) if row.encrypted" in func(channels, "guild_channel_senders")
    assert "matrix.event_sender(" in func(channels, "_remove_encrypted_message") and "matrix.redact_as_bot(" in func(channels, "_remove_encrypted_message")
    assert "store.encrypted_rooms_of(g.db, board.id, target.id)" in func(channels, "guild_channel_mute")
    chat = read("ruqqus", "routes", "chat.py")
    guard = func(chat, "_guard_encrypted_guild_channel")
    for needle in ('kind != "m.room.encrypted"', "guild_channel_store.burst_count(", "guild_channel_rules.PER_MINUTE", "remove(\"Not allowed in an encrypted channel\")"):
        assert needle in guard, needle
    assert "if channel.encrypted:\n        _guard_encrypted_guild_channel(channel, event, now, remove)" in func(chat, "_guard_guild_channel")
    # the feeds run as a step of recomputes, as the bot, signed and rated like anyone's message
    feeds = read("ruqqus", "helpers", "guild_channel_feeds.py")
    for needle in ("rules.site_mark(secret, room, mxid, severity, nonce, body)", "store.set_feed_cursor(", "rules.FEED_PER_DAY", "adopt_existing=False"):
        assert needle in feeds, needle
    assert "guild_channel_feeds.run(" in read("scripts", "recomputes.py")
    # the client writes an encrypted channel like a group
    index = read("ruqqus", "assets", "chat_src", "src", "index.js")
    assert "state.active.gchannel && !state.active.gchannel.encrypted" in index and "threadable: !!(gchannel && !gchannel.encrypted" in index
    page = read("ruqqus", "templates", "guild", "chat.html")
    assert "selected.encrypted" in page and "/chat?room={{ selected.room_id | urlencode }}" in page
    manage = read("ruqqus", "templates", "guild", "channels.html")
    assert 'name="encrypted"' in manage and "/mod/channels/automod" in manage and "/feeds/add" in manage and "| safe" not in manage
    logs = read("ruqqus", "classes", "mod_logs.py")
    for kind in ("automod_settings", "channel_feed_add", "channel_feed_remove"):
        assert f'"{kind}":{{' in logs, kind
    migration = read("scripts", "migrations", "2026-11-09_guild_channels_extras.sql")
    schema = read("schema.sql")
    assert "('2026-11-09_guild_channels_extras.sql')" in schema and "ADD COLUMN IF NOT EXISTS encrypted boolean" in migration
    block = schema.split("-- Guild channels, part 5", 1)[1].split("-- Which migrations", 1)[0]
    for table in ("guild_chat_settings", "guild_channel_feeds"):
        create = re.search(rf"CREATE TABLE (?:IF NOT EXISTS )?public\.{table} \((.*?)\n\);", migration, re.S).group(1)
        mirrored = re.search(rf"CREATE TABLE public\.{table} \((.*?)\n\);", block, re.S).group(1)
        assert create.strip() == mirrored.strip(), table
    docs = read("CLAUDE.md")
    for needle in ("2026-11-09_guild_channels_extras.sql", "Auto-moderation", "subscriber lounge", "End-to-end encrypted channels"):
        assert needle in docs, needle
