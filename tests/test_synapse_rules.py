"""The homeserver's own copy of the site's rules (synapse/modules/uqqus_rules.py): only the site's bot makes rooms,
invites, names rooms, sends room state and names accounts; nobody publishes a room, uploads to the media store or starts
a call from an outside app; a member joins only a room they were invited to or are in. An outside Matrix app talks to
the homeserver directly, so these must hold there and not only in the site's routes. The rules are checked here
without Synapse; homeserver.yaml and the compose file must load them; and the site's own chat client must never need
what the module refuses."""
import asyncio
import importlib.util
import re
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BOT = "@ruqqus_bot:ruqqus.local"
MEMBER = "@ruqqus_12:ruqqus.local"


def load():
    spec = importlib.util.spec_from_file_location("uqqus_rules", ROOT / "synapse" / "modules" / "uqqus_rules.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rules = load()


def test_only_the_bot_makes_rooms_invites_names_rooms_and_sends_state():
    for check in (rules.may_create_room, rules.may_invite, rules.may_create_alias, rules.may_send_state):
        assert check(BOT, BOT) is True, check.__name__
        assert check(MEMBER, BOT) is False, check.__name__
        assert check("@ruqqus_bot:elsewhere", BOT) is False, check.__name__


def test_a_member_joins_only_a_room_they_were_invited_to_or_are_in():
    assert rules.may_join(MEMBER, True, BOT) is True
    assert rules.may_join(MEMBER, False, BOT) is False
    assert rules.may_join(BOT, False, BOT) is True
    assert rules.may_join(MEMBER, False, BOT, already_joined=True) is True, "a new chat name reaches each room as a join"


def test_nobody_publishes_a_room_or_uploads_a_file():
    assert rules.may_publish(BOT, BOT) is False and rules.may_publish(MEMBER, BOT) is False
    assert rules.may_upload_media() is False


def test_nobody_but_the_bot_sends_call_or_call_widget_events():
    for kind in ("m.call.invite", "m.call.answer", "org.matrix.msc3401.call.member", "org.matrix.msc4143.rtc.member",
                 "m.rtc.member", "io.element.call.encryption_keys", "im.vector.modular.widgets", "m.widget"):
        assert rules.may_send_event(kind, MEMBER, BOT) is False, kind
        assert rules.may_send_event(kind, BOT, BOT) is True, kind
    for kind in ("m.room.message", "m.room.encrypted", "m.reaction", "m.room.redaction", "org.uqqus.call.signal", None):
        assert rules.may_send_event(kind, MEMBER, BOT) is True, kind


def test_only_the_bot_names_accounts_and_only_site_accounts():
    assert rules.may_name(BOT, True, BOT) and not rules.may_name(BOT, False, BOT) and not rules.may_name(MEMBER, True, BOT)
    ok = dict(bot=BOT, user_prefix="ruqqus_", server_name="ruqqus.local")
    assert rules.name_refusal(MEMBER, "alice", **ok) is None and rules.name_refusal(BOT, "Laba", **ok) is None
    for user, name in (("@someone:ruqqus.local", "x"), ("@ruqqus_12:elsewhere", "x"), ("@ruqqus_x:ruqqus.local", "x"),
                       (MEMBER, ""), (MEMBER, "   "), (MEMBER, "a" * 101), (MEMBER, 5), (MEMBER, "line" + chr(10) + "break"),
                       (MEMBER, "al" + chr(0x202e) + "ice"), (MEMBER, "zero" + chr(0x200b) + "width"),
                       (MEMBER, chr(0xfeff) + "bom")):
        assert rules.name_refusal(user, name, **ok), (user, name)
    assert rules.NAME_PATH == "/_synapse/client/uqqus/display_name"


def test_the_config_names_plain_localparts():
    assert rules.parse_config({}) == {"bot_localpart": "ruqqus_bot", "user_prefix": "ruqqus_"}
    assert rules.parse_config({"bot_localpart": "site_bot", "user_prefix": "u_"}) == {"bot_localpart": "site_bot",
                                                                                      "user_prefix": "u_"}
    for bad in ("", "@ruqqus_bot", "ruqqus_bot:ruqqus.local", 5):
        with pytest.raises(ValueError):
            rules.parse_config({"bot_localpart": bad})
    assert rules.bot_id("ruqqus_bot", "ruqqus.local") == BOT


class FakeApi:
    server_name = "ruqqus.local"

    def __init__(self):
        self.callbacks = {}
        self.resources = {}

    def register_spam_checker_callbacks(self, **callbacks):
        self.callbacks.update(callbacks)

    def register_web_resource(self, path, resource):
        self.resources[path] = resource


@pytest.fixture
def module(monkeypatch):
    errors = types.ModuleType("synapse.api.errors")
    errors.Codes = types.SimpleNamespace(FORBIDDEN="M_FORBIDDEN", INVALID_PARAM="M_INVALID_PARAM")
    errors.SynapseError = Exception
    module_api = types.ModuleType("synapse.module_api")
    module_api.DirectServeJsonResource = type("DirectServeJsonResource", (), {})
    module_api.UserID = object
    module_api.parse_json_object_from_request = None
    for name, value in (("synapse", types.ModuleType("synapse")), ("synapse.api", types.ModuleType("synapse.api")),
                        ("synapse.api.errors", errors), ("synapse.module_api", module_api)):
        monkeypatch.setitem(sys.modules, name, value)
    api = FakeApi()
    rules.UqqusRules(rules.UqqusRules.parse_config({"bot_localpart": "ruqqus_bot"}), api)
    return api


def run(callback, *args):
    return asyncio.run(callback(*args))


def test_the_module_registers_every_callback_and_answers_as_synapse_expects(module):
    callbacks = module.callbacks
    assert set(module.resources) == {rules.NAME_PATH}
    assert set(callbacks) == {"user_may_create_room", "user_may_invite", "user_may_send_3pid_invite", "user_may_join_room",
                              "user_may_publish_room", "user_may_create_room_alias", "user_may_send_state_event",
                              "check_media_file_for_spam", "check_event_for_spam"}
    assert run(callbacks["user_may_create_room"], BOT, {}) == "NOT_SPAM"
    assert run(callbacks["user_may_create_room"], MEMBER, {}) == "M_FORBIDDEN"
    assert run(callbacks["user_may_invite"], BOT, MEMBER, "!r") == "NOT_SPAM"
    assert run(callbacks["user_may_invite"], MEMBER, BOT, "!r") == "M_FORBIDDEN"
    assert run(callbacks["user_may_send_3pid_invite"], MEMBER, "email", "x@example.org", "!r") == "M_FORBIDDEN"
    assert run(callbacks["user_may_join_room"], MEMBER, "!r", True) == "NOT_SPAM"
    assert run(callbacks["user_may_publish_room"], BOT, "!r") == "M_FORBIDDEN"
    assert run(callbacks["user_may_create_room_alias"], MEMBER, "#x:ruqqus.local") == "M_FORBIDDEN"
    assert run(callbacks["user_may_send_state_event"], BOT, "!r", "m.room.power_levels", "", {}) == "NOT_SPAM"
    assert run(callbacks["user_may_send_state_event"], MEMBER, "!r", "m.room.member", MEMBER, {}) == "M_FORBIDDEN"
    assert run(callbacks["check_media_file_for_spam"], None, None) == "M_FORBIDDEN"
    assert run(callbacks["check_event_for_spam"], types.SimpleNamespace(type="m.call.invite", sender=MEMBER)) == "M_FORBIDDEN"
    assert run(callbacks["check_event_for_spam"], types.SimpleNamespace(type="m.room.encrypted", sender=MEMBER)) == "NOT_SPAM"


def test_an_uninvited_join_asks_the_room_whether_they_are_already_in(module):
    join = module.callbacks["user_may_join_room"]
    for membership, answer in (("join", "NOT_SPAM"), ("leave", "M_FORBIDDEN"), ("ban", "M_FORBIDDEN"), (None, "M_FORBIDDEN")):
        async def state(room_id, event_filter, membership=membership):
            assert list(event_filter) == [("m.room.member", MEMBER)]
            if membership is None:
                return {}
            return {("m.room.member", MEMBER): types.SimpleNamespace(membership=membership)}
        module.get_room_state = state
        assert run(join, MEMBER, "!r", False) == answer, membership


def test_the_homeserver_loads_the_module_for_the_appservices_bot_and_hides_people():
    config = (ROOT / "synapse" / "homeserver.yaml").read_text(encoding="utf-8")
    appservice = (ROOT / "synapse" / "ruqqus-appservice.yaml.template").read_text(encoding="utf-8")
    bot = re.search(r"^sender_localpart:\s*(\S+)", appservice, re.M).group(1)
    prefix = re.search(r'regex: "@(\w+)\.\*"', appservice).group(1)
    block = ("modules:\n  - module: uqqus_rules.UqqusRules\n    config:\n"
             f"      bot_localpart: {bot}\n      user_prefix: {prefix}\n")
    assert block in config, "the module must name the appservice's own bot and namespace"
    assert re.search(r"^user_directory:\n  enabled: false$", config, re.M)
    for line in ("require_auth_for_profile_requests: true", "limit_profile_requests_to_users_who_share_rooms: true",
                 "enable_set_displayname: false", "enable_set_avatar_url: false", "enable_registration: false",
                 "federation_domain_whitelist: []"):
        assert re.search("^" + re.escape(line) + "$", config, re.M), line
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    synapse = compose[compose.index("\n  synapse:\n"):compose.index("\n  coturn:\n")]
    assert '"./synapse/modules:/modules:ro"' in synapse and "PYTHONPATH=/modules" in synapse


def test_the_sites_chat_client_never_needs_what_the_module_refuses():
    """The browser (and so any outside app) only sends messages, reacts, takes back its own and joins rooms it was
    invited to. A new client feature that makes rooms, invites, names a room, sends state, changes a profile, uploads
    to the homeserver or sends a call event into a room would be refused by the homeserver: it has to go through the
    site's bot."""
    source = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "ruqqus" / "assets" / "chat_src" / "src").rglob("*.js"))
    for call in ("createRoom(", ".invite(", "sendStateEvent(", "setDisplayName(", "setAvatarUrl(", "uploadContent(",
                 "createAlias(", "setRoomDirectoryVisibility(", "setPowerLevel(", "setRoomName(", "setRoomTopic(",
                 '"m.call.', "EventType.Call", "msc3401", "msc4143"):
        assert call not in source, call
    # and the site's own calls that make rooms, invite or send state go as the bot itself (no ?user_id= masquerade)
    lines = (ROOT / "ruqqus" / "helpers" / "matrix_client.py").read_text(encoding="utf-8").splitlines()
    checked = 0
    for i, line in enumerate(lines):
        if re.search(r'f".*/_matrix/client/v3/(createRoom|rooms/\{\w+\}/(invite|state/))', line):
            assert "user_id=" not in line, line
            rest = []
            for later in lines[i + 1:]:
                if later.startswith("def "):
                    break
                rest.append(later)
            headers = re.findall(r"headers=([\w()]+)", "\n".join(rest))
            assert headers and set(headers) == {"_as_headers()"}, line
            checked += 1
    assert checked >= 6
