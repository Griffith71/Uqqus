"""The site's rules, held by the homeserver itself (a Synapse module, configured in homeserver.yaml `modules:`).

The site's own chat client never makes a room, invites anyone, names or publishes a room, sends a room's state or
uploads a file to the homeserver: the site's bot (the appservice's sender) does all of that, after the site's own rules
(who may message whom, requests, blocks, group adds: ruqqus/routes/chat.py, chat_groups.py, broadcasts.py,
guild_channels.py, spaces.py). An outside Matrix app talks to the homeserver directly, so the same must hold here:

- only the bot makes rooms, invites (also by e-mail or phone), makes room addresses and sends state events;
- nobody publishes a room to the directory (there is none);
- a member joins only a room they are invited to (every join the site makes for a member follows the bot's invite)
  or are already in (a new chat name reaches each room as a join);
- nobody uploads to the homeserver's media store (chat attachments live on the member's own linked storage);
- nobody starts a call from an outside app (call and call-widget room events): calls go through the site, its rules
  and the relay's monthly allowance (the site's own calls are encrypted to-device messages, not room events);
- nobody changes their chat name: the site's bot sets each account's name to its @handle (a web resource only the
  appservice can call, POST /_synapse/client/uqqus/display_name), so an outside app shows who is who.

The decisions are plain functions so tests/test_synapse_rules.py can check them without Synapse. Server admins skip
Synapse's spam checks altogether (Synapse's own rule); the site has none.
"""

import re

NOT_SPAM = "NOT_SPAM"


def bot_id(localpart, server_name):
    return f"@{localpart}:{server_name}"


def may_create_room(user_id, bot):
    return user_id == bot


def may_invite(inviter, bot):
    return inviter == bot


def may_join(user_id, is_invited, bot, already_joined=False):
    """The bot; someone invited; or someone already in the room (Synapse sends a member's new chat name into every room
    they are in as a join, and a client may join a room it is in again: neither lets anyone in)."""
    return user_id == bot or bool(is_invited) or bool(already_joined)


def may_publish(user_id, bot):
    return False


def may_create_alias(user_id, bot):
    return user_id == bot


def may_send_state(user_id, bot):
    return user_id == bot


def may_upload_media():
    return False


CALL_EVENT_PREFIXES = ("m.call.", "org.matrix.call.", "org.matrix.msc3401.call", "org.matrix.msc4143.", "m.rtc.",
                       "io.element.call", "im.vector.modular.widgets", "m.widget")


def may_send_event(event_type, sender, bot):
    """Everything but call and call-widget events; those only from the bot (which sends none today)."""
    if sender == bot:
        return True
    return not (event_type or "").startswith(CALL_EVENT_PREFIXES)


NAME_PATH = "/_synapse/client/uqqus/display_name"
MAX_NAME = 100


def may_name(caller, is_appservice, bot):
    """Only the site's bot, calling with the appservice's own token, sets chat names."""
    return bool(is_appservice) and caller == bot


def _hidden(code):
    """Control characters and the invisible ones that would let a name pass for another (zero-width, direction)."""
    return (code < 32 or 0x7f <= code < 0xa0 or 0x200b <= code <= 0x200f or 0x202a <= code <= 0x202e
            or 0x2066 <= code <= 0x2069 or code == 0xfeff)


def name_refusal(user_id, name, bot, user_prefix, server_name):
    """Why this name can't be set, or None: only the bot's own or a site account's (@<prefix><digits>:<server>),
    plain text of 1 to 100 characters with no control characters."""
    member = re.fullmatch(rf"@{re.escape(user_prefix)}\d+:{re.escape(server_name)}", user_id or "")
    if user_id != bot and not member:
        return "not a site account"
    if not isinstance(name, str) or not name.strip() or len(name) > MAX_NAME:
        return "a name is 1 to 100 characters"
    if any(_hidden(ord(c)) for c in name):
        return "no control or direction characters"
    return None


def _plain(value, label):
    if not isinstance(value, str) or not value or ":" in value or value.startswith("@"):
        raise ValueError(f"uqqus_rules: {label} must be a plain localpart part, such as ruqqus_")
    return value


def parse_config(config):
    config = config or {}
    return {"bot_localpart": _plain(config.get("bot_localpart", "ruqqus_bot"), "bot_localpart"),
            "user_prefix": _plain(config.get("user_prefix", "ruqqus_"), "user_prefix")}


class UqqusRules:
    """Synapse loads this from `modules:` with `module: uqqus_rules.UqqusRules`."""

    def __init__(self, config, api):
        from synapse.api.errors import Codes

        self._forbidden = Codes.FORBIDDEN
        self._api = api
        self._bot = bot_id(config["bot_localpart"], api.server_name)
        self._prefix = config["user_prefix"]
        api.register_web_resource(NAME_PATH, _name_resource(self))
        api.register_spam_checker_callbacks(
            user_may_create_room=self.user_may_create_room,
            user_may_invite=self.user_may_invite,
            user_may_send_3pid_invite=self.user_may_send_3pid_invite,
            user_may_join_room=self.user_may_join_room,
            user_may_publish_room=self.user_may_publish_room,
            user_may_create_room_alias=self.user_may_create_room_alias,
            user_may_send_state_event=self.user_may_send_state_event,
            check_media_file_for_spam=self.check_media_file_for_spam,
            check_event_for_spam=self.check_event_for_spam,
        )

    @staticmethod
    def parse_config(config):
        return parse_config(config)

    def _answer(self, allowed):
        return NOT_SPAM if allowed else self._forbidden

    async def user_may_create_room(self, user_id, room_config=None):
        return self._answer(may_create_room(user_id, self._bot))

    async def user_may_invite(self, inviter, invitee, room_id):
        return self._answer(may_invite(inviter, self._bot))

    async def user_may_send_3pid_invite(self, inviter, medium, address, room_id):
        return self._answer(may_invite(inviter, self._bot))

    async def user_may_join_room(self, user_id, room_id, is_invited):
        if may_join(user_id, is_invited, self._bot):
            return NOT_SPAM
        return self._answer(may_join(user_id, False, self._bot, await self._joined(room_id, user_id)))

    async def _joined(self, room_id, user_id):
        state = await self._api.get_room_state(room_id, [("m.room.member", user_id)])
        event = state.get(("m.room.member", user_id))
        return event is not None and event.membership == "join"

    async def user_may_publish_room(self, user_id, room_id):
        return self._answer(may_publish(user_id, self._bot))

    async def user_may_create_room_alias(self, user_id, room_alias):
        return self._answer(may_create_alias(user_id, self._bot))

    async def user_may_send_state_event(self, user_id, room_id, event_type, state_key, content):
        return self._answer(may_send_state(user_id, self._bot))

    async def check_media_file_for_spam(self, file_wrapper, file_info):
        return self._answer(may_upload_media())

    async def check_event_for_spam(self, event):
        return self._answer(may_send_event(event.type, event.sender, self._bot))


def _name_resource(module):
    """POST {"user_id", "display_name"} with the appservice's token: the site names an account's chat profile."""
    from synapse.api.errors import Codes, SynapseError
    from synapse.module_api import DirectServeJsonResource, UserID, parse_json_object_from_request

    class NameResource(DirectServeJsonResource):
        async def _async_render_POST(self, request):
            requester = await module._api.get_user_by_req(request)
            if not may_name(requester.user.to_string(), requester.app_service_id is not None, module._bot):
                raise SynapseError(403, "Only the site sets chat names", Codes.FORBIDDEN)
            body = parse_json_object_from_request(request)
            user_id, name = body.get("user_id"), body.get("display_name")
            problem = name_refusal(user_id, name, module._bot, module._prefix, module._api.server_name)
            if problem:
                raise SynapseError(400, problem, Codes.INVALID_PARAM)
            await module._api.set_displayname(UserID.from_string(user_id), name.strip())
            return 200, {}

    return NameResource()
